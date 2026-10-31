import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

# Python 3.14 compatibility for Django template context copying during tests
try:
    from django.template import context as django_template_context
    def _safe_base_context_copy(self):
        duplicate = object.__new__(self.__class__)
        duplicate.__dict__.update(self.__dict__)
        duplicate.dicts = self.dicts[:]
        return duplicate
    django_template_context.BaseContext.__copy__ = _safe_base_context_copy
except Exception:
    pass

from apps.categories.models import Category
from apps.documents.models import Document, ExpenseItem
from apps.expenses.models import Expense
from apps.suppliers.models import Supplier
from rest_framework.test import APIClient

User = get_user_model()


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user_password():
    return 'Str0ngP@ssw0rd!'


@pytest.fixture
def user(db, user_password):
    return User.objects.create_user(
        email='test@receiptflow.com',
        password=user_password,
        first_name='Test',
        last_name='User',
        is_email_verified=True,
    )


@pytest.fixture
def user2(db, user_password):
    return User.objects.create_user(
        email='test2@receiptflow.com',
        password=user_password,
        first_name='Segundo',
        last_name='Usuario',
        is_email_verified=True,
    )


@pytest.fixture
def auth_client(api_client, user, user_password):
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def system_categories(db):
    categories = []
    for entry in [
        ('Food', 'utensils', '#F59E0B', 10),
        ('Transportation', 'car', '#3B82F6', 20),
        ('Technology', 'laptop', '#8B5CF6', 30),
        ('Health', 'heart-pulse', '#EF4444', 40),
        ('Home', 'home', '#10B981', 50),
        ('Entertainment', 'film', '#EC4899', 60),
        ('Education', 'graduation-cap', '#06B6D4', 70),
        ('Shopping', 'shopping-bag', '#F97316', 80),
        ('Other', 'folder', '#6B7280', 99),
    ]:
        name, icon, color, sort = entry
        categories.append(
            Category.objects.create(
            name=name, icon=icon, color=color, sort_order=sort,
            scope=Category.SCOPE_SYSTEM, user=None, is_active=True,
        )
        )
    return categories


@pytest.fixture
def supplier(db, user):
    return Supplier.objects.create(
        user=user,
        name='Proveedor Pruebas SA de CV',
        tax_id='ABC123456XYZ',
        email='contacto@proveedor.com',
        phone='5512345678',
        address='Calle Falsa 123, CDMX',
    )


@pytest.fixture
def category_user(db, user):
    return Category.objects.create(
        user=user,
        name='Categoría Personal',
        scope=Category.SCOPE_USER,
        color='#FF0000',
    )


@pytest.fixture
def expense(db, user, system_categories, supplier):
    category = system_categories[0]
    return Expense.objects.create(
        user=user,
        category=category,
        supplier=supplier,
        description='Cena en restaurante',
        amount=250.00,
        tax_amount=40.00,
        currency='MXN',
        payment_method=Expense.PAYMENT_METHOD_CARD,
        incurred_at=timezone.now(),
    )
