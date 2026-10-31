"""Bills to pay: infer payment state from the document and bucket unpaid ones by due date."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Optional

from django.utils import timezone

from .models import Document

DUE_SOON_DAYS = 7
REMINDER_DAYS = 3
REMINDER_INTERVAL_DAYS = 3

PAID_AT_SALE_CLASSES = {'POS_RECEIPT', 'RECEIPT'}
BILL_CLASSES = {'UTILITY_BILL', 'ENERGY_BILL', 'WATER_BILL', 'GAS_BILL', 'TELECOM_BILL'}


def _parse_iso_date(value) -> Optional[date]:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _aware(day: date) -> datetime:
    return timezone.make_aware(datetime.combine(day, time(12, 0)))


def apply_payment_inference(document: Document, document_class: Optional[str], extra_fields: dict) -> None:
    """Fill is_paid / due_date from what the document says. Never marks a paid document as unpaid."""
    is_bill = (document_class or '') in BILL_CLASSES
    if not document.due_date and is_bill:
        # Utility bills print "pago oportuno" instead of a due date.
        payment_day = _parse_iso_date(extra_fields.get('payment_date'))
        if payment_day:
            document.due_date = _aware(payment_day)
    if document.is_paid:
        return
    if extra_fields.get('settled_by_payment'):
        document.is_paid = True
        return
    paid_at_sale = (
        (document_class or '') in PAID_AT_SALE_CLASSES
        or document.document_type in (Document.DOCUMENT_TYPE_TICKET, Document.DOCUMENT_TYPE_RECEIPT)
    )
    if paid_at_sale and not document.due_date and not is_bill:
        document.is_paid = True


def due_state(document: Document, today: Optional[date] = None) -> str:
    today = today or timezone.localdate()
    if document.is_paid:
        return 'paid'
    if not document.due_date:
        return 'no_due_date'
    days = (timezone.localdate(document.due_date) - today).days
    if days < 0:
        return 'overdue'
    if days <= DUE_SOON_DAYS:
        return 'due_soon'
    return 'upcoming'


def unpaid_documents(user):
    return (
        Document.objects.filter(
            user=user,
            is_paid=False,
            total_amount__isnull=False,
            status__in=[Document.STATUS_COMPLETED, Document.STATUS_REVIEW_REQUIRED],
        )
        .select_related('supplier', 'category')
        .order_by('due_date', 'issued_at')
    )


def payables_overview(user, today: Optional[date] = None) -> dict:
    today = today or timezone.localdate()
    buckets = {'overdue': [], 'due_soon': [], 'upcoming': [], 'no_due_date': []}
    totals = {key: 0 for key in buckets}
    for doc in unpaid_documents(user):
        state = due_state(doc, today)
        days = (timezone.localdate(doc.due_date) - today).days if doc.due_date else None
        buckets[state].append({
            'id': doc.pk,
            'title': doc.title or doc.original_filename,
            'supplier_name': doc.supplier.name if doc.supplier else None,
            'category_name': doc.category.name if doc.category else None,
            'total_amount': doc.total_amount,
            'currency': doc.currency,
            'due_date': doc.due_date,
            'days_until_due': days,
            'state': state,
        })
        totals[state] += doc.total_amount or 0
    return {'buckets': buckets, 'totals': totals}


def documents_needing_reminder(user, today: Optional[date] = None) -> list[Document]:
    today = today or timezone.localdate()
    resend_before = timezone.now() - timedelta(days=REMINDER_INTERVAL_DAYS)
    limit = _aware(today + timedelta(days=REMINDER_DAYS))
    docs = unpaid_documents(user).filter(due_date__isnull=False, due_date__lte=limit)
    return [
        doc for doc in docs
        if doc.last_due_reminder_at is None or doc.last_due_reminder_at < resend_before
    ]
