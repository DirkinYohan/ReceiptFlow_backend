import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status

from apps.categories.models import Category
from apps.expenses.models import Expense

User = get_user_model()


@pytest.mark.django_db
class TestExpenseEndpoints:
    def test_create_expense_success(self, auth_client, system_categories, supplier):
        category = system_categories[0]
        url = '/api/v1/expenses/'
        payload = {
            'category': category.pk,
            'supplier': supplier.pk,
            'description': 'Gasto nuevo',
            'amount': 150.50,
            'currency': 'MXN',
            'payment_method': Expense.PAYMENT_METHOD_CASH,
            'incurred_at': timezone.now().isoformat(),
            'notes': 'Notas del gasto',
        }
        response = auth_client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data['status'] == 'success'
        assert float(data['data']['amount']) == 150.5
        assert data['data']['currency'] == 'MXN'

    def test_create_expense_without_category(self, auth_client, supplier):
        url = '/api/v1/expenses/'
        payload = {
            'supplier': supplier.pk,
            'description': 'Sin categoría',
            'amount': 100,
            'payment_method': Expense.PAYMENT_METHOD_CASH,
            'incurred_at': timezone.now().isoformat(),
        }
        response = auth_client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_expenses(self, auth_client, expense, user2):
        Expense.objects.create(
            user=user2, category=Category.objects.filter(scope=Category.SCOPE_SYSTEM).first(),
            description='Gasto usuario 2', amount=999,
            incurred_at=timezone.now(),
        )
        url = '/api/v1/expenses/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        results = data.get('results') or data.get('data') or []
        assert len(list(results)) >= 1

    def test_expense_filter_by_category(self, auth_client, user, system_categories, supplier):
        cat_food, cat_transport = system_categories[0], system_categories[1]
        Expense.objects.create(
            user=user, category=cat_food, description='a', amount=100,
            incurred_at=timezone.now(), supplier=supplier,
        )
        Expense.objects.create(
            user=user, category=cat_transport, description='b', amount=200,
            incurred_at=timezone.now(), supplier=supplier,
        )
        url = f'/api/v1/expenses/?category={cat_food.pk}'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_expense_search(self, auth_client, user, system_categories, supplier):
        Expense.objects.create(
            user=user, category=system_categories[0], supplier=supplier,
            description='Gasto supermercado', amount=500,
            incurred_at=timezone.now(),
        )
        Expense.objects.create(
            user=user, category=system_categories[0], supplier=supplier,
            description='Taxi aeropuerto', amount=300,
            incurred_at=timezone.now(),
        )
        url = '/api/v1/expenses/?search=taxi'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_expense_stats(self, auth_client, expense):
        url = '/api/v1/expenses/stats/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert data['count'] >= 1

    def test_update_expense(self, auth_client, expense):
        url = f'/api/v1/expenses/{expense.pk}/'
        payload = {'description': 'Actualizado', 'amount': 123.45,
                   'category': expense.category_id,
                   'incurred_at': timezone.now().isoformat()}
        response = auth_client.patch(url, payload, format='json')
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data['data']['description'] == 'Actualizado'
        assert float(data['data']['amount']) == 123.45

    def test_delete_expense(self, auth_client, expense):
        url = f'/api/v1/expenses/{expense.pk}/'
        response = auth_client.delete(url)
        assert response.status_code == status.HTTP_200_OK
        assert not Expense.objects.filter(pk=expense.pk).exists()
