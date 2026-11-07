from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional

from django.contrib.auth import get_user_model
from django.db.models import (
    Avg, Count, DecimalField, ExpressionWrapper, F, IntegerField, Max, Min, OuterRef, Q, Subquery, Sum, Value,
)
from django.db.models.functions import Coalesce, ExtractMonth, ExtractYear, TruncMonth
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.categories.models import Category
from apps.documents.models import Document
from apps.expenses.models import Expense
from apps.suppliers.models import Supplier

User = get_user_model()


EXPENSE_TOTAL = ExpressionWrapper(
    F('amount') + F('tax_amount') - F('discount_amount') + F('tip_amount'),
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


def count_rows(queryset):
    rows = queryset.order_by().annotate(_one=Value(1)).values('_one').annotate(total=Count('pk')).values('total')
    return Coalesce(Subquery(rows, output_field=IntegerField()), 0)


def _parse_bound(value: Optional[str], end: bool = False) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            moment = datetime.combine(day, time.max if end else time.min)
            return timezone.make_aware(moment)
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return moment if timezone.is_aware(moment) else timezone.make_aware(moment)


def _get_date_range(request):
    now = timezone.now()
    end_date = _parse_bound(request.query_params.get('end_date'), end=True) or now
    start_date = _parse_bound(request.query_params.get('start_date'))
    if start_date:
        return start_date, end_date

    try:
        months_count = int(request.query_params.get('months') or 6)
    except (TypeError, ValueError):
        months_count = 6
    start_date = end_date - timedelta(days=30 * months_count)
    return start_date, end_date


class DashboardViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='summary')
    def summary(self, request):
        start_date, end_date = _get_date_range(request)
        user = request.user

        previous_start = start_date - (end_date - start_date)
        current = Q(incurred_at__gte=start_date, incurred_at__lte=end_date)
        previous = Q(incurred_at__gte=previous_start, incurred_at__lt=start_date)
        expense_stats = Expense.objects.filter(user=user, incurred_at__gte=previous_start, incurred_at__lte=end_date).aggregate(
            total=Sum(EXPENSE_TOTAL, filter=current, default=Decimal('0')),
            count=Count('id', filter=current),
            avg=Avg(EXPENSE_TOTAL, filter=current, default=Decimal('0')),
            max=Max(EXPENSE_TOTAL, filter=current),
            min=Min(EXPENSE_TOTAL, filter=current),
            previous_total=Sum(EXPENSE_TOTAL, filter=previous, default=Decimal('0')),
        )
        expense_total = expense_stats['total'] or Decimal('0')
        previous_expense_total = expense_stats['previous_total'] or Decimal('0')
        if previous_expense_total > 0:
            change_pct = float(expense_total - previous_expense_total) / float(previous_expense_total) * 100
        else:
            change_pct = 0.0 if expense_total == 0 else 100.0

        in_period = Document.objects.filter(user=OuterRef('pk'), created_at__gte=start_date, created_at__lte=end_date)
        counts = User.objects.filter(pk=user.pk).annotate(
            documents_total=count_rows(in_period),
            documents_completed=count_rows(in_period.filter(status=Document.STATUS_COMPLETED)),
            suppliers_total=count_rows(Supplier.objects.filter(user=OuterRef('pk'))),
            categories_total=count_rows(
                Category.objects.filter(Q(scope=Category.SCOPE_SYSTEM) | Q(user=OuterRef('pk')))
            ),
        ).values('documents_total', 'documents_completed', 'suppliers_total', 'categories_total').get()

        data = {
            'total_expenses': expense_total,
            'expenses_count': expense_stats['count'],
            'average_expense': expense_stats['avg'] or 0,
            'max_expense': expense_stats['max'],
            'min_expense': expense_stats['min'],
            'documents_count': counts['documents_total'],
            'documents_processed': counts['documents_completed'],
            'suppliers_count': counts['suppliers_total'],
            'categories_count': counts['categories_total'],
            'expense_variation_pct': round(change_pct, 2),
            'period': {
                'start': start_date.isoformat(),
                'end': end_date.isoformat(),
            },
        }
        return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='monthly-expenses')
    def monthly_expenses(self, request):
        start_date, end_date = _get_date_range(request)
        user = request.user
        qs = (
            Expense.objects.filter(
                user=user,
                incurred_at__gte=start_date,
                incurred_at__lte=end_date,
            )
            .annotate(month=TruncMonth('incurred_at'))
            .values('month')
            .annotate(
                count=Count('id'),
                total=Sum('amount', default=Decimal('0'))
                + Sum('tax_amount', default=Decimal('0'))
                - Sum('discount_amount', default=Decimal('0'))
                + Sum('tip_amount', default=Decimal('0')),
            )
            .order_by('month')
        )
        data = [
            {
                'month': entry['month'].strftime('%Y-%m') if entry['month'] else None,
                'label': entry['month'].strftime('%b %Y') if entry['month'] else None,
                'count': entry['count'],
                'total': entry['total'] or 0,
            }
            for entry in qs
        ]
        return Response(
            {'status': 'success', 'data': data},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='expenses-by-category')
    def expenses_by_category(self, request):
        start_date, end_date = _get_date_range(request)
        user = request.user
        qs = (
            Expense.objects.filter(
                user=user,
                incurred_at__gte=start_date,
                incurred_at__lte=end_date,
            )
            .values(
                'category__id',
                'category__name',
                'category__icon',
                'category__color',
                'category__scope',
            )
            .annotate(
                count=Count('id'),
                total=Sum('amount', default=Decimal('0'))
                + Sum('tax_amount', default=Decimal('0'))
                - Sum('discount_amount', default=Decimal('0'))
                + Sum('tip_amount', default=Decimal('0')),
            )
            .order_by('-total')
        )
        data = [
            {
                'category_id': entry['category__id'],
                'name': entry['category__name'],
                'icon': entry['category__icon'],
                'color': entry['category__color'],
                'scope': entry['category__scope'],
                'count': entry['count'],
                'total': entry['total'] or 0,
            }
            for entry in qs
        ]
        return Response(
            {'status': 'success', 'data': data},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='expenses-by-supplier')
    def expenses_by_supplier(self, request):
        start_date, end_date = _get_date_range(request)
        user = request.user
        qs = (
            Expense.objects.filter(
                user=user,
                incurred_at__gte=start_date,
                incurred_at__lte=end_date,
                supplier__isnull=False,
            )
            .values('supplier__id', 'supplier__name', 'supplier__tax_id')
            .annotate(
                count=Count('id'),
                total=Sum('amount', default=Decimal('0'))
                + Sum('tax_amount', default=Decimal('0'))
                - Sum('discount_amount', default=Decimal('0'))
                + Sum('tip_amount', default=Decimal('0')),
            )
            .order_by('-total')[:20]
        )
        data = [
            {
                'supplier_id': entry['supplier__id'],
                'name': entry['supplier__name'],
                'tax_id': entry['supplier__tax_id'],
                'count': entry['count'],
                'total': entry['total'] or 0,
            }
            for entry in qs
        ]
        return Response(
            {'status': 'success', 'data': data},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='recent-expenses')
    def recent_expenses(self, request):
        user = request.user
        try:
            limit = min(int(request.query_params.get('limit', 10)), 50)
        except (TypeError, ValueError):
            limit = 10
        qs = (
            Expense.objects.filter(user=user)
            .select_related('category', 'supplier', 'document')
            .order_by('-incurred_at')[:limit]
        )
        data = [
            {
                'id': exp.id,
                'description': exp.description,
                'amount': float(exp.total_amount),
                'currency': exp.currency,
                'incurred_at': exp.incurred_at.isoformat(),
                'category': {
                    'id': exp.category_id,
                    'name': exp.category.name if exp.category else None,
                    'color': exp.category.color if exp.category else None,
                    'icon': exp.category.icon if exp.category else None,
                },
                'supplier': {
                    'id': exp.supplier_id,
                    'name': exp.supplier.name if exp.supplier else None,
                },
                'document_id': exp.document_id,
            }
            for exp in qs
        ]
        return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='recent-documents')
    def recent_documents(self, request):
        user = request.user
        try:
            limit = min(int(request.query_params.get('limit', 10)), 50)
        except (TypeError, ValueError):
            limit = 10
        qs = (
            Document.objects.filter(user=user)
            .select_related('supplier', 'category')
            .order_by('-created_at')[:limit]
        )
        data = [
            {
                'id': doc.id,
                'title': doc.title or doc.original_filename or f'Document #{doc.id}',
                'document_type': doc.document_type,
                'status': doc.status,
                'total_amount': float(doc.total_amount) if doc.total_amount else None,
                'currency': doc.currency,
                'supplier_name': doc.supplier.name if doc.supplier else None,
                'category_name': doc.category.name if doc.category else None,
                'created_at': doc.created_at.isoformat(),
                'processed_at': doc.processed_at.isoformat() if doc.processed_at else None,
                'issued_at': doc.issued_at.isoformat() if doc.issued_at else None,
            }
            for doc in qs
        ]
        return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='expense-evolution')
    def expense_evolution(self, request):
        start_date, end_date = _get_date_range(request)
        user = request.user
        months = sorted(set())
        results = {}
        current = start_date.replace(day=1)
        end_month = end_date.replace(day=1)
        while current <= end_month:
            key = current.strftime('%Y-%m')
            months.append(key)
            results[key] = {'total': Decimal('0'), 'count': 0}
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)

        qs = (
            Expense.objects.filter(
                user=user,
                incurred_at__gte=start_date,
                incurred_at__lte=end_date,
            )
            .annotate(
                year=ExtractYear('incurred_at'),
                month=ExtractMonth('incurred_at'),
            )
            .values('year', 'month')
            .annotate(
                count=Count('id'),
                total=Sum('amount', default=Decimal('0'))
                + Sum('tax_amount', default=Decimal('0'))
                - Sum('discount_amount', default=Decimal('0'))
                + Sum('tip_amount', default=Decimal('0')),
            )
        )
        for entry in qs:
            key = f"{entry['year']:04d}-{entry['month']:02d}"
            if key in results:
                results[key] = {
                    'total': entry['total'] or Decimal('0'),
                    'count': entry['count'],
                }
        data = [
            {
                'month': key,
                'total': float(results[key]['total']),
                'count': results[key]['count'],
            }
            for key in months
        ]
        return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)
