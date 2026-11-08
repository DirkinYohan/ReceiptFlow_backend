from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

from django.conf import settings
from django.utils import timezone as dj_timezone

from apps.documents.models import Document
from services.money import parse_decimal
from services.money import parse_decimal as _parse_decimal

logger = logging.getLogger(__name__)


CURRENCY_SYMBOL_MAP = {
    'COP': 'COP',
    'COL$': 'COP',
    '$': 'COP',
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
    full_text = ' '.join(lines[:25] + [text])
    
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

    pattern_base = re.compile(
        r'\bN\.?I\.?T\.?(?:\s+(?:EMISOR|DEL\s+EMISOR|PROVEEDOR|DE\s+VENTA))?[:\s\.]*([0-9]{1,3}(?:\.[0-9]{3}){2,3}|[0-9]{8,10})\b',
        re.IGNORECASE,
    )
    m2 = pattern_base.search(full_text)
    if m2:
        raw_nit = re.sub(r'\D', '', m2.group(1))
        if 8 <= len(raw_nit) <= 10:
            return raw_nit, None, raw_nit

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
    full = ' '.join(lines + [text])
    
    match = re.search(r'\b(?:CUFE|CUDE)[\s:=#]*([0-9a-fA-F\s\n]{64,128})', full, re.IGNORECASE)
    if match:
        clean = re.sub(r'[^0-9a-fA-F]', '', match.group(1)).lower()
        if len(clean) >= 64:
            return clean[:96]

    match_hex = re.search(r'\b([0-9a-fA-F]{64,96})\b', full)
    if match_hex:
        return match_hex.group(1).lower()

    return None


def _find_document_number(text: str, lines: list) -> Optional[str]:
    search_lines = lines[:35]
    
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
    all_text = ' '.join(lines[:35])
    
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
        if 'USD' not in full and 'EUR' not in full:
            return 'COP'
    for sym, code in [('USD', 'USD'), ('US$', 'USD'), ('EUR', 'EUR'), ('€', 'EUR'), ('MXN', 'MXN'), ('PEN', 'PEN')]:
        if sym in full:
            return code
    return 'COP'


def _find_financial_breakdown(lines: list, currency: str = 'COP') -> dict:
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

        _is_subtotal_line = any(lbl in upper for lbl in subtotal_labels)

        if total is None and not _is_subtotal_line and any(lbl in upper for lbl in total_labels):
            val = _extract_number_from_line(line)
            if val and val > 0:
                total = val

        if subtotal is None and _is_subtotal_line:
            val = _extract_number_from_line(line)
            if val and val > 0:
                subtotal = val

        if iva_amount is None and any(lbl in upper for lbl in tax_labels):
            val = _extract_number_from_line(line)
            if val is not None:
                iva_amount = val
                tax_total = val

        if discount is None and any(lbl in upper for lbl in discount_labels):
            val = _extract_number_from_line(line)
            if val and val >= 0:
                discount = val

        if tip is None and any(lbl in upper for lbl in tip_labels):
            val = _extract_number_from_line(line)
            if val and val >= 0:
                tip = val

        for w_lbl in withholding_labels:
            if w_lbl in upper:
                val = _extract_number_from_line(line)
                if val and val > 0:
                    withholdings[w_lbl.lower()] = f'{val:.2f}'
                    withholdings_total += val

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

        amounts = re.findall(r'\$?\s?([0-9]{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|[0-9]+(?:[.,]\d{1,2})?)', stripped)
        dec_amounts = [_parse_decimal(a, default_currency=currency) for a in amounts]
        dec_amounts = [d for d in dec_amounts if d is not None and d > 0]

        if not dec_amounts:
            continue

        sku = ''
        sku_match = re.match(r'^([A-Za-z0-9\-]{3,15})\s+', stripped)
        if sku_match and not sku_match.group(1).isalpha():
            sku = sku_match.group(1)

        qty = Decimal('1')
        qty_match = re.match(r'^(?:[A-Za-z0-9\-]{3,15}\s+)?(\d+(?:[.,]\d+)?)\s+', stripped)
        if qty_match:
            try:
                parsed_qty = Decimal(qty_match.group(1).replace(',', '.'))
                if 0 < parsed_qty <= 1000:
                    qty = parsed_qty
            except (InvalidOperation, ValueError):
                logger.debug('Ignoring unreadable quantity %r', qty_match.group(1))

        item_total = dec_amounts[-1]
        unit_price = dec_amounts[-2] if len(dec_amounts) >= 2 else (item_total / qty if qty else item_total)

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
    charges = Decimal('0')
    payments = Decimal('0')
    for section in sections or []:
        for row in section.get('rows') or []:
            amounts = []
            for cell in (row.get('cells') or [])[1:]:
                try:
                    amount = parse_decimal(str(cell.get('value') or ''), default_currency='COP')
                except Exception:
                    logger.debug('Ignoring unreadable section amount %r', cell.get('value'))
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
