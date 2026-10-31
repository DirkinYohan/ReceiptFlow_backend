from __future__ import annotations

import logging
import os
from time import perf_counter
from typing import Optional

from celery import Task, shared_task
from celery.exceptions import SoftTimeLimitExceeded, Ignore
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Document

logger = logging.getLogger(__name__)

PROCESSING_LOCK_KEY_TEMPLATE = 'receiptflow:document:processing:lock:{document_id}'
DOCUMENT_CACHE_KEY_TEMPLATE = 'receiptflow:document:data:{document_id}'
CACHE_LOCK_TIMEOUT = 60 * 30


def _acquire_processing_lock(document_id) -> bool:
    key = PROCESSING_LOCK_KEY_TEMPLATE.format(document_id=document_id)
    return cache.add(key, '1', timeout=CACHE_LOCK_TIMEOUT)


def _release_processing_lock(document_id) -> None:
    key = PROCESSING_LOCK_KEY_TEMPLATE.format(document_id=document_id)
    try:
        cache.delete(key)
    except Exception:
        pass


def _is_duplicate_task(document_id) -> bool:
    key = PROCESSING_LOCK_KEY_TEMPLATE.format(document_id=document_id)
    return cache.get(key) is not None


def _sanitize_error(exc: BaseException) -> str:
    safe = str(exc) or exc.__class__.__name__
    if len(safe) > 400:
        safe = safe[:400] + '...'
    for secret_token in (
        getattr(settings, 'SECRET_KEY', ''),
        getattr(settings, 'DB_PASSWORD', ''),
        os.environ.get('DB_PASSWORD', ''),
    ):
        if secret_token and secret_token in safe:
            safe = safe.replace(secret_token, '[REDACTED]')
    return safe


class DocumentTaskBase(Task):
    autoretry_for = (
        SoftTimeLimitExceeded,
        ConnectionError,
        TimeoutError,
    )
    retry_backoff = True
    retry_backoff_max = 120
    retry_jitter = True
    max_retries = getattr(settings, 'PROCESSING_MAX_ATTEMPTS', 3)
    acks_late = True
    reject_on_worker_lost = True
    time_limit = 60 * 20
    soft_time_limit = 60 * 15

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        doc_id = args[0] if args else kwargs.get('document_id')
        logger.error(
            'Document task failed for id=%s task_id=%s exc=%s',
            doc_id, task_id, _sanitize_error(exc),
        )
        if doc_id:
            _release_processing_lock(doc_id)
            try:
                doc = Document.objects.filter(pk=doc_id).only('status', 'processing_error').first()
                if doc and doc.status == Document.STATUS_PROCESSING:
                    doc.mark_failed(
                        f'Error en worker ({exc.__class__.__name__}). {_sanitize_error(exc)}'
                    )
            except Exception:
                logger.exception('Could not mark document %s as FAILED after worker failure', doc_id)

    def on_success(self, retval, task_id, args, kwargs):
        doc_id = args[0] if args else kwargs.get('document_id')
        if doc_id:
            _release_processing_lock(doc_id)


@shared_task(base=DocumentTaskBase, bind=True, name='apps.documents.tasks.process_document_task')
def process_document_task(self, document_id: int):
    if not isinstance(document_id, int) or document_id <= 0:
        logger.warning('Invalid document_id=%s, skipping.', document_id)
        return {'ok': False, 'reason': 'invalid_id'}

    try:
        document = Document.objects.get(pk=document_id)
    except Document.DoesNotExist:
        logger.warning('Document %s does not exist, abandoning task.', document_id)
        return {'ok': False, 'reason': 'not_found'}

    if document.status == Document.STATUS_COMPLETED:
        logger.info('Document %s already COMPLETED, skipping duplicate task.', document_id)
        return {'ok': True, 'skipped': True, 'reason': 'already_completed'}

    if not _acquire_processing_lock(document_id):
        logger.info('Could not acquire lock for document %s, skipping duplicate task.', document_id)
        return {'ok': True, 'skipped': True, 'reason': 'duplicate_lock'}

    if document.status != Document.STATUS_PROCESSING:
        document.mark_processing()

    t0 = perf_counter()
    try:
        from services.document_processor.processor import process_document_pipeline
        result = process_document_pipeline(document)
        processing_ms = (perf_counter() - t0) * 1000
        requires_review = bool(result.get('requires_review'))
        if requires_review:
            reasons = '; '.join(result.get('review_reasons', [])[:3]) or 'Campos incompletos.'
            document.mark_review_required(reasons)
        else:
            document.mark_completed(processing_time_ms=int(processing_ms))
            from apps.expenses.automation import sync_expense_safely
            expense = sync_expense_safely(document)
            if expense is not None:
                logger.info('Document %s auto-registered as expense %s', document_id, expense.pk)
        logger.info(
            'Document %s processed in %.1fms status=%s',
            document_id, processing_ms, document.status,
        )
        return {
            'ok': True,
            'document_id': document_id,
            'status': document.status,
            'processing_time_ms': int(processing_ms),
            'requires_review': requires_review,
        }
    except SoftTimeLimitExceeded as exc:
        processing_ms = (perf_counter() - t0) * 1000
        logger.warning('Document %s timed out after %.1fms', document_id, processing_ms)
        document.mark_failed(f'Tiempo de procesamiento excedido. {_sanitize_error(exc)}')
        raise self.retry(exc=exc, countdown=15)
    except Exception as exc:
        processing_ms = (perf_counter() - t0) * 1000
        safe_error = _sanitize_error(exc)
        logger.exception(
            'Document %s processing failed after %.1fms err=%s',
            document_id, processing_ms, safe_error,
        )
        retriable = any(
            name in exc.__class__.__name__
            for name in ('Timeout', 'Connection', 'Network', 'Temporary', 'Unavailable')
        )
        attempts = getattr(document, 'processing_attempts', 0) or 0
        max_attempts = getattr(settings, 'PROCESSING_MAX_ATTEMPTS', 3)
        if retriable and attempts < max_attempts:
            document.mark_failed(f'Error transitorio. Reintentando ({attempts}/{max_attempts})...')
            raise self.retry(exc=exc, countdown=10 + 2 * attempts)
        document.mark_failed(f'Fallo el procesamiento. {safe_error}')
        return {
            'ok': False,
            'document_id': document_id,
            'status': Document.STATUS_FAILED,
            'error': safe_error,
            'processing_time_ms': int(processing_ms),
        }
    finally:
        _release_processing_lock(document_id)


@shared_task(name='apps.documents.tasks.mark_stuck_processing_documents', ignore_result=True)
def mark_stuck_processing_documents(hours: int = 1):
    threshold_minutes = max(int(getattr(settings, 'PROCESSING_TIMEOUT_MINUTES', 15)), 5)
    threshold = timezone.now() - timezone.timedelta(minutes=threshold_minutes)
    stuck = Document.objects.filter(
        status=Document.STATUS_PROCESSING,
        processing_started_at__lte=threshold,
    )
    count = 0
    for doc in stuck.iterator():
        try:
            doc.mark_failed(
                f'Procesamiento marcado como atascado ({threshold_minutes} min sin respuesta del worker).'
            )
            _release_processing_lock(doc.pk)
            count += 1
        except Exception:
            logger.exception('Could not mark stuck document %s', doc.pk)
    logger.info('Marked %s stuck documents as FAILED (threshold=%s min).', count, threshold_minutes)
    return {'marked': count, 'threshold_minutes': threshold_minutes}


@shared_task(name='apps.documents.tasks.cleanup_old_token_blacklist', ignore_result=True)
def cleanup_old_token_blacklist(days: int = 30):
    try:
        from rest_framework_simplejwt.token_blacklist.models import (
            OutstandingToken, BlacklistedToken,
        )
        cutoff = timezone.now() - timezone.timedelta(days=max(int(days), 1))
        bl_qs = BlacklistedToken.objects.filter(token__expires_at__lt=cutoff)
        bl_count = bl_qs._result_cache if False else 0
        deleted_blacklist, _ = bl_qs.delete()
        out_qs = OutstandingToken.objects.filter(expires_at__lt=cutoff)
        deleted_outstanding, _ = out_qs.delete()
        logger.info(
            'Token cleanup: %s blacklisted, %s outstanding removed (cutoff=%sd).',
            deleted_blacklist, deleted_outstanding, days,
        )
        return {'blacklisted_removed': deleted_blacklist, 'outstanding_removed': deleted_outstanding}
    except Exception:
        logger.exception('Token blacklist cleanup failed.')
        return {'error': True}


@shared_task(name='apps.documents.tasks.cleanup_old_failed_documents', ignore_result=True)
def cleanup_old_failed_documents(days: int = 30):
    days = max(int(days), 7)
    cutoff = timezone.now() - timezone.timedelta(days=days)
    qs = Document.objects.filter(
        status=Document.STATUS_FAILED,
        processed_at__lt=cutoff,
    )
    logger.info(
        'Found %s failed documents older than %s days for manual review note.',
        qs.count(), days,
    )
    note_count = 0
    for doc in qs.only('id', 'notes', 'updated_at'):
        note = (doc.notes + ' | ' if doc.notes else '') + (
            f'[Mantenimiento] Fallido desde el {doc.updated_at.isoformat()}. Considerar reintento.'
        )
        doc.notes = note[:1000]
        try:
            doc.save(update_fields=['notes', 'updated_at'])
            note_count += 1
        except Exception:
            logger.exception('Could not annotate failed doc %s', doc.pk)
    logger.info('Annotated %s old failed documents.', note_count)
    return {'annotated': note_count}


@shared_task(name='apps.documents.tasks.cleanup_expired_password_reset_tokens', ignore_result=True)
def cleanup_expired_password_reset_tokens():
    try:
        from django.contrib.auth.tokens import default_token_generator
        from apps.users.models import User
        logger.info('Running auth token maintenance pass (no DB tokens exist; framework uses signed URLs).')
        return {'cleanup': 'signed_tokens_noop'}
    except Exception:
        logger.exception('Password reset token maintenance failed.')
        return {'error': True}


@shared_task(name='apps.documents.tasks.cleanup_orphan_media_files', ignore_result=True)
def cleanup_orphan_media_files(dry_run: bool = False, hours_old: int = 24):
    """
    Scans media/documents/ (when using filesystem storage) and safely removes orphaned
    files that have no corresponding Document record in the database and are older than `hours_old`.
    """
    import os
    import time
    from pathlib import Path

    media_root = getattr(settings, 'MEDIA_ROOT', None)
    if not media_root or not os.path.isdir(str(media_root)):
        return {'status': 'skipped', 'reason': 'local_media_root_not_found'}

    docs_dir = Path(media_root) / 'documents'
    if not docs_dir.exists():
        return {'status': 'skipped', 'reason': 'documents_dir_not_found'}

    # Collect all known filenames from Document model
    known_files = set(Document.objects.exclude(file='').values_list('file', flat=True))
    known_filenames = {os.path.basename(f) for f in known_files if f}

    cutoff_time = time.time() - (max(int(hours_old), 1) * 3600)
    scanned = 0
    removed = 0

    for file_path in docs_dir.rglob('*'):
        if file_path.is_file():
            scanned += 1
            try:
                if file_path.stat().st_mtime < cutoff_time:
                    if file_path.name not in known_filenames:
                        if not dry_run:
                            file_path.unlink()
                            logger.info('Deleted orphaned media file: %s', file_path)
                        removed += 1
            except OSError as exc:
                logger.warning('Error inspecting or deleting orphan candidate %s: %s', file_path, exc)

    logger.info('Orphan cleanup completed. Scanned: %s, Removed/Orphaned: %s', scanned, removed)
    return {'scanned': scanned, 'orphaned_removed': removed, 'dry_run': dry_run}


@shared_task(name='apps.documents.tasks.cleanup_temporary_processing_files', ignore_result=True)
def cleanup_temporary_processing_files(hours_old: int = 12):
    """
    Cleans up stale temporary files left behind during OCR processing or uploads.
    """
    import tempfile
    import time
    from pathlib import Path

    temp_root = Path(tempfile.gettempdir())
    cutoff_time = time.time() - (max(int(hours_old), 1) * 3600)
    removed = 0

    for tmp_file in temp_root.glob('receiptflow_*'):
        try:
            if tmp_file.is_file() and tmp_file.stat().st_mtime < cutoff_time:
                tmp_file.unlink()
                removed += 1
        except OSError:
            pass

    logger.info('Temporary processing files cleanup completed: %s files removed.', removed)
    return {'temp_files_removed': removed}


@shared_task(name='apps.documents.tasks.send_due_reminders', ignore_result=True)
def send_due_reminders():
    """Daily email: unpaid bills due within a few days or overdue, plus recurring bills that never arrived."""
    from django.contrib.auth import get_user_model

    from apps.documents.payables import documents_needing_reminder, due_state
    from apps.expenses.recurring import recurring_overview
    from apps.users.services import EmailService

    today = timezone.localdate()
    users = get_user_model().objects.filter(
        is_active=True,
        documents__is_paid=False,
        documents__due_date__isnull=False,
    ).distinct()
    sent = 0
    for user in users.iterator():
        docs = documents_needing_reminder(user, today)
        missing = [item for item in recurring_overview(user, today) if item['state'] == 'missing']
        if not docs:
            continue
        bills = [
            {
                'name': (doc.supplier.name if doc.supplier else doc.title) or f'Documento #{doc.pk}',
                'amount': doc.total_amount,
                'currency': doc.currency,
                'due_date': timezone.localdate(doc.due_date),
                'overdue': due_state(doc, today) == 'overdue',
            }
            for doc in docs
        ]
        if EmailService.send_due_reminder(user, bills, missing):
            Document.objects.filter(pk__in=[d.pk for d in docs]).update(last_due_reminder_at=timezone.now())
            sent += 1
    logger.info('Due reminders sent to %s users.', sent)
    return {'users_notified': sent}
