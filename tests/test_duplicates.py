"""Detecting invoices the user already uploaded."""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.documents.duplicates import find_logical_duplicate, same_supplier
from apps.documents.models import Document
from apps.expenses.automation import sync_expense_from_document
from apps.suppliers.models import Supplier


def _doc(user, supplier=None, number='17627', total='143700', issued_at=None, **extra):
    extra.setdefault('status', Document.STATUS_COMPLETED)
    return Document.objects.create(
        user=user, supplier=supplier, document_number=number, total_amount=Decimal(total),
        issued_at=issued_at, file='fake/doc.png', **extra,
    )


@pytest.fixture
def cojardin(user):
    return Supplier.objects.create(user=user, name='COJARDIN S.A. - E.S.P.', tax_id='8301315724')


class TestSameSupplier:
    def test_name_variants_match(self, user):
        a = Supplier.objects.create(user=user, name='EMPOTAM ESP')
        b = Supplier.objects.create(user=user, name='EMPOTAM E.S.P.')
        assert same_supplier(a, b)

    def test_nit_with_and_without_dv_match(self, user):
        a = Supplier.objects.create(user=user, name='Cojardin', tax_id='8301315724')
        b = Supplier.objects.create(user=user, name='Aseo Cojardin SA', tax_id='830131572')
        assert same_supplier(a, b)

    def test_different_companies(self, user):
        a = Supplier.objects.create(user=user, name='Tienda A', tax_id='900111222')
        b = Supplier.objects.create(user=user, name='Tienda B', tax_id='800333444')
        assert not same_supplier(a, b)


class TestFindLogicalDuplicate:
    def test_matches_when_previous_has_no_date(self, user, cojardin):
        original = _doc(user, cojardin, issued_at=None)
        new = _doc(user, cojardin, status=Document.STATUS_PROCESSING)
        assert find_logical_duplicate(new, cojardin, '17627', Decimal('143700'), timezone.now()) == original

    def test_matches_supplier_read_differently(self, user):
        first = Supplier.objects.create(user=user, name='EMPOTAM E.S.P.')
        second = Supplier.objects.create(user=user, name='EMPOTAM ESP')
        original = _doc(user, first, number='320329', total='7238')
        new = _doc(user, second, number='320329', total='7238')
        assert find_logical_duplicate(new, second, '320329', Decimal('7238'), None) == original

    def test_different_dates_are_not_duplicates(self, user, cojardin):
        now = timezone.now()
        _doc(user, cojardin, issued_at=now - timedelta(days=30))
        new = _doc(user, cojardin)
        assert find_logical_duplicate(new, cojardin, '17627', Decimal('143700'), now) is None

    def test_different_total_or_number(self, user, cojardin):
        _doc(user, cojardin)
        new = _doc(user, cojardin, number='99999')
        assert find_logical_duplicate(new, cojardin, '17627', Decimal('1'), None) is None
        assert find_logical_duplicate(new, cojardin, '', Decimal('143700'), None) is None

    def test_other_users_documents_are_ignored(self, user, user2, cojardin):
        _doc(user2, None)
        new = _doc(user, cojardin)
        assert find_logical_duplicate(new, cojardin, '17627', Decimal('143700'), None) is None


class TestExactFileReupload:
    def test_reupload_goes_to_review(self, user):
        from services.document_processor.processor import process_document_pipeline

        _doc(user, checksum='samefile', structured_data={'supplier_name': 'X'})
        dup = _doc(user, checksum='samefile', status=Document.STATUS_PENDING)
        result = process_document_pipeline(dup)
        assert result['requires_review'] is True
        assert 'ya lo habías subido' in result['review_reasons'][0]

    def test_approved_reupload_registers_expense(self, user, cojardin, system_categories):
        doc = _doc(
            user, cojardin, category=system_categories[4],
            structured_data={'is_exact_file_duplicate': True},
        )
        assert sync_expense_from_document(doc) is None
        assert sync_expense_from_document(doc, user_confirmed=True) is not None
