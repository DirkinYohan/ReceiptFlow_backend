"""Automatic bookkeeping: category resolution, supplier learning and document -> expense sync."""
from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import Q

from apps.categories.budgets import check_budget_alert_safely
from apps.categories.models import Category
from apps.documents.models import Document
from apps.expenses.models import Expense
from apps.expenses.recurring import refresh_supplier_recurrence

logger = logging.getLogger(__name__)

FALLBACK_CATEGORY_NAME = 'Other'


def _active_categories_for(user):
    return Category.objects.filter(
        Q(scope=Category.SCOPE_SYSTEM) | Q(scope=Category.SCOPE_USER, user=user),
        is_active=True,
    )


def resolve_category(user, supplier=None, suggested_name: Optional[str] = None) -> Optional[Category]:
    """Pick a category: what the user taught us for this supplier, then the AI guess, then 'Other'."""
    learned = getattr(supplier, 'default_category', None) if supplier else None
    if learned and learned.is_active:
        return learned
    categories = _active_categories_for(user)
    if suggested_name:
        name = suggested_name.strip()
        # User categories win over system ones with the same name.
        match = (
            categories.filter(scope=Category.SCOPE_USER, name__iexact=name).first()
            or categories.filter(scope=Category.SCOPE_SYSTEM, name__iexact=name).first()
        )
        if match:
            return match
    fallback, _ = Category.objects.get_or_create(
        scope=Category.SCOPE_SYSTEM,
        user=None,
        name=FALLBACK_CATEGORY_NAME,
        defaults={'slug': 'other', 'icon': 'folder', 'color': '#6B7280', 'sort_order': 99},
    )
    return fallback if fallback.is_active else None


def learn_supplier_category(supplier, category) -> bool:
    """Remember the category the user chose so future documents from this supplier reuse it."""
    if supplier is None or category is None or supplier.default_category_id == category.pk:
        return False
    supplier.default_category = category
    supplier.save(update_fields=['default_category', 'updated_at'])
    return True


def _user_title(document: Document) -> str:
    """The title, unless it is just the uploaded file name (e.g. 'WhatsApp Image 2026-10-05')."""
    title = (document.title or '').strip()
    filename = document.original_filename or ''
    if not title or title in (filename, os.path.splitext(filename)[0]):
        return ''
    return title


def _auto_description(document: Document) -> str:
    supplier = document.supplier.name if document.supplier else 'Sin proveedor'
    kind = document.get_document_type_display()
    number = f' {document.document_number}' if document.document_number else ''
    return f'{supplier} · {kind}{number}'


def _expense_values(document: Document) -> dict:
    total = document.total_amount or Decimal('0')
    tax = document.tax_amount or Decimal('0')
    discount = document.discount_amount or Decimal('0')
    tip = document.tip_amount or Decimal('0')
    # Expense.total_amount = amount + tax - discount + tip, so this keeps it equal to the printed total.
    amount = max(total - tax + discount - tip, Decimal('0'))
    supplier = document.supplier
    description = (_user_title(document) or _auto_description(document))[:300]
    return {
        'category': document.category,
        'supplier': supplier,
        'description': description,
        'amount': amount,
        'tax_amount': tax,
        'discount_amount': discount,
        'tip_amount': tip,
        'currency': document.currency or 'COP',
        'payment_method': document.payment_method or Expense.PAYMENT_METHOD_OTHER,
        'incurred_at': document.issued_at or document.processed_at or document.created_at,
    }


def _guess_from_ocr(document: Document) -> Optional[str]:
    from services.document_processor.processor import _guess_category_enhanced

    raw = document.raw_ocr_data or {}
    lines = [str(line) for line in (raw.get('ocr_lines') or [])][:80]
    supplier_name = document.supplier.name if document.supplier else None
    return _guess_category_enhanced(supplier_name, lines, [])


def sync_expense_from_document(document: Document, user_confirmed: bool = False) -> Optional[Expense]:
    """Create (or refresh) the expense of a completed document. Returns None when it isn't ready."""
    if document.status != Document.STATUS_COMPLETED or not document.total_amount:
        return None
    # A re-uploaded file only counts when the user approved it on purpose.
    if not user_confirmed and (document.structured_data or {}).get('is_exact_file_duplicate'):
        return None
    if document.category is None:
        document.category = resolve_category(
            document.user, document.supplier, _guess_from_ocr(document),
        )
        if document.category is None:
            return None
        document.save(update_fields=['category', 'updated_at'])

    values = _expense_values(document)
    with transaction.atomic():
        expense = document.expenses.select_for_update().first()
        if expense is None:
            from apps.expenses.services import expense_service
            expense = expense_service.create_from_document(document.user, document.pk, **values)
        else:
            for field, value in values.items():
                setattr(expense, field, value)
            expense.save()
    refresh_supplier_recurrence(document.user, expense.supplier)
    check_budget_alert_safely(expense)
    return expense


def sync_expense_safely(document: Document, user_confirmed: bool = False) -> Optional[Expense]:
    try:
        return sync_expense_from_document(document, user_confirmed)
    except Exception:
        logger.exception('Could not auto-create expense for document %s', document.pk)
        return None
