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

    def test_list_and_detail_include_category_and_supplier_names(self, auth_client, expense):
        listed = auth_client.get('/api/v1/expenses/').json()
        rows = listed.get('results') or listed.get('data') or []
        row = next(item for item in rows if item['id'] == expense.pk)
        assert row['category_name'] == expense.category.name
        assert row['supplier_name'] == expense.supplier.name

        detail = auth_client.get(f'/api/v1/expenses/{expense.pk}/').json()['data']
        assert detail['category_name'] == expense.category.name
        assert detail['supplier_name'] == expense.supplier.name

    def test_expense_without_supplier_has_null_supplier_name(self, auth_client, expense):
        Expense.objects.filter(pk=expense.pk).update(supplier=None)
        detail = auth_client.get(f'/api/v1/expenses/{expense.pk}/').json()['data']
        assert detail['supplier_name'] is None

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


@pytest.mark.django_db
class TestExpenseOwnership:
    def _payload(self, **extra):
        payload = {'description': 'Gasto', 'amount': '10', 'incurred_at': timezone.now().isoformat()}
        payload.update(extra)
        return payload

    def test_cannot_use_another_users_supplier(self, auth_client, user2, system_categories):
        from apps.suppliers.models import Supplier
        other = Supplier.objects.create(user=user2, name='Proveedor de otro')
        response = auth_client.post('/api/v1/expenses/', self._payload(
            category=system_categories[0].pk, supplier=other.pk,
        ), format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not Expense.objects.filter(supplier=other).exists()

    def test_cannot_use_another_users_category(self, auth_client, user2):
        private = Category.objects.create(user=user2, name='Privada', scope=Category.SCOPE_USER)
        response = auth_client.post('/api/v1/expenses/', self._payload(category=private.pk), format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_link_another_users_document(self, auth_client, user2, system_categories):
        from apps.documents.models import Document
        doc = Document.objects.create(user=user2, original_filename='otro.png', file='documents/otro.png')
        response = auth_client.post('/api/v1/expenses/', self._payload(
            category=system_categories[0].pk, document=doc.pk,
        ), format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_cannot_move_expense_to_another_users_supplier(self, auth_client, expense, user2):
        from apps.suppliers.models import Supplier
        other = Supplier.objects.create(user=user2, name='Ajeno')
        response = auth_client.patch(f'/api/v1/expenses/{expense.pk}/', {'supplier': other.pk}, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        expense.refresh_from_db()
        assert expense.supplier_id != other.pk

    def test_own_supplier_and_system_category_still_work(self, auth_client, supplier, system_categories):
        response = auth_client.post('/api/v1/expenses/', self._payload(
            category=system_categories[0].pk, supplier=supplier.pk,
        ), format='json')
        assert response.status_code == status.HTTP_201_CREATED
