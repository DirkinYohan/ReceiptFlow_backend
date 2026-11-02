from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce


def count_of(model, field: str):
    rows = (
        model.objects.filter(**{field: OuterRef('pk')})
        .order_by()
        .values(field)
        .annotate(total=Count('pk'))
        .values('total')
    )
    return Coalesce(Subquery(rows, output_field=IntegerField()), 0)
