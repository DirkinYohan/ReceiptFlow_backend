import pytest
from django.contrib.auth import get_user_model
from rest_framework import status

from apps.categories.models import Category
from apps.suppliers.models import Supplier

User = get_user_model()


@pytest.mark.django_db
class TestCategoryEndpoints:
    def test_list_includes_system_categories(self, auth_client, system_categories, user):
        Category.objects.create(
            user=user, name='Personal', scope=Category.SCOPE_USER, color='#111',
        )
        url = '/api/v1/categories/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        results = response.json().get('results') or response.json().get('data')
        assert len(results) >= len(system_categories)

    def test_create_user_category(self, auth_client):
        url = '/api/v1/categories/'
        payload = {'name': 'Mi Categoría', 'color': '#AABBCC', 'icon': 'star'}
        response = auth_client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json() if 'name' in response.json() else response.json().get('data', response.json())
        cat = Category.objects.filter(name='Mi Categoría').first()
        assert cat is not None
        assert cat.scope == Category.SCOPE_USER

    def test_cannot_delete_system_category(self, auth_client, system_categories):
        cat = system_categories[0]
        url = f'/api/v1/categories/{cat.pk}/'
        response = auth_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert Category.objects.filter(pk=cat.pk).exists()

    def test_update_user_category(self, auth_client, category_user):
        url = f'/api/v1/categories/{category_user.pk}/'
        response = auth_client.patch(
            url, {'name': 'Categoría Editada', 'color': '#FFFFFF'}, format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        category_user.refresh_from_db()
        assert category_user.name == 'Categoría Editada'

    def test_available_categories(self, auth_client, system_categories, category_user):
        url = '/api/v1/categories/available/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert len(data) >= len(system_categories) + 1


@pytest.mark.django_db
class TestSupplierEndpoints:
    def test_create_supplier(self, auth_client):
        url = '/api/v1/suppliers/'
        payload = {
            'name': 'Nuevo Proveedor',
            'tax_id': 'NPROV123456',
            'email': 'hola@nuevo.com',
            'phone': '5500112233',
            'address': 'Calle 1',
        }
        response = auth_client.post(url, payload, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert Supplier.objects.filter(name='Nuevo Proveedor').exists()

    def test_supplier_autocomplete(self, auth_client, supplier):
        url = '/api/v1/suppliers/autocomplete/?q=Proveedor'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()['data']
        assert len(data) >= 1

    def test_supplier_toggle_favorite(self, auth_client, supplier):
        assert supplier.is_favorite is False
        url = f'/api/v1/suppliers/{supplier.pk}/toggle-favorite/'
        response = auth_client.post(url)
        assert response.status_code == status.HTTP_200_OK
        supplier.refresh_from_db()
        assert supplier.is_favorite is True

    def test_list_suppliers_isolated(self, auth_client, supplier, user, user2):
        Supplier.objects.create(user=user2, name='Supplier user 2')
        url = '/api/v1/suppliers/'
        response = auth_client.get(url)
        assert response.status_code == status.HTTP_200_OK
