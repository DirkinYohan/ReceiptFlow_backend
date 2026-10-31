import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status

from apps.categories.models import Category
from apps.documents.models import Document
from apps.expenses.models import Expense
from apps.suppliers.models import Supplier

User = get_user_model()


@pytest.mark.django_db
class TestDashboardEndpoints:
    def test_dashboard_summary_requires_auth(self, api_client):
        url = '/api/v1/reports/dashboard/summary/'
        response = api_client.get(url)
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_dashboard_summary(self, auth_client, user, system_categories, supplier):
        cat = system_categories[0]
        Expense.objects.create(
            user=user, category=cat, supplier=supplier,
            description='D1', amount=100, incurred_at=timezone.now(),
        )
        Expense.objects.create(
            user=user, category=cat, supplier=supplier,
            description='D2', amount=250, incurred_at=timezone.now(),
        )
        Document.objects.create(
            user=user, title='doc', original_filename='a.png',
            file='fake/a.png', document_type=Document.DOCUMENT_TYPE_RECEIPT,
        )
        url = '/api/v1/reports/dashboard/summary/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert data['expenses_count'] == 2
        assert data['documents_count'] == 1

    def test_monthly_expenses(self, auth_client, expense):
        url = '/api/v1/reports/dashboard/monthly-expenses/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert isinstance(data, list)

    def test_expenses_by_category(self, auth_client, expense):
        url = '/api/v1/reports/dashboard/expenses-by-category/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_expenses_by_supplier(self, auth_client, expense):
        url = '/api/v1/reports/dashboard/expenses-by-supplier/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.json()['data'], list)

    def test_recent_expenses(self, auth_client, expense):
        url = '/api/v1/reports/dashboard/recent-expenses/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert len(data) >= 1

    def test_recent_documents(self, auth_client, user):
        Document.objects.create(
            user=user, title='reciente', original_filename='r.png',
            file='fake/r.png',
        )
        url = '/api/v1/reports/dashboard/recent-documents/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert len(data) >= 1

    def test_expense_evolution(self, auth_client, expense):
        url = '/api/v1/reports/dashboard/expense-evolution/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert isinstance(data, list)


@pytest.mark.django_db
class TestPermissions:
    def test_unauthenticated_cannot_access_expenses(self, api_client):
        response = api_client.get('/api/v1/expenses/')
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_access_documents(self, api_client):
        response = api_client.get('/api/v1/documents/')
        assert response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN)

    def test_cannot_access_other_user_expense(self, auth_client, user, user2, system_categories, supplier):
        from django.db.models import Q
        other_category = system_categories[0]
        expense = Expense.objects.create(
            user=user2, category=other_category, supplier=supplier,
            description='Ajeno', amount=9999, incurred_at=timezone.now(),
        )
        url = f'/api/v1/expenses/{expense.pk}/'
        response = auth_client.get(url)
        assert response.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN)

    def test_cannot_modify_other_user_supplier(self, auth_client, user, user2):
        supplier = Supplier.objects.create(user=user2, name='Ajeno')
        url = f'/api/v1/suppliers/{supplier.pk}/'
        response = auth_client.patch(url, {'name': 'Editado'}, format='json')
        assert response.status_code in (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)
