"""Parseo de montos compartido por OCR y extracción estructurada."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional


def parse_decimal(value: Optional[str], default_currency: str = 'COP') -> Optional[Decimal]:
    """
    Parses a string into a Decimal with support for Colombian & Latin formats:
    - Period as thousands separator: 125.000 -> 125000.00
    - Period thousands and comma decimal: 1.250.000,50 -> 1250000.50
    - Comma thousands and dot decimal: 1,250,000.50 -> 1250000.50
    """
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
            # European / Latin format: 1.250.000,50 -> 1250000.50
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            # US format: 1,250,000.50 -> 1250000.50
            cleaned = cleaned.replace(',', '')
    elif num_dots > 1 and num_commas == 0:
        # Multiple dots as thousands: 1.250.000 -> 1250000
        cleaned = cleaned.replace('.', '')
    elif num_commas > 1 and num_dots == 0:
        # Multiple commas as thousands: 1,250,000 -> 1250000
        cleaned = cleaned.replace(',', '')
    elif num_dots == 1 and num_commas == 0:
        parts = cleaned.split('.')
        # If exactly 3 digits after dot, e.g. 125.000 or 45.000:
        # In Colombia (COP) this is always a thousands separator
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
