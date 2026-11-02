from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from apps.suppliers.models import Supplier

from .models import Document


def _tax_base(tax_id: str) -> str:
    digits = re.sub(r'\D', '', tax_id or '')
    return digits[:9] if len(digits) >= 9 else digits


def same_supplier(a: Optional[Supplier], b: Optional[Supplier]) -> bool:
    if a is None or b is None:
        return True
    if a.pk == b.pk:
        return True
    if _tax_base(a.tax_id) and _tax_base(a.tax_id) == _tax_base(b.tax_id):
        return True
    normalize = Supplier.objects._normalize
    return bool(normalize(a.name)) and normalize(a.name) == normalize(b.name)


def find_logical_duplicate(
    document: Document,
    supplier: Optional[Supplier],
    number: Optional[str],
    total: Optional[Decimal],
    issued_at: Optional[datetime],
) -> Optional[Document]:
    if not number or not number.strip() or total is None:
        return None
    candidates = (
        Document.objects.filter(
            user=document.user,
            document_number__iexact=number.strip(),
            total_amount=total,
        )
        .exclude(pk=document.pk)
        .exclude(status=Document.STATUS_FAILED)
        .select_related('supplier')
        .order_by('created_at')
    )
    for candidate in candidates:
        if not same_supplier(supplier, candidate.supplier):
            continue
        if issued_at and candidate.issued_at and (
            timezone.localdate(issued_at) != timezone.localdate(candidate.issued_at)
        ):
            continue
        return candidate
    return None
