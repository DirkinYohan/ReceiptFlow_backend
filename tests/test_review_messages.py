"""Readable review reasons and the phone-as-NIT guard."""
from apps.documents.review_messages import GENERIC, PRODUCTS_DONT_ADD_UP, humanize, humanize_all
from services.extraction.schemas import ExtractedDocument

SALDO_APPS_WARNINGS = [
    'TOTAL_MISMATCH: subtotal(8000.0)+taxes(450.0)-discounts(0)-withholdings(0)+other(0)=8450.0 vs total_amount=8480.0',
    'Los montos de la tabla no coinciden con el subtotal ni el total parcial expresados en el resumen.',
    'GEMINI_MODEL_USED: gemini-3.5-flash',
    'LINE_PRICE_COLUMN_AMBIGUOUS: item #1 qty*price=40460900000.00 exceeds subtotal=8000.0; printed values were kept',
    'LINE_ITEM_MATH_MISMATCH: item #1 qty*price-discount=40460900000.00 vs total=20230450.0',
    'LINE_TOTALS_SUM_MISMATCH: sum(line_items.total)=40460900.0 vs subtotal=8000.0',
]


class TestHumanize:
    def test_total_mismatch_shows_the_math(self):
        text = humanize(SALDO_APPS_WARNINGS[0], 'USD')
        assert text == (
            'La suma no cuadra: subtotal USD 8.000 + impuestos USD 450 da USD 8.450, '
            'pero el total impreso es USD 8.480.'
        )

    def test_internal_info_is_hidden(self):
        assert humanize('GEMINI_MODEL_USED: gemini-3.5-flash') is None

    def test_plain_spanish_is_kept(self):
        assert humanize('No se identificó el monto total.') == 'No se identificó el monto total.'

    def test_unknown_code_is_generic(self):
        assert humanize('SOMETHING_NEW: details') == GENERIC

    def test_product_warnings_collapse_into_one(self):
        reasons = humanize_all(SALDO_APPS_WARNINGS, 'USD')
        assert reasons.count(PRODUCTS_DONT_ADD_UP) == 1
        assert len(reasons) == 3
        assert not any('GEMINI' in r or '_' in r for r in reasons)


class TestPhoneIsNotTaxId:
    def test_phone_read_as_nit_is_dropped(self):
        doc = ExtractedDocument.model_validate({
            'supplier_tax_id': '802969795',
            'supplier_tax_id_verification_digit': '7',
            'phone_numbers': ['80296979597'],
        })
        assert doc.supplier_tax_id is None
        assert doc.supplier_tax_id_verification_digit is None
        assert any(w.startswith('SUPPLIER_TAX_ID_IS_PHONE') for w in doc.warnings)

    def test_real_nit_is_kept(self):
        doc = ExtractedDocument.model_validate({
            'supplier_tax_id': '830131572',
            'supplier_tax_id_verification_digit': '4',
            'phone_numbers': ['(602) 733 1010', '300 123 4567'],
        })
        assert doc.supplier_tax_id == '830131572'
        assert doc.warnings == []


class TestSettledReceipt:
    """University receipt: 'Pext Seminario 185,000' and 'BANCOLOMBIA -185,000' -> valor a pagar 0."""

    SECTIONS = [{
        'name': 'Conceptos',
        'columns': ['Conceptos', 'Valor'],
        'rows': [
            {'cells': [{'column': 'Conceptos', 'value': 'Pext Seminario'}, {'column': 'Valor', 'value': '185,000'}]},
            {'cells': [{'column': 'Conceptos', 'value': 'BANCOLOMBIA 00884917085 AHO'},
                       {'column': 'Valor', 'value': '-185,000'}]},
        ],
    }]

    def test_charge_cancelled_by_payment_is_the_amount(self):
        from decimal import Decimal
        from services.document_processor.processor import _settled_charges
        assert _settled_charges(self.SECTIONS) == Decimal('185000')

    def test_partial_payment_is_not_settled(self):
        from services.document_processor.processor import _settled_charges
        sections = [dict(self.SECTIONS[0], rows=[
            self.SECTIONS[0]['rows'][0],
            {'cells': [{'value': 'Abono'}, {'value': '-100,000'}]},
        ])]
        assert _settled_charges(sections) is None
        assert _settled_charges([]) is None

    def test_settled_document_is_marked_paid(self, user):
        from apps.documents.models import Document
        from apps.documents.payables import apply_payment_inference
        doc = Document(user=user, document_type=Document.DOCUMENT_TYPE_INVOICE)
        apply_payment_inference(doc, 'SERVICE_INVOICE', {'settled_by_payment': True})
        assert doc.is_paid is True
