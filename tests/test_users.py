import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from rest_framework import status

User = get_user_model()


@pytest.mark.django_db
class TestUserRegistration:
    REGISTER_PAYLOAD = {
        'email': 'new@receiptflow.com',
        'password': 'Str0ngP@ssword!',
        'password_confirm': 'Str0ngP@ssword!',
        'first_name': 'Nuevo',
        'last_name': 'Usuario',
        'accept_terms': True,
    }

    @override_settings(EMAIL_VERIFICATION_REQUIRED=False)
    def test_register_success_without_verification(self, api_client):
        response = api_client.post('/api/auth/register/', self.REGISTER_PAYLOAD, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body['status'] == 'success'
        assert 'access' in body['tokens']
        assert User.objects.get(email='new@receiptflow.com').is_email_verified

    @override_settings(EMAIL_VERIFICATION_REQUIRED=True)
    def test_register_requires_email_verification(self, api_client):
        response = api_client.post('/api/auth/register/', self.REGISTER_PAYLOAD, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body['status'] == 'success'
        assert body['requires_email_verification'] is True
        assert body['tokens'] is None
        user = User.objects.get(email='new@receiptflow.com')
        assert not user.is_email_verified
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ['new@receiptflow.com']

    @override_settings(EMAIL_VERIFICATION_REQUIRED=True, EMAIL_SEND_IN_BACKGROUND=True)
    def test_register_does_not_wait_for_a_blocked_mail_server(self, api_client):
        import threading
        from unittest.mock import patch

        release = threading.Event()
        delivered = threading.Event()

        def blocked_delivery(email, to_email, template_name):
            release.wait(5)
            delivered.set()
            return True

        with patch('apps.users.services._deliver', side_effect=blocked_delivery):
            response = api_client.post('/api/auth/register/', self.REGISTER_PAYLOAD, format='json')
            assert response.status_code == status.HTTP_201_CREATED
            assert not delivered.is_set()
            release.set()
            assert delivered.wait(5)
        assert User.objects.filter(email='new@receiptflow.com').exists()

    @override_settings(EMAIL_VERIFICATION_REQUIRED=True)
    def test_unverified_user_cannot_log_in(self, api_client):
        api_client.post('/api/auth/register/', self.REGISTER_PAYLOAD, format='json')
        response = api_client.post(
            '/api/auth/login/',
            {'email': 'new@receiptflow.com', 'password': 'Str0ngP@ssword!'},
            format='json',
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert not response.json().get('tokens')

    def test_register_email_already_exists(self, api_client, user):
        url = '/api/auth/register/'
        payload = {
            'email': user.email,
            'password': 'Str0ngP@ssword!',
            'password_confirm': 'Str0ngP@ssword!',
            'first_name': 'Duplicado',
            'last_name': 'Email',
            'accept_terms': True,
        }
        response = api_client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_password_mismatch(self, api_client):
        url = '/api/auth/register/'
        payload = {
            'email': 'mismatch@receiptflow.com',
            'password': 'Str0ngP@ssword1',
            'password_confirm': 'Str0ngP@ssword2',
            'first_name': 'Password',
            'last_name': 'Mismatch',
            'accept_terms': True,
        }
        response = api_client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_short_password(self, api_client):
        url = '/api/auth/register/'
        payload = {
            'email': 'weak@receiptflow.com',
            'password': '12345',
            'password_confirm': '12345',
            'first_name': 'Debil',
            'last_name': 'Password',
            'accept_terms': True,
        }
        response = api_client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_without_accepting_terms(self, api_client):
        url = '/api/auth/register/'
        payload = {
            'email': 'noterms@receiptflow.com',
            'password': 'Str0ngP@ssword!',
            'password_confirm': 'Str0ngP@ssword!',
            'first_name': 'No',
            'last_name': 'Terms',
            'accept_terms': False,
        }
        response = api_client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestUserLogin:
    def test_login_success(self, api_client, user, user_password):
        url = '/api/auth/login/'
        payload = {'email': user.email, 'password': user_password}
        response = api_client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['status'] == 'success'
        assert 'tokens' in data
        assert 'access' in data['tokens']
        assert 'refresh' in data['tokens']
        assert data['user']['email'] == user.email

    def test_login_wrong_password(self, api_client, user):
        url = '/api/auth/login/'
        response = api_client.post(
            url, {'email': user.email, 'password': 'wrongpass'}, format='json',
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_unknown_email(self, api_client):
        url = '/api/auth/login/'
        response = api_client.post(
            url, {'email': 'unknown@receiptflow.com', 'password': 'abc'}, format='json',
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_login_inactive_user(self, api_client, user, user_password):
        user.is_active = False
        user.save()
        url = '/api/auth/login/'
        response = api_client.post(
            url, {'email': user.email, 'password': user_password}, format='json',
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_token_refresh(self, api_client, user, user_password):
        login_url = '/api/auth/login/'
        login_response = api_client.post(
            login_url, {'email': user.email, 'password': user_password}, format='json',
        )
        refresh_token = login_response.json()['tokens']['refresh']
        refresh_url = '/api/auth/refresh/'
        refresh_response = api_client.post(
            refresh_url, {'refresh': refresh_token}, format='json',
        )
        assert refresh_response.status_code == status.HTTP_200_OK
        assert 'access' in refresh_response.json()['tokens']

    def test_logout_success(self, api_client, user, user_password):
        login_url = '/api/auth/login/'
        login_response = api_client.post(
            login_url, {'email': user.email, 'password': user_password}, format='json',
        )
        refresh_token = login_response.json()['tokens']['refresh']
        api_client.force_authenticate(user=user)
        logout_url = '/api/auth/logout/'
        logout_response = api_client.post(
            logout_url, {'refresh': refresh_token}, format='json',
        )
        assert logout_response.status_code == status.HTTP_200_OK


@pytest.mark.django_db
class TestBrevoEmail:
    @override_settings(
        EMAIL_BACKEND='anymail.backends.brevo.EmailBackend',
        ANYMAIL={'BREVO_API_KEY': 'test-brevo-key', 'REQUESTS_TIMEOUT': 10},
        DEFAULT_FROM_EMAIL='ReceiptFlow <sender@receiptflow.com>',
        EMAIL_SEND_IN_BACKGROUND=False,
    )
    def test_verification_email_goes_through_brevo_api(self, user):
        import json
        from unittest.mock import MagicMock, patch

        from apps.users.services import email_service

        response = MagicMock(status_code=201)
        response.json.return_value = {'messageId': '<abc@smtp-relay.brevo.com>'}
        with patch('requests.Session.request', return_value=response) as request:
            assert email_service.send_email_verification(user) is True

        kwargs = request.call_args.kwargs
        assert kwargs['method'].upper() == 'POST'
        assert kwargs['url'] == 'https://api.brevo.com/v3/smtp/email'
        assert kwargs['headers']['api-key'] == 'test-brevo-key'
        assert kwargs['timeout'] == 10
        payload = json.loads(kwargs['data'])
        assert payload['to'] == [{'email': user.email}]
        assert payload['sender']['email'] == 'sender@receiptflow.com'
        assert 'verify-email?token=' in payload['htmlContent']


@pytest.mark.django_db
class TestPasswordReset:
    NEW_PASSWORD = 'Nuev4Clave!Segura'

    def _login(self, api_client, email, password):
        return api_client.post('/api/auth/login/', {'email': email, 'password': password}, format='json')

    def test_reset_saves_the_new_password(self, api_client, user, user_password):
        api_client.post('/api/auth/password-reset/', {'email': user.email}, format='json')
        user.refresh_from_db()
        token = user.password_reset_token
        assert token

        response = api_client.post('/api/auth/password-reset/confirm/', {
            'email': user.email,
            'token': token,
            'new_password': self.NEW_PASSWORD,
            'confirm_password': self.NEW_PASSWORD,
        }, format='json')
        assert response.status_code == status.HTTP_200_OK

        user.refresh_from_db()
        assert user.check_password(self.NEW_PASSWORD)
        assert user.password_reset_token == ''
        assert self._login(api_client, user.email, self.NEW_PASSWORD).status_code == status.HTTP_200_OK
        assert self._login(api_client, user.email, user_password).status_code == status.HTTP_401_UNAUTHORIZED

    def test_reset_token_cannot_be_reused(self, api_client, user):
        token = user.regenerate_password_reset_token()
        payload = {
            'email': user.email,
            'token': token,
            'new_password': self.NEW_PASSWORD,
            'confirm_password': self.NEW_PASSWORD,
        }
        assert api_client.post('/api/auth/password-reset/confirm/', payload, format='json').status_code == 200
        second = api_client.post('/api/auth/password-reset/confirm/', payload, format='json')
        assert second.status_code == status.HTTP_400_BAD_REQUEST

    def test_reset_rejects_weak_password_with_field_error(self, api_client, user, user_password):
        token = user.regenerate_password_reset_token()
        response = api_client.post('/api/auth/password-reset/confirm/', {
            'email': user.email,
            'token': token,
            'new_password': '12345678',
            'confirm_password': '12345678',
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        fields = {item['field'] for item in response.json().get('errors', [])}
        assert 'new_password' in fields
        user.refresh_from_db()
        assert user.check_password(user_password)


@pytest.mark.django_db
class TestChangePassword:
    def test_change_password_from_profile(self, auth_client, api_client, user, user_password):
        new_password = 'Otr4Clave!Distinta'
        response = auth_client.put('/api/v1/users/me/change-password/', {
            'current_password': user_password,
            'new_password': new_password,
            'confirm_password': new_password,
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.check_password(new_password)
        assert not user.check_password(user_password)


@pytest.mark.django_db
class TestSessionsAfterPasswordReset:
    def test_reset_closes_open_sessions(self, api_client, user, user_password):
        login = api_client.post('/api/auth/login/', {'email': user.email, 'password': user_password}, format='json')
        refresh = login.json()['tokens']['refresh']
        token = user.regenerate_password_reset_token()
        new_password = 'Nuev4Clave!Segura'
        api_client.post('/api/auth/password-reset/confirm/', {
            'email': user.email, 'token': token,
            'new_password': new_password, 'confirm_password': new_password,
        }, format='json')
        response = api_client.post('/api/auth/refresh/', {'refresh': refresh}, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.django_db
class TestLinksWithoutEmail:
    def test_verification_email_link_has_no_address(self, api_client):
        with override_settings(EMAIL_VERIFICATION_REQUIRED=True):
            api_client.post('/api/auth/register/', TestUserRegistration.REGISTER_PAYLOAD, format='json')
        body = mail.outbox[-1].alternatives[0][0] if mail.outbox[-1].alternatives else mail.outbox[-1].body
        assert 'verify-email?token=' in body
        assert 'email=' not in body

    def test_verify_with_token_only(self, api_client):
        with override_settings(EMAIL_VERIFICATION_REQUIRED=True):
            api_client.post('/api/auth/register/', TestUserRegistration.REGISTER_PAYLOAD, format='json')
        user = User.objects.get(email='new@receiptflow.com')
        response = api_client.post('/api/auth/verify-email/', {'token': user.email_verification_token}, format='json')
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.is_email_verified

    def test_reset_with_token_only(self, api_client, user):
        token = user.regenerate_password_reset_token()
        new_password = 'Nuev4Clave!Segura'
        response = api_client.post('/api/auth/password-reset/confirm/', {
            'token': token, 'new_password': new_password, 'confirm_password': new_password,
        }, format='json')
        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.check_password(new_password)

    def test_short_or_empty_tokens_are_rejected(self, api_client, user):
        for token in ('', 'abc'):
            response = api_client.post('/api/auth/verify-email/', {'token': token}, format='json')
            assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_token_from_another_email_is_rejected(self, api_client, user, user2):
        token = user.regenerate_password_reset_token()
        response = api_client.post('/api/auth/password-reset/confirm/', {
            'email': user2.email, 'token': token,
            'new_password': 'Nuev4Clave!Segura', 'confirm_password': 'Nuev4Clave!Segura',
        }, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
