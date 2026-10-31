"""Turn the extractor's technical warnings into reasons a person can act on."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

_NUMBER = r'(-?\d+(?:\.\d+)?)'
_TOTAL_MISMATCH = re.compile(rf'={_NUMBER}\s+vs\s+total_amount={_NUMBER}')
_SUBTOTAL_PARTS = re.compile(rf'subtotal\({_NUMBER}\)\+taxes\({_NUMBER}\)')

PRODUCTS_DONT_ADD_UP = (
    'Los valores de los productos no cuadran con el subtotal. '
    'Se dejaron tal como están impresos; revisa la tabla.'
)

# Codes that only describe how the document was read: nothing for the user to fix.
_SILENT = (
    'GEMINI_MODEL_USED', 'GEMINI_STRUCTURED_OUTPUT_ENABLED', 'DATE_ROLE_ADJUSTED',
    'TAXABLE_BASE_RECONCILED', 'SECTION_TABLES_SPLIT', 'INVOICE_NUMBER_RELABELED',
    'NO_PRODUCT_TABLE', 'FAUX_LINE_ITEMS', 'LINE_ITEM_DROPPED', 'LINE_ITEMS_DROPPED',
    'SETTLED_BY_PAYMENT',
)

_PRODUCT_CODES = (
    'LINE_PRICE_COLUMN_AMBIGUOUS', 'LINE_ITEM_MATH_MISMATCH', 'LINE_TOTALS_SUM_MISMATCH',
    'LINE_TOTAL_AMBIGUOUS', 'LINE_ITEM_COHERENCE', 'LINE_ITEM_INCOMPLETE',
    'LINE_ITEM_QUANTITY_CLEARED', 'LINE_ITEM_MONEY_CLEARED',
)

_FIXED = {
    'SUBTOTAL_EXCEEDS_TOTAL_WITHOUT_DISCOUNT': 'El subtotal es mayor que el total y no aparece ningún descuento.',
    'IVA_RATE_AMOUNT_MISMATCH': 'El IVA no corresponde a la tarifa impresa.',
    'FINANCIAL_CONTRADICTION': 'Los montos impresos se contradicen entre sí.',
    'AMBIGUOUS_INVOICE_NUMBER': 'No está claro cuál es el número de factura.',
    'INVOICE_NUMBER_SUSPICIOUS': 'Lo que parecía el número de factura era otro dato, así que se dejó vacío.',
    'NIT_INVALID_FORMAT': 'El NIT del proveedor no tiene un formato válido.',
    'NIT_DV_INVALID': 'El dígito de verificación del NIT no es válido.',
    'SUPPLIER_TAX_ID_IS_PHONE': 'El número que parecía el NIT era un teléfono, así que se descartó.',
    'DATE_INVALID_FORMAT': 'Alguna fecha no se pudo leer.',
    'DATE_ORDER_INVALID': 'Las fechas no son coherentes (por ejemplo, vence antes de emitirse).',
    'CURRENCY_NOT_ALLOWED': 'La moneda del documento no se reconoció.',
    'UTILITY_PARTIAL_TOTAL': 'El total parece ser solo una parte de la factura de servicios.',
    'STRUCTURED_OUTPUT_FAILED': 'La IA no pudo leer el documento. Prueba con una foto más nítida.',
    'STRUCTURED_OUTPUT_UNAVAILABLE': 'La IA no pudo leer el documento. Prueba con una foto más nítida.',
    'GEMINI_SCHEMA_VALIDATION_FAILED': 'La IA devolvió datos incompletos. Puedes leerlo de nuevo o corregirlo a mano.',
    'GEMINI_API_KEY_NOT_CONFIGURED': 'El servicio de lectura no está configurado.',
    'GOOGLE_GENAI_NOT_INSTALLED': 'El servicio de lectura no está configurado.',
}

GENERIC = 'Hay datos que la IA marcó como dudosos.'
QUOTA_EXHAUSTED = (
    'Se agotó el cupo diario de lecturas de la IA (plan gratuito de Gemini). '
    'Vuelve a intentarlo más tarde.'
)


def _money(value: str, currency: str) -> str:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return value
    text = f'{amount:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    if text.endswith(',00'):
        text = text[:-3]
    return f'{currency} {text}'.strip()


def _code(warning: str) -> Optional[str]:
    head = warning.split(':', 1)[0].strip()
    return head if re.fullmatch(r'[A-Z][A-Z0-9_]{3,}', head) else None


def humanize(warning: str, currency: str = '') -> Optional[str]:
    """One readable sentence, or None when the warning is not something the user needs to see."""
    text = (warning or '').strip()
    if not text:
        return None
    if 'RESOURCE_EXHAUSTED' in text or 'quota' in text.lower():
        return QUOTA_EXHAUSTED
    code = _code(text)
    if code is None:
        return text  # already written for people (e.g. "No se identificó el monto total.")
    if code.startswith(_SILENT):
        return None
    if code.startswith(_PRODUCT_CODES):
        return PRODUCTS_DONT_ADD_UP
    if code == 'TOTAL_MISMATCH':
        match = _TOTAL_MISMATCH.search(text)
        if not match:
            return 'La suma de subtotal e impuestos no coincide con el total impreso.'
        expected, printed = match.groups()
        parts = _SUBTOTAL_PARTS.search(text)
        detail = 'subtotal + impuestos − descuentos'
        if parts:
            detail = f'subtotal {_money(parts.group(1), currency)} + impuestos {_money(parts.group(2), currency)}'
        return (
            f'La suma no cuadra: {detail} da {_money(expected, currency)}, '
            f'pero el total impreso es {_money(printed, currency)}.'
        )
    for prefix, message in _FIXED.items():
        if code.startswith(prefix):
            return message
    return GENERIC


def humanize_all(warnings: Iterable[str], currency: str = '', limit: int = 6) -> list[str]:
    seen: list[str] = []
    for warning in warnings:
        message = humanize(str(warning), currency)
        if message and message not in seen:
            seen.append(message)
    return seen[:limit]
