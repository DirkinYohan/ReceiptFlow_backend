from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.documents.models import Document
from apps.expenses.models import Expense
from apps.suppliers.models import Supplier

LISTS = [
    '/api/v1/documents/?page_size=50',
    '/api/v1/documents/review-queue/',
    '/api/v1/expenses/?page_size=50',
    '/api/v1/suppliers/?page_size=50',
]


def _seed(user, category, n):
    for i in range(n):
        supplier = Supplier.objects.create(user=user, name=f'Proveedor {Supplier.objects.count()}')
        doc = Document.objects.create(
            user=user, supplier=supplier, category=category, file='fake/x.png',
            status=Document.STATUS_REVIEW_REQUIRED if i % 2 else Document.STATUS_COMPLETED,
            total_amount=Decimal('1000'),
        )
        Expense.objects.create(
            user=user, document=doc, supplier=supplier, category=category, description='Gasto',
            amount=Decimal('1000'), incurred_at=timezone.now(),
        )


def _queries(client, url):
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(url).status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.parametrize('url', LISTS)
def test_list_queries_do_not_grow_with_rows(auth_client, user, system_categories, url):
    _seed(user, system_categories[0], 2)
    few = _queries(auth_client, url)
    _seed(user, system_categories[0], 10)
    many = _queries(auth_client, url)
    assert many == few, f'{url}: {few} consultas con 2 filas, {many} con 12'


def test_document_list_exposes_linked_expense(auth_client, user, system_categories):
    _seed(user, system_categories[0], 1)
    row = auth_client.get('/api/v1/documents/').json()['results'][0]
    assert row['expense_id'] == Expense.objects.get(user=user).pk
    assert row['line_items_count'] == 0


def test_dashboard_summary_is_two_queries_plus_auth(auth_client, user, system_categories):
    _seed(user, system_categories[0], 3)
    assert _queries(auth_client, '/api/v1/reports/dashboard/summary/?months=6') <= 3


def test_api_responses_are_not_cacheable_by_shared_caches(auth_client):
    response = auth_client.get('/api/v1/expenses/')
    assert response['Cache-Control'] == 'private, no-store'
    assert 'Authorization' in response['Vary']
