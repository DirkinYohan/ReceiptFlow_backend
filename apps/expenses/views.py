from datetime import date
from decimal import Decimal

from django.db.models import Avg, Count, Max, Min, Sum, Q
from django.http import HttpResponse
from django.utils import timezone
from django_filters import rest_framework as filters
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.categories.budgets import check_budget_alert_safely
from apps.expenses.automation import learn_supplier_category
from apps.expenses.exports import build_csv, build_xlsx, export_queryset
from apps.expenses.recurring import recurring_overview, refresh_supplier_recurrence
from apps.common.pagination import StandardPagination
from apps.common.permissions import IsOwnerOrAdmin
from apps.documents.models import Document

from .models import Expense
from .serializers import (
    ExpenseCreateFromDocumentSerializer,
    ExpenseDetailSerializer,
    ExpenseFilter,
    ExpenseSerializer,
    ExpenseStatsSerializer,
)
from .services import expense_service



def _parse_day(value):
    return date.fromisoformat(value[:10]) if value else None


class ExpenseViewSet(viewsets.ModelViewSet):
    serializer_class = ExpenseSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    pagination_class = StandardPagination
    filter_backends = list(viewsets.ModelViewSet.filter_backends) + [
        filters.DjangoFilterBackend,
    ]
    filterset_class = ExpenseFilter
    search_fields = [
        'description', 'notes',
        'supplier__name', 'supplier__tax_id',
        'category__name',
    ]
    ordering_fields = [
        '-incurred_at', 'incurred_at',
        '-amount', 'amount',
        '-total_amount', 'total_amount',
        '-created_at', 'created_at',
        'payment_method', 'currency',
    ]
    ordering = ['-incurred_at']
    lookup_field = 'pk'

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Expense.objects.none()
        qs = (
            Expense.objects.filter(user=self.request.user)
            .select_related('user', 'category', 'supplier', 'document')
            .annotate(line_items_count=Count('line_items', distinct=True))
        )
        if self.action in ('retrieve', 'create_from_document'):
            qs = qs.prefetch_related('line_items')
        return qs

    def get_serializer_class(self):
        if self.action in ('retrieve', 'create_from_document'):
            return ExpenseDetailSerializer
        return ExpenseSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['request'] = self.request
        return ctx

    def perform_create(self, serializer):
        expense = serializer.save(user=self.request.user)
        learn_supplier_category(expense.supplier, expense.category)
        refresh_supplier_recurrence(expense.user, expense.supplier)
        check_budget_alert_safely(expense)

    def perform_update(self, serializer):
        expense = serializer.save()
        if 'category' in serializer.validated_data:
            learn_supplier_category(expense.supplier, expense.category)
        check_budget_alert_safely(expense)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        if response.status_code == status.HTTP_201_CREATED:
            instance = Expense.objects.get(pk=response.data['id'])
            return Response(
                {
                    'status': 'success',
                    'message': 'Gasto registrado correctamente.',
                    'data': ExpenseDetailSerializer(
                        instance, context={'request': request}
                    ).data,
                },
                status=status.HTTP_201_CREATED,
            )
        return response

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {'status': 'success', 'message': 'Detalle de gasto.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = ExpenseSerializer(
            instance, data=request.data, partial=partial, context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(
            {
                'status': 'success',
                'message': 'Gasto actualizado correctamente.',
                'data': ExpenseDetailSerializer(
                    instance, context={'request': request}
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.line_items.all().delete()
        self.perform_destroy(instance)
        return Response(
            {'status': 'success', 'message': 'Gasto eliminado correctamente.'},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['post'], url_path='from-document')
    def create_from_document(self, request):
        serializer = ExpenseCreateFromDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        overrides = {
            'category': data.get('category_id'),
            'supplier_id': data.get('supplier_id'),
            'description': data.get('description', ''),
            'payment_method': data.get('payment_method', ''),
            'notes': data.get('notes', ''),
        }
        from apps.categories.models import Category
        if overrides['category'] is not None:
            try:
                overrides['category'] = Category.objects.filter(
                    pk=overrides['category'],
                ).filter(
                    Q(scope=Category.SCOPE_SYSTEM) | Q(user=request.user)
                ).first()
            except Exception:
                overrides['category'] = None
        try:
            expense = expense_service.create_from_document(
                request.user, data['document_id'], **overrides,
            )
        except ValueError as exc:
            return Response(
                {'status': 'error', 'message': str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if expense is None:
            return Response(
                {'status': 'error', 'message': 'Documento no encontrado.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        if overrides['category'] is not None:
            learn_supplier_category(expense.supplier, expense.category)
        return Response(
            {
                'status': 'success',
                'message': 'Gasto creado a partir del documento.',
                'data': ExpenseDetailSerializer(expense, context={'request': request}).data,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=['get'], url_path='export')
    def export(self, request):
        # "file_format", not "format": DRF reserves ?format= for renderer negotiation.
        file_format = (request.query_params.get('file_format') or 'xlsx').lower()
        if file_format not in ('xlsx', 'csv'):
            return Response(
                {'status': 'error', 'message': 'Formato no soportado. Usa xlsx o csv.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            start = _parse_day(request.query_params.get('start_date'))
            end = _parse_day(request.query_params.get('end_date'))
        except ValueError:
            return Response(
                {'status': 'error', 'message': 'Fechas inválidas. Usa AAAA-MM-DD.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        expenses = export_queryset(
            request.user, start, end, request.query_params.get('category') or None,
        )
        period = f"{start or 'inicio'}_{end or timezone.localdate()}"
        if file_format == 'csv':
            content, content_type = build_csv(expenses), 'text/csv; charset=utf-8'
        else:
            content = build_xlsx(expenses, f'Gastos {period}')
            content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        response = HttpResponse(content, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="receiptflow_gastos_{period}.{file_format}"'
        return response

    @action(detail=False, methods=['get'], url_path='recurring')
    def recurring(self, request):
        return Response(
            {'status': 'success', 'data': recurring_overview(request.user)},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        qs = self.filter_queryset(self.get_queryset())
        agg = qs.aggregate(
            count=Count('id'),
            total_amount=Sum('amount', default=Decimal('0.00'))
            + Sum('tax_amount', default=Decimal('0.00'))
            - Sum('discount_amount', default=Decimal('0.00'))
            + Sum('tip_amount', default=Decimal('0.00')),
            average_amount=Avg('amount', default=Decimal('0.00')),
            max_amount=Max('amount'),
            min_amount=Min('amount'),
            recurring_count=Count('id', filter=Q(is_recurring=True)),
            reimbursable_total=Sum(
                'amount',
                default=Decimal('0.00'),
                filter=Q(is_reimbursable=True),
            ),
        )
        return Response(
            {'status': 'success', 'data': agg},
            status=status.HTTP_200_OK,
        )
