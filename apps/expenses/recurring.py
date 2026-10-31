"""Detect recurring expenses per supplier and tell which ones are due or missing."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Optional

from django.utils import timezone

from apps.documents.payables import BILL_CLASSES
from apps.expenses.models import Expense

# (frequency, min interval days, max interval days, nominal period days)
FREQUENCY_WINDOWS = [
    (Expense.RECURRENCE_WEEKLY, 5, 9, 7),
    (Expense.RECURRENCE_BIWEEKLY, 12, 17, 14),
    (Expense.RECURRENCE_MONTHLY, 25, 37, 30),
    (Expense.RECURRENCE_QUARTERLY, 80, 100, 91),
    (Expense.RECURRENCE_YEARLY, 350, 380, 365),
]
PERIOD_DAYS = {freq: period for freq, _, _, period in FREQUENCY_WINDOWS}
PERIOD_DAYS[Expense.RECURRENCE_DAILY] = 1


def _frequency_for_interval(days: float) -> Optional[str]:
    for frequency, low, high, _ in FREQUENCY_WINDOWS:
        if low <= days <= high:
            return frequency
    return None


def _is_bill(expense: Expense) -> bool:
    data = getattr(expense.document, 'structured_data', None) or {}
    return data.get('document_class') in BILL_CLASSES


def detect_frequency(expenses: list[Expense]) -> Optional[str]:
    days = sorted({timezone.localdate(e.incurred_at) for e in expenses})
    if len(days) >= 2:
        intervals = [(b - a).days for a, b in zip(days, days[1:])]
        frequency = _frequency_for_interval(median(intervals))
        # The median alone lies with few points (4 and 162 days -> "quarterly"): most gaps must agree.
        allowed_outliers = 1 if len(intervals) >= 3 else 0
        matching = sum(1 for gap in intervals if _frequency_for_interval(gap) == frequency)
        if frequency and matching >= len(intervals) - allowed_outliers:
            return frequency
    # A single utility bill (water, power, gas, phone) is monthly by nature.
    if any(_is_bill(e) for e in expenses):
        return Expense.RECURRENCE_MONTHLY
    return None


def refresh_supplier_recurrence(user, supplier) -> Optional[str]:
    """Mark the supplier's expenses as recurring when a pattern shows up. Manual choices are kept."""
    if supplier is None:
        return None
    expenses = list(
        Expense.objects.filter(user=user, supplier=supplier)
        .select_related('document')
        .order_by('incurred_at')
    )
    frequency = detect_frequency(expenses)
    if frequency:
        Expense.objects.filter(
            pk__in=[e.pk for e in expenses], recurrence_frequency='',
        ).update(is_recurring=True, recurrence_frequency=frequency)
    return frequency


def recurring_overview(user, today: Optional[date] = None) -> list[dict]:
    today = today or timezone.localdate()
    by_supplier: dict = {}
    qs = (
        Expense.objects.filter(user=user, is_recurring=True)
        .exclude(recurrence_frequency='')
        .select_related('supplier', 'category')
        .order_by('incurred_at')
    )
    for expense in qs:
        key = expense.supplier_id or f'desc:{expense.description.lower()}'
        by_supplier.setdefault(key, []).append(expense)

    items = []
    for group in by_supplier.values():
        last = group[-1]
        frequency = last.recurrence_frequency
        period = PERIOD_DAYS.get(frequency, 30)
        last_day = timezone.localdate(last.incurred_at)
        next_expected = last_day + timedelta(days=period)
        grace = max(3, period // 4)
        if today > next_expected + timedelta(days=grace):
            state = 'missing'
        elif (next_expected - today).days <= 7:
            state = 'due_soon'
        else:
            state = 'ok'
        recent = group[-3:]
        average = sum((e.total_amount for e in recent), Decimal('0')) / len(recent)
        items.append({
            'supplier_id': last.supplier_id,
            'name': last.supplier.name if last.supplier else last.description,
            'category_name': last.category.name if last.category else None,
            'frequency': frequency,
            'occurrences': len(group),
            'average_amount': average.quantize(Decimal('0.01')),
            'currency': last.currency,
            'last_date': last_day,
            'next_expected': next_expected,
            'state': state,
            'last_expense_id': last.pk,
        })
    order = {'missing': 0, 'due_soon': 1, 'ok': 2}
    return sorted(items, key=lambda item: (order[item['state']], item['next_expected']))
