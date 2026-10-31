from __future__ import annotations

import re
import unicodedata
from datetime import date as _date
from decimal import Decimal
from typing import List, Optional

from services.extraction.business_rules import decide_validation
from services.extraction.financial_validator import (
    document_total_warnings,
    neutralize_implausible_line_totals,
    reconcile_taxes,
)
from services.extraction.schemas import (
    DocumentSection,
    ExtractedDocument,
    LineItem,
    SectionCell,
    SectionRow,
    ServiceConcept,
    VALID_CURRENCIES,
    _try_iso_date,
)

_UTILITY_CLASSES = {'UTILITY_BILL', 'ENERGY_BILL', 'WATER_BILL', 'GAS_BILL'}


def _is_date(iso: Optional[str]) -> bool:
    if not iso:
        return True
    try:
        _date.fromisoformat(iso)
        return True
    except Exception:
        return False


_DATE_RE = re.compile(r'^\d{2,4}[-/]\d{1,2}[-/]\d{1,4}$')

_MONTH_NAMES = (
    "enero", "ene", "febrero", "feb", "marzo", "mar", "abril", "abr",
    "mayo", "junio", "jun", "julio", "jul", "agosto", "ago",
    "septiembre", "sep", "octubre", "oct", "noviembre", "nov",
    "diciembre", "dic", "january", "jan", "february", "march",
    "april", "june", "july", "august", "september", "october",
    "november", "december", "dec",
)

# Descriptions that are document metadata, not products. Matched on accent-folded text.
_FAUX_PATTERNS = (
    re.compile(r'\blecturas?\s+(anterior|actual|inicial|final|medidor)\b'),
    re.compile(r'\bconsumo\s+(mes|del\s+mes|kwh|kw|m3|metros)\b'),
    re.compile(r'\bconsumo\b.{0,40}\b(m3|kwh|kw|metros)\b'),
    re.compile(r'\bperiodo\s+(facturad\w*|de\s+facturacion|de\s+consumo)\b'),
    re.compile(r'\bhistorico\b'),
    re.compile(r'\b(cufe|cude)\b'),
    re.compile(r'^\s*nit\b'),
    re.compile(r'\bnit\s+(proveedor|emisor|del)\b'),
    re.compile(r'\bfecha\s+de\s+(emision|generacion|vencimiento|suspension|expedicion|pago)\b'),
    re.compile(r'\btotal\s+a\s+pagar\b'),
    re.compile(r'^\s*subtotal\b'),
    re.compile(r'^\s*(iva|inc|retefuente|reteica|reteiva|impoconsumo)\b'),
    re.compile(r'^\s*descuentos?\b'),
    re.compile(r'^\s*observaciones\b'),
    re.compile(r'^\s*direccion\b'),
    re.compile(r'^\s*(telefono|celular|tel)\b'),
    re.compile(r'\b(codigo\s+de\s+barras)\b'),
    re.compile(r'^\s*qr\b'),
    re.compile(r'\b(resolucion|autorizacion)\s+(dian|de\s+facturacion)\b'),
    re.compile(r'\bfactura\s+(electronica|n[o°º]|de\s+venta)\b'),
    re.compile(r'\bnumero\s+de\s+factura\b'),
    re.compile(r'^\s*referencias?\b'),
    re.compile(r'\b(orden\s+de\s+(compra|pedido|servicio))\b'),
    re.compile(r'^\s*efectivo\b'),
    re.compile(r'\bpago\s+en\b'),
    re.compile(r'^\s*cambio\s*[:$]'),
)

_UTILITY_SUPPLIER_MARKERS = (
    'cedenar', 'empotam', 'acueducto', 'alcantarillado', 'e.s.p',
    'emcali', 'electricaribe', 'air-e',
)

_UTILITY_TEXT_MARKERS = (
    'lectura anterior', 'lectura actual', 'periodo facturado',
    'periodo de facturacion', 'consumo kwh', 'kwh', 'm3',
)

PRODUCT_TABLE_KEYWORDS = [
    "cantidad", "cant", "qty", "descripción", "descripcion", "producto",
    "concepto", "servicio", "valor unitario", "v. unit", "vlr unit",
    "precio", "unitario", "importe", "vr. unit", "vr unit",
]

_EXTRACTION_FAILURE_PREFIXES = (
    'STRUCTURED_OUTPUT_FAILED',
    'GEMINI_SCHEMA_VALIDATION_FAILED',
    'GEMINI_API_KEY_NOT_CONFIGURED',
    'GOOGLE_GENAI_NOT_INSTALLED',
)


def _fold(value: str) -> str:
    nfkd = unicodedata.normalize('NFKD', value)
    stripped = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.lower()


def _looks_like_date(value: str) -> bool:
    if not value:
        return False
    if _DATE_RE.match(value.strip()):
        return True
    if not any(ch.isdigit() for ch in value):
        return False
    low = _fold(value)
    return any(re.search(rf'\b{re.escape(month)}\b', low) for month in _MONTH_NAMES)


def _description_is_only_a_date(description: str) -> bool:
    if not _looks_like_date(description):
        return False
    folded = _fold(description)
    for month in _MONTH_NAMES:
        folded = re.sub(rf'\b{month}\b', ' ', folded)
    letters = re.sub(r'[^a-z]', '', folded)
    return len(letters) <= 4


def _is_faux_description(description: Optional[str]) -> bool:
    if not description or not description.strip():
        return True
    folded = _fold(description)
    if any(pattern.search(folded) for pattern in _FAUX_PATTERNS):
        return True
    if _description_is_only_a_date(description):
        return True
    digits = re.sub(r'\D', '', description)
    letters = re.sub(r'[^A-Za-zÁÉÍÓÚáéíóúñÑ]', '', description)
    if len(digits) >= 8 and len(letters) <= 3:
        return True
    return False


def _is_utility_supplier(name: Optional[str]) -> bool:
    folded = _fold(name or '')
    return any(marker in folded for marker in _UTILITY_SUPPLIER_MARKERS)


def _raw_looks_like_utility(raw_text: Optional[str]) -> bool:
    folded = _fold(raw_text or '')
    if not folded:
        return False
    hits = sum(1 for marker in _UTILITY_TEXT_MARKERS if marker in folded)
    return hits >= 2


def _refine_document_class(doc: ExtractedDocument, raw_text_hint: Optional[str]) -> Optional[str]:
    """Narrow a generic utility bill to energy, water or gas when the document says so."""
    current = (doc.document_class or '').upper()
    blob = _fold(f"{doc.supplier_name or ''} {doc.document_kind or ''} {raw_text_hint or ''}")
    if current in {'SALES_INVOICE', 'ELECTRONIC_INVOICE', 'POS_RECEIPT', 'TELECOM_BILL', 'RECEIPT'}:
        return doc.document_class
    specific = None
    if 'cedenar' in blob or ('energia activa' in blob) or ('kwh' in blob and 'alumbrado' in blob):
        specific = 'ENERGY_BILL'
    elif (
        'acueducto' in blob
        or 'alcantarillado' in blob
        or 'empotam' in blob
        or ('cojardin' in blob and 'e.s.p' in blob)
    ):
        specific = 'WATER_BILL'
    elif 'gas natural' in blob or 'gasodomest' in blob:
        specific = 'GAS_BILL'
    if specific and current in {'', 'UTILITY_BILL', 'OTHER', 'SERVICE_INVOICE'}:
        return specific
    return doc.document_class


def _split_side_by_side_sections(sections: List[DocumentSection]) -> List[DocumentSection]:
    """A water bill often prints two tables on one row. Keep them as two sections."""
    split: List[DocumentSection] = []
    for section in sections:
        folded = [_fold(column) for column in section.columns]
        cut = None
        for index, name in enumerate(folded):
            if index >= 3 and ('otros cobro' in name or name == 'saldo' or 'que adeuda' in name):
                cut = index
                break
        if cut is None:
            split.append(section)
            continue
        left_columns = section.columns[:cut]
        right_columns = section.columns[cut:]

        def _take(row: SectionRow, columns: List[str], start: int) -> SectionRow:
            by_name = {cell.column: cell.value for cell in row.cells if cell.column}
            if len(row.cells) == len(section.columns):
                chosen = row.cells[start:start + len(columns)]
                return SectionRow(cells=[
                    SectionCell(column=columns[i], value=chosen[i].value if i < len(chosen) else None)
                    for i in range(len(columns))
                ])
            return SectionRow(cells=[
                SectionCell(column=column, value=by_name.get(column))
                for column in columns
            ])

        left_rows = []
        right_rows = []
        for row in section.rows:
            left = _take(row, left_columns, 0)
            right = _take(row, right_columns, cut)
            if any(cell.value for cell in left.cells):
                left_rows.append(left)
            if any(cell.value for cell in right.cells):
                right_rows.append(right)
        if left_rows:
            split.append(DocumentSection(name=section.name, columns=left_columns, rows=left_rows))
        if right_rows:
            split.append(DocumentSection(name='Otros cobros', columns=right_columns, rows=right_rows))
    return split


_METER_HEADER = ('medidor', 'diametro', 'lectura', 'pulg', 'promedio', 'causal')
_CHARGE_HEADER = (
    'valor unit', 'valor total', 'interes', 'mora', 'subsidio', 'aporte',
    'valor a pagar', 'tarifa', 'vlr unit', 'vlr total',
)
_DATE_TOKEN = re.compile(
    r'\d{1,2}[-/](?:\d{1,2}|[A-Za-zÁÉÍÓÚáéíóúñ]{3,12})[-/]\d{4}'
    r'|\d{4}[-/]\d{1,2}[-/]\d{1,2}'
    r'|\d{1,2}\s+de\s+[A-Za-zÁÉÍÓÚáéíóúñ]+\s+de\s+\d{4}'
    r'|\d{1,2}\s+[A-Za-zÁÉÍÓÚáéíóúñ]{3,12}\s+\d{4}',
    re.IGNORECASE,
)
_INVOICE_LABEL = re.compile(
    r'factura(?:\s+electr[oó]nica)?(?:\s+de\s+(?:venta|servicios\s+p[uú]blicos))?'
    r'\s*(?:n[°ºo]|no|nro|n[uú]mero)?\.?\s*[:.]?\s*'
    r'([A-Za-z]{0,10}\d[\dA-Za-z\-]{1,24})',
    re.IGNORECASE,
)


def _header_role(column: str, has_meter: bool, has_charge: bool) -> Optional[str]:
    """Tell a measurement header from a charge header. Shared names follow their table."""
    name = _fold(column)
    if any(hint in name for hint in _METER_HEADER):
        return 'measurement'
    if any(hint in name for hint in _CHARGE_HEADER):
        return 'charge'
    compact = name.replace(' ', '')
    if compact in {'m3', 'm³'}:
        if has_charge:
            return 'charge'
        if has_meter:
            return 'measurement'
    if 'consumo' in name and has_meter and ('promedio' in name or 'm3' in compact or 'mes' in name):
        return 'measurement'
    return None


def _roles_for(columns: List[str]) -> List[Optional[str]]:
    folded = [_fold(column) for column in columns]
    has_meter = any(any(hint in name for hint in _METER_HEADER) for name in folded)
    has_charge = any(any(hint in name for hint in _CHARGE_HEADER) for name in folded)
    roles = [_header_role(column, has_meter, has_charge) for column in columns]
    known = [role for role in roles if role]
    if 'measurement' not in known or 'charge' not in known:
        return roles
    filled: List[Optional[str]] = list(roles)
    last = None
    for index, role in enumerate(filled):
        if role:
            last = role
        elif last:
            filled[index] = last
    next_known = None
    for index in range(len(filled) - 1, -1, -1):
        if roles[index]:
            next_known = roles[index]
        elif filled[index] is None and next_known:
            filled[index] = next_known
    return filled


def _rows_for(section: DocumentSection, columns: List[str], indexes: List[int]) -> List[SectionRow]:
    rows: List[SectionRow] = []
    for row in section.rows:
        if len(row.cells) == len(section.columns):
            chosen = [row.cells[index] for index in indexes if index < len(row.cells)]
            cells = [
                SectionCell(column=columns[pos], value=chosen[pos].value if pos < len(chosen) else None)
                for pos in range(len(columns))
            ]
        else:
            by_name = {cell.column: cell.value for cell in row.cells if cell.column}
            cells = [SectionCell(column=column, value=by_name.get(column)) for column in columns]
        rows.append(SectionRow(cells=cells))
    return rows


def _split_measurement_from_charges(sections: List[DocumentSection]) -> List[DocumentSection]:
    """Keep a meter reading table out of the charge table when both were returned as one."""
    split: List[DocumentSection] = []
    for section in sections:
        roles = _roles_for(section.columns)
        if 'measurement' not in roles or 'charge' not in roles:
            split.append(section)
            continue
        groups: List[tuple] = []
        start = 0
        for index in range(1, len(roles) + 1):
            if index == len(roles) or roles[index] != roles[start]:
                groups.append((roles[start], start, index))
                start = index
        original = _fold(section.name or '')
        for role, start, end in groups:
            columns = section.columns[start:end]
            if role == 'measurement' and not any(token in original for token in ('medic', 'lectura', 'medidor')):
                name = 'Información de medición'
            elif role == 'charge' and any(token in original for token in ('medic', 'lectura', 'medidor')):
                name = 'Cargos'
            else:
                name = section.name
            split.append(DocumentSection(
                name=name,
                columns=columns,
                rows=_rows_for(section, columns, list(range(start, end))),
            ))
    return split


def _date_after(text: str, label: str, window: int = 70) -> Optional[str]:
    for match in re.finditer(label, text, flags=re.IGNORECASE):
        snippet = text[match.end():match.end() + window]
        token = _DATE_TOKEN.search(snippet)
        if not token:
            continue
        parsed = _try_iso_date(token.group(0))
        if parsed:
            return parsed
    return None


def _header_issue_date(text: str, utility: bool) -> Optional[str]:
    """The invoice date is the bare Fecha label, not límite, suspensión or último pago."""
    bare = _date_after(text, r'(?<![\w])fecha\s*:')
    issued = _date_after(text, r'fecha\s+de\s+(?:emisi[oó]n|expedici[oó]n)')
    if utility and bare:
        return bare
    return issued or bare


def _period_bounds(text: str) -> tuple:
    match = re.search(r'periodo\s+facturado', text, flags=re.IGNORECASE)
    if not match:
        return None, None
    snippet = text[match.end():match.end() + 80]
    tokens = _DATE_TOKEN.findall(snippet)
    parsed = [item for item in (_try_iso_date(token) for token in tokens) if item]
    if len(parsed) >= 2:
        return parsed[0], parsed[1]
    return None, None


def _normalize_invoice_token(value: str) -> str:
    return re.sub(r'[^0-9A-Za-z]', '', value).upper()


def _labeled_invoice_numbers(text: str) -> List[str]:
    found: List[str] = []
    for match in _INVOICE_LABEL.finditer(text):
        token = match.group(1).strip(' .:-')
        if _looks_like_date(token):
            continue
        if token not in found:
            found.append(token)
    distinct = []
    for token in found:
        key = _normalize_invoice_token(token)
        if key and all(_normalize_invoice_token(item) != key for item in distinct):
            distinct.append(token)
    return distinct


def _apply_labeled_fields(doc: ExtractedDocument, raw_text_hint: Optional[str], warn) -> ExtractedDocument:
    """Use a printed label when it clearly names the invoice number or a date role."""
    if not raw_text_hint:
        return doc
    updates = {}
    labels = _labeled_invoice_numbers(raw_text_hint)
    if len(labels) == 1:
        labeled = labels[0]
        current = doc.invoice_number or ''
        tax_digits = re.sub(r'\D', '', doc.supplier_tax_id or '')
        labeled_digits = re.sub(r'\D', '', labeled)
        same = _normalize_invoice_token(labeled) == _normalize_invoice_token(current)
        is_nit = bool(tax_digits) and labeled_digits == tax_digits
        if current and not same and not is_nit:
            updates['invoice_number'] = labeled
            warn(
                'INVOICE_NUMBER_RELABELED',
                'invoice number was taken from the Factura label',
            )
    issue = _header_issue_date(raw_text_hint, _is_utility_document(doc, raw_text_hint))
    period_start, period_end = _period_bounds(raw_text_hint)
    due = _date_after(raw_text_hint, r'fecha\s+l[ií]mite(?:\s+de\s+pago)?')
    suspension = _date_after(raw_text_hint, r'fecha\s+de\s+suspensi[oó]n')
    last_payment = _date_after(raw_text_hint, r'[uú]ltimo\s+pago')
    utility = _is_utility_document(doc, raw_text_hint)
    wrong_role = doc.issue_date in {period_start, period_end, last_payment} or (
        due and doc.issue_date == due
    )
    if issue and doc.issue_date != issue and (utility or wrong_role):
        updates['issue_date'] = issue
    if period_start and doc.billing_period_start != period_start:
        updates['billing_period_start'] = period_start
    if period_end and doc.billing_period_end != period_end:
        updates['billing_period_end'] = period_end
    if due and doc.due_date != due:
        updates['due_date'] = due
    elif (
        period_end
        and doc.due_date == period_end
        and due is None
        and 'fecha limite' not in _fold(raw_text_hint)
        and 'fecha de vencimiento' not in _fold(raw_text_hint)
        and utility
    ):
        updates['due_date'] = None
    if suspension and doc.suspension_date != suspension:
        updates['suspension_date'] = suspension
    if last_payment and doc.payment_date != last_payment:
        updates['payment_date'] = last_payment
    if any(key != 'invoice_number' for key in updates):
        warn('DATE_ROLE_ADJUSTED', 'labeled dates were kept in their own fields')
    if not updates:
        return doc
    return doc.model_copy(update=updates)


def _month_token(value: Optional[str]) -> bool:
    token = _fold(value or '').strip(' .')
    return token in _MONTH_NAMES


def _relocate_month_used_as_stratum(sections: List[DocumentSection]) -> List[DocumentSection]:
    """A month name in the stratum column is the billed month, not the stratum number."""
    for section in sections:
        stratum_columns = [column for column in section.columns if 'estrato' in _fold(column)]
        month_columns = [column for column in section.columns if 'mes factur' in _fold(column)]
        if not stratum_columns:
            continue
        for row in section.rows:
            for cell in row.cells:
                if cell.column not in stratum_columns or not _month_token(cell.value):
                    continue
                for other in row.cells:
                    if other.column in month_columns and not other.value:
                        other.value = cell.value
                        break
                cell.value = None
    return sections


def _section_from_concepts(concepts: List[ServiceConcept]) -> Optional[DocumentSection]:
    rows: List[SectionRow] = []
    for concept in concepts:
        if not concept.description:
            continue
        rows.append(SectionRow(cells=[
            SectionCell(column='Concepto', value=concept.description),
            SectionCell(
                column='Valor',
                value=None if concept.amount is None else str(concept.amount),
            ),
        ]))
    if not rows:
        return None
    return DocumentSection(name='Cargos', columns=['Concepto', 'Valor'], rows=rows)


def _is_utility_document(doc: ExtractedDocument, raw_text_hint: Optional[str]) -> bool:
    if (doc.document_class or '').upper() in _UTILITY_CLASSES:
        return True
    kind = _fold(doc.document_kind or '').replace(' ', '_')
    if kind in {'public_service', 'servicio_publico', 'servicios_publicos', 'utility'}:
        return True
    if _is_utility_supplier(doc.supplier_name):
        return True
    return _raw_looks_like_utility(raw_text_hint)


def _relabel_utility_dates(doc: ExtractedDocument, raw_text_hint: Optional[str], warn) -> ExtractedDocument:
    """Keep generation and payment dates out of issue_date and due_date on utility bills."""
    if not raw_text_hint or not _is_utility_document(doc, raw_text_hint):
        return doc
    folded = _fold(raw_text_hint)
    updates = {}
    has_issue_label = any(label in folded for label in (
        'fecha de emision', 'fecha de expedicion', 'fecha factura', 'fecha de venta',
    ))
    has_generation_label = 'fecha de generacion' in folded or 'fecha generacion' in folded
    if doc.issue_date and not doc.generation_date and has_generation_label and not has_issue_label:
        updates['generation_date'] = doc.issue_date
        updates['issue_date'] = None
        warn(
            'DATE_ROLE_ADJUSTED',
            'utility bill date labeled as generation was removed from issue_date',
        )
    has_due_label = 'fecha de vencimiento' in folded or 'fecha limite' in folded
    has_payment_label = 'fecha de pago oportuno' in folded or 'pago oportuno' in folded
    if (doc.due_date and doc.payment_date and doc.due_date == doc.payment_date
            and has_payment_label and not has_due_label):
        updates['due_date'] = None
        warn(
            'DATE_ROLE_ADJUSTED',
            'utility bill payment date was removed from due_date',
        )
    if not updates:
        return doc
    return doc.model_copy(update=updates)


def structured_extraction_failed(doc: ExtractedDocument) -> bool:
    return any(warning.startswith(_EXTRACTION_FAILURE_PREFIXES) for warning in doc.warnings)


def _sum_items_totals(items: List[LineItem]) -> Decimal:
    total = Decimal('0')
    for item in items:
        if item.total is not None:
            total += item.total
    return total


def _sum_discounts(items: List[LineItem]) -> Decimal:
    total = Decimal('0')
    for item in items:
        if item.discount is not None:
            total += item.discount
    return total


def _sum_taxes(doc: ExtractedDocument) -> Decimal:
    total = Decimal('0')
    for tax in doc.taxes:
        if tax.amount is not None:
            total += tax.amount
    return total


def _parse_iso(value: Optional[str]) -> Optional[_date]:
    if not value or not _is_date(value):
        return None
    try:
        return _date.fromisoformat(value)
    except Exception:
        return None


def validate_extracted(doc: ExtractedDocument, raw_text_hint: Optional[str] = None) -> ExtractedDocument:
    """Validate Gemini output. Clears values that are clearly the wrong field.

    Monetary amounts printed on the document are kept. Line items that are
    dates, readings, tax ids or totals are removed. A suspicious invoice
    number that is actually the NIT, the CUFE or a date is set to null.
    """
    refined_class = _refine_document_class(doc, raw_text_hint)
    if refined_class != doc.document_class:
        doc = doc.model_copy(update={'document_class': refined_class})

    warnings: List[str] = list(doc.warnings)

    def _warn(code: str, msg: Optional[str] = None, *, critical: bool = False) -> None:
        del critical  # severity is decided from the code in business_rules
        text = code if msg is None else f"{code}: {msg}"
        if text not in warnings:
            warnings.append(text)

    if doc.supplier_tax_id is not None:
        digits = re.sub(r'\D', '', doc.supplier_tax_id)
        if not (8 <= len(digits) <= 12):
            _warn("NIT_INVALID_FORMAT",
                  f"supplier_tax_id={doc.supplier_tax_id!r} must be 8-12 digits")
    if doc.supplier_tax_id_verification_digit is not None:
        digit = doc.supplier_tax_id_verification_digit
        if not (len(digit) == 1 and digit.isdigit()):
            _warn("NIT_DV_INVALID", f"verification_digit={digit!r}")

    for fname in (
        "issue_date", "generation_date", "due_date", "billing_period",
        "billing_period_start", "billing_period_end", "suspension_date", "payment_date",
    ):
        value = getattr(doc, fname, None)
        if value is None or _is_date(value):
            continue
        if fname == "billing_period" and re.fullmatch(r'\d{4}-\d{2}', value or ''):
            continue
        if isinstance(value, str) and len(value) <= 40 and _looks_like_date(value):
            continue
        _warn("DATE_INVALID_FORMAT", f"{fname}={value!r}")

    issue = _parse_iso(doc.issue_date)
    due = _parse_iso(doc.due_date)
    if issue and due and due < issue:
        _warn("DATE_ORDER_INVALID",
              f"due_date={doc.due_date} is earlier than issue_date={doc.issue_date}")

    doc = _relabel_utility_dates(doc, raw_text_hint, _warn)
    doc = _apply_labeled_fields(doc, raw_text_hint, _warn)

    if doc.currency is not None and doc.currency not in VALID_CURRENCIES:
        _warn("CURRENCY_NOT_ALLOWED", f"currency={doc.currency!r}")

    kept: List[LineItem] = []
    removed = 0
    for idx, item in enumerate(doc.line_items, start=1):
        if item.quantity is not None and item.quantity <= 0:
            _warn("LINE_ITEM_COHERENCE", f"item #{idx} quantity <=0 ({item.quantity})")
        if item.unit_price is not None and item.unit_price < 0:
            _warn("LINE_ITEM_COHERENCE", f"item #{idx} unit_price <0 ({item.unit_price})")
        if item.total is not None and item.total < 0:
            _warn("LINE_ITEM_COHERENCE", f"item #{idx} total <0 ({item.total})")
        if _is_faux_description(item.description):
            removed += 1
            _warn("FAUX_LINE_ITEMS_DETECTED",
                  f"item #{idx} description {item.description!r} is not a product or service")
            continue
        kept.append(item)

    items = kept
    if removed and not items:
        _warn("FAUX_LINE_ITEMS_STRIPPED", "line_items were not real products; replaced with []")
    elif removed:
        _warn("FAUX_LINE_ITEMS_FILTERED",
              f"removed {removed} non-product line(s); kept {len(items)} product line(s)")

    service_concepts = list(doc.service_concepts)
    if items and _is_utility_document(doc, raw_text_hint):
        for item in items:
            service_concepts.append(ServiceConcept(
                description=item.description,
                amount=item.total if item.total is not None else item.unit_price,
            ))
        _warn(
            "PUBLIC_SERVICE_NO_LINE_ITEMS",
            "public-service document has no product table; charges moved to service_concepts",
        )
        items = []
    elif doc.has_product_table is False and items:
        _warn("NO_PRODUCT_TABLE", "has_product_table is false; line_items replaced with []")
        items = []

    sections = _split_side_by_side_sections(list(doc.sections))
    section_count = len(sections)
    sections = _split_measurement_from_charges(sections)
    sections = _relocate_month_used_as_stratum(sections)
    if len(sections) > section_count:
        _warn(
            'SECTION_TABLES_SPLIT',
            'measurement columns and charge columns were returned in one table',
        )
    if _is_utility_document(doc, raw_text_hint) and service_concepts and not sections:
        built = _section_from_concepts(service_concepts)
        if built is not None:
            sections.append(built)

    tolerance = Decimal('2.00') if (doc.currency or "COP") == "COP" else Decimal('0.50')
    items, ambiguous = neutralize_implausible_line_totals(items, doc.subtotal, tolerance)
    for code, message, _critical in ambiguous:
        _warn(code, message)

    for idx, item in enumerate(items, start=1):
        if item.quantity is None and item.unit_price is None and item.total is None:
            _warn("LINE_ITEM_INCOMPLETE", f"item #{idx} has no quantity, price or total")
        if item.quantity is None or item.unit_price is None or item.total is None:
            continue
        discount = item.discount or Decimal('0')
        base = (item.quantity * item.unit_price) - discount
        with_tax = base + (item.tax_amount or Decimal('0'))
        if abs(base - item.total) > tolerance and abs(with_tax - item.total) > tolerance:
            _warn("LINE_ITEM_MATH_MISMATCH",
                  f"item #{idx} qty*price-discount={base} vs total={item.total}")
            if sections:
                items[idx - 1] = item.model_copy(update={'quantity': None, 'unit_price': None})

    if items and doc.subtotal is not None and all(item.total is not None for item in items):
        items_sum = _sum_items_totals(items)
        if abs(items_sum - doc.subtotal) > tolerance:
            _warn("LINE_TOTALS_SUM_MISMATCH",
                  f"sum(line_items.total)={items_sum} vs subtotal={doc.subtotal}")

    discounts = _sum_discounts(items)
    taxes, tax_warnings = reconcile_taxes(
        doc.taxes, items, doc.subtotal, discounts, tolerance, doc.currency,
    )
    doc = doc.model_copy(update={'taxes': taxes})
    for code, message, _critical in tax_warnings:
        _warn(code, message)
    for code, message, _critical in document_total_warnings(doc, items, tolerance):
        _warn(code, message)

    invoice_number = doc.invoice_number
    if invoice_number is not None:
        invoice_text = str(invoice_number).strip()
        invoice_digits = re.sub(r'\D', '', invoice_text)
        tax_digits = re.sub(r'\D', '', doc.supplier_tax_id or '')
        verification = doc.supplier_tax_id_verification_digit or ''
        tax_variants = {tax_digits}
        if verification.isdigit():
            tax_variants.add(tax_digits + verification)
        suspicious = False
        reason = ""
        if tax_digits and invoice_digits in tax_variants:
            suspicious = True
            reason = "invoice_number matches supplier_tax_id or supplier_tax_id+DV"
        elif doc.cufe and invoice_text.lower() == doc.cufe.strip().lower():
            suspicious = True
            reason = "invoice_number matches CUFE"
        elif re.fullmatch(r'[0-9a-fA-F]{48,}', invoice_text):
            suspicious = True
            reason = "invoice_number looks like a CUFE/CUDE"
        elif _looks_like_date(invoice_text):
            suspicious = True
            reason = f"invoice_number={invoice_text!r} looks like a date"
        elif re.match(r'^\s*(nit|cufe|cude)\b', invoice_text, flags=re.IGNORECASE):
            suspicious = True
            reason = "invoice_number is labeled as NIT or CUFE"
        if suspicious:
            invoice_number = None
            _warn("INVOICE_NUMBER_SUSPICIOUS", f"{reason}; cleared")

    level, requires_review = decide_validation(warnings)
    return doc.model_copy(update={
        "line_items": items,
        "service_concepts": service_concepts,
        "sections": sections,
        "document_class": doc.document_class,
        "invoice_number": invoice_number,
        "taxes": doc.taxes,
        "warnings": warnings,
        "requires_review": requires_review,
        "validation_level": level,
    })
