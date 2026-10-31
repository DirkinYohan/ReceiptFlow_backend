from __future__ import annotations

import logging
from time import perf_counter

from django.db import transaction
from django.db.models import Count, Sum, Q
from django_filters import rest_framework as filters
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.expenses.automation import learn_supplier_category, sync_expense_safely
from apps.documents.payables import payables_overview
from apps.documents.review_messages import humanize_all
from apps.common.pagination import StandardPagination
from apps.common.permissions import IsOwnerOrAdmin
from apps.common.ratelimit import (
    ratelimit_documents_create,
    ratelimit_documents_process,
)
from apps.common.validators import validate_uploaded_file

from .models import Document
from .serializers import (
    DocumentCreateSerializer,
    DocumentDetailSerializer,
    DocumentFilter,
    DocumentSerializer,
    DocumentStatsSerializer,
    DocumentUpdateExtractedDataSerializer,
)
from .services import document_service

logger = logging.getLogger(__name__)




def _is_stale(document) -> bool:
    from django.conf import settings
    from django.utils import timezone

    started = document.processing_started_at
    limit = timezone.timedelta(minutes=getattr(settings, 'PROCESSING_TIMEOUT_MINUTES', 15))
    return started is None or timezone.now() - started > limit


def _review_reasons(document) -> list[str]:
    raw = [r for r in (document.processing_error or '').split('; ') if r.strip()]
    raw += list((document.structured_data or {}).get('warnings') or [])
    return humanize_all(raw, currency=document.currency or '')


def _missing_fields(document) -> list[str]:
    checks = {
        'total_amount': document.total_amount,
        'supplier': document.supplier_id,
        'issued_at': document.issued_at,
        'category': document.category_id,
    }
    return [field for field, value in checks.items() if not value]

def _after_user_edit(document, category_changed: bool) -> None:
    """A human just reviewed this document: learn from it and keep its expense in sync."""
    if category_changed:
        learn_supplier_category(document.supplier, document.category)
    sync_expense_safely(document, user_confirmed=True)


class DocumentViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    pagination_class = StandardPagination
    filter_backends = list(viewsets.ModelViewSet.filter_backends) + [
        filters.DjangoFilterBackend,
    ]
    filterset_class = DocumentFilter
    search_fields = [
        'title', 'original_filename', 'document_number',
        'supplier__name', 'category__name', 'notes',
    ]
    ordering_fields = [
        '-created_at', 'created_at', '-issued_at', 'issued_at',
        '-total_amount', 'total_amount', '-processed_at', 'processed_at',
        'status', 'document_type',
    ]
    ordering = ['-created_at']
    lookup_field = 'pk'

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Document.objects.none()
        qs = (
            Document.objects.filter(user=self.request.user)
            .select_related('user', 'supplier', 'category')
            .annotate(line_items_count=Count('line_items', distinct=True))
        )
        if self.action in ('retrieve', 'update_extracted_data'):
            qs = qs.prefetch_related('line_items')
        return qs

    def get_serializer_class(self):
        if self.action == 'create':
            return DocumentCreateSerializer
        if self.action in ('retrieve', 'update_extracted_data'):
            return DocumentDetailSerializer
        if self.action == 'stats':
            return DocumentStatsSerializer
        return DocumentSerializer

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['request'] = self.request
        return ctx

    @ratelimit_documents_create()
    def create(self, request, *args, **kwargs):
        file_obj = request.data.get('file')
        if file_obj is None:
            return Response(
                {'status': 'error', 'message': 'Se requiere un archivo para subir.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            validate_uploaded_file(file_obj)
        except Exception as exc:
            messages = getattr(exc, 'messages', None)
            if messages is None:
                messages = [str(exc)]
            return Response(
                {
                    'status': 'error',
                    'message': 'El archivo no cumple los requisitos.',
                    'errors': [{'field': 'file', 'message': str(msg)} for msg in messages],
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        document_type = (request.data.get('document_type') or Document.DOCUMENT_TYPE_RECEIPT)
        title = request.data.get('title', '') or ''
        notes = request.data.get('notes', '') or ''
        extra = {'document_type': document_type, 'title': title, 'notes': notes}
        try:
            document = document_service.create_document(request.user, file_obj, **extra)
        except Exception as exc:
            logger.exception('Error creating document for user %s', request.user.pk)
            return Response(
                {'status': 'error', 'message': f'Error al subir el documento: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # Enqueue background OCR task asynchronously
        try:
            from .tasks import process_document_task
            transaction.on_commit(lambda: process_document_task.delay(document_id=document.pk))
        except Exception as exc:
            logger.warning('Could not enqueue async processing task for %s: %s', document.pk, exc)

        serializer = DocumentDetailSerializer(instance=document, context={'request': request})
        return Response(
            {
                'status': 'success',
                'message': 'Documento subido correctamente.',
                'data': serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {'status': 'success', 'message': 'Detalle de documento.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = DocumentSerializer(page, many=True)
            paginated = self.paginator.get_paginated_response(serializer.data)
            return paginated
        serializer = DocumentSerializer(queryset, many=True)
        return Response(
            {'status': 'success', 'message': 'Documentos.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        if instance.status == Document.STATUS_PROCESSING:
            return Response(
                {'status': 'error', 'message': 'No puedes editar un documento en procesamiento.'},
                status=status.HTTP_409_CONFLICT,
            )
        allowed = {
            'title', 'notes', 'supplier', 'category',
            'issued_at', 'due_date', 'currency', 'payment_method',
            'document_type', 'document_number', 'is_paid',
            'subtotal_amount', 'total_amount', 'tax_amount',
            'discount_amount', 'tip_amount',
        }
        data = {k: v for k, v in request.data.items() if k in allowed}
        serializer = DocumentDetailSerializer(
            instance, data=data, partial=True, context={'request': request},
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        _after_user_edit(instance, category_changed='category' in data)
        return Response(
            {
                'status': 'success',
                'message': 'Documento actualizado correctamente.',
                'data': DocumentDetailSerializer(
                    instance=instance, context={'request': request}
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            document_service.delete_document_safe(instance)
        except Exception as exc:
            logger.exception('Error deleting document %s', instance.pk)
            return Response(
                {'status': 'error', 'message': f'Error al eliminar el documento: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(
            {'status': 'success', 'message': 'Documento eliminado correctamente.'},
            status=status.HTTP_200_OK,
        )

    @ratelimit_documents_process()
    @action(detail=True, methods=['post'], url_path='process')
    def process_document(self, request, pk=None):
        document = self.get_object()
        if document.status == Document.STATUS_PROCESSING:
            return Response(
                {
                    'status': 'error',
                    'message': 'El documento ya está siendo procesado.',
                    'data': {
                        'status': document.status,
                        'processing_started_at': (
                            document.processing_started_at.isoformat()
                            if document.processing_started_at else None
                        ),
                    },
                },
                status=status.HTTP_409_CONFLICT,
            )
        if document.processing_attempts >= 5:
            return Response(
                {
                    'status': 'error',
                    'message': (
                        'Se alcanzó el máximo de intentos de procesamiento. '
                        'Contacta soporte si el problema persiste.'
                    ),
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        try:
            from .tasks import process_document_task
        except Exception as imp_exc:
            logger.warning('Celery tasks not importable, falling back to sync pipeline: %s', imp_exc)
            process_document_task = None

        if process_document_task is None:
            try:
                from services.document_processor.processor import process_document_pipeline
            except Exception:
                process_document_pipeline = None
            if process_document_pipeline is None:
                document.mark_failed('Pipeline de procesamiento no disponible.')
                return Response(
                    {'status': 'error', 'message': 'Servicio de OCR no disponible.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            document.mark_processing()
            t0 = perf_counter()
            try:
                result = process_document_pipeline(document)
                processing_ms = (perf_counter() - t0) * 1000
                if result.get('requires_review'):
                    document.mark_review_required(result.get('warning', ''))
                else:
                    document.mark_completed(processing_time_ms=int(processing_ms))
                    sync_expense_safely(document)
            except Exception as exc:
                logger.exception('Sync processing failed for document %s', document.pk)
                document.mark_failed(str(exc)[:500])
                return Response(
                    {'status': 'error', 'message': 'Error durante el procesamiento.', 'errors': [str(exc)]},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            return Response(
                {
                    'status': 'success',
                    'message': 'Documento procesado.',
                    'data': DocumentDetailSerializer(
                        document, context={'request': request}
                    ).data,
                },
                status=status.HTTP_200_OK,
            )

        document.mark_processing()
        try:
            task_result = process_document_task.delay(document_id=document.pk)
        except Exception as exc:
            logger.exception('Failed to enqueue process_document_task for %s', document.pk)
            document.mark_failed(f'No se pudo encolar la tarea: {exc}')
            return Response(
                {'status': 'error', 'message': 'No se pudo programar el procesamiento.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                'status': 'success',
                'message': (
                    'Documento enviado a procesamiento de forma asíncrona. '
                    'El estado se actualizará automáticamente cuando finalice.'
                ),
                'data': {
                    'task_id': task_result.id if hasattr(task_result, 'id') else None,
                    'document_id': document.pk,
                    'status': document.status,
                    'processing_started_at': (
                        document.processing_started_at.isoformat()
                        if document.processing_started_at else None
                    ),
                    'processing_attempts': document.processing_attempts,
                },
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(detail=True, methods=['post'], url_path='retry')
    def retry_processing(self, request, pk=None):
        document = self.get_object()
        retryable = (Document.STATUS_FAILED, Document.STATUS_REVIEW_REQUIRED, Document.STATUS_PENDING)
        if document.status == Document.STATUS_PROCESSING and _is_stale(document):
            document.status = Document.STATUS_FAILED
        if document.status == Document.STATUS_PROCESSING:
            return Response(
                {'status': 'error', 'message': 'Ya se está leyendo este documento. Espera unos segundos.'},
                status=status.HTTP_409_CONFLICT,
            )
        if document.status not in retryable:
            return Response(
                {
                    'status': 'error',
                    'message': (
                        f'No se puede reintentar un documento en estado "{document.get_status_display()}".'
                    ),
                },
                status=status.HTTP_409_CONFLICT,
            )
        document.status = Document.STATUS_PENDING
        document.processing_error = ''
        document.processed_at = None
        document.processing_attempts = 0
        document.save(
            update_fields=['status', 'processing_error', 'processed_at', 'processing_attempts', 'updated_at']
        )
        return self.process_document(request, pk=pk)

    @action(detail=True, methods=['put', 'patch'], url_path='extracted-data')
    def update_extracted_data(self, request, pk=None):
        document = self.get_object()
        if document.status == Document.STATUS_PROCESSING:
            return Response(
                {'status': 'error', 'message': 'Documento en procesamiento.'},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = DocumentUpdateExtractedDataSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        status_value = data.pop('status', None)
        line_items = data.pop('line_items', None)
        if 'supplier_id' in data:
            sid = data.pop('supplier_id')
            if sid is None:
                document.supplier = None
            else:
                from apps.suppliers.models import Supplier
                try:
                    document.supplier = Supplier.objects.get(pk=sid, user=request.user)
                except Supplier.DoesNotExist:
                    return Response(
                        {'status': 'error', 'message': 'Proveedor no encontrado.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )
        supplier_name = (data.pop('supplier_name', '') or '').strip()
        if supplier_name and 'supplier_id' not in data:
            from apps.suppliers.models import Supplier
            document.supplier, _ = Supplier.objects.get_or_create_for_user(
                user=request.user, name=supplier_name,
            )
        category_changed = 'category_id' in data
        if 'category_id' in data:
            cid = data.pop('category_id')
            if cid is None:
                document.category = None
            else:
                from apps.categories.models import Category
                try:
                    cat = Category.objects.filter(
                        Q(pk=cid),
                        Q(scope=Category.SCOPE_SYSTEM) | Q(user=request.user),
                    ).first()
                    if not cat:
                        raise Category.DoesNotExist
                    document.category = cat
                except Category.DoesNotExist:
                    return Response(
                        {'status': 'error', 'message': 'Categoría no encontrada.'},
                        status=status.HTTP_404_NOT_FOUND,
                    )
        for field, value in data.items():
            setattr(document, field, value)
        if status_value:
            document.status = status_value
        document.save()
        if line_items is not None:
            document_service.set_line_items(document, line_items)
        _after_user_edit(document, category_changed=category_changed)
        return Response(
            {
                'status': 'success',
                'message': 'Datos del documento actualizados.',
                'data': DocumentDetailSerializer(
                    document, context={'request': request}
                ).data,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='review-queue')
    def review_queue(self, request):
        docs = (
            Document.objects.filter(
                user=request.user,
                status__in=[Document.STATUS_REVIEW_REQUIRED, Document.STATUS_FAILED],
            )
            .select_related('supplier', 'category')
            .prefetch_related('line_items')
            .order_by('created_at')
        )
        items = []
        for doc in docs:
            data = DocumentDetailSerializer(doc, context={'request': request}).data
            data['review_reasons'] = _review_reasons(doc)
            data['missing_fields'] = _missing_fields(doc)
            items.append(data)
        return Response(
            {'status': 'success', 'data': {'count': len(items), 'results': items}},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='payables')
    def payables(self, request):
        return Response(
            {'status': 'success', 'data': payables_overview(request.user)},
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=['get'], url_path='stats')
    def stats(self, request):
        qs = Document.objects.filter(user=request.user)
        agg = qs.aggregate(
            total=Count('id'),
            pending=Count('id', filter=Q(status=Document.STATUS_PENDING)),
            processing=Count('id', filter=Q(status=Document.STATUS_PROCESSING)),
            completed=Count('id', filter=Q(status=Document.STATUS_COMPLETED)),
            review_required=Count('id', filter=Q(status=Document.STATUS_REVIEW_REQUIRED)),
            failed=Count('id', filter=Q(status=Document.STATUS_FAILED)),
            paid=Count('id', filter=Q(is_paid=True)),
            unpaid=Count('id', filter=Q(is_paid=False)),
            total_amount_sum=Sum('total_amount', default=0),
        )
        return Response(
            {'status': 'success', 'data': agg},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['get'], url_path='status')
    def processing_status(self, request, pk=None):
        document = self.get_object()
        return Response(
            {
                'status': 'success',
                'data': {
                    'id': document.id,
                    'status': document.status,
                    'status_display': document.get_status_display(),
                    'processing_started_at': (
                        document.processing_started_at.isoformat()
                        if document.processing_started_at else None
                    ),
                    'processed_at': (
                        document.processed_at.isoformat()
                        if document.processed_at else None
                    ),
                    'processing_time_ms': document.processing_time_ms,
                    'processing_attempts': document.processing_attempts,
                    'processing_error': document.processing_error,
                    'requires_review': document.requires_review,
                    'is_processed': document.is_processed,
                },
            },
            status=status.HTTP_200_OK,
        )
