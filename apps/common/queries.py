"""Correlated subqueries for list endpoints.

Annotating with Count() over a JOIN makes every row and the pagination COUNT(*) go through
a GROUP BY. A scalar subquery keeps the main query flat, and Django drops it from count().
"""
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce


def count_of(model, field: str):
    """Number of `model` rows whose `field` points to the outer row (0 when there are none)."""
    rows = (
        model.objects.filter(**{field: OuterRef('pk')})
        .order_by()
        .values(field)
        .annotate(total=Count('pk'))
        .values('total')
    )
    return Coalesce(Subquery(rows, output_field=IntegerField()), 0)
