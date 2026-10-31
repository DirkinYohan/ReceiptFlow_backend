import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import override_settings
from django.urls import reverse
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
