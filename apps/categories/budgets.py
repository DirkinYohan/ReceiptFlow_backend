"""Monthly budgets per category: how much is spent and when to warn."""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from django.db.models import DecimalField, F, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.expenses.models import Expense

from .models import CategoryBudget

logger = logging.getLogger(__name__)

WARNING_PCT = 80
EXCEEDED_PCT = 100


def month_start(day: Optional[date] = None) -> date:
    day = day or timezone.localdate()
    return day.replace(day=1)


def _next_month(start: date) -> date:
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


def spent_in_month(user, category, currency: str, start: date) -> Decimal:
    total = (
        Expense.objects.filter(
            user=user,
            category=category,
            currency=currency,
            incurred_at__date__gte=start,
            incurred_at__date__lt=_next_month(start),
        )
        .aggregate(
            total=Coalesce(
                Sum(F('amount') + F('tax_amount') - F('discount_amount') + F('tip_amount')),
                Decimal('0'),
                output_field=DecimalField(max_digits=16, decimal_places=2),
            )
        )['total']
    )
    return Decimal(total)


def budget_state(pct: Decimal) -> str:
    if pct >= EXCEEDED_PCT:
        return 'exceeded'
    if pct >= WARNING_PCT:
        return 'warning'
    return 'ok'


def budget_status(budget: CategoryBudget, day: Optional[date] = None) -> dict:
    start = month_start(day)
    spent = spent_in_month(budget.user, budget.category, budget.currency, start)
    pct = (spent / budget.monthly_amount * 100).quantize(Decimal('0.1'))
    return {
        'spent': spent,
        'remaining': budget.monthly_amount - spent,
        'percentage': pct,
        'state': budget_state(pct),
        'month': start,
    }


def check_budget_alert(user, category, day: Optional[date] = None) -> Optional[str]:
    """Email the user the first time this month a budget passes 80% and again when it passes 100%."""
    if category is None:
        return None
    budget = CategoryBudget.objects.filter(user=user, category=category).select_related('category').first()
    if budget is None:
        return None
    status = budget_status(budget, day)
    state = status['state']
    start = status['month']
    if state == 'exceeded' and budget.exceeded_sent_for != start:
        budget.exceeded_sent_for = start
        budget.warning_sent_for = start
    elif state == 'warning' and budget.warning_sent_for != start:
        budget.warning_sent_for = start
    else:
        return None
    from apps.users.services import EmailService
    EmailService.send_budget_alert(user, budget, status)
    budget.save(update_fields=['warning_sent_for', 'exceeded_sent_for', 'updated_at'])
    return state


def check_budget_alert_safely(expense) -> Optional[str]:
    # Old receipts uploaded today must not trigger alerts for months that are already closed.
    if month_start(timezone.localdate(expense.incurred_at)) != month_start():
        return None
    try:
        return check_budget_alert(expense.user, expense.category)
    except Exception:
        logger.exception('Budget alert check failed for expense %s', expense.pk)
        return None
