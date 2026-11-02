from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

_NUMBER = r'(-?\d+(?:\.\d+)?)'
_TOTAL_MISMATCH = re.compile(rf'={_NUMBER}\s+vs\s+total_amount={_NUMBER}')
_SUBTOTAL_PARTS = re.compile(rf'subtotal\({_NUMBER}\)\+taxes\({_NUMBER}\)')

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

_TEXT = {
    'es': {
        'products': 'Los valores de los productos no cuadran con el subtotal. Se dejaron tal como están impresos; revisa la tabla.',
        'generic': 'Hay datos que la IA marcó como dudosos.',
        'quota': 'Se agotó el cupo diario de lecturas de la IA (plan gratuito de Gemini). Vuelve a intentarlo más tarde.',
        'total_simple': 'La suma de subtotal e impuestos no coincide con el total impreso.',
        'total_parts': 'subtotal {subtotal} + impuestos {taxes}',
        'total_detail': 'subtotal + impuestos − descuentos',
        'total': 'La suma no cuadra: {detail} da {expected}, pero el total impreso es {printed}.',
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
    },
    'en': {
        'products': "The product amounts don't add up to the subtotal. They were kept as printed; check the table.",
        'generic': 'The AI flagged some details as uncertain.',
        'quota': "The AI's daily reading quota ran out (Gemini free plan). Try again later.",
        'total_simple': "Subtotal plus taxes doesn't match the printed total.",
        'total_parts': 'subtotal {subtotal} + taxes {taxes}',
        'total_detail': 'subtotal + taxes − discounts',
        'total': "The math doesn't add up: {detail} is {expected}, but the printed total is {printed}.",
        'SUBTOTAL_EXCEEDS_TOTAL_WITHOUT_DISCOUNT': 'The subtotal is higher than the total and there is no discount.',
        'IVA_RATE_AMOUNT_MISMATCH': "The VAT doesn't match the printed rate.",
        'FINANCIAL_CONTRADICTION': 'The printed amounts contradict each other.',
        'AMBIGUOUS_INVOICE_NUMBER': "It's not clear which one is the invoice number.",
        'INVOICE_NUMBER_SUSPICIOUS': 'What looked like the invoice number was something else, so it was left empty.',
        'NIT_INVALID_FORMAT': "The supplier's tax ID (NIT) has an invalid format.",
        'NIT_DV_INVALID': "The tax ID's check digit is not valid.",
        'SUPPLIER_TAX_ID_IS_PHONE': 'What looked like the tax ID was a phone number, so it was discarded.',
        'DATE_INVALID_FORMAT': 'A date could not be read.',
        'DATE_ORDER_INVALID': "The dates don't make sense (e.g. it's due before it was issued).",
        'CURRENCY_NOT_ALLOWED': "The document's currency was not recognized.",
        'UTILITY_PARTIAL_TOTAL': 'The total seems to be only part of the utility bill.',
        'STRUCTURED_OUTPUT_FAILED': 'The AI could not read the document. Try a sharper photo.',
        'STRUCTURED_OUTPUT_UNAVAILABLE': 'The AI could not read the document. Try a sharper photo.',
        'GEMINI_SCHEMA_VALIDATION_FAILED': 'The AI returned incomplete data. Read it again or fix it by hand.',
        'GEMINI_API_KEY_NOT_CONFIGURED': 'The reading service is not configured.',
        'GOOGLE_GENAI_NOT_INSTALLED': 'The reading service is not configured.',
    },
}

_PLAIN_EN = [
    (re.compile(r'^No se identificó el monto total\.$'), 'The total amount was not found.'),
    (re.compile(r'^No se identificó el nombre del proveedor\.$'), "The supplier's name was not found."),
    (re.compile(r'^No se identificó la fecha de emisión\.$'), 'The issue date was not found.'),
    (re.compile(r'^No se pudo extraer texto legible del documento\.$'), 'No readable text could be extracted from the document.'),
    (re.compile(r'^La suma de subtotal \+ impuestos no coincide exactamente con el total\.$'),
     "Subtotal plus taxes doesn't exactly match the total."),
    (re.compile(r'^El total impreso es 0 y no se pudo saber cuánto se pagó\.$'),
     'The printed total is 0 and the amount paid could not be determined.'),
    (re.compile(r'^Este archivo ya lo habías subido \(documento #(\d+)\)\..*$'),
     'You already uploaded this file (document #{0}). Discard it, or approve it if it really is another expense.'),
    (re.compile(r'^Posible documento duplicado: ya existe el documento #(\d+) .*número \(([^)]*)\) y total \(([^)]*)\)\.$'),
     'Possible duplicate: document #{0} already has the same supplier, number ({1}) and total ({2}).'),
    (re.compile(r'^Fallo el procesamiento\.\s*(.*)$', re.S), 'Reading failed. {0}'),
    (re.compile(r'^Error transitorio\. Reintentando \((\d+)/(\d+)\)\.\.\.$'), 'Temporary error. Retrying ({0}/{1})...'),
    (re.compile(r'^Tiempo de procesamiento excedido\.\s*(.*)$', re.S), 'Reading took too long. {0}'),
    (re.compile(r'^Error en worker \((\w+)\)\.\s*(.*)$', re.S), 'Worker error ({0}). {1}'),
    (re.compile(r'^Procesamiento marcado como atascado \((\d+) min sin respuesta del worker\)\.$'),
     'Reading marked as stuck ({0} min without a response).'),
]

PRODUCTS_DONT_ADD_UP = _TEXT['es']['products']
GENERIC = _TEXT['es']['generic']
QUOTA_EXHAUSTED = _TEXT['es']['quota']


def language_from(request) -> str:
    header = (getattr(request, 'headers', {}) or {}).get('Accept-Language', '') if request else ''
    return 'en' if header.lower().startswith('en') else 'es'


def _money(value: str, currency: str, lang: str = 'es') -> str:
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return value
    text = f'{amount:,.2f}'
    if lang == 'es':
        text = text.replace(',', '_').replace('.', ',').replace('_', '.')
        if text.endswith(',00'):
            text = text[:-3]
    elif text.endswith('.00'):
        text = text[:-3]
    return f'{currency} {text}'.strip()


def _code(warning: str) -> Optional[str]:
    head = warning.split(':', 1)[0].strip()
    return head if re.fullmatch(r'[A-Z][A-Z0-9_]{3,}', head) else None


def _plain(text: str, lang: str) -> str:
    if lang == 'es':
        return text
    for pattern, english in _PLAIN_EN:
        match = pattern.match(text)
        if match:
            return english.format(*match.groups())
    return text


def humanize(warning: str, currency: str = '', lang: str = 'es') -> Optional[str]:
    words = _TEXT.get(lang, _TEXT['es'])
    text = (warning or '').strip()
    if not text:
        return None
    if 'RESOURCE_EXHAUSTED' in text or 'quota' in text.lower():
        return words['quota']
    code = _code(text)
    if code is None:
        return _plain(text, lang)
    if code.startswith(_SILENT):
        return None
    if code.startswith(_PRODUCT_CODES):
        return words['products']
    if code == 'TOTAL_MISMATCH':
        match = _TOTAL_MISMATCH.search(text)
        if not match:
            return words['total_simple']
        expected, printed = match.groups()
        parts = _SUBTOTAL_PARTS.search(text)
        detail = words['total_detail']
        if parts:
            detail = words['total_parts'].format(
                subtotal=_money(parts.group(1), currency, lang), taxes=_money(parts.group(2), currency, lang),
            )
        return words['total'].format(
            detail=detail, expected=_money(expected, currency, lang), printed=_money(printed, currency, lang),
        )
    for prefix, message in words.items():
        if prefix.isupper() and code.startswith(prefix):
            return message
    return words['generic']


def humanize_all(warnings: Iterable[str], currency: str = '', limit: int = 6, lang: str = 'es') -> list[str]:
    seen: list[str] = []
    for warning in warnings:
        message = humanize(str(warning), currency, lang)
        if message and message not in seen:
            seen.append(message)
    return seen[:limit]
