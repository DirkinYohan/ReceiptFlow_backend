from __future__ import annotations

import re
import unicodedata
from datetime import date as _date
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from services.money import parse_decimal

EXPENSE_CATEGORIES = (
    "Food", "Transportation", "Technology", "Health", "Home",
    "Entertainment", "Education", "Shopping", "Other",
)
VALID_CURRENCIES = {"COP", "USD", "EUR", "MXN", "BRL", "ARS", "CLP", "PEN", "GBP"}

_EMPTY_STRINGS = {"", "n/a", "null", "none", "na", "nan"}

_SPANISH_MONTHS = {
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

_KIND_FROM_TYPE = {
    'commercial_invoice': 'commercial_invoice',
    'factura_comercial': 'commercial_invoice',
    'electronic_invoice': 'electronic_invoice',
    'factura_electronica': 'electronic_invoice',
    'public_service': 'public_service',
    'servicio_publico': 'public_service',
    'servicios_publicos': 'public_service',
    'utility': 'public_service',
    'pos': 'pos',
    'tirilla': 'pos',
}

_TYPE_FROM_KEY = {
    'invoice': 'invoice',
    'factura': 'invoice',
    'commercial_invoice': 'invoice',
    'factura_comercial': 'invoice',
    'electronic_invoice': 'invoice',
    'factura_electronica': 'invoice',
    'public_service': 'invoice',
    'servicio_publico': 'invoice',
    'servicios_publicos': 'invoice',
    'utility': 'invoice',
    'receipt': 'receipt',
    'recibo': 'receipt',
    'ticket': 'ticket',
    'pos': 'ticket',
    'tirilla': 'ticket',
    'other': 'other',
    'otro': 'other',
}

_KIND_NORMALIZE = {
    **_KIND_FROM_TYPE,
    'receipt': 'receipt',
    'recibo': 'receipt',
    'service_invoice': 'service_invoice',
    'factura_de_servicios': 'service_invoice',
    'other': 'other',
    'otro': 'other',
    'invoice': 'commercial_invoice',
}

_CLASS_FROM_KIND = {
    'commercial_invoice': 'SALES_INVOICE',
    'electronic_invoice': 'ELECTRONIC_INVOICE',
    'pos': 'POS_RECEIPT',
    'public_service': 'UTILITY_BILL',
    'receipt': 'RECEIPT',
    'service_invoice': 'SERVICE_INVOICE',
    'other': 'OTHER',
}

_CLASS_NORMALIZE = {
    'sales_invoice': 'SALES_INVOICE',
    'factura_comercial': 'SALES_INVOICE',
    'electronic_invoice': 'ELECTRONIC_INVOICE',
    'factura_electronica': 'ELECTRONIC_INVOICE',
    'pos_receipt': 'POS_RECEIPT',
    'pos': 'POS_RECEIPT',
    'utility_bill': 'UTILITY_BILL',
    'public_service': 'UTILITY_BILL',
    'servicio_publico': 'UTILITY_BILL',
    'energy_bill': 'ENERGY_BILL',
    'factura_de_energia': 'ENERGY_BILL',
    'water_bill': 'WATER_BILL',
    'factura_de_agua': 'WATER_BILL',
    'gas_bill': 'GAS_BILL',
    'telecom_bill': 'TELECOM_BILL',
    'factura_de_internet': 'TELECOM_BILL',
    'receipt': 'RECEIPT',
    'recibo': 'RECEIPT',
    'service_invoice': 'SERVICE_INVOICE',
    'other': 'OTHER',
    'otro': 'OTHER',
}


def _fold_key(value: str) -> str:
    folded = unicodedata.normalize('NFKD', value.strip().lower())
    folded = ''.join(c for c in folded if not unicodedata.combining(c))
    folded = folded.replace('-', '_').replace(' ', '_')
    return re.sub(r'_+', '_', folded)


def _to_decimal(value) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        return parse_decimal(value, default_currency='COP')
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _safe_iso(year: int, month: int, day: int) -> Optional[str]:
    try:
        return _date(year, month, day).isoformat()
    except ValueError:
        return None


def _try_iso_date(value: str) -> Optional[str]:
    text = value.strip()
    text = re.sub(r'[T\s]\d{1,2}:\d{2}(?::\d{2})?.*$', '', text).strip()
    match = re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', text)
    if match:
        return _safe_iso(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    match = re.fullmatch(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', text)
    if match:
        first, second, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if first > 12:
            day, month = first, second
        elif second > 12:
            month, day = first, second
        else:
            day, month = first, second
        return _safe_iso(year, month, day)
    match = re.fullmatch(
        r'(\d{1,2})\s+de\s+([a-zA-ZáéíóúñÁÉÍÓÚÑ]+)\s+de\s+(\d{4})',
        text,
        flags=re.IGNORECASE,
    )
    if match:
        month_name = _fold_key(match.group(2)).replace('_', '')
        month = _SPANISH_MONTHS.get(month_name)
        if month:
            return _safe_iso(int(match.group(3)), month, int(match.group(1)))
    match = re.fullmatch(
        r'(\d{1,2})[-\s/]([a-zA-ZáéíóúñÁÉÍÓÚÑ]{3,})[-\s/](\d{4})',
        text,
        flags=re.IGNORECASE,
    )
    if match:
        month_name = _fold_key(match.group(2)).replace('_', '')
        month = _SPANISH_MONTHS.get(month_name)
        if month:
            return _safe_iso(int(match.group(3)), month, int(match.group(1)))
    return None


def _blank_to_none(value):
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in _EMPTY_STRINGS:
            return None
        return stripped
    return value


def _normalize_date_value(value):
    value = _blank_to_none(value)
    if not isinstance(value, str):
        return value
    return _try_iso_date(value) or value


def _normalize_billing_period(value):
    value = _blank_to_none(value)
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r'(\d{4})[-/](\d{1,2})', value)
    if match and 1 <= int(match.group(2)) <= 12:
        return f"{int(match.group(1)):04d}-{int(match.group(2)):02d}"
    match = re.fullmatch(r'(\d{1,2})[-/](\d{4})', value)
    if match and 1 <= int(match.group(1)) <= 12:
        return f"{int(match.group(2)):04d}-{int(match.group(1)):02d}"
    return _try_iso_date(value) or value


class TaxItem(BaseModel):
    type: Optional[str] = Field(
        default=None,
        description='IVA, INC, RETEFUENTE, RETEICA, RETEIVA, EXENTO, EXCLUIDO or OTRO. null if it is not a printed tax.',
    )
    rate: Optional[Decimal] = Field(
        default=None,
        description='Printed rate (19, 5, 0). JSON number. null if missing. Do not assume 19.',
    )
    taxable_base: Optional[Decimal] = Field(
        default=None,
        description=(
            'Taxable base of this tax, if printed or tied to specific lines. Do not use the whole '
            'subtotal unless the tax applies to all of it. null if it cannot be determined.'
        ),
    )
    amount: Optional[Decimal] = Field(
        default=None,
        description='Printed tax amount, as a JSON number without thousands separators. null if missing.',
    )

    @field_validator("rate", "taxable_base", "amount", mode="before")
    @classmethod
    def _dec(cls, v):
        return _to_decimal(v)

    @field_validator("type")
    @classmethod
    def _empty_type(cls, v):
        return _blank_to_none(v)


_CELL_PLACEHOLDERS = {"-", "—", "–", "n/a", "na", "n.a.", "null", "none", "undefined"}


class SectionCell(BaseModel):
    column: Optional[str] = None
    value: Optional[str] = None

    @field_validator("column", mode="before")
    @classmethod
    def _column(cls, v):
        if v is None:
            return None
        text = str(v).strip()
        return text or None

    @field_validator("value", mode="before")
    @classmethod
    def _value(cls, v):
        if v is None:
            return None
        text = str(v).strip()
        if not text or text.lower() in _CELL_PLACEHOLDERS:
            return None
        return text


class SectionRow(BaseModel):
    cells: List[SectionCell] = Field(default_factory=list)


class DocumentSection(BaseModel):
    name: Optional[str] = None
    columns: List[str] = Field(default_factory=list)
    rows: List[SectionRow] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _name(cls, v):
        return _blank_to_none(v)

    @field_validator("columns", mode="before")
    @classmethod
    def _columns(cls, v):
        if not v:
            return []
        return [str(item).strip() for item in v if str(item).strip()]


class ServiceConcept(BaseModel):
    description: Optional[str] = None
    amount: Optional[Decimal] = None

    @field_validator("amount", mode="before")
    @classmethod
    def _dec(cls, v):
        return _to_decimal(v)

    @field_validator("description")
    @classmethod
    def _empty_text(cls, v):
        return _blank_to_none(v)


class LineItem(BaseModel):
    description: Optional[str] = Field(
        default=None,
        description=(
            'Product or service name printed in the detail table. null if the row is a date, meter '
            'reading, NIT, CUFE, reference, total or note.'
        ),
    )
    sku: Optional[str] = None
    quantity: Optional[Decimal] = Field(
        default=None,
        description='Value of the quantity column. null if not printed. Do not default to 1.',
    )
    unit_price: Optional[Decimal] = Field(
        default=None,
        description='Printed unit price. JSON number. null if the column does not exist or cannot be read.',
    )
    discount: Optional[Decimal] = Field(
        default=None,
        description='Discount of this line, if there is a column for it. null if missing.',
    )
    tax_rate: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    total: Optional[Decimal] = Field(
        default=None,
        description='Line total exactly as printed. Do not compute it. null if missing.',
    )

    @field_validator("quantity", mode="after")
    @classmethod
    def _qty_positive(cls, v):
        if v is not None and v <= 0:
            raise ValueError("quantity must be > 0")
        return v

    @field_validator("unit_price", "discount", "tax_amount", "total", mode="after")
    @classmethod
    def _non_negative(cls, v):
        if v is not None and v < 0:
            raise ValueError("monetary fields must be >= 0")
        return v

    @field_validator("quantity", "unit_price", "discount", "tax_rate", "tax_amount", "total", mode="before")
    @classmethod
    def _dec(cls, v):
        return _to_decimal(v)

    @field_validator("description", "sku")
    @classmethod
    def _empty_text(cls, v):
        return _blank_to_none(v)


class ExtractedDocument(BaseModel):
    document_type: Optional[str] = Field(
        default=None,
        description='invoice, receipt, ticket or other. Commercial or electronic invoice: invoice. POS slip: ticket. Receipt: receipt.',
    )
    document_kind: Optional[str] = Field(
        default=None,
        description=(
            'commercial_invoice, electronic_invoice, pos, public_service, service_invoice, receipt or'
            ' other. Water, power, gas, internet or phone: public_service. DIAN invoice with CUFE: '
            'electronic_invoice. POS slip: pos.'
        ),
    )
    document_class: Optional[str] = Field(
        default=None,
        description=(
            'SALES_INVOICE, ELECTRONIC_INVOICE, POS_RECEIPT, UTILITY_BILL, ENERGY_BILL, WATER_BILL, '
            'GAS_BILL, TELECOM_BILL, RECEIPT, SERVICE_INVOICE or OTHER. Classify before reading '
            'amounts.'
        ),
    )
    has_product_table: Optional[bool] = Field(
        default=None,
        description=(
            'true only if there is a clear table or list of products or services with description and'
            ' amount. false for utility bills, documents without detail, or when there are only '
            'readings, dates, NIT or totals.'
        ),
    )
    supplier_name: Optional[str] = Field(
        default=None,
        description="Issuer's legal name. Never the customer's, buyer's or subscriber's name.",
    )
    supplier_tax_id: Optional[str] = Field(
        default=None,
        description="Issuer's NIT, digits only, without the check digit. Never the invoice number.",
    )
    supplier_tax_id_verification_digit: Optional[str] = Field(
        default=None,
        description="Check digit of the issuer's NIT (a single digit). null if missing.",
    )
    phone_numbers: List[str] = Field(
        default_factory=list,
        description='Phone numbers printed on the document, so they are not mistaken for the NIT.',
    )
    invoice_number: Optional[str] = Field(
        default=None,
        description=(
            'Invoice number only when labelled Factura No, Factura N°, Número de factura, Factura de '
            'venta No, Nro Factura, Folio or prefix+sequence. Never a NIT, CUFE, CUDE, reference, '
            'order, contract, meter or date.'
        ),
    )
    issue_date: Optional[str] = Field(
        default=None,
        description='Issue date as YYYY-MM-DD. null if that label is missing. Do not copy another date here.',
    )
    issue_time: Optional[str] = Field(
        default=None,
        description='Issue time as HH:MM:SS. null if missing.',
    )
    generation_date: Optional[str] = Field(
        default=None,
        description='DIAN generation date as YYYY-MM-DD. null if there is no generation label.',
    )
    due_date: Optional[str] = Field(
        default=None,
        description='Due or payment date as YYYY-MM-DD. null if missing. Never use it as the issue date.',
    )
    billing_period: Optional[str] = Field(
        default=None,
        description='Billed period, e.g. 2024-08. null if missing. It is not a product.',
    )
    billing_period_start: Optional[str] = Field(
        default=None,
        description='Start of the billed period as YYYY-MM-DD. null if missing.',
    )
    billing_period_end: Optional[str] = Field(
        default=None,
        description='End of the billed period as YYYY-MM-DD. null if missing.',
    )
    suspension_date: Optional[str] = Field(
        default=None,
        description='Service suspension date as YYYY-MM-DD. null if missing.',
    )
    payment_date: Optional[str] = Field(
        default=None,
        description='Pay-by date or the date it was paid, YYYY-MM-DD. null if missing. Do not copy it into issue_date.',
    )
    currency: Optional[str] = Field(
        default=None,
        description='COP if the document is Colombian and shows no other currency. USD, EUR or another only if printed.',
    )
    subtotal: Optional[Decimal] = Field(
        default=None,
        description='Printed subtotal. JSON number without thousands separators. null if missing. Do not compute it.',
    )
    other_charges: Optional[Decimal] = Field(
        default=None,
        description='Other charges added to the total, if printed apart from the subtotal. null if none.',
    )
    withholdings_amount: Optional[Decimal] = Field(
        default=None,
        description='Withholdings subtracted from the total, if printed. null if none.',
    )
    taxes: List[TaxItem] = Field(
        default_factory=list,
        description='Printed taxes (IVA, INC, withholdings). Empty list if none. Do not invent rates.',
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        description='Printed amount due. JSON number. null if missing. Do not derive it from other figures.',
    )
    payment_method: Optional[str] = Field(
        default=None,
        description='cash, card, transfer, digital or other. null if not stated.',
    )
    cufe: Optional[str] = Field(
        default=None,
        description='Hexadecimal CUFE or CUDE, 64 to 96 characters. null if not an electronic invoice. Never use it as the invoice number.',
    )
    sections: List[DocumentSection] = Field(
        default_factory=list,
        description=(
            'Printed tables, with the columns the document really has. Do not invent quantity or unit'
            ' price columns.'
        ),
    )
    service_concepts: List[ServiceConcept] = Field(
        default_factory=list,
        description=(
            'Utility charges (water, power, gas). Empty for commercial invoices. No meter readings or'
            ' history here.'
        ),
    )
    line_items: List[LineItem] = Field(
        default_factory=list,
        description=(
            'Only rows of a product or service table. Empty list if has_product_table is false. Never'
            ' dates, readings, consumption, history, NIT, CUFE, references, addresses, phones, '
            'totals, taxes, notes or payment methods.'
        ),
    )
    requires_review: bool = False
    validation_level: Optional[str] = Field(
        default=None,
        description='confirmed, completed_with_warnings, review_required or failed. Set by the backend.',
    )
    supplier_confidence: Optional[Decimal] = None
    invoice_number_confidence: Optional[Decimal] = None
    date_confidence: Optional[Decimal] = None
    total_confidence: Optional[Decimal] = None
    line_items_confidence: Optional[Decimal] = None
    suggested_category: Optional[str] = Field(
        default=None,
        description='Expense category, one of EXPENSE_CATEGORIES. null if it cannot be inferred.',
    )
    warnings: List[str] = Field(default_factory=list)

    @model_validator(mode='before')
    @classmethod
    def _preprocess(cls, data: Any):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        raw_type = data.get('document_type')
        if isinstance(raw_type, str) and raw_type.strip():
            key = _fold_key(raw_type)
            if key in _KIND_FROM_TYPE and not data.get('document_kind'):
                data['document_kind'] = _KIND_FROM_TYPE[key]
            mapped = _TYPE_FROM_KEY.get(key)
            if mapped:
                data['document_type'] = mapped
        raw_kind = data.get('document_kind')
        if isinstance(raw_kind, str) and raw_kind.strip():
            data['document_kind'] = _KIND_NORMALIZE.get(_fold_key(raw_kind), raw_kind.strip())
        raw_class = data.get('document_class')
        if isinstance(raw_class, str) and raw_class.strip():
            data['document_class'] = _CLASS_NORMALIZE.get(_fold_key(raw_class), raw_class.strip().upper())
        elif data.get('document_kind') in _CLASS_FROM_KIND and not data.get('document_class'):
            data['document_class'] = _CLASS_FROM_KIND[data['document_kind']]
        return data

    @field_validator(
        "subtotal", "total_amount", "other_charges", "withholdings_amount",
        "supplier_confidence", "invoice_number_confidence", "date_confidence",
        "total_confidence", "line_items_confidence",
        mode="before",
    )
    @classmethod
    def _dec(cls, v):
        return _to_decimal(v)

    @field_validator(
        "invoice_number", "supplier_name", "supplier_tax_id",
        "supplier_tax_id_verification_digit", "issue_time", "currency",
        "payment_method", "cufe", "document_type", "document_kind", "document_class",
        "validation_level",
    )
    @classmethod
    def _empty_to_none(cls, v):
        return _blank_to_none(v)

    @field_validator(
        "issue_date", "generation_date", "due_date", "suspension_date",
        "billing_period_start", "billing_period_end", "payment_date",
        mode="before",
    )
    @classmethod
    def _dates(cls, v):
        return _normalize_date_value(v)

    @field_validator("billing_period", mode="before")
    @classmethod
    def _period(cls, v):
        return _normalize_billing_period(v)

    @field_validator("suggested_category", mode="before")
    @classmethod
    def _category(cls, v):
        if not isinstance(v, str):
            return None
        key = v.strip().lower()
        return next((name for name in EXPENSE_CATEGORIES if name.lower() == key), None)

    @field_validator("has_product_table", mode="before")
    @classmethod
    def _bool_flag(cls, v):
        if isinstance(v, str):
            low = v.strip().lower()
            if low in {"true", "si", "sí", "yes", "1"}:
                return True
            if low in {"false", "no", "0"}:
                return False
            if low in _EMPTY_STRINGS:
                return None
        return v

    @field_validator("supplier_tax_id_verification_digit")
    @classmethod
    def _dv_single_char(cls, v):
        if v is None:
            return v
        text = str(v).strip()
        if len(text) == 1 and text.isdigit():
            return text
        return None

    @field_validator("phone_numbers", mode="before")
    @classmethod
    def _phones(cls, v):
        if not isinstance(v, list):
            return []
        return [str(item) for item in v if item not in (None, '')]

    @model_validator(mode='after')
    def _drop_phone_as_tax_id(self):
        tax_digits = re.sub(r'\D', '', self.supplier_tax_id or '')
        if len(tax_digits) < 7:
            return self
        phones = [re.sub(r'\D', '', phone) for phone in self.phone_numbers]
        if any(len(phone) >= 7 and tax_digits in phone for phone in phones):
            self.add_warning('SUPPLIER_TAX_ID_IS_PHONE', f'tax_id={self.supplier_tax_id} matches a phone number')
            self.supplier_tax_id = None
            self.supplier_tax_id_verification_digit = None
        return self

    def add_warning(self, code: str, message: Optional[str] = None) -> None:
        text = code if message is None else f"{code}: {message}"
        if text not in self.warnings:
            self.warnings.append(text)
