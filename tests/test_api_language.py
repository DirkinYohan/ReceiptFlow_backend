import pytest

from apps.common.i18n import MESSAGES, translate_message, translate_payload


def test_every_catalog_entry_is_translated():
    assert all(english and english != spanish for spanish, english in MESSAGES.items())


def test_messages_with_data():
    assert translate_message('El archivo excede el tamaño máximo permitido (20MB).') == (
        'The file is larger than the maximum size allowed (20 MB).'
    )
    assert translate_message('No se puede reintentar un documento en estado "Procesado".') == (
        'A document with status "Processed" cannot be read again.'
    )


def test_only_message_fields_are_translated():
    payload = {
        'status': 'error',
        'message': 'Proveedor no encontrado.',
        'errors': [{'field': 'email', 'message': 'Las contraseñas no coinciden.'}],
        'data': {'description': 'Proveedor no encontrado.', 'notes': 'Registro exitoso.'},
    }
    out = translate_payload(payload)
    assert out['message'] == 'Supplier not found.'
    assert out['errors'][0]['message'] == 'The passwords do not match.'
    assert out['data'] == payload['data']


@pytest.mark.django_db
class TestApiResponses:
    def test_login_error_in_english(self, api_client, user):
        response = api_client.post(
            '/api/auth/login/', {'email': user.email, 'password': 'mala-clave-123'},
            format='json', HTTP_ACCEPT_LANGUAGE='en',
        )
        assert response.status_code in (400, 401)
        assert 'Incorrect password.' in response.content.decode()

    def test_login_error_in_spanish_by_default(self, api_client, user):
        response = api_client.post(
            '/api/auth/login/', {'email': user.email, 'password': 'mala-clave-123'}, format='json',
        )
        assert 'Contraseña incorrecta.' in response.content.decode()

    def test_django_messages_follow_language(self, api_client):
        en = api_client.get('/api/v1/expenses/', HTTP_ACCEPT_LANGUAGE='en').json()
        es = api_client.get('/api/v1/expenses/', HTTP_ACCEPT_LANGUAGE='es').json()
        assert 'credentials were not provided' in en['message']
        assert en['message'] != es['message']

    def test_view_message_in_english(self, auth_client):
        response = auth_client.get('/api/v1/expenses/export/?file_format=pdf', HTTP_ACCEPT_LANGUAGE='en')
        assert response.json()['message'] == 'Unsupported format. Use xlsx or csv.'

    def test_processing_failure_reason_in_english(self):
        from apps.documents.review_messages import humanize
        assert humanize('Fallo el procesamiento. timeout', lang='en') == 'Reading failed. timeout'
