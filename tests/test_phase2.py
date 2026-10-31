"""Review inbox, recurring expense detection and payables/reminders."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.core import mail
from django.utils import timezone

from apps.documents.models import Document
from apps.documents.payables import apply_payment_inference, due_state, payables_overview
from apps.documents.tasks import send_due_reminders
from apps.expenses.models import Expense
from apps.expenses.recurring import recurring_overview, refresh_supplier_recurrence

TODAY = date(2026, 10, 25)


def _at(day: date):
    return timezone.make_aware(datetime.combine(day, time(12, 0)))


def _doc(user, tmp_path, **fields):
    fake_file = tmp_path / f'doc{Document.objects.count()}.png'
    fake_file.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 50)
    defaults = dict(
        user=user, original_filename='doc.png', file=str(fake_file),
        status=Document.STATUS_COMPLETED, total_amount=Decimal('50000'), currency='COP',
    )
    defaults.update(fields)
    return Document.objects.create(**defaults)


def _expense(user, supplier, category, day: date, amount='50000'):
    return Expense.objects.create(
        user=user, supplier=supplier, category=category, description='Factura',
        amount=Decimal(amount), currency='COP', incurred_at=_at(day),
    )


class TestPaymentInference:
    def test_pos_ticket_is_paid(self, user, tmp_path):
        doc = _doc(user, tmp_path, document_type=Document.DOCUMENT_TYPE_TICKET)
        apply_payment_inference(doc, 'POS_RECEIPT', {})
        assert doc.is_paid is True

    def test_utility_bill_uses_payment_date_as_due_date(self, user, tmp_path):
        doc = _doc(user, tmp_path, document_type=Document.DOCUMENT_TYPE_INVOICE)
        apply_payment_inference(doc, 'ENERGY_BILL', {'payment_date': '2026-11-02'})
        assert doc.is_paid is False
        assert timezone.localdate(doc.due_date) == date(2026, 11, 2)

    def test_never_unmarks_paid(self, user, tmp_path):
        doc = _doc(user, tmp_path, is_paid=True, document_type=Document.DOCUMENT_TYPE_INVOICE)
        apply_payment_inference(doc, 'WATER_BILL', {'payment_date': '2026-11-02'})
        assert doc.is_paid is True


class TestPayables:
    def test_buckets(self, user, tmp_path):
        _doc(user, tmp_path, due_date=_at(TODAY - timedelta(days=2)))
        _doc(user, tmp_path, due_date=_at(TODAY + timedelta(days=3)))
        _doc(user, tmp_path, due_date=_at(TODAY + timedelta(days=20)))
        _doc(user, tmp_path)
        _doc(user, tmp_path, is_paid=True, due_date=_at(TODAY))
        overview = payables_overview(user, TODAY)
        sizes = {key: len(items) for key, items in overview['buckets'].items()}
        assert sizes == {'overdue': 1, 'due_soon': 1, 'upcoming': 1, 'no_due_date': 1}
        assert overview['totals']['overdue'] == Decimal('50000')

    def test_due_state(self, user, tmp_path):
        doc = _doc(user, tmp_path, due_date=_at(TODAY - timedelta(days=1)))
        assert due_state(doc, TODAY) == 'overdue'

    def test_endpoint(self, auth_client, user, tmp_path):
        _doc(user, tmp_path, due_date=timezone.now() - timedelta(days=1))
        response = auth_client.get('/api/v1/documents/payables/')
        assert response.status_code == 200
        assert len(response.data['data']['buckets']['overdue']) == 1


class TestReminders:
    def test_sends_once_then_waits(self, user, tmp_path):
        _doc(user, tmp_path, due_date=timezone.now() + timedelta(days=1))
        send_due_reminders()
        assert len(mail.outbox) == 1
        assert 'por vencer' in mail.outbox[0].subject.lower()
        send_due_reminders()
        assert len(mail.outbox) == 1

    def test_no_email_for_far_due_dates(self, user, tmp_path):
        _doc(user, tmp_path, due_date=timezone.now() + timedelta(days=30))
        send_due_reminders()
        assert len(mail.outbox) == 0


class TestRecurring:
    def test_monthly_pattern_detected(self, user, supplier, system_categories):
        for day in (date(2026, 7, 5), date(2026, 8, 4), date(2026, 9, 5)):
            _expense(user, supplier, system_categories[4], day)
        assert refresh_supplier_recurrence(user, supplier) == Expense.RECURRENCE_MONTHLY
        assert Expense.objects.filter(supplier=supplier, is_recurring=True).count() == 3

    def test_irregular_purchases_not_recurring(self, user, supplier, system_categories):
        for day in (date(2026, 1, 5), date(2026, 1, 9), date(2026, 6, 20)):
            _expense(user, supplier, system_categories[0], day)
        assert refresh_supplier_recurrence(user, supplier) is None

    def test_manual_frequency_is_kept(self, user, supplier, system_categories):
        first = _expense(user, supplier, system_categories[4], date(2026, 8, 4))
        first.recurrence_frequency = Expense.RECURRENCE_QUARTERLY
        first.is_recurring = True
        first.save()
        _expense(user, supplier, system_categories[4], date(2026, 9, 4))
        refresh_supplier_recurrence(user, supplier)
        first.refresh_from_db()
        assert first.recurrence_frequency == Expense.RECURRENCE_QUARTERLY

    def test_overview_flags_missing_bill(self, user, supplier, system_categories):
        for day in (date(2026, 7, 5), date(2026, 8, 5)):
            _expense(user, supplier, system_categories[4], day)
        refresh_supplier_recurrence(user, supplier)
        item = recurring_overview(user, TODAY)[0]
        assert item['state'] == 'missing'
        assert item['next_expected'] == date(2026, 9, 4)

    def test_endpoint(self, auth_client, user, supplier, system_categories):
        for day in (date(2026, 9, 1), date(2026, 10, 1)):
            _expense(user, supplier, system_categories[4], day)
        refresh_supplier_recurrence(user, supplier)
        response = auth_client.get('/api/v1/expenses/recurring/')
        assert response.status_code == 200
        assert response.data['data'][0]['frequency'] == 'monthly'


class TestReviewQueue:
    def test_lists_reasons_and_missing_fields(self, auth_client, user, tmp_path):
        _doc(user, tmp_path, status=Document.STATUS_REVIEW_REQUIRED, total_amount=None,
             processing_error='No se identificó el monto total.')
        _doc(user, tmp_path)
        response = auth_client.get('/api/v1/documents/review-queue/')
        assert response.status_code == 200
        data = response.data['data']
        assert data['count'] == 1
        item = data['results'][0]
        assert 'No se identificó el monto total.' in item['review_reasons']
        assert 'total_amount' in item['missing_fields']

    def test_approve_with_new_supplier_registers_expense(
        self, auth_client, user, tmp_path, system_categories,
    ):
        doc = _doc(user, tmp_path, status=Document.STATUS_REVIEW_REQUIRED, total_amount=None)
        response = auth_client.patch(
            f'/api/v1/documents/{doc.pk}/extracted-data/',
            {
                'status': 'completed', 'total_amount': '80000',
                'supplier_name': 'Tienda Nueva', 'category_id': system_categories[0].pk,
                'is_paid': True,
            },
            format='json',
        )
        assert response.status_code == 200
        doc.refresh_from_db()
        assert doc.supplier.name == 'Tienda Nueva'
        assert doc.is_paid is True
        assert doc.supplier.default_category == system_categories[0]
        assert Expense.objects.get(document=doc).total_amount == Decimal('80000')
