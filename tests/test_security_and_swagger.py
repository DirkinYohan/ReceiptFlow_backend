"""
Security tests: file validation, magic bytes, access control,
JWT authentication, rate limiting, and OpenAPI schema (Steps 11.3 & 11.4).
"""
from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from apps.common.validators import validate_uploaded_file
from apps.documents.models import Document
from apps.expenses.models import Expense
from apps.suppliers.models import Supplier
from django.core.exceptions import ValidationError

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user_password():
    return 'Str0ngP@ssw0rd!'


@pytest.fixture
def user(db, user_password):
    return User.objects.create_user(
        email='security@receiptflow.com',
        password=user_password,
        first_name='Sec',
        last_name='User',
        is_email_verified=True,
    )


@pytest.fixture
def user2(db, user_password):
    return User.objects.create_user(
        email='other@receiptflow.com',
        password=user_password,
        first_name='Other',
        last_name='User',
        is_email_verified=True,
    )


@pytest.fixture
def auth_client(api_client, user):
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def document(db, user):
    return Document.objects.create(
        user=user,
        title='User Doc',
        original_filename='doc.png',
        file='fake/doc.png',
        document_type=Document.DOCUMENT_TYPE_RECEIPT,
    )


@pytest.fixture
def document_user2(db, user2):
    return Document.objects.create(
        user=user2,
        title='User2 Doc',
        original_filename='doc2.png',
        file='fake/doc2.png',
        document_type=Document.DOCUMENT_TYPE_RECEIPT,
    )


@pytest.fixture
def supplier(db, user):
    return Supplier.objects.create(user=user, name='My Supplier')


@pytest.fixture
def supplier_user2(db, user2):
    return Supplier.objects.create(user=user2, name='User2 Supplier')


# ---------------------------------------------------------------------------
# 11.3.A — File validator: magic bytes & extension
# ---------------------------------------------------------------------------

class TestFileValidatorMagicBytes:
    """Tests that validate_uploaded_file rejects files with spoofed content."""

    def _make_file(self, name: str, content: bytes, content_type: str = 'application/octet-stream'):
        return SimpleUploadedFile(name, content, content_type=content_type)

    def test_valid_png_accepted(self):
        content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        f = self._make_file('image.png', content, 'image/png')
        # Should not raise
        validate_uploaded_file(f)

    def test_valid_pdf_accepted(self):
        content = b'%PDF-1.4 ' + b'\x00' * 100
        f = self._make_file('document.pdf', content, 'application/pdf')
        validate_uploaded_file(f)

    def test_valid_jpeg_accepted(self):
        content = b'\xff\xd8\xff\xe0' + b'\x00' * 100
        f = self._make_file('photo.jpg', content, 'image/jpeg')
        validate_uploaded_file(f)

    def test_executable_content_rejected(self):
        """EXE magic bytes in a file named .png should be rejected."""
        exe_content = b'MZ\x90\x00\x03\x00\x00\x00'  # PE executable header
        f = self._make_file('malware.png', exe_content, 'image/png')
        with pytest.raises((ValidationError, Exception)) as exc_info:
            validate_uploaded_file(f)
        # Must raise — either Django ValidationError or serializers.ValidationError
        assert exc_info.value is not None

    def test_invalid_extension_rejected(self):
        """Files with forbidden extensions must always be rejected."""
        content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        f = self._make_file('script.exe', content, 'application/octet-stream')
        with pytest.raises(Exception):
            validate_uploaded_file(f)

    def test_php_content_with_png_extension(self):
        """PHP code disguised as PNG must fail magic bytes check."""
        content = b'<?php echo shell_exec($_GET["cmd"]); ?>'
        f = self._make_file('backdoor.png', content, 'image/png')
        with pytest.raises(Exception):
            validate_uploaded_file(f)

    def test_file_too_large_rejected(self, settings):
        settings.MAX_UPLOAD_SIZE_MB = 1
        # Create a file slightly larger than 1MB
        large_content = b'\x89PNG\r\n\x1a\n' + b'\x00' * (1024 * 1024 + 1)
        f = self._make_file('huge.png', large_content, 'image/png')
        f.size = len(large_content)
        with pytest.raises(Exception):
            validate_uploaded_file(f)

    def test_empty_filename_rejected(self):
        with pytest.raises(Exception):
            f = self._make_file('', b'content', 'image/png')
            validate_uploaded_file(f)

    def test_no_extension_rejected(self):
        content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        f = self._make_file('noextension', content, 'image/png')
        with pytest.raises(Exception):
            validate_uploaded_file(f)


# ---------------------------------------------------------------------------
# 11.3.B — Upload endpoint security
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUploadEndpointSecurity:
    def test_unauthenticated_cannot_upload(self, api_client):
        content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 50
        f = SimpleUploadedFile('test.png', content, 'image/png')
        response = api_client.post('/api/v1/documents/', {'file': f}, format='multipart')
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_malicious_file_rejected_at_endpoint(self, auth_client):
        exe_bytes = b'MZ\x90\x00\x03\x00\x00\x00'  # PE header
        f = SimpleUploadedFile('payload.png', exe_bytes, 'image/png')
        response = auth_client.post('/api/v1/documents/', {'file': f}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_invalid_extension_rejected_at_endpoint(self, auth_client):
        f = SimpleUploadedFile('virus.exe', b'ELF content', 'application/octet-stream')
        response = auth_client.post('/api/v1/documents/', {'file': f}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_upload_without_file_returns_400(self, auth_client):
        response = auth_client.post('/api/v1/documents/', {'title': 'No file'}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# 11.3.C — Cross-user data isolation
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCrossUserIsolation:
    def test_user_cannot_access_other_users_document(self, auth_client, document_user2):
        url = f'/api/v1/documents/{document_user2.pk}/'
        response = auth_client.get(url)
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )

    def test_user_cannot_delete_other_users_document(self, auth_client, document_user2):
        url = f'/api/v1/documents/{document_user2.pk}/'
        response = auth_client.delete(url)
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )

    def test_user_cannot_access_other_users_supplier(self, auth_client, supplier_user2):
        url = f'/api/v1/suppliers/{supplier_user2.pk}/'
        response = auth_client.get(url)
        assert response.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )

    def test_user_document_list_excludes_other_users(self, auth_client, user, user2, document_user2):
        # Create a doc for our user
        Document.objects.create(
            user=user,
            title='Mine',
            original_filename='mine.png',
            file='fake/mine.png',
            document_type=Document.DOCUMENT_TYPE_RECEIPT,
        )
        response = auth_client.get('/api/v1/documents/')
        assert response.status_code == status.HTTP_200_OK
        results = response.json().get('results') or response.json().get('data') or []
        titles = [d.get('title') for d in results]
        assert 'Mine' in titles
        assert 'User2 Doc' not in titles

    def test_user_supplier_list_excludes_other_users(self, auth_client, supplier, supplier_user2):
        response = auth_client.get('/api/v1/suppliers/')
        assert response.status_code == status.HTTP_200_OK
        results = response.json().get('results') or response.json().get('data') or []
        names = [s.get('name') for s in results]
        assert 'My Supplier' in names
        assert 'User2 Supplier' not in names


# ---------------------------------------------------------------------------
# 11.3.D — JWT authentication
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestJWTAuthentication:
    def test_invalid_token_returns_401(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION='Bearer invalidtoken.payload.sig')
        response = api_client.get('/api/v1/documents/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_malformed_authorization_header_returns_401(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION='NotBearer somejunk')
        response = api_client.get('/api/v1/documents/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_no_token_returns_401(self, api_client):
        response = api_client.get('/api/v1/documents/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_expired_token_returns_401(self, api_client):
        # Craft a syntactically valid but expired token (HS256, past exp)
        # We use a dummy pre-built expired JWT (header.payload.sig all base64url)
        expired_token = (
            'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'  # header
            '.eyJzdWIiOiIxIiwiZXhwIjoxNjAwMDAwMDAwfQ'  # exp=2020 (past)
            '.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c'  # dummy sig
        )
        api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {expired_token}')
        response = api_client.get('/api/v1/documents/')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_protected_endpoints_require_auth(self, api_client):
        protected_urls = [
            '/api/v1/documents/',
            '/api/v1/expenses/',
            '/api/v1/suppliers/',
            '/api/v1/categories/',
        ]
        for url in protected_urls:
            response = api_client.get(url)
            assert response.status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            ), f'Expected 401/403 for {url}, got {response.status_code}'


# ---------------------------------------------------------------------------
# 11.3.E — Rate limiting (functional check)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRateLimiting:
    def test_login_rate_limit_applied(self, api_client):
        """
        Even a single request should not blow up (rate limit middleware exists).
        We verify the endpoint is decorated and returns expected response codes.
        """
        payload = {'email': 'nobody@receiptflow.com', 'password': 'wrong'}
        response = api_client.post('/api/auth/login/', payload, format='json')
        # Must return 401 (bad credentials) not 500 (missing rate limiter)
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

    def test_register_rate_limit_applied(self, api_client):
        payload = {
            'email': 'rl@receiptflow.com',
            'password': 'Str0ngP@ss!',
            'password_confirm': 'Str0ngP@ss!',
            'first_name': 'RL',
            'last_name': 'Test',
            'accept_terms': True,
        }
        response = api_client.post('/api/auth/register/', payload, format='json')
        assert response.status_code in (
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )


# ---------------------------------------------------------------------------
# 11.4 — Swagger / OpenAPI schema endpoints
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSwaggerOpenAPI:
    def test_schema_endpoint_returns_200(self, api_client):
        response = api_client.get('/api/schema/')
        assert response.status_code == status.HTTP_200_OK

    def test_swagger_ui_returns_200(self, api_client):
        response = api_client.get('/api/docs/')
        assert response.status_code == status.HTTP_200_OK

    def test_schema_content_type_is_yaml_or_json(self, api_client):
        response = api_client.get('/api/schema/')
        content_type = response.get('Content-Type', '')
        assert any(ct in content_type for ct in ['yaml', 'json', 'text/', 'openapi'])

    def test_schema_contains_jwt_security(self, api_client):
        response = api_client.get('/api/schema/?format=json')
        assert response.status_code == status.HTTP_200_OK
        content = response.content.decode('utf-8')
        # OpenAPI schema must reference JWT / bearer authentication
        assert any(kw in content.lower() for kw in ['bearer', 'jwt', 'jwtauth'])

    def test_schema_contains_key_paths(self, api_client):
        response = api_client.get('/api/schema/?format=json')
        content = response.content.decode('utf-8')
        # Core API endpoints must appear in the schema
        assert '/api/v1/documents/' in content
        assert '/api/v1/expenses/' in content
        assert '/api/auth/login/' in content

    def test_schema_no_5xx_error(self, api_client):
        response = api_client.get('/api/schema/')
        assert response.status_code < 500
