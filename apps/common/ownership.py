from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.categories.models import Category
from apps.documents.models import Document
from apps.suppliers.models import Supplier


def categories_for(user) -> QuerySet:
    return Category.objects.filter(Q(scope=Category.SCOPE_SYSTEM) | Q(user=user))


def suppliers_for(user) -> QuerySet:
    return Supplier.objects.filter(user=user)


def documents_for(user) -> QuerySet:
    return Document.objects.filter(user=user)


def find_category_for(user, category_id):
    if category_id is None:
        return None
    return categories_for(user).filter(pk=category_id).first()
