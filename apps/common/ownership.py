from __future__ import annotations

from django.db.models import Q, QuerySet


def categories_for(user) -> QuerySet:
    from apps.categories.models import Category

    return Category.objects.filter(Q(scope=Category.SCOPE_SYSTEM) | Q(user=user))


def suppliers_for(user) -> QuerySet:
    from apps.suppliers.models import Supplier

    return Supplier.objects.filter(user=user)


def documents_for(user) -> QuerySet:
    from apps.documents.models import Document

    return Document.objects.filter(user=user)


def find_category_for(user, category_id):
    if category_id is None:
        return None
    return categories_for(user).filter(pk=category_id).first()
