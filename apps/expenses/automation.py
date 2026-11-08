from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction

from apps.categories.budgets import check_budget_alert_safely
from apps.documents.models import Document
from apps.expenses.document_values import (  # noqa: F401
    FALLBACK_CATEGORY_NAME,
    _expense_values,
    resolve_category,
)
from apps.expenses.models import Expense
from apps.expenses.recurring import refresh_supplier_recurrence
from apps.expenses.services import expense_service
from services.document_processor.text_parsing import _guess_category_enhanced

logger = logging.getLogger(__name__)


def learn_supplier_category(supplier, category) -> bool:
    if supplier is None or category is None or supplier.default_category_id == category.pk:
        return False
    supplier.default_category = category
    supplier.save(update_fields=['default_category', 'updated_at'])
    return True


def _guess_from_ocr(document: Document) -> Optional[str]:
    raw = document.raw_ocr_data or {}
    lines = [str(line) for line in (raw.get('ocr_lines') or [])][:80]
    supplier_name = document.supplier.name if document.supplier else None
    return _guess_category_enhanced(supplier_name, lines, [])


def sync_expense_from_document(document: Document, user_confirmed: bool = False) -> Optional[Expense]:
    if document.status != Document.STATUS_COMPLETED or not document.total_amount:
        return None
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
