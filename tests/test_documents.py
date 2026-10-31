import io

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status

from apps.categories.models import Category
from apps.documents.models import Document
from apps.suppliers.models import Supplier
from PIL import Image

User = get_user_model()


def _small_png_bytes():
    buf = io.BytesIO()
    image = Image.new('RGB', (100, 100), color='red')
    image.save(buf, format='PNG')
    return buf.getvalue()


@pytest.mark.django_db
class TestDocumentUpload:
    def test_upload_document_success(self, auth_client, user):
        url = '/api/v1/documents/'
        file_obj = SimpleUploadedFile(
            'recibo_prueba.png', _small_png_bytes(), content_type='image/png',
        )
        payload = {
            'file': file_obj,
            'document_type': Document.DOCUMENT_TYPE_RECEIPT,
            'title': 'Recibo de prueba',
        }
        response = auth_client.post(url, payload, format='multipart')
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data['status'] == 'success'
        assert data['data']['document_type'] == Document.DOCUMENT_TYPE_RECEIPT
        assert data['data']['status'] == Document.STATUS_PENDING
        assert Document.objects.filter(user=user).count() == 1

    def test_upload_document_without_file(self, auth_client):
        url = '/api/v1/documents/'
        response = auth_client.post(url, {'title': 'Sin archivo'}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_upload_document_invalid_extension(self, auth_client):
        url = '/api/v1/documents/'
        content = b'This is not a valid document'
        file_obj = SimpleUploadedFile(
            'document.exe', content, content_type='application/octet-stream',
        )
        response = auth_client.post(url, {'file': file_obj}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_documents_isolated(self, auth_client, user, user2):
        Document.objects.create(
            user=user, title='doc user 1', original_filename='a.png',
            file='fake/a.png', document_type=Document.DOCUMENT_TYPE_RECEIPT,
        )
        Document.objects.create(
            user=user2, title='doc user 2', original_filename='b.png',
            file='fake/b.png', document_type=Document.DOCUMENT_TYPE_RECEIPT,
        )
        url = '/api/v1/documents/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        results = response.json().get('results') or response.json().get('data') or []
        if isinstance(results, dict):
            results = results
        assert len(results) >= 1
        user_doc_titles = [d.get('title') for d in (results if isinstance(results, list) else [])]
        assert 'doc user 1' in user_doc_titles
        assert 'doc user 2' not in user_doc_titles

    def test_delete_document(self, auth_client, user):
        doc = Document.objects.create(
            user=user, title='borrar', original_filename='x.png',
            file='fake/x.png', document_type=Document.DOCUMENT_TYPE_OTHER,
        )
        url = f'/api/v1/documents/{doc.pk}/'
        response = auth_client.delete(url)
        assert response.status_code == status.HTTP_200_OK
        assert not Document.objects.filter(pk=doc.pk).exists()

    def test_document_stats_endpoint(self, auth_client, user):
        Document.objects.create(
            user=user, title='doc1', original_filename='a.png',
            file='fake/a.png', status=Document.STATUS_COMPLETED,
            document_type=Document.DOCUMENT_TYPE_INVOICE,
            total_amount=500, currency='MXN',
        )
        Document.objects.create(
            user=user, title='doc2', original_filename='b.png',
            file='fake/b.png', status=Document.STATUS_PENDING,
            document_type=Document.DOCUMENT_TYPE_RECEIPT,
        )
        url = '/api/v1/documents/stats/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert data['total'] == 2
        assert data['completed'] == 1
        assert data['pending'] == 1
        assert float(data['total_amount_sum']) >= 500
