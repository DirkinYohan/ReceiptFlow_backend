from __future__ import annotations

import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Tuple

from django.conf import settings
from django.db import transaction
from django.utils import timezone as dj_timezone

from apps.documents.duplicates import find_logical_duplicate
from apps.documents.models import Document
from apps.documents.payables import apply_payment_inference
from apps.documents.services import document_service
from apps.expenses.document_values import resolve_category
from apps.suppliers.models import Supplier
from services.document_processor.text_parsing import (
    _find_colombian_nit,
    _find_cufe,
    _find_currency,
    _find_document_number,
    _find_document_type,
    _find_financial_breakdown,
    _find_payment_method,
    _find_spanish_datetime,
    _find_supplier_address,
    _find_supplier_name,
    _find_supplier_phone,
    _guess_category_enhanced,
    _parse_products_and_line_items,
    _settled_charges,
)
from services.extraction.business_rules import is_critical
from services.extraction.post_validator import structured_extraction_failed, validate_extracted
from services.money import parse_decimal as _parse_decimal
from services.ocr import OCRResult, extract_ocr

if TYPE_CHECKING:
    from services.extraction.schemas import ExtractedDocument

logger = logging.getLogger(__name__)


def _json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


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
        if 'warnings' not in converted or not isinstance(converted['warnings'], list):
            converted['warnings'] = []
        return converted


def _aware_from_iso_date(value: str) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if settings.USE_TZ and parsed.tzinfo is None:
        return dj_timezone.make_aware(parsed, dj_timezone.get_current_timezone())
    return parsed


def _sum_taxes(taxes) -> Tuple[Decimal, Optional[Decimal]]:
    tax_total = Decimal('0')
    iva_total = None
    for tax in taxes:
        if tax.amount is None:
            continue
        tax_total += tax.amount
        if (tax.type or '').upper() in ('IVA', 'I.V.A.', 'IVA 19%', 'IVA 5%', 'IVA 0%'):
            iva_total = (iva_total or Decimal('0')) + tax.amount
    return tax_total, iva_total


def _map_structured_line_items(line_items) -> Tuple[list, Decimal, bool]:
    discount_total = Decimal('0')
    saw_discount = False
    mapped = []
    for idx, item in enumerate(line_items, start=1):
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
    return mapped, discount_total, saw_discount


def _structured_extra_fields(structured: 'ExtractedDocument') -> dict:
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
    return extra_fields


def _apply_structured_review_rules(processed: ProcessedDocument, structured: 'ExtractedDocument') -> None:
    processed.warnings = list(structured.warnings)
    processed.validation_level = structured.validation_level
    processed.requires_review = bool(structured.requires_review)
    if processed.total_amount == 0:
        settled = _settled_charges(processed.sections)
        if settled:
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


_GEMINI_TEXT_FIELDS = {
    'supplier_name': 'supplier_name',
    'cufe': 'cufe',
    'document_number': 'document_number',
    'supplier_address': 'supplier_address',
    'supplier_phone': 'supplier_phone',
    'supplier_website': 'supplier_website',
}

_GEMINI_AMOUNT_FIELDS = (
    'total_amount', 'subtotal_amount', 'tax_amount', 'iva_amount', 'discount_amount', 'tip_amount',
)


def _gemini_line_items(raw_items: list) -> list:
    g_items = []
    for idx, item in enumerate(raw_items, start=1):
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
    return g_items


def _merge_gemini_fallback(processed: ProcessedDocument, gemini_data, currency: str) -> None:
    for attr, key in _GEMINI_TEXT_FIELDS.items():
        if not getattr(processed, attr) and gemini_data.get(key):
            setattr(processed, attr, str(gemini_data[key]).strip())

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

    if not processed.supplier_email and gemini_data.get('supplier_email'):
        processed.supplier_email = str(gemini_data['supplier_email']).strip().lower()

    if not processed.issue_date and gemini_data.get('issue_date'):
        try:
            parsed_dt = datetime.strptime(str(gemini_data['issue_date']).strip(), '%Y-%m-%d')
            processed.issue_date = parsed_dt.strftime('%Y-%m-%d')
            if not processed.issued_at:
                processed.issued_at = dj_timezone.make_aware(parsed_dt)
        except ValueError:
            logger.debug('Ignoring unreadable issue date %r', gemini_data.get('issue_date'))

    for attr in _GEMINI_AMOUNT_FIELDS:
        if not getattr(processed, attr) and gemini_data.get(attr) is not None:
            setattr(processed, attr, _parse_decimal(str(gemini_data[attr]), default_currency=currency))

    if (not processed.payment_method or processed.payment_method == Document.PAYMENT_METHOD_OTHER) and gemini_data.get('payment_method'):
        pm = str(gemini_data['payment_method']).lower().strip()
        if pm in dict(Document.PAYMENT_METHOD_CHOICES):
            processed.payment_method = pm

    if not processed.line_items and gemini_data.get('line_items') and isinstance(gemini_data['line_items'], list):
        g_items = _gemini_line_items(gemini_data['line_items'])
        if g_items:
            processed.line_items = g_items

    if not processed.category and gemini_data.get('suggested_category'):
        processed.category = str(gemini_data['suggested_category']).strip()


def _line_items_with_defaults(line_items) -> list:
    mapped = []
    for idx, item in enumerate(line_items, start=1):
        description = (item.description or '').strip()[:500]
        if not description:
            continue
        mapped.append({
            'sequence': idx,
            'sku': item.sku or '',
            'description': description,
            'quantity': item.quantity if item.quantity is not None else Decimal('1'),
            'unit_price': item.unit_price if item.unit_price is not None else Decimal('0'),
            'tax_rate': item.tax_rate,
            'tax_amount': item.tax_amount or Decimal('0'),
            'discount_amount': item.discount or Decimal('0'),
            'total_amount': item.total if item.total is not None else Decimal('0'),
            'extra': {'source': 'gemini_structured'},
        })
    return mapped


def _overlay_structured_identity(processed: ProcessedDocument, structured: 'ExtractedDocument') -> None:
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


class DocumentProcessor:
    def __init__(self, ocr_service_name: Optional[str] = None):
        self.ocr_service_name = ocr_service_name or getattr(settings, 'OCR_SERVICE', 'gemini')

    def _process_via_structured(self, file_path: str) -> Optional['ExtractedDocument']:
        try:
            from services.extraction import GeminiStructuredExtractor
        except ImportError as exc:
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
        _overlay_structured_identity(processed, structured)
        if structured.currency:
            processed.currency = structured.currency
        if structured.payment_method:
            processed.payment_method = structured.payment_method
        if structured.subtotal is not None:
            processed.subtotal_amount = structured.subtotal
        if structured.total_amount is not None:
            processed.total_amount = structured.total_amount
        if structured.taxes:
            tax_total, iva_total = _sum_taxes(structured.taxes)
            if tax_total > 0:
                processed.tax_amount = tax_total
            if iva_total is not None:
                processed.iva_amount = iva_total
        if not structured_extraction_failed(structured):
            processed.line_items = _line_items_with_defaults(structured.line_items)
        if structured.warnings:
            for w in structured.warnings:
                if w not in processed.warnings:
                    processed.warnings.append(w)
        if structured.requires_review:
            processed.requires_review = True

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
            processed.tax_amount, processed.iva_amount = _sum_taxes(structured.taxes)

        mapped, discount_total, saw_discount = _map_structured_line_items(structured.line_items)
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

        extra_fields = _structured_extra_fields(structured)
        if extra_fields:
            processed.raw['extracted_extra_fields'] = _json_safe(extra_fields)

        _apply_structured_review_rules(processed, structured)
        return processed

    def process_file(self, file_path: str, document_type: str = 'receipt',
                     original_filename: str = '') -> ProcessedDocument:

        with ThreadPoolExecutor(max_workers=2) as pool:
            structured_future = pool.submit(self._process_via_structured, file_path)
            ocr_future = pool.submit(extract_ocr, file_path, service_name=self.ocr_service_name)
            ocr_result = None
            ocr_error = None
            try:
                ocr_result = ocr_future.result()
            except Exception as exc:
                logger.warning('OCR failed for %s: %s', file_path, exc)
                ocr_error = exc
            structured_doc = structured_future.result()

        structured_ok = structured_doc is not None and not structured_extraction_failed(structured_doc)
        if structured_ok:
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

        nit_base, dv, full_tax_id = _find_colombian_nit(text, lines)
        processed.supplier_tax_id = full_tax_id
        processed.supplier_nit = nit_base
        processed.supplier_verification_digit = dv

        cufe = _find_cufe(text, lines)
        processed.cufe = cufe

        has_cufe = bool(cufe)
        processed.document_type = _find_document_type(text, lines, original_filename, has_cufe=has_cufe) or document_type_hint
        processed.document_number = _find_document_number(text, lines)

        processed.supplier_name = _find_supplier_name(lines, text)
        processed.supplier_address = _find_supplier_address(lines)
        processed.supplier_phone = _find_supplier_phone(lines, text)

        email_matches = re.findall(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b', text)
        if email_matches:
            processed.supplier_email = email_matches[0].lower()

        url_matches = re.findall(r'\b(?:https?://)?(?:www\.)?[A-Za-z0-9\-]+\.[A-Za-z]{2,}(?:/[^\s]*)?\b', text)
        if url_matches:
            processed.supplier_website = url_matches[0]

        issued_dt, issue_date_str, issue_time_str = _find_spanish_datetime(text, lines)
        processed.issued_at = issued_dt
        processed.issue_date = issue_date_str
        processed.issue_time = issue_time_str

        currency = _find_currency(text, lines, has_nit=bool(nit_base), has_cufe=bool(cufe))
        processed.currency = currency

        fin = _find_financial_breakdown(lines, currency=currency)
        processed.subtotal_amount = fin['subtotal']
        processed.tax_amount = fin['tax_amount']
        processed.iva_amount = fin['iva_amount']
        processed.discount_amount = fin['discount_amount']
        processed.tip_amount = fin['tip_amount']
        processed.withholdings = fin['withholdings']
        processed.withholdings_amount = fin['withholdings_total']
        processed.total_amount = fin['total']

        processed.payment_method = _find_payment_method(text, lines) or Document.PAYMENT_METHOD_OTHER

        processed.line_items = _parse_products_and_line_items(lines, text, currency=currency)

        processed.category = _guess_category_enhanced(
            supplier_name=processed.supplier_name,
            lines=lines,
            line_items=processed.line_items,
        )

        gemini_data = ocr_result.extra.get('gemini_structured') if isinstance(ocr_result.extra, dict) else None
        if gemini_data and isinstance(gemini_data, dict):
            _merge_gemini_fallback(processed, gemini_data, currency)


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


def _reuse_exact_duplicate(document: Document, user) -> Optional[dict]:
    if not document.checksum:
        return None
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

        return {
            'requires_review': True,
            'review_reasons': [
                f'Este archivo ya lo habías subido (documento #{existing_completed.pk}). '
                'Descártalo o apruébalo si de verdad es otro gasto.'
            ],
            'reused_from_document_id': existing_completed.pk,
            'processed_fields': document.structured_data,
        }
    return None


def _extract_from_file(document: Document) -> ProcessedDocument:
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
        return DocumentProcessor().process_file(
            file_path,
            document_type=document.document_type,
            original_filename=document.original_filename or document.file.name,
        )
    finally:
        if temp_file and os.path.exists(temp_file.name):
            try:
                os.unlink(temp_file.name)
            except OSError:
                logger.warning('Could not delete temporary file %s', temp_file.name)


def _resolve_supplier(user, processed: ProcessedDocument) -> Optional[Supplier]:
    if not processed.supplier_name:
        return None
    try:
        supplier, _ = Supplier.objects.get_or_create_for_user(
            user=user,
            name=processed.supplier_name,
            tax_id=processed.supplier_tax_id or '',
            address=processed.supplier_address or '',
            phone=processed.supplier_phone or '',
            email=processed.supplier_email or '',
        )
        return supplier
    except Exception:
        logger.warning('Could not create supplier %r; looking for a match', processed.supplier_name, exc_info=True)
        return Supplier.objects.find_match(
            user=user,
            name=processed.supplier_name,
            tax_id=processed.supplier_tax_id or '',
        )


def _flag_logical_duplicate(document: Document, supplier, processed: ProcessedDocument) -> None:
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


def _model_fields_for(processed: ProcessedDocument) -> dict:
    model_fields = processed.to_model_fields()
    if processed.raw.get('extraction_source') == 'gemini_structured':
        model_fields['document_number'] = processed.document_number or ''
        model_fields['issued_at'] = processed.issued_at
        model_fields['due_date'] = processed.due_date
        model_fields['subtotal_amount'] = processed.subtotal_amount
        model_fields['tax_amount'] = processed.tax_amount
        model_fields['total_amount'] = processed.total_amount
        model_fields['payment_method'] = processed.payment_method or ''
        if processed.currency:
            model_fields['currency'] = processed.currency
    return model_fields


def _save_processed(document: Document, processed: ProcessedDocument, model_fields: dict, supplier, category) -> None:
    structured_source = processed.raw.get('extraction_source') == 'gemini_structured'
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


def process_document_pipeline(document: Document) -> dict:
    if not document.file:
        raise ValueError('Document has no file attached.')

    user = document.user

    reused = _reuse_exact_duplicate(document, user)
    if reused is not None:
        return reused

    processed = _extract_from_file(document)
    supplier = _resolve_supplier(user, processed)
    category = resolve_category(user, supplier, processed.category)
    _flag_logical_duplicate(document, supplier, processed)
    model_fields = _model_fields_for(processed)
    _save_processed(document, processed, model_fields, supplier, category)

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
