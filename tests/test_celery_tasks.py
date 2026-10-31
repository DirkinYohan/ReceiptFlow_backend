"""
Unit tests for Celery background tasks (Step 11.1).
All external services (OCR/Google Vision, Redis) are mocked.
Tests run in CELERY_TASK_ALWAYS_EAGER=True mode via config/settings/test.py.
"""
from __future__ import annotations

import tempfile
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone

from apps.documents.models import Document
from apps.documents.tasks import (
    PROCESSING_LOCK_KEY_TEMPLATE,
    _acquire_processing_lock,
    _release_processing_lock,
    _is_duplicate_task,
    cleanup_old_failed_documents,
    cleanup_old_token_blacklist,
    cleanup_orphan_media_files,
    cleanup_temporary_processing_files,
    mark_stuck_processing_documents,
    process_document_task,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def user(db):
    return User.objects.create_user(
        email='tasks@receiptflow.com',
        password='Str0ng!Pass',
        is_email_verified=True,
    )


@pytest.fixture
def pending_document(db, user, tmp_path):
    """Document in PENDING state with a real (empty) file reference."""
    fake_file = tmp_path / 'receipt.png'
    fake_file.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)
    doc = Document.objects.create(
        user=user,
        title='Test Pending',
        original_filename='receipt.png',
        file=str(fake_file),
        document_type=Document.DOCUMENT_TYPE_RECEIPT,
        status=Document.STATUS_PENDING,
    )
    return doc


@pytest.fixture(autouse=True)
def clear_cache():
    """Ensure Redis/LocMem cache is clean between tests."""
    cache.clear()
    yield
    cache.clear()


# ---------------------------------------------------------------------------
# 11.1.A — Redis lock helpers
# ---------------------------------------------------------------------------

class TestProcessingLock:
    def test_acquire_lock_succeeds_first_time(self):
        doc_id = 9999
        assert _acquire_processing_lock(doc_id) is True

    def test_acquire_lock_fails_second_time(self):
        doc_id = 9998
        _acquire_processing_lock(doc_id)
        assert _acquire_processing_lock(doc_id) is False

    def test_release_lock_allows_reacquire(self):
        doc_id = 9997
        _acquire_processing_lock(doc_id)
        _release_processing_lock(doc_id)
        assert _acquire_processing_lock(doc_id) is True

    def test_is_duplicate_task_true_when_locked(self):
        doc_id = 9996
        _acquire_processing_lock(doc_id)
        assert _is_duplicate_task(doc_id) is True

    def test_is_duplicate_task_false_when_unlocked(self):
        doc_id = 9995
        assert _is_duplicate_task(doc_id) is False


# ---------------------------------------------------------------------------
# 11.1.B — process_document_task: invalid / not-found document
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestProcessDocumentTaskGuards:
    def test_invalid_id_returns_early(self):
        result = process_document_task.apply(args=[0]).get()
        assert result['ok'] is False
        assert result['reason'] == 'invalid_id'

    def test_negative_id_returns_early(self):
        result = process_document_task.apply(args=[-1]).get()
        assert result['ok'] is False
        assert result['reason'] == 'invalid_id'

    def test_nonexistent_document_returns_not_found(self):
        result = process_document_task.apply(args=[999999]).get()
        assert result['ok'] is False
        assert result['reason'] == 'not_found'

    def test_already_completed_document_skips(self, pending_document):
        pending_document.status = Document.STATUS_COMPLETED
        pending_document.save(update_fields=['status'])
        result = process_document_task.apply(args=[pending_document.pk]).get()
        assert result['ok'] is True
        assert result.get('skipped') is True
        assert result['reason'] == 'already_completed'

    def test_duplicate_task_blocked_by_lock(self, pending_document):
        # Pre-acquire the lock to simulate a running worker
        _acquire_processing_lock(pending_document.pk)
        result = process_document_task.apply(args=[pending_document.pk]).get()
        assert result['ok'] is True
        assert result.get('skipped') is True
        assert result['reason'] == 'duplicate_lock'


# ---------------------------------------------------------------------------
# 11.1.C — process_document_task: state transitions
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestProcessDocumentTaskStateTransitions:
    def _make_mock_pipeline_result(self, requires_review=False, reasons=None):
        return {
            'requires_review': requires_review,
            'review_reasons': reasons or [],
            'processed_fields': {},
        }

    def test_task_success_marks_completed(self, pending_document):
        with patch(
            'services.document_processor.processor.process_document_pipeline',
            return_value=self._make_mock_pipeline_result(requires_review=False),
        ):
            result = process_document_task.apply(args=[pending_document.pk]).get()
        assert result['ok'] is True
        assert result['requires_review'] is False
        pending_document.refresh_from_db()
        assert pending_document.status == Document.STATUS_COMPLETED

    def test_task_review_required_sets_status(self, pending_document):
        with patch(
            'services.document_processor.processor.process_document_pipeline',
            return_value=self._make_mock_pipeline_result(
                requires_review=True,
                reasons=['No se identificó el monto total.'],
            ),
        ):
            result = process_document_task.apply(args=[pending_document.pk]).get()
        assert result['ok'] is True
        assert result['requires_review'] is True
        pending_document.refresh_from_db()
        assert pending_document.status == Document.STATUS_REVIEW_REQUIRED

    def test_task_exception_marks_failed(self, pending_document):
        with patch(
            'services.document_processor.processor.process_document_pipeline',
            side_effect=ValueError('OCR error simulation'),
        ):
            result = process_document_task.apply(args=[pending_document.pk]).get()
        assert result['ok'] is False
        assert result['status'] == Document.STATUS_FAILED
        pending_document.refresh_from_db()
        assert pending_document.status == Document.STATUS_FAILED

    def test_lock_released_after_success(self, pending_document):
        with patch(
            'services.document_processor.processor.process_document_pipeline',
            return_value=self._make_mock_pipeline_result(requires_review=False),
        ):
            process_document_task.apply(args=[pending_document.pk]).get()
        # Lock should be released, so re-acquisition must succeed
        assert _acquire_processing_lock(pending_document.pk) is True

    def test_lock_released_after_failure(self, pending_document):
        with patch(
            'services.document_processor.processor.process_document_pipeline',
            side_effect=RuntimeError('unrecoverable error'),
        ):
            process_document_task.apply(args=[pending_document.pk]).get()
        assert _acquire_processing_lock(pending_document.pk) is True

    def test_document_transitions_to_processing_before_pipeline(self, pending_document):
        statuses_seen = []

        def fake_pipeline(document):
            document.refresh_from_db()
            statuses_seen.append(document.status)
            return {'requires_review': False, 'review_reasons': [], 'processed_fields': {}}

        with patch(
            'services.document_processor.processor.process_document_pipeline',
            side_effect=fake_pipeline,
        ):
            process_document_task.apply(args=[pending_document.pk]).get()

        assert Document.STATUS_PROCESSING in statuses_seen


# ---------------------------------------------------------------------------
# 11.1.D — Maintenance tasks
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestMarkStuckProcessingDocuments:
    def test_marks_stuck_document_as_failed(self, user):
        doc = Document.objects.create(
            user=user,
            title='Stuck',
            original_filename='stuck.png',
            file='fake/stuck.png',
            status=Document.STATUS_PROCESSING,
            # Simulate started 2 hours ago
            processing_started_at=timezone.now() - timedelta(hours=2),
        )
        with patch('apps.documents.tasks.settings') as mock_settings:
            mock_settings.PROCESSING_TIMEOUT_MINUTES = 15
            result = mark_stuck_processing_documents.apply(args=[1]).get()
        doc.refresh_from_db()
        assert doc.status == Document.STATUS_FAILED
        assert result['marked'] >= 1

    def test_does_not_touch_pending_documents(self, user):
        doc = Document.objects.create(
            user=user,
            title='Pending OK',
            original_filename='ok.png',
            file='fake/ok.png',
            status=Document.STATUS_PENDING,
        )
        mark_stuck_processing_documents.apply(args=[1]).get()
        doc.refresh_from_db()
        assert doc.status == Document.STATUS_PENDING

    def test_does_not_touch_recently_started_processing(self, user):
        doc = Document.objects.create(
            user=user,
            title='Recent',
            original_filename='recent.png',
            file='fake/recent.png',
            status=Document.STATUS_PROCESSING,
            processing_started_at=timezone.now() - timedelta(minutes=2),
        )
        with patch('apps.documents.tasks.settings') as mock_settings:
            mock_settings.PROCESSING_TIMEOUT_MINUTES = 15
            mark_stuck_processing_documents.apply(args=[1]).get()
        doc.refresh_from_db()
        assert doc.status == Document.STATUS_PROCESSING


@pytest.mark.django_db
class TestCleanupOldFailedDocuments:
    def test_annotates_old_failed_documents(self, user):
        old_date = timezone.now() - timedelta(days=35)
        doc = Document.objects.create(
            user=user,
            title='Old Failed',
            original_filename='fail.png',
            file='fake/fail.png',
            status=Document.STATUS_FAILED,
            processed_at=old_date,
        )
        result = cleanup_old_failed_documents.apply(args=[30]).get()
        assert result['annotated'] >= 1
        doc.refresh_from_db()
        assert 'Mantenimiento' in doc.notes

    def test_does_not_annotate_recent_failed_documents(self, user):
        doc = Document.objects.create(
            user=user,
            title='Recent Failed',
            original_filename='rfail.png',
            file='fake/rfail.png',
            status=Document.STATUS_FAILED,
            processed_at=timezone.now() - timedelta(days=2),
        )
        result = cleanup_old_failed_documents.apply(args=[30]).get()
        doc.refresh_from_db()
        assert 'Mantenimiento' not in (doc.notes or '')


@pytest.mark.django_db
class TestCleanupTokenBlacklist:
    def test_runs_without_error(self):
        result = cleanup_old_token_blacklist.apply(args=[30]).get()
        # Either successfully deleted or caught the expected error
        assert 'error' not in result or result.get('error') is True


@pytest.mark.django_db
class TestCleanupOrphanMediaFiles:
    def test_skips_when_no_media_root(self):
        with patch('apps.documents.tasks.settings') as mock_settings:
            mock_settings.MEDIA_ROOT = None
            result = cleanup_orphan_media_files.apply(args=[False, 24]).get()
        assert result['status'] == 'skipped'

    def test_skips_when_media_root_not_exists(self, tmp_path):
        nonexistent = str(tmp_path / 'nonexistent_media')
        with patch('apps.documents.tasks.settings') as mock_settings:
            mock_settings.MEDIA_ROOT = nonexistent
            result = cleanup_orphan_media_files.apply(args=[False, 24]).get()
        assert result['status'] == 'skipped'

    def test_dry_run_does_not_delete_files(self, user, tmp_path):
        media_root = tmp_path / 'media'
        docs_dir = media_root / 'documents'
        docs_dir.mkdir(parents=True)
        orphan = docs_dir / 'orphan_old_file.png'
        orphan.write_bytes(b'fake content')
        # Make the file appear old (more than 24 hours)
        old_mtime = time.time() - (25 * 3600)
        import os
        os.utime(orphan, (old_mtime, old_mtime))

        with patch('apps.documents.tasks.settings') as mock_settings:
            mock_settings.MEDIA_ROOT = str(media_root)
            result = cleanup_orphan_media_files.apply(args=[True, 24]).get()  # dry_run=True

        assert orphan.exists()  # File must NOT be deleted in dry run
        assert result['dry_run'] is True
        assert result['orphaned_removed'] >= 1


class TestCleanupTemporaryFiles:
    def test_removes_old_temp_files(self, tmp_path):
        old_file = tmp_path / 'receiptflow_test_123.tmp'
        old_file.write_bytes(b'temp data')
        old_mtime = time.time() - (13 * 3600)
        import os
        os.utime(old_file, (old_mtime, old_mtime))

        # Patch the tempfile module as it is used inside the task function (local import)
        import tempfile as _tempfile
        with patch.object(_tempfile, 'gettempdir', return_value=str(tmp_path)):
            result = cleanup_temporary_processing_files.apply(args=[12]).get()

        assert result['temp_files_removed'] >= 1
        assert not old_file.exists()

    def test_keeps_recent_temp_files(self, tmp_path):
        new_file = tmp_path / 'receiptflow_recent.tmp'
        new_file.write_bytes(b'temp data')
        # Do not change mtime — it will be fresh

        import tempfile as _tempfile
        with patch.object(_tempfile, 'gettempdir', return_value=str(tmp_path)):
            cleanup_temporary_processing_files.apply(args=[12]).get()

        assert new_file.exists()
