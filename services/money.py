from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional


def parse_decimal(value: Optional[str], default_currency: str = 'COP') -> Optional[Decimal]:
    if value is None:
        return None
    raw = str(value).strip()
    cleaned = re.sub(r'[^0-9.,\-]', '', raw)
    if not cleaned:
        return None

    is_negative = cleaned.startswith('-')
    cleaned = cleaned.lstrip('-')
    if not cleaned:
        return None

    num_dots = cleaned.count('.')
    num_commas = cleaned.count(',')

    if num_dots > 0 and num_commas > 0:
        if cleaned.rfind(',') > cleaned.rfind('.'):
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
    elif num_dots > 1 and num_commas == 0:
        cleaned = cleaned.replace('.', '')
    elif num_commas > 1 and num_dots == 0:
        cleaned = cleaned.replace(',', '')
    elif num_dots == 1 and num_commas == 0:
        parts = cleaned.split('.')
        if len(parts[1]) == 3 and (default_currency == 'COP' or len(parts[0]) >= 1):
            cleaned = parts[0] + parts[1]
        elif len(parts[1]) in (1, 2):
            cleaned = parts[0] + '.' + parts[1]
        else:
            cleaned = parts[0] + parts[1]
    elif num_commas == 1 and num_dots == 0:
        parts = cleaned.split(',')
        if len(parts[1]) == 3 and default_currency == 'COP':
            cleaned = parts[0] + parts[1]
        elif len(parts[1]) in (1, 2):
            cleaned = parts[0] + '.' + parts[1]
        else:
            cleaned = parts[0] + parts[1]

    try:
        dec = Decimal(cleaned).quantize(Decimal('0.01'))
        return -dec if is_negative else dec
    except (InvalidOperation, ValueError):
        return None
