"""Automatic expense registration and supplier category learning."""
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.documents.models import Document
from apps.documents.tasks import process_document_task
from apps.expenses.automation import (
    learn_supplier_category,
    resolve_category,
    sync_expense_from_document,
)
from apps.expenses.models import Expense


def _category(categories, name):
    return next(c for c in categories if c.name == name)


@pytest.fixture
def completed_document(db, user, supplier, system_categories, tmp_path):
    fake_file = tmp_path / 'factura.png'
    fake_file.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)
    return Document.objects.create(
        user=user,
        title='Factura supermercado',
        original_filename='factura.png',
        file=str(fake_file),
        status=Document.STATUS_COMPLETED,
        supplier=supplier,
        category=_category(system_categories, 'Food'),
        issued_at=timezone.now(),
        total_amount=Decimal('119000'),
        tax_amount=Decimal('19000'),
        currency='COP',
    )


class TestResolveCategory:
    def test_learned_category_wins_over_ai(self, user, supplier, system_categories):
        supplier.default_category = _category(system_categories, 'Home')
        supplier.save()
        assert resolve_category(user, supplier, 'Food').name == 'Home'

    def test_ai_suggestion_used_without_learning(self, user, supplier, system_categories):
        assert resolve_category(user, supplier, 'Health').name == 'Health'

    def test_falls_back_to_other(self, user, supplier, system_categories):
        assert resolve_category(user, supplier, None).name == 'Other'
        assert resolve_category(user, None, 'Inventada').name == 'Other'


class TestSyncExpense:
    def test_creates_expense_matching_printed_total(self, completed_document):
        expense = sync_expense_from_document(completed_document)
        assert expense is not None
        assert expense.total_amount == Decimal('119000')
        assert expense.amount == Decimal('100000')
        assert expense.currency == 'COP'
        assert expense.category.name == 'Food'

    def test_is_idempotent_and_updates_existing(self, completed_document):
        first = sync_expense_from_document(completed_document)
        completed_document.total_amount = Decimal('238000')
        completed_document.tax_amount = Decimal('38000')
        completed_document.save()
        second = sync_expense_from_document(completed_document)
        assert first.pk == second.pk
        assert Expense.objects.filter(document=completed_document).count() == 1
        second.refresh_from_db()
        assert second.total_amount == Decimal('238000')

    def test_skips_documents_needing_review(self, completed_document):
        completed_document.status = Document.STATUS_REVIEW_REQUIRED
        completed_document.save()
        assert sync_expense_from_document(completed_document) is None

    def test_skips_exact_file_duplicates(self, completed_document):
        completed_document.structured_data = {'is_exact_file_duplicate': True}
        completed_document.save()
        assert sync_expense_from_document(completed_document) is None

    def test_missing_category_falls_back_to_learned(self, completed_document, system_categories):
        completed_document.category = None
        completed_document.save()
        supplier = completed_document.supplier
        supplier.default_category = _category(system_categories, 'Technology')
        supplier.save()
        expense = sync_expense_from_document(completed_document)
        assert expense.category.name == 'Technology'


class TestTaskAutoExpense:
    def test_completed_task_registers_expense(self, completed_document):
        completed_document.status = Document.STATUS_PENDING
        completed_document.save()
        with patch(
            'services.document_processor.processor.process_document_pipeline',
            return_value={'requires_review': False, 'review_reasons': [], 'processed_fields': {}},
        ):
            process_document_task.apply(args=[completed_document.pk]).get()
        assert Expense.objects.filter(document=completed_document).count() == 1

    def test_review_task_does_not_register_expense(self, completed_document):
        completed_document.status = Document.STATUS_PENDING
        completed_document.save()
        with patch(
            'services.document_processor.processor.process_document_pipeline',
            return_value={'requires_review': True, 'review_reasons': ['Total ilegible'], 'processed_fields': {}},
        ):
            process_document_task.apply(args=[completed_document.pk]).get()
        assert not Expense.objects.filter(document=completed_document).exists()


class TestLearning:
    def test_learn_supplier_category(self, supplier, system_categories):
        health = _category(system_categories, 'Health')
        assert learn_supplier_category(supplier, health) is True
        supplier.refresh_from_db()
        assert supplier.default_category == health
        assert learn_supplier_category(supplier, health) is False

    def test_editing_expense_category_teaches_supplier(self, auth_client, expense, system_categories):
        education = _category(system_categories, 'Education')
        response = auth_client.patch(
            f'/api/v1/expenses/{expense.pk}/', {'category': education.pk}, format='json',
        )
        assert response.status_code == 200
        expense.supplier.refresh_from_db()
        assert expense.supplier.default_category == education

    def test_reviewing_document_registers_expense_and_learns(
        self, auth_client, completed_document, system_categories,
    ):
        completed_document.status = Document.STATUS_REVIEW_REQUIRED
        completed_document.save()
        shopping = _category(system_categories, 'Shopping')
        response = auth_client.patch(
            f'/api/v1/documents/{completed_document.pk}/extracted-data/',
            {'status': 'completed', 'category_id': shopping.pk},
            format='json',
        )
        assert response.status_code == 200
        assert response.data['data']['expense_id'] is not None
        completed_document.supplier.refresh_from_db()
        assert completed_document.supplier.default_category == shopping
        assert Expense.objects.get(document=completed_document).category == shopping


class TestDeletingDocument:
    def test_deleting_document_deletes_its_expense(self, auth_client, completed_document):
        expense = sync_expense_from_document(completed_document)
        response = auth_client.delete(f'/api/v1/documents/{completed_document.pk}/')
        assert response.status_code == 200
        assert not Expense.objects.filter(pk=expense.pk).exists()

    def test_reupload_after_delete_counts_once(self, auth_client, user, completed_document):
        sync_expense_from_document(completed_document)
        auth_client.delete(f'/api/v1/documents/{completed_document.pk}/')
        completed_document.pk = None
        completed_document.save()
        sync_expense_from_document(completed_document)
        assert Expense.objects.filter(user=user).count() == 1

    def test_manual_expenses_are_untouched(self, auth_client, user, expense, completed_document):
        auth_client.delete(f'/api/v1/documents/{completed_document.pk}/')
        assert Expense.objects.filter(pk=expense.pk).exists()
