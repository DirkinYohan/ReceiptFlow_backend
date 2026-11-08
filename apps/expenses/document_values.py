from __future__ import annotations

import os
from decimal import Decimal
from typing import Optional

from django.db.models import Q

from apps.categories.models import Category
from apps.documents.models import Document
from apps.expenses.models import Expense

FALLBACK_CATEGORY_NAME = 'Other'


def _active_categories_for(user):
    return Category.objects.filter(
        Q(scope=Category.SCOPE_SYSTEM) | Q(scope=Category.SCOPE_USER, user=user),
        is_active=True,
    )


def resolve_category(user, supplier=None, suggested_name: Optional[str] = None) -> Optional[Category]:
    learned = getattr(supplier, 'default_category', None) if supplier else None
    if learned and learned.is_active:
        return learned
    categories = _active_categories_for(user)
    if suggested_name:
        name = suggested_name.strip()
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


def _user_title(document: Document) -> str:
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
