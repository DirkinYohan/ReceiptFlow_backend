"""Reading a document again from the review inbox, and embedding its file."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from apps.documents.models import Document

PIPELINE = 'services.document_processor.processor.process_document_pipeline'
OK = {'requires_review': False, 'review_reasons': [], 'processed_fields': {}}


def _doc(user, **fields):
    defaults = dict(user=user, file='fake/doc.pdf', status=Document.STATUS_REVIEW_REQUIRED,
                    total_amount=Decimal('8480'), issued_at=timezone.now())
    defaults.update(fields)
    return Document.objects.create(**defaults)


class TestRetry:
    def test_review_document_with_all_fields_can_be_read_again(self, auth_client, user, supplier):
        doc = _doc(user, supplier=supplier, processing_attempts=5)
        with patch(PIPELINE, return_value=OK):
            response = auth_client.post(f'/api/v1/documents/{doc.pk}/retry/')
        assert response.status_code in (200, 202)
        doc.refresh_from_db()
        assert doc.status == Document.STATUS_COMPLETED

    def test_document_being_read_returns_clear_message(self, auth_client, user):
        doc = _doc(user, status=Document.STATUS_PROCESSING, processing_started_at=timezone.now())
        response = auth_client.post(f'/api/v1/documents/{doc.pk}/retry/')
        assert response.status_code == 409
        assert 'Ya se está leyendo' in response.json()['message']

    def test_stuck_processing_document_can_be_retried(self, auth_client, user):
        doc = _doc(user, status=Document.STATUS_PROCESSING,
                   processing_started_at=timezone.now() - timedelta(hours=2))
        with patch(PIPELINE, return_value=OK):
            response = auth_client.post(f'/api/v1/documents/{doc.pk}/retry/')
        assert response.status_code in (200, 202)

    def test_completed_document_cannot_be_retried(self, auth_client, user):
        doc = _doc(user, status=Document.STATUS_COMPLETED)
        assert auth_client.post(f'/api/v1/documents/{doc.pk}/retry/').status_code == 409


def test_quota_error_is_readable():
    from apps.documents.review_messages import QUOTA_EXHAUSTED, humanize
    assert humanize('Fallo el procesamiento. 429 RESOURCE_EXHAUSTED quota exceeded') == QUOTA_EXHAUSTED


@override_settings(DEBUG=True, FRONTEND_URL='http://localhost:3000')
def test_media_can_be_embedded_by_frontend(client, settings, tmp_path):
    from config.urls import serve_media
    from django.test import RequestFactory
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / 'factura.pdf').write_bytes(b'%PDF-1.4')
    response = serve_media(RequestFactory().get('/media/factura.pdf'), 'factura.pdf')
    assert response['Content-Security-Policy'] == "frame-ancestors 'self' http://localhost:3000"
