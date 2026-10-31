from __future__ import annotations

import logging
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, time
from decimal import Decimal
from typing import Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone as dj_timezone

from apps.categories.models import Category
from apps.suppliers.models import Supplier
from apps.documents.models import Document, ExpenseItem
from apps.documents.services import document_service

from services.money import parse_decimal as _parse_decimal
from services.ocr import extract_ocr, OCRResult

logger = logging.getLogger(__name__)


def _json_safe(value):
    """Values stored in JSONField must survive the stdlib JSON encoder."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


CURRENCY_SYMBOL_MAP = {
    'COP': 'COP',
    'COL$': 'COP',
    '$': 'COP',  # In Colombia default $ is COP
    'MXN': 'MXN',
    'MX$': 'MXN',
    'USD': 'USD',
    'US$': 'USD',
    'EUR': 'EUR',
    '€': 'EUR',
    'GBP': 'GBP',
    '£': 'GBP',
    'BRL': 'BRL',
    'R$': 'BRL',
    'ARS': 'ARS',
    '$AR': 'ARS',
    'CLP': 'CLP',
    'PEN': 'PEN',
    'S/': 'PEN',
}

PAYMENT_METHOD_KEYWORDS = {
    Document.PAYMENT_METHOD_CASH: [
        'efectivo', 'cash', 'contado', 'pagado en efectivo', 'caja',
    ],
    Document.PAYMENT_METHOD_CARD: [
        'tarjeta', 'card', 'crédito', 'credito', 'debito', 'débito',
        'visa', 'mastercard', 'amex', 'datafono', 'datáfono', 'maestro',
        'terminación', 'terminacion', '4 últimos', 'voucher',
    ],
    Document.PAYMENT_METHOD_TRANSFER: [
        'transferencia', 'transfer', 'transferido', 'depósito', 'deposito',
        'nequi', 'daviplata', 'pse', 'bancolombia', 'consignacion', 'consignación',
    ],
    Document.PAYMENT_METHOD_DIGITAL: [
        'paypal', 'paypal.me', 'mercado pago', 'mercadopago', 'stripe',
        'apple pay', 'google pay', 'wallet', 'bold', 'rappi pay', 'rappipay',
    ],
}

DOCUMENT_TYPE_KEYWORDS = {
    Document.DOCUMENT_TYPE_INVOICE: [
        'factura electrónica de venta', 'factura electronica de venta',
        'factura electrónica', 'factura electronica', 'factura de venta',
        'factura', 'invoice', 'cfdi', 'bill',
    ],
    Document.DOCUMENT_TYPE_RECEIPT: [
        'recibo de caja', 'recibo', 'comprobante de pago', 'comprobante',
        'receipt', 'nota de venta', 'nota de pago', 'tirilla',
    ],
    Document.DOCUMENT_TYPE_TICKET: [
        'ticket', 'sistema pos', 'pos', 'tirilla pos', 'boleta',
    ],
}

SPANISH_MONTHS = {
    'enero': 1, 'ene': 1, 'january': 1, 'jan': 1,
    'febrero': 2, 'feb': 2, 'february': 2,
    'marzo': 3, 'mar': 3, 'march': 3,
    'abril': 4, 'abr': 4, 'april': 4, 'apr': 4,
    'mayo': 5, 'may': 5,
    'junio': 6, 'jun': 6, 'june': 6,
    'julio': 7, 'jul': 7, 'july': 7,
    'agosto': 8, 'ago': 8, 'august': 8, 'aug': 8,
    'septiembre': 9, 'sep': 9, 'sept': 9, 'september': 9,
    'octubre': 10, 'oct': 10, 'october': 10,
    'noviembre': 11, 'nov': 11, 'november': 11,
    'diciembre': 12, 'dic': 12, 'december': 12, 'dec': 12,
}


def _normalize_text(text: str) -> str:
    if not text:
        return ''
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def _find_colombian_nit(text: str, lines: list) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extracts Colombian NIT and Verification Digit (DV).
    Returns (clean_nit_base, verification_digit, full_tax_id).
    Examples:
      NIT 900.123.456-7 -> ('900123456', '7', '900123456-7')
      NIT: 800197268-4  -> ('800197268', '4', '800197268-4')
      NIT 900123456 DV 1 -> ('900123456', '1', '900123456-1')
    """
    full_text = ' '.join(lines[:25] + [text])
    
    # Pattern 1: NIT with DV indicator or hyphen
    pattern_dv = re.compile(
        r'\bN\.?I\.?T\.?(?:\s+(?:EMISOR|DEL\s+EMISOR|PROVEEDOR|DE\s+VENTA))?[:\s\.]*([0-9]{1,3}(?:\.[0-9]{3}){2,3}|[0-9]{8,10})[\s\-–—]*(?:DV|D\.V\.?|VERIFICACION)?[\s\-–—:]*([0-9])\b',
        re.IGNORECASE,
    )
    m = pattern_dv.search(full_text)
    if m:
        raw_nit = re.sub(r'\D', '', m.group(1))
        dv = m.group(2)
        if 8 <= len(raw_nit) <= 10:
            return raw_nit, dv, f'{raw_nit}-{dv}'

    # Pattern 2: NIT without DV
    pattern_base = re.compile(
        r'\bN\.?I\.?T\.?(?:\s+(?:EMISOR|DEL\s+EMISOR|PROVEEDOR|DE\s+VENTA))?[:\s\.]*([0-9]{1,3}(?:\.[0-9]{3}){2,3}|[0-9]{8,10})\b',
        re.IGNORECASE,
    )
    m2 = pattern_base.search(full_text)
    if m2:
        raw_nit = re.sub(r'\D', '', m2.group(1))
        if 8 <= len(raw_nit) <= 10:
            return raw_nit, None, raw_nit

    # Generic foreign tax IDs (RFC, RUT, etc.)
    foreign_patterns = [
        (r'\bRFC[\s:=]*([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{2,3})\b', 'MX'),
        (r'\bRUT[\s:=]*([0-9.\-]{10,12})\b', 'CL'),
        (r'\bRUC[\s:=]*(\d{10,13})\b', 'PE'),
        (r'\bNIF[\s:=]*([A-Z0-9]{9})\b', 'ES'),
        (r'\bCIF[\s:=]*([A-Z]\d{8})\b', 'ES-CIF'),
    ]
    for pat, _ in foreign_patterns:
        fm = re.search(pat, full_text, re.IGNORECASE)
        if fm:
            tax_id = re.sub(r'[^A-Za-z0-9\-]', '', fm.group(1).upper())
            return tax_id, None, tax_id

    return None, None, None


def _find_cufe(text: str, lines: list) -> Optional[str]:
    """
    Extracts CUFE (Código Único de Factura Electrónica) or CUDE.
    CUFE is a SHA-384 hex string (typically 64-96 characters).
    """
    full = ' '.join(lines + [text])
    
    # Labelled CUFE
    match = re.search(r'\b(?:CUFE|CUDE)[\s:=#]*([0-9a-fA-F\s\n]{64,128})', full, re.IGNORECASE)
    if match:
        clean = re.sub(r'[^0-9a-fA-F]', '', match.group(1)).lower()
        if len(clean) >= 64:
            return clean[:96]

    # Continuous hex sequence
    match_hex = re.search(r'\b([0-9a-fA-F]{64,96})\b', full)
    if match_hex:
        return match_hex.group(1).lower()

    return None


def _find_document_number(text: str, lines: list) -> Optional[str]:
    """
    Extracts invoice / receipt number.
    Prioritizes Colombian invoice numbers (e.g. SETP990001234, FE-1234, FAC 456).
    """
    search_lines = lines[:35]
    
    # Colombian electronic invoice pattern
    colombian_invoice_patterns = [
        r'(?:FACTURA\s+ELECTR[OÓ]NICA(?:\s+DE\s+VENTA)?|FACTURA\s+DE\s+VENTA|FACTURA|FAC\.)[\s:]*([A-Za-z0-9\-]{2,20})',
        r'\b(?:PREFIJO|SERIE)[:\s]*([A-Za-z0-9]+)[\s\-]+(?:N[O°º]|CONSECUTIVO|NUMERO)[:\s]*([0-9]+)',
        r'(?:NO\.\s*FACTURA|FACTURA\s+N[O°º]?|FOLIO|TICKET|INVOICE|RECIBO\s+N[O°º]?)[:\s#]*([A-Za-z0-9\-/]+)',
        r'\b([A-Za-z]{2,5})[\-\s]?(\d{3,10})\b',
    ]

    for line in search_lines:
        for pat in colombian_invoice_patterns:
            m = re.search(pat, line, re.IGNORECASE)
            if m:
                groups = [g for g in m.groups() if g]
                if len(groups) == 2:
                    return f'{groups[0]}-{groups[1]}'
                if groups:
                    val = groups[0].strip()
                    if len(val) >= 2 and not any(kw in val.lower() for kw in ['fecha', 'hora', 'total', 'nit', 'iva']):
                        return val

    return None


def _find_spanish_datetime(text: str, lines: list) -> Tuple[Optional[datetime], Optional[str], Optional[str]]:
    """
    Parses date and time from text/lines, supporting Spanish textual formats:
    - 15 de agosto de 2024
    - 15/08/2024, 15-08-2024, 2024-08-15
    - Issue time HH:MM:SS or HH:MM AM/PM
    Returns (datetime_obj, date_str_iso, time_str_iso).
    """
    all_text = ' '.join(lines[:35])
    
    # 1. Spanish written date: DD de [Mes] de YYYY
    es_match = re.search(
        r'(\d{1,2})\s+de\s+([a-zA-Z]+)\s+de\s+(\d{4})',
        all_text,
        re.IGNORECASE,
    )
    found_date: Optional[date] = None
    if es_match:
        day = int(es_match.group(1))
        month_name = es_match.group(2).lower()
        year = int(es_match.group(3))
        month = SPANISH_MONTHS.get(month_name)
        if month and 1 <= day <= 31 and 2000 <= year <= 2099:
            try:
                found_date = date(year, month, day)
            except ValueError:
                pass

    # 2. Standard numeric date patterns
    if not found_date:
        numeric_patterns = [
            (r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', 'YMD'),
            (r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', 'DMY'),
            (r'(\d{1,2})[-/](\d{1,2})[-/](\d{2})', 'DMY2'),
        ]
        for line in lines[:30]:
            if any(kw in _normalize_text(line).upper() for kw in ['FECHA', 'DATE', 'EMISION', 'EMISIÓN', 'EXPEDICION', 'EXPEDICIÓN']):
                search_scope = line
            else:
                search_scope = line

            for pat, order in numeric_patterns:
                m = re.search(pat, search_scope)
                if m:
                    try:
                        if order == 'YMD':
                            y, m_val, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        elif order == 'DMY':
                            d, m_val, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                        else:
                            d, m_val, y = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
                        if 1 <= d <= 31 and 1 <= m_val <= 12 and 2000 <= y <= 2099:
                            found_date = date(y, m_val, d)
                            break
                    except ValueError:
                        continue
            if found_date:
                break

    # 3. Extract time: HH:MM:SS or HH:MM AM/PM
    found_time: Optional[time] = None
    time_match = re.search(
        r'(\d{1,2}):(\d{2})(?::(\d{2}))?\s*([AaPp][Mm])?',
        all_text,
    )
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        second = int(time_match.group(3) or 0)
        ampm = time_match.group(4)
        if ampm:
            ampm = ampm.upper()
            if ampm == 'PM' and hour < 12:
                hour += 12
            elif ampm == 'AM' and hour == 12:
                hour = 0
        if 0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59:
            found_time = time(hour, minute, second)

    if found_date:
        dt = datetime.combine(found_date, found_time or time(0, 0, 0))
        if settings.USE_TZ:
            dt = dj_timezone.make_aware(dt, dj_timezone.get_current_timezone())
        date_str = found_date.isoformat()
        time_str = found_time.isoformat() if found_time else None
        return dt, date_str, time_str

    return None, None, None


def _find_supplier_name(lines: list, text: str) -> Optional[str]:
    """Identifies supplier company name from top lines of document."""
    candidates = []
    skip_keywords = [
        'fecha', 'total', 'subtotal', 'iva', 'rfc', 'nit', 'cuit', 'rut',
        'pago', 'efectivo', 'tarjeta', 'folio', 'ticket', 'recibo',
        'factura', 'resolucion', 'resolución', 'dian', 'cufe', 'cude',
        'cajero', 'caja', 'hora', 'tel', 'telefono', 'teléfono', 'direccion',
        'dirección', 'cliente', 'adquiriente', 'regimen', 'régimen',
    ]
    for line in lines[:15]:
        stripped = line.strip()
        if len(stripped) < 3 or len(stripped) > 100:
            continue
        lower = _normalize_text(stripped).lower()
        if any(lower.startswith(kw) for kw in ['nit', 'rfc', 'rut', 'tel', 'dir', 'regimen']):
            continue
        if any(kw in lower for kw in skip_keywords):
            continue
        if re.search(r'\d{6,}', stripped):
            continue
        candidates.append(stripped)
        if len(candidates) >= 3:
            break

    if candidates:
        # Return first reasonable name
        return candidates[0].strip()
    return None


def _find_supplier_address(lines: list) -> Optional[str]:
    address_keywords = ['direccion', 'dirección', 'domicilio', 'calle', 'carrera', 'cra', 'cll', 'av', 'avenida', 'diagonal', 'transversal']
    for idx, line in enumerate(lines[:20]):
        lower = _normalize_text(line).lower()
        if any(kw in lower for kw in address_keywords):
            if ':' in line:
                addr = line.split(':', 1)[1].strip()
                if len(addr) >= 5:
                    return addr[:300]
            if len(line.strip()) >= 5:
                return line.strip()[:300]
    return None


def _find_supplier_phone(lines: list, text: str) -> Optional[str]:
    for line in lines[:20]:
        lower = line.lower()
        if any(kw in lower for kw in ['tel', 'telefono', 'teléfono', 'cel', 'celular', 'pbx']):
            match = re.search(r'(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{3,4}', line)
            if match:
                digits = re.sub(r'\D', '', match.group(0))
                if 7 <= len(digits) <= 15:
                    return match.group(0).strip()
    return None


def _find_currency(text: str, lines: list, has_nit: bool = False, has_cufe: bool = False) -> str:
    full = _normalize_text(' '.join(lines[:20] + [text])).upper()
    if 'COP' in full or 'COL$' in full or 'PESOS COLOMBIANOS' in full:
        return 'COP'
    if has_nit or has_cufe or 'DIAN' in full or 'CUFE' in full or 'FACTURA ELECTRONICA' in full:
        # Colombian invoice indicator present -> currency is COP unless explicitly stated otherwise
        if 'USD' not in full and 'EUR' not in full:
            return 'COP'
    for sym, code in [('USD', 'USD'), ('US$', 'USD'), ('EUR', 'EUR'), ('€', 'EUR'), ('MXN', 'MXN'), ('PEN', 'PEN')]:
        if sym in full:
            return code
    return 'COP'


def _find_financial_breakdown(lines: list, currency: str = 'COP') -> dict:
    """
    Extracts subtotal, taxes (IVA, INC), discounts, tip, withholdings, and total.
    """
    subtotal: Optional[Decimal] = None
    tax_total: Optional[Decimal] = None
    iva_amount: Optional[Decimal] = None
    discount: Optional[Decimal] = None
    tip: Optional[Decimal] = None
    withholdings: dict = {}
    withholdings_total: Decimal = Decimal('0')
    total: Optional[Decimal] = None

    subtotal_labels = ['SUBTOTAL', 'SUB TOTAL', 'VALOR BRUTO', 'BASE GRAVABLE', 'TOTAL ANTES DE IMPUESTOS']
    tax_labels = ['TOTAL IVA', 'IVA', 'IMPUESTO A LAS VENTAS', 'IMPUESTOS', 'INC', 'IMPOCONSUMO']
    discount_labels = ['DESCUENTO', 'DESCUENTOS', 'DESC.', 'DISCOUNT']
    tip_labels = ['PROPINA', 'PROPINA VOLUNTARIA', 'TIP', 'SERVICIO']
    withholding_labels = ['RETEFUENTE', 'RETEIVA', 'RETEICA', 'RETENCION', 'RETENCIONES', 'AUTORRETENCION']
    total_labels = ['TOTAL A PAGAR', 'VALOR TOTAL', 'TOTAL FACTURA', 'NETO A PAGAR', 'TOTAL COP', 'TOTAL']

    def _extract_number_from_line(line: str) -> Optional[Decimal]:
        parts = line.split(':')
        target = parts[-1] if len(parts) > 1 else line
        matches = re.findall(r'\$?\s?([0-9]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|[0-9]+(?:[.,]\d{1,2})?)', target)
        if matches:
            return _parse_decimal(matches[-1], default_currency=currency)
        return None

    for line in lines:
        upper = _normalize_text(line).upper()

        # Determine if this line is a subtotal line (to avoid capturing it as 'total')
        _is_subtotal_line = any(lbl in upper for lbl in subtotal_labels)

        # Total — only if not a subtotal line
        if total is None and not _is_subtotal_line and any(lbl in upper for lbl in total_labels):
            val = _extract_number_from_line(line)
            if val and val > 0:
                total = val

        # Subtotal
        if subtotal is None and _is_subtotal_line:
            val = _extract_number_from_line(line)
            if val and val > 0:
                subtotal = val

        # IVA / Tax
        if iva_amount is None and any(lbl in upper for lbl in tax_labels):
            val = _extract_number_from_line(line)
            if val is not None:
                iva_amount = val
                tax_total = val

        # Discount
        if discount is None and any(lbl in upper for lbl in discount_labels):
            val = _extract_number_from_line(line)
            if val and val >= 0:
                discount = val

        # Tip
        if tip is None and any(lbl in upper for lbl in tip_labels):
            val = _extract_number_from_line(line)
            if val and val >= 0:
                tip = val

        # Withholdings
        for w_lbl in withholding_labels:
            if w_lbl in upper:
                val = _extract_number_from_line(line)
                if val and val > 0:
                    withholdings[w_lbl.lower()] = f'{val:.2f}'
                    withholdings_total += val

    # Fallback for total: highest monetary amount in the last 20 lines
    if total is None:
        amounts = []
        for line in reversed(lines[-20:]):
            val = _extract_number_from_line(line)
            if val and val > 0:
                amounts.append(val)
        if amounts:
            total = max(amounts)

    return {
        'subtotal': subtotal,
        'tax_amount': tax_total,
        'iva_amount': iva_amount,
        'discount_amount': discount or Decimal('0'),
        'tip_amount': tip or Decimal('0'),
        'withholdings': withholdings,
        'withholdings_total': withholdings_total,
        'total': total,
    }


def _find_payment_method(text: str, lines: list) -> Optional[str]:
    full = _normalize_text(' '.join(lines + [text])).lower()
    for method, keywords in PAYMENT_METHOD_KEYWORDS.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', full):
                return method
    return None


def _find_document_type(text: str, lines: list, original_filename: str = '', has_cufe: bool = False) -> str:
    if has_cufe:
        return Document.DOCUMENT_TYPE_INVOICE
    full = _normalize_text(' '.join(lines[:10] + [text])).lower()
    fname = original_filename.lower()
    for doc_type, keywords in DOCUMENT_TYPE_KEYWORDS.items():
        if any(kw in full for kw in keywords) or any(kw in fname for kw in keywords):
            return doc_type
    return Document.DOCUMENT_TYPE_RECEIPT


def _parse_products_and_line_items(lines: list, text: str, currency: str = 'COP') -> list:
    """
    Parses product line items: code/sku, description, quantity, unit price, tax, discount, item total.
    """
    items = []
    table_started = False
    
    stop_labels = ['SUBTOTAL', 'IVA', 'TOTAL', 'NETO', 'DESCUENTO', 'PROPINA', 'RETENCION', 'SON:', 'FIRMA']
    header_keywords = ['CANT', 'CANTIDAD', 'QTY', 'DESCRIPCION', 'DESCRIPCIÓN', 'PRODUCTO', 'CONCEPTO', 'VALOR', 'PRECIO', 'UNITARIO', 'IMPORTE', 'TOTAL']

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        upper = _normalize_text(stripped).upper()

        if any(kw in upper for kw in header_keywords):
            table_started = True
            continue

        if table_started and any(lbl in upper for lbl in stop_labels) and len(items) >= 1:
            break

        if not table_started:
            continue

        # Try to parse line item: [SKU]? [Quantity]? [Description] [Unit Price]? [Total]
        amounts = re.findall(r'\$?\s?([0-9]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|[0-9]+(?:[.,]\d{1,2})?)', stripped)
        dec_amounts = [_parse_decimal(a, default_currency=currency) for a in amounts]
        dec_amounts = [d for d in dec_amounts if d is not None and d > 0]

        if not dec_amounts:
            continue

        # Extract SKU / Code if line starts with code (e.g. 770..., PLU, ABC12)
        sku = ''
        sku_match = re.match(r'^([A-Za-z0-9\-]{3,15})\s+', stripped)
        if sku_match and not sku_match.group(1).isalpha():
            sku = sku_match.group(1)

        # Extract Quantity if present at start
        qty = Decimal('1')
        qty_match = re.match(r'^(?:[A-Za-z0-9\-]{3,15}\s+)?(\d+(?:[.,]\d+)?)\s+', stripped)
        if qty_match:
            try:
                parsed_qty = Decimal(qty_match.group(1).replace(',', '.'))
                if 0 < parsed_qty <= 1000:
                    qty = parsed_qty
            except Exception:
                pass

        # Total amount and unit price
        item_total = dec_amounts[-1]
        unit_price = dec_amounts[-2] if len(dec_amounts) >= 2 else (item_total / qty if qty else item_total)

        # Clean description
        desc = re.sub(r'[\$0-9.,]{3,}', '', stripped).strip()
        desc = re.sub(r'\s+', ' ', desc)
        if len(desc) < 2:
            desc = f'Item {len(items) + 1}'

        items.append({
            'sequence': len(items) + 1,
            'sku': sku,
            'description': desc[:500],
            'quantity': qty,
            'unit_price': unit_price,
            'tax_rate': Decimal('19.00') if currency == 'COP' else None,
            'tax_amount': Decimal('0'),
            'discount_amount': Decimal('0'),
            'total_amount': item_total,
        })

        if len(items) >= 40:
            break

    return items


def _settled_charges(sections) -> Optional[Decimal]:
    """Charges that a printed payment cancels out exactly (e.g. Seminario 185,000 / Bancolombia -185,000)."""
    from services.money import parse_decimal

    charges = Decimal('0')
    payments = Decimal('0')
    for section in sections or []:
        for row in section.get('rows') or []:
            amounts = []
            for cell in (row.get('cells') or [])[1:]:
                try:
                    amount = parse_decimal(str(cell.get('value') or ''), default_currency='COP')
                except Exception:
                    amount = None
                if amount is not None:
                    amounts.append(amount)
            if not amounts:
                continue
            value = amounts[-1]
            if value > 0:
                charges += value
            elif value < 0:
                payments += -value
    if charges > 0 and charges == payments:
        return charges
    return None


def _guess_category_enhanced(supplier_name: Optional[str], lines: list, line_items: list) -> Optional[str]:
    parts = [supplier_name or ''] + lines + [item.get('description', '') for item in line_items]
    text = _normalize_text(' '.join(parts)).lower()

    def has_any(keywords) -> bool:
        # Whole words only: 'ara' must not match 'para', nor 'bar' match 'barrio'.
        return any(re.search(rf'(?<!\w){re.escape(k)}(?!\w)', text) for k in keywords)

    if has_any([
        'restaurante', 'cafe', 'café', 'comida', 'panaderia', 'panadería', 'pasteleria',
        'pizza', 'burger', 'hamburguesa', 'supermercado', 'exito', 'éxito', 'carulla',
        'jumbo', 'olimpica', 'olímpica', 'd1', 'ara', 'isimo', 'alimentos', 'mercado',
    ]):
        return 'Food'

    if has_any([
        'gasolina', 'estacion de servicio', 'terpel', 'primax', 'biomax', 'peaje',
        'taxi', 'uber', 'didi', 'cabify', 'avianca', 'latam', 'vuelo', 'aerolinea',
        'taller', 'repuestos', 'mantenimiento automotriz', 'parqueadero',
    ]):
        return 'Transportation'

    if has_any([
        'apple', 'google', 'microsoft', 'amazon', 'software', 'laptop', 'computador',
        'tecnologia', 'tecnología', 'internet', 'claro', 'tigo', 'movistar', 'hosting',
    ]):
        return 'Technology'

    if has_any([
        'drogueria', 'droguería', 'farmacia', 'cruz verde', 'farmatodo', 'medico',
        'médico', 'clinica', 'clínica', 'hospital', 'laboratorio', 'optica', 'óptica',
    ]):
        return 'Health'

    if has_any([
        'enel', 'codensa', 'epm', 'vanti', 'gas natural', 'acueducto', 'servicios publicos',
        'e.s.p', 'empresa de servicios', 'energia', 'electrica', 'cedenar', 'alcantarillado', 'aseo',
        'homecenter', 'constructor', 'ferreteria', 'ferretería', 'arriendo', 'alquiler',
    ]):
        return 'Home'

    if has_any([
        'cine', 'cine colombia', 'cinemark', 'procinal', 'concierto', 'teatro',
        'spotify', 'netflix', 'disney', 'bar', 'licor',
    ]):
        return 'Entertainment'

    if has_any([
        'universidad', 'colegio', 'cursos', 'libreria', 'librería', 'educacion',
        'educación', 'platzi', 'coursera', 'udemy',
    ]):
        return 'Education'

    if has_any([
        'falabella', 'zara', 'bershka', 'arturo calle', 'velez', 'vélez', 'ropa',
        'calzado', 'moda', 'tienda', 'mercado libre',
    ]):
        return 'Shopping'

    return None


@dataclass
class ProcessedDocument:
    document_type: Optional[str] = None
    document_number: Optional[str] = None
    supplier_name: Optional[str] = None
    supplier_tax_id: Optional[str] = None
    supplier_nit: Optional[str] = None
    supplier_verification_digit: Optional[str] = None
    cufe: Optional[str] = None
    supplier_email: Optional[str] = None
    supplier_phone: Optional[str] = None
    supplier_website: Optional[str] = None
    supplier_address: Optional[str] = None
    category: Optional[str] = None
    issued_at: Optional[datetime] = None
    issue_date: Optional[str] = None
    issue_time: Optional[str] = None
    due_date: Optional[datetime] = None
    subtotal_amount: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    iva_amount: Optional[Decimal] = None
    discount_amount: Optional[Decimal] = None
    tip_amount: Optional[Decimal] = None
    withholdings_amount: Optional[Decimal] = None
    withholdings: dict = field(default_factory=dict)
    total_amount: Optional[Decimal] = None
    currency: Optional[str] = None
    payment_method: Optional[str] = None
    line_items: list = field(default_factory=list)
    sections: list = field(default_factory=list)
    document_class: Optional[str] = None
    notes: Optional[str] = None
    requires_review: bool = False
    validation_level: Optional[str] = None
    review_reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    def to_model_fields(self) -> dict:
        fields = {}
        if self.document_type:
            fields['document_type'] = self.document_type
        if self.document_number:
            fields['document_number'] = self.document_number
        if self.issued_at is not None:
            fields['issued_at'] = self.issued_at
        if self.due_date is not None:
            fields['due_date'] = self.due_date
        if self.subtotal_amount is not None:
            fields['subtotal_amount'] = self.subtotal_amount
        if self.tax_amount is not None:
            fields['tax_amount'] = self.tax_amount
        if self.discount_amount is not None:
            fields['discount_amount'] = self.discount_amount
        if self.tip_amount is not None:
            fields['tip_amount'] = self.tip_amount
        if self.total_amount is not None:
            fields['total_amount'] = self.total_amount
        if self.currency:
            fields['currency'] = self.currency
        if self.payment_method:
            fields['payment_method'] = self.payment_method
        return fields

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ['issued_at', 'due_date']:
            val = getattr(self, key, None)
            if isinstance(val, datetime):
                data[key] = val.isoformat()
        def _convert(obj):
            if isinstance(obj, Decimal):
                return f'{obj:.2f}'
            if isinstance(obj, list):
                return [_convert(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            return obj
        converted = _convert(data)
        # Ensure warnings is always a list in JSON output
        if 'warnings' not in converted or not isinstance(converted['warnings'], list):
            converted['warnings'] = []
        return converted


def _aware_from_iso_date(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value)
    except Exception:
        return None
    if settings.USE_TZ and parsed.tzinfo is None:
        return dj_timezone.make_aware(parsed, dj_timezone.get_current_timezone())
    return parsed


class DocumentProcessor:
    def __init__(self, ocr_service_name: Optional[str] = None):
        self.ocr_service_name = ocr_service_name or getattr(settings, 'OCR_SERVICE', 'gemini')

    def _process_via_structured(self, file_path: str) -> Optional['ExtractedDocument']:
        try:
            from services.extraction import GeminiStructuredExtractor
        except Exception as exc:
            logger.info('Structured extraction imports unavailable: %s', exc)
            return None
        if not getattr(settings, 'GEMINI_STRUCTURED_OUTPUT_ENABLED', True):
            return None
        try:
            extractor = GeminiStructuredExtractor()
            if not extractor.is_configured:
                return None
            raw = extractor.extract_file(file_path)
            return raw
        except FileNotFoundError:
            raise
        except Exception as exc:
            logger.warning('Structured extraction failed for %s: %s', file_path, exc)
            return None

    def _apply_structured_to_processed(
        self,
        processed: 'ProcessedDocument',
        structured: 'ExtractedDocument',
    ) -> 'ProcessedDocument':
        """Overlay structured values onto an existing ProcessedDocument.

        Used when a caller already has parser output. process_file does not
        use this overlay: a successful Gemini extraction is the only source
        of the business fields.
        """
        # Direct fields mapping: if structured has non-None, use it
        if structured.document_type:
            processed.document_type = structured.document_type
        if structured.supplier_name:
            processed.supplier_name = structured.supplier_name
        if structured.invoice_number:
            processed.document_number = structured.invoice_number
        if structured.supplier_tax_id:
            processed.supplier_nit = structured.supplier_tax_id
        if structured.supplier_tax_id_verification_digit:
            processed.supplier_verification_digit = structured.supplier_tax_id_verification_digit
            if processed.supplier_nit and '-' not in processed.supplier_nit:
                processed.supplier_tax_id = (
                    f"{processed.supplier_nit}-{structured.supplier_tax_id_verification_digit}"
                )
        elif structured.supplier_tax_id:
            processed.supplier_tax_id = structured.supplier_tax_id
        if structured.cufe:
            processed.cufe = structured.cufe
        if structured.issue_date:
            processed.issue_date = structured.issue_date
            issued = _aware_from_iso_date(structured.issue_date)
            if issued is not None:
                processed.issued_at = issued
        if structured.due_date:
            due = _aware_from_iso_date(structured.due_date)
            if due is not None:
                processed.due_date = due
        if structured.issue_time:
            processed.issue_time = structured.issue_time
        if structured.currency:
            processed.currency = structured.currency
        if structured.payment_method:
            processed.payment_method = structured.payment_method
        if structured.subtotal is not None:
            processed.subtotal_amount = structured.subtotal
        if structured.total_amount is not None:
            processed.total_amount = structured.total_amount
        # Taxes breakdown
        if structured.taxes:
            from decimal import Decimal as _Dec
            tax_total = _Dec('0')
            iva_total = None
            for t in structured.taxes:
                if t.amount is not None:
                    tax_total += t.amount
                    ttype = (t.type or '').upper() if t.type else ''
                    if ttype in ('IVA', 'I.V.A.', 'IVA 19%', 'IVA 5%', 'IVA 0%'):
                        iva_total = (iva_total or _Dec('0')) + t.amount
            if tax_total > 0:
                processed.tax_amount = tax_total
            if iva_total is not None:
                processed.iva_amount = iva_total
        # line_items: a successful structured extraction replaces regex items,
        # including the empty list (no product table). A failed extraction
        # leaves the regex items in place.
        from services.extraction.post_validator import structured_extraction_failed
        if not structured_extraction_failed(structured):
            mapped = []
            from decimal import Decimal as _Dec2
            for idx, item in enumerate(structured.line_items, start=1):
                description = (item.description or '').strip()[:500]
                if not description:
                    continue
                mapped.append({
                    'sequence': idx,
                    'sku': item.sku or '',
                    'description': description,
                    'quantity': item.quantity if item.quantity is not None else _Dec2('1'),
                    'unit_price': item.unit_price if item.unit_price is not None else _Dec2('0'),
                    'tax_rate': item.tax_rate,
                    'tax_amount': item.tax_amount or _Dec2('0'),
                    'discount_amount': item.discount or _Dec2('0'),
                    'total_amount': item.total if item.total is not None else _Dec2('0'),
                    'extra': {'source': 'gemini_structured'},
                })
            processed.line_items = mapped
        # Warnings propagation
        if structured.warnings:
            for w in structured.warnings:
                if w not in processed.warnings:
                    processed.warnings.append(w)
        if structured.requires_review:
            processed.requires_review = True

        # Extra fields preserved in raw so consumers can inspect due_date/generation_date etc.
        extra_fields = {}
        for fname in ('generation_date', 'due_date', 'billing_period', 'suspension_date', 'document_kind'):
            v = getattr(structured, fname, None)
            if v is not None:
                extra_fields[fname] = v
        if structured.has_product_table is not None:
            extra_fields['has_product_table'] = structured.has_product_table
        if extra_fields:
            processed.raw.setdefault('extracted_extra_fields', {})
            processed.raw['extracted_extra_fields'].update(extra_fields)

        return processed

    def _processed_from_structured(
        self,
        structured: 'ExtractedDocument',
        ocr_result: Optional[OCRResult],
        document_type_hint: str = 'receipt',
    ) -> ProcessedDocument:
        """Map a validated ExtractedDocument onto ProcessedDocument.

        Null means the value was not on the document. This path does not
        fill those nulls with regex guesses.
        """
        from decimal import Decimal as _Dec

        text = ocr_result.raw_text if ocr_result else ''
        lines = list(ocr_result.lines or []) if ocr_result else []
        processed = ProcessedDocument(
            raw={
                'ocr_text': text or '',
                'ocr_lines': lines,
                'ocr_words_count': len(ocr_result.words) if ocr_result and ocr_result.words else 0,
                'ocr_blocks_count': len(ocr_result.blocks) if ocr_result and ocr_result.blocks else 0,
                'ocr_extra': (ocr_result.extra or {}) if ocr_result else {},
                'extraction_source': 'gemini_structured',
            }
        )

        allowed_types = {
            Document.DOCUMENT_TYPE_INVOICE,
            Document.DOCUMENT_TYPE_RECEIPT,
            Document.DOCUMENT_TYPE_TICKET,
            Document.DOCUMENT_TYPE_OTHER,
        }
        if structured.document_type in allowed_types:
            processed.document_type = structured.document_type
        else:
            processed.document_type = document_type_hint
        processed.document_number = structured.invoice_number
        processed.supplier_name = structured.supplier_name
        processed.supplier_nit = structured.supplier_tax_id
        processed.supplier_verification_digit = structured.supplier_tax_id_verification_digit
        if structured.supplier_tax_id and structured.supplier_tax_id_verification_digit:
            processed.supplier_tax_id = (
                f"{structured.supplier_tax_id}-{structured.supplier_tax_id_verification_digit}"
            )
        else:
            processed.supplier_tax_id = structured.supplier_tax_id
        processed.cufe = structured.cufe
        processed.issue_date = structured.issue_date
        processed.issue_time = structured.issue_time
        if structured.issue_date:
            processed.issued_at = _aware_from_iso_date(structured.issue_date)
        if structured.due_date:
            processed.due_date = _aware_from_iso_date(structured.due_date)
        if structured.currency in {'COP', 'USD', 'EUR', 'MXN', 'BRL', 'ARS', 'CLP', 'PEN', 'GBP'}:
            processed.currency = structured.currency
        allowed_payments = {choice[0] for choice in Document.PAYMENT_METHOD_CHOICES}
        if structured.payment_method in allowed_payments:
            processed.payment_method = structured.payment_method
        processed.subtotal_amount = structured.subtotal
        processed.total_amount = structured.total_amount

        if structured.taxes:
            tax_total = _Dec('0')
            iva_total = None
            for tax in structured.taxes:
                if tax.amount is None:
                    continue
                tax_total += tax.amount
                if (tax.type or '').upper() in ('IVA', 'I.V.A.', 'IVA 19%', 'IVA 5%', 'IVA 0%'):
                    iva_total = (iva_total or _Dec('0')) + tax.amount
            processed.tax_amount = tax_total
            processed.iva_amount = iva_total

        discount_total = _Dec('0')
        saw_discount = False
        mapped = []
        for idx, item in enumerate(structured.line_items, start=1):
            description = (item.description or '').strip()[:500]
            if not description:
                continue
            if item.discount is not None:
                saw_discount = True
                discount_total += item.discount
            extra = {'source': 'gemini_structured'}
            if item.quantity is None:
                extra['quantity_missing'] = True
            if item.unit_price is None:
                extra['unit_price_missing'] = True
            if item.total is None:
                extra['total_missing'] = True
            mapped.append({
                'sequence': idx,
                'sku': item.sku or '',
                'description': description,
                'quantity': item.quantity,
                'unit_price': item.unit_price,
                'tax_rate': item.tax_rate,
                'tax_amount': item.tax_amount,
                'discount_amount': item.discount,
                'total_amount': item.total,
                'extra': extra,
            })
        processed.line_items = mapped
        processed.category = structured.suggested_category or _guess_category_enhanced(
            supplier_name=structured.supplier_name,
            lines=lines,
            line_items=mapped,
        )
        processed.document_class = structured.document_class
        processed.sections = _json_safe([
            section.model_dump(mode='json') for section in structured.sections
        ])
        if saw_discount:
            processed.discount_amount = discount_total

        extra_fields = {}
        for fname in (
            'generation_date', 'due_date', 'billing_period', 'billing_period_start',
            'billing_period_end', 'suspension_date', 'payment_date', 'document_kind',
            'document_class', 'validation_level', 'other_charges', 'withholdings_amount',
        ):
            value = getattr(structured, fname, None)
            if value is not None:
                extra_fields[fname] = value
        if structured.has_product_table is not None:
            extra_fields['has_product_table'] = structured.has_product_table
        if structured.service_concepts:
            extra_fields['service_concepts'] = [
                {'description': concept.description, 'amount': concept.amount}
                for concept in structured.service_concepts
            ]
        if structured.taxes:
            extra_fields['taxes'] = [
                {
                    'type': tax.type,
                    'rate': tax.rate,
                    'taxable_base': tax.taxable_base,
                    'amount': tax.amount,
                }
                for tax in structured.taxes
            ]
        if extra_fields:
            processed.raw['extracted_extra_fields'] = _json_safe(extra_fields)

        from services.extraction.business_rules import is_critical

        processed.warnings = list(structured.warnings)
        processed.validation_level = structured.validation_level
        processed.requires_review = bool(structured.requires_review)
        if processed.total_amount == 0:
            settled = _settled_charges(processed.sections)
            if settled:
                # "Valor a pagar 0" because a payment was already applied: the expense is the charge.
                processed.total_amount = settled
                processed.raw['settled_by_payment'] = True
                processed.warnings.append(
                    f'SETTLED_BY_PAYMENT: printed balance 0, charges {settled} fully paid'
                )
        if not processed.total_amount:
            processed.requires_review = True
            processed.validation_level = 'review_required'
            processed.review_reasons.append(
                'El total impreso es 0 y no se pudo saber cuánto se pagó.'
                if processed.total_amount == 0 else 'No se identificó el monto total.'
            )
        if not processed.supplier_name:
            processed.requires_review = True
            processed.validation_level = 'review_required'
            processed.review_reasons.append('No se identificó el nombre del proveedor.')
        if not processed.issued_at:
            note = 'No se identificó la fecha de emisión.'
            if note not in processed.warnings:
                processed.warnings.append(note)
            if processed.validation_level in (None, 'confirmed'):
                processed.validation_level = 'completed_with_warnings'
        if processed.requires_review:
            for warning in structured.warnings:
                if is_critical(warning) and warning not in processed.review_reasons:
                    processed.review_reasons.append(warning)
        return processed

    def process_file(self, file_path: str, document_type: str = 'receipt',
                     original_filename: str = '') -> ProcessedDocument:
        """Extract business fields from Gemini structured output.

        The regex parser runs only when structured extraction fails. It does
        not fill nulls left by a successful Gemini response.
        """
        from services.extraction.post_validator import structured_extraction_failed

        structured_doc = self._process_via_structured(file_path)
        ocr_result = None
        ocr_error = None
        try:
            ocr_result = extract_ocr(file_path, service_name=self.ocr_service_name)
        except Exception as exc:
            ocr_error = exc

        structured_ok = structured_doc is not None and not structured_extraction_failed(structured_doc)
        if structured_ok:
            from services.extraction import validate_extracted
            hint = ocr_result.raw_text if ocr_result is not None else None
            structured_doc = validate_extracted(structured_doc, raw_text_hint=hint)
            return self._processed_from_structured(
                structured_doc,
                ocr_result,
                document_type_hint=document_type,
            )

        if ocr_result is None:
            if ocr_error is not None:
                raise ocr_error
            raise RuntimeError(f'No OCR result for {file_path}')

        processed = self.parse_ocr_text(
            ocr_result,
            document_type_hint=document_type,
            original_filename=original_filename,
            file_path=file_path,
        )
        processed.requires_review = True
        if structured_doc is not None:
            for warning in structured_doc.warnings:
                if warning not in processed.warnings:
                    processed.warnings.append(warning)
                if warning not in processed.review_reasons:
                    processed.review_reasons.append(warning)
        else:
            message = 'STRUCTURED_OUTPUT_UNAVAILABLE'
            if message not in processed.warnings:
                processed.warnings.append(message)
            if message not in processed.review_reasons:
                processed.review_reasons.append(message)
        return processed

    def parse_ocr_text(
        self,
        ocr_result: OCRResult,
        *,
        document_type_hint: str = 'receipt',
        original_filename: str = '',
        file_path: str = '',
    ) -> ProcessedDocument:
        text = ocr_result.raw_text or ''
        lines = list(ocr_result.lines or [])
        words = list(ocr_result.words or [])
        blocks = list(ocr_result.blocks or [])

        processed = ProcessedDocument(
            raw={
                'ocr_text': text,
                'ocr_lines': lines,
                'ocr_words_count': len(words),
                'ocr_blocks_count': len(blocks),
                'ocr_extra': ocr_result.extra or {},
            }
        )

        if not text or len(text.strip()) < 5:
            processed.requires_review = True
            processed.review_reasons.append('No se pudo extraer texto legible del documento.')
            return processed

        # 1. Colombian NIT & Verification Digit
        nit_base, dv, full_tax_id = _find_colombian_nit(text, lines)
        processed.supplier_tax_id = full_tax_id
        processed.supplier_nit = nit_base
        processed.supplier_verification_digit = dv

        # 2. CUFE
        cufe = _find_cufe(text, lines)
        processed.cufe = cufe

        # 3. Document Type & Invoice Number
        has_cufe = bool(cufe)
        processed.document_type = _find_document_type(text, lines, original_filename, has_cufe=has_cufe) or document_type_hint
        processed.document_number = _find_document_number(text, lines)

        # 4. Supplier Info
        processed.supplier_name = _find_supplier_name(lines, text)
        processed.supplier_address = _find_supplier_address(lines)
        processed.supplier_phone = _find_supplier_phone(lines, text)
        
        email_matches = re.findall(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b', text)
        if email_matches:
            processed.supplier_email = email_matches[0].lower()

        url_matches = re.findall(r'\b(?:https?://)?(?:www\.)?[A-Za-z0-9\-]+\.[A-Za-z]{2,}(?:/[^\s]*)?\b', text)
        if url_matches:
            processed.supplier_website = url_matches[0]

        # 5. Issue Date & Time
        issued_dt, issue_date_str, issue_time_str = _find_spanish_datetime(text, lines)
        processed.issued_at = issued_dt
        processed.issue_date = issue_date_str
        processed.issue_time = issue_time_str

        # 6. Currency
        currency = _find_currency(text, lines, has_nit=bool(nit_base), has_cufe=bool(cufe))
        processed.currency = currency

        # 7. Financial Breakdown (Subtotal, IVA, Discount, Tip, Withholdings, Total)
        fin = _find_financial_breakdown(lines, currency=currency)
        processed.subtotal_amount = fin['subtotal']
        processed.tax_amount = fin['tax_amount']
        processed.iva_amount = fin['iva_amount']
        processed.discount_amount = fin['discount_amount']
        processed.tip_amount = fin['tip_amount']
        processed.withholdings = fin['withholdings']
        processed.withholdings_amount = fin['withholdings_total']
        processed.total_amount = fin['total']

        # 8. Payment Method
        processed.payment_method = _find_payment_method(text, lines) or Document.PAYMENT_METHOD_OTHER

        # 9. Line Items / Products
        processed.line_items = _parse_products_and_line_items(lines, text, currency=currency)

        # 10. Category
        processed.category = _guess_category_enhanced(
            supplier_name=processed.supplier_name,
            lines=lines,
            line_items=processed.line_items,
        )

        # Enrich / Fallback with Gemini Structured Data if available
        gemini_data = ocr_result.extra.get('gemini_structured') if isinstance(ocr_result.extra, dict) else None
        if gemini_data and isinstance(gemini_data, dict):
            if not processed.supplier_name and gemini_data.get('supplier_name'):
                processed.supplier_name = str(gemini_data['supplier_name']).strip()
            
            if not processed.supplier_nit and gemini_data.get('supplier_nit'):
                nit_clean = re.sub(r'\D', '', str(gemini_data['supplier_nit']))
                if nit_clean:
                    processed.supplier_nit = nit_clean
                    dv = str(gemini_data.get('supplier_verification_digit') or '')
                    if dv and dv.isdigit():
                        processed.supplier_verification_digit = dv
                        processed.supplier_tax_id = f'{nit_clean}-{dv}'
                    else:
                        processed.supplier_tax_id = nit_clean
            
            if not processed.cufe and gemini_data.get('cufe'):
                processed.cufe = str(gemini_data['cufe']).strip()
                
            if not processed.document_number and gemini_data.get('document_number'):
                processed.document_number = str(gemini_data['document_number']).strip()
                
            if not processed.supplier_address and gemini_data.get('supplier_address'):
                processed.supplier_address = str(gemini_data['supplier_address']).strip()
                
            if not processed.supplier_phone and gemini_data.get('supplier_phone'):
                processed.supplier_phone = str(gemini_data['supplier_phone']).strip()
                
            if not processed.supplier_email and gemini_data.get('supplier_email'):
                processed.supplier_email = str(gemini_data['supplier_email']).strip().lower()
                
            if not processed.supplier_website and gemini_data.get('supplier_website'):
                processed.supplier_website = str(gemini_data['supplier_website']).strip()
                
            if not processed.issue_date and gemini_data.get('issue_date'):
                try:
                    parsed_dt = datetime.strptime(str(gemini_data['issue_date']).strip(), '%Y-%m-%d')
                    processed.issue_date = parsed_dt.strftime('%Y-%m-%d')
                    if not processed.issued_at:
                        processed.issued_at = dj_timezone.make_aware(parsed_dt)
                except Exception:
                    pass
                    
            if not processed.total_amount and gemini_data.get('total_amount') is not None:
                processed.total_amount = _parse_decimal(str(gemini_data['total_amount']), default_currency=currency)
                
            if not processed.subtotal_amount and gemini_data.get('subtotal_amount') is not None:
                processed.subtotal_amount = _parse_decimal(str(gemini_data['subtotal_amount']), default_currency=currency)
                
            if not processed.tax_amount and gemini_data.get('tax_amount') is not None:
                processed.tax_amount = _parse_decimal(str(gemini_data['tax_amount']), default_currency=currency)
                
            if not processed.iva_amount and gemini_data.get('iva_amount') is not None:
                processed.iva_amount = _parse_decimal(str(gemini_data['iva_amount']), default_currency=currency)
                
            if not processed.discount_amount and gemini_data.get('discount_amount') is not None:
                processed.discount_amount = _parse_decimal(str(gemini_data['discount_amount']), default_currency=currency)
                
            if not processed.tip_amount and gemini_data.get('tip_amount') is not None:
                processed.tip_amount = _parse_decimal(str(gemini_data['tip_amount']), default_currency=currency)
                
            if (not processed.payment_method or processed.payment_method == Document.PAYMENT_METHOD_OTHER) and gemini_data.get('payment_method'):
                pm = str(gemini_data['payment_method']).lower().strip()
                if pm in dict(Document.PAYMENT_METHOD_CHOICES):
                    processed.payment_method = pm
                    
            if not processed.line_items and gemini_data.get('line_items') and isinstance(gemini_data['line_items'], list):
                g_items = []
                for idx, item in enumerate(gemini_data['line_items'], start=1):
                    if isinstance(item, dict):
                        desc = str(item.get('description', '')).strip()
                        qty = _parse_decimal(str(item.get('quantity', 1))) or Decimal('1.00')
                        u_price = _parse_decimal(str(item.get('unit_price', 0))) or Decimal('0.00')
                        t_amt = _parse_decimal(str(item.get('total_amount', 0))) or (qty * u_price)
                        tax_rt = _parse_decimal(str(item.get('tax_rate', 0)))
                        g_items.append({
                            'sequence': idx,
                            'sku': '',
                            'description': desc,
                            'quantity': qty,
                            'unit_price': u_price,
                            'total_amount': t_amt,
                            'tax_rate': tax_rt,
                            'tax_amount': None,
                            'discount_amount': Decimal('0.00'),
                            'notes': '',
                            'extra': {'source': 'gemini'},
                        })
                if g_items:
                    processed.line_items = g_items
                    
            if not processed.category and gemini_data.get('suggested_category'):
                processed.category = str(gemini_data['suggested_category']).strip()


        # 11. Validation & Review Required evaluation
        if not processed.total_amount:
            processed.requires_review = True
            processed.review_reasons.append('No se identificó el monto total.')
        if not processed.issued_at:
            processed.requires_review = True
            processed.review_reasons.append('No se identificó la fecha de emisión.')
        if not processed.supplier_name:
            processed.requires_review = True
            processed.review_reasons.append('No se identificó el nombre del proveedor.')

        if processed.subtotal_amount and processed.tax_amount and processed.total_amount:
            expected = processed.subtotal_amount + processed.tax_amount - (processed.discount_amount or Decimal('0')) + (processed.tip_amount or Decimal('0'))
            diff = abs(expected - processed.total_amount)
            tolerance = Decimal('2.00') if currency == 'COP' else Decimal('0.50')
            if diff > tolerance:
                processed.requires_review = True
                processed.review_reasons.append('La suma de subtotal + impuestos no coincide exactamente con el total.')

        return processed


def process_document_pipeline(document: Document) -> dict:
    """
    Executes full pipeline for document with duplicate detection and storage abstraction.
    """
    if not document.file:
        raise ValueError('Document has no file attached.')

    user = document.user

    # 1. Exact Duplicate Checksum Reuse
    if document.checksum:
        existing_completed = Document.objects.filter(
            user=user,
            checksum=document.checksum,
            status=Document.STATUS_COMPLETED,
        ).exclude(pk=document.pk).first()

        if existing_completed and existing_completed.structured_data:
            logger.info(
                'Document %s matches checksum of completed document %s. Reusing extracted data.',
                document.pk, existing_completed.pk,
            )
            with transaction.atomic():
                document.raw_ocr_data = existing_completed.raw_ocr_data
                structured = dict(existing_completed.structured_data)
                structured['duplicate_of_id'] = existing_completed.pk
                structured['is_exact_file_duplicate'] = True
                document.structured_data = structured
                document.supplier = existing_completed.supplier
                document.category = existing_completed.category
                document.document_number = existing_completed.document_number
                document.document_type = existing_completed.document_type
                document.issued_at = existing_completed.issued_at
                document.total_amount = existing_completed.total_amount
                document.subtotal_amount = existing_completed.subtotal_amount
                document.tax_amount = existing_completed.tax_amount
                document.discount_amount = existing_completed.discount_amount
                document.tip_amount = existing_completed.tip_amount
                document.currency = existing_completed.currency
                document.payment_method = existing_completed.payment_method
                document.notes = (document.notes or '') + f' [Duplicado de archivo del documento #{existing_completed.pk}]'
                document.save()

                # Recreate line items
                items_data = []
                for item in existing_completed.line_items.all():
                    items_data.append({
                        'sequence': item.sequence,
                        'sku': item.sku,
                        'description': item.description,
                        'quantity': item.quantity,
                        'unit_price': item.unit_price,
                        'unit': item.unit,
                        'tax_rate': item.tax_rate,
                        'tax_amount': item.tax_amount,
                        'discount_amount': item.discount_amount,
                        'total_amount': item.total_amount,
                        'notes': item.notes,
                        'extra': item.extra,
                    })
                if items_data:
                    document_service.set_line_items(document, items_data)

            # Same file uploaded again: keep the data but ask the user instead of counting it twice.
            return {
                'requires_review': True,
                'review_reasons': [
                    f'Este archivo ya lo habías subido (documento #{existing_completed.pk}). '
                    'Descártalo o apruébalo si de verdad es otro gasto.'
                ],
                'reused_from_document_id': existing_completed.pk,
                'processed_fields': document.structured_data,
            }

    # 2. File Path Resolution with Storage Abstraction
    temp_file = None
    try:
        file_path = document.file.path
    except (NotImplementedError, AttributeError, ValueError):
        ext = document.file_extension or 'bin'
        temp_file = tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False)
        document.file.open('rb')
        try:
            for chunk in document.file.chunks():
                temp_file.write(chunk)
            temp_file.flush()
            file_path = temp_file.name
        finally:
            temp_file.close()
            document.file.close()

    try:
        processor = DocumentProcessor()
        processed = processor.process_file(
            file_path,
            document_type=document.document_type,
            original_filename=document.original_filename or document.file.name,
        )
    finally:
        if temp_file and os.path.exists(temp_file.name):
            try:
                os.unlink(temp_file.name)
            except Exception:
                pass

    # 3. Match / Create Supplier
    supplier = None
    if processed.supplier_name:
        try:
            supplier, _ = Supplier.objects.get_or_create_for_user(
                user=user,
                name=processed.supplier_name,
                tax_id=processed.supplier_tax_id or '',
                address=processed.supplier_address or '',
                phone=processed.supplier_phone or '',
                email=processed.supplier_email or '',
            )
        except Exception:
            supplier = Supplier.objects.find_match(
                user=user,
                name=processed.supplier_name,
                tax_id=processed.supplier_tax_id or '',
            )

    # 4. Category: learned from the supplier, else the AI suggestion, else "Other"
    from apps.expenses.automation import resolve_category
    category = resolve_category(user, supplier, processed.category)

    # 5. Logical Duplicate Detection (Supplier + Document Number + Total, date when both have one)
    from apps.documents.duplicates import find_logical_duplicate
    logical_dup = find_logical_duplicate(
        document, supplier, processed.document_number, processed.total_amount, processed.issued_at,
    )
    if logical_dup:
        processed.requires_review = True
        processed.review_reasons.append(
            f'Posible documento duplicado: ya existe el documento #{logical_dup.pk} '
            f'con el mismo proveedor, número ({processed.document_number}) '
            f'y total ({processed.total_amount}).'
        )
        processed.raw['logical_duplicate_of_id'] = logical_dup.pk

    # 6. Save Structured Data & Line Items
    model_fields = processed.to_model_fields()
    structured_source = processed.raw.get('extraction_source') == 'gemini_structured'
    if structured_source:
        # Persist nulls from Gemini. Omitting the key would keep a previous guess.
        model_fields['document_number'] = processed.document_number or ''
        model_fields['issued_at'] = processed.issued_at
        model_fields['due_date'] = processed.due_date
        model_fields['subtotal_amount'] = processed.subtotal_amount
        model_fields['tax_amount'] = processed.tax_amount
        model_fields['total_amount'] = processed.total_amount
        model_fields['payment_method'] = processed.payment_method or ''
        if processed.currency:
            model_fields['currency'] = processed.currency
    with transaction.atomic():
        for field_name, value in model_fields.items():
            setattr(document, field_name, value)
        document.raw_ocr_data = _json_safe(processed.raw)
        document.structured_data = processed.to_dict()
        if supplier is not None:
            document.supplier = supplier
        if category is not None:
            document.category = category
        if processed.notes:
            document.notes = processed.notes
        from apps.documents.payables import apply_payment_inference
        apply_payment_inference(
            document,
            getattr(processed, 'document_class', None),
            {
                **(processed.raw.get('extracted_extra_fields') or {}),
                'settled_by_payment': processed.raw.get('settled_by_payment', False),
            },
        )
        document.processing_error = '; '.join(processed.review_reasons)[:500]
        document.save()

        if structured_source or processed.line_items:
            document_service.set_line_items(document, processed.line_items)

    return {
        'requires_review': processed.requires_review,
        'review_reasons': processed.review_reasons,
        'processed_fields': model_fields,
        'supplier_id': supplier.pk if supplier else None,
        'category_id': category.pk if category else None,
        'cufe': processed.cufe,
        'nit': processed.supplier_nit,
        'verification_digit': processed.supplier_verification_digit,
    }


document_processor = DocumentProcessor()
