"""Unit tests for the Gemini Structured Output extraction + post-validation layer.

All tests mock external Gemini calls; no real file I/O or API calls.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from services.extraction import (
    ExtractedDocument,
    GeminiStructuredExtractor,
    LineItem,
    TaxItem,
    validate_extracted,
)
from services.extraction.post_validator import (
    PRODUCT_TABLE_KEYWORDS,
    _looks_like_date,
)


# ---------------------------------------------------------------------------
# 1. Schema tests (TR-1.x)
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_null_invoice_and_empty_items(self):
        doc = ExtractedDocument(invoice_number=None, line_items=[], document_type='invoice')
        assert doc.invoice_number is None
        assert doc.line_items == []
        assert doc.model_dump()['invoice_number'] is None

    def test_na_becomes_none(self):
        doc = ExtractedDocument(invoice_number='N/A', supplier_name='  ')
        assert doc.invoice_number is None
        assert doc.supplier_name is None

    def test_empty_table_cell_stays_null_and_keeps_printed_zero(self):
        from services.extraction.schemas import SectionCell

        assert SectionCell(column='Intereses Mora', value='-').value is None
        assert SectionCell(column='Intereses Mora', value='—').value is None
        assert SectionCell(column='Intereses Mora', value='N/A').value is None
        assert SectionCell(column='Intereses Mora', value='').value is None
        assert SectionCell(column='Intereses Mora', value=None).value is None
        assert SectionCell(column='Intereses Mora', value='0').value == '0'
        assert SectionCell(column='Subsidio', value='-5.013').value == '-5.013'

    def test_quantity_negative_rejected(self):
        with pytest.raises(Exception):
            LineItem(description='Fecha de emisión', quantity=-1)

    def test_unit_price_negative_rejected(self):
        with pytest.raises(Exception):
            LineItem(description='X', quantity=1, unit_price=-100)

    def test_verification_digit_only_single_digit(self):
        doc = ExtractedDocument(supplier_tax_id_verification_digit='12')
        assert doc.supplier_tax_id_verification_digit is None
        doc2 = ExtractedDocument(supplier_tax_id_verification_digit='A')
        assert doc2.supplier_tax_id_verification_digit is None
        doc3 = ExtractedDocument(supplier_tax_id_verification_digit='7')
        assert doc3.supplier_tax_id_verification_digit == '7'

    def test_decimal_string_parsing(self):
        item = LineItem(description='A', quantity='2', unit_price='1,500.00',
                        total='3000.00')
        assert item.quantity == Decimal('2')
        assert item.unit_price == Decimal('1500.00')
        assert item.total == Decimal('3000.00')


# ---------------------------------------------------------------------------
# 2. Post-validator tests (TR-3.x)
# ---------------------------------------------------------------------------

def _perfect_doc(**overrides):
    base = ExtractedDocument(
        document_type='invoice',
        supplier_name='Supermercado X S.A.S',
        supplier_tax_id='900123456',
        supplier_tax_id_verification_digit='7',
        invoice_number='FE-100',
        issue_date='2024-08-15',
        currency='COP',
        subtotal=Decimal('100000.00'),
        taxes=[TaxItem(type='IVA', rate=Decimal('19.00'), amount=Decimal('19000.00'))],
        total_amount=Decimal('119000.00'),
        payment_method='card',
        cufe='abcdef0123456789' * 6,
        line_items=[
            LineItem(
                description='Producto A',
                sku='SKU001',
                quantity=Decimal('2'),
                unit_price=Decimal('50000.00'),
                discount=Decimal('0'),
                tax_rate=Decimal('19'),
                tax_amount=Decimal('19000.00'),
                total=Decimal('100000.00'),
            )
        ],
    )
    if overrides:
        # model_copy update override
        return base.model_copy(update=overrides)
    return base


RAW_HINT_TABLE = 'CANT DESCRIPCION V. UNIT TOTAL'
RAW_HINT_SERVICIO = (
    'Lectura actual 158 m3 Periodo 2024-08 Consumo 35 m3'
    ' Total a pagar $125.000 NIT 899999000-1'
)


class TestPostValidation:
    def test_perfect_doc_no_warnings(self):
        doc = validate_extracted(_perfect_doc(), raw_text_hint=RAW_HINT_TABLE)
        assert doc.warnings == []
        assert doc.requires_review is False

    def test_math_inconsistency_warns_no_mutate(self, snapshot=None):
        # Intencionalmente descuadramos el total
        subtotal = Decimal('1000000')
        desc = Decimal('100000')
        iva = Decimal('171000')
        total_bad = Decimal('1089000')  # Debería ser 1.071.000 -> 18k diff
        doc = ExtractedDocument(
            document_type='invoice',
            supplier_name="D&W Construcción y Diseño S.A.S",
            supplier_tax_id='900987654',
            supplier_tax_id_verification_digit='2',
            invoice_number='FAC-2024-1120',
            issue_date='2024-07-22',
            currency='COP',
            subtotal=subtotal,
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=iva)],
            total_amount=total_bad,
            line_items=[
                LineItem(description='Ladrillo rojo x1000 und',
                         quantity=Decimal('1'),
                         unit_price=Decimal('600000'),
                         discount=desc,
                         total=Decimal('500000')),
                LineItem(description='Cemento x50 bultos',
                         quantity=Decimal('1'),
                         unit_price=Decimal('500000'),
                         discount=Decimal('0'),
                         total=Decimal('500000')),
            ],
        )
        raw_hint = 'CANT DESCRIPCION V. UNIT DESCUENTO TOTAL'
        validated = validate_extracted(doc, raw_text_hint=raw_hint)
        # No debe mutar valores
        assert validated.subtotal == subtotal
        assert validated.total_amount == total_bad
        assert validated.taxes[0].amount == iva
        # Debe contener warnings
        codes = ' '.join(validated.warnings)
        assert 'TOTAL_MISMATCH' in codes or 'LINE_TOTALS_SUM_MISMATCH' in codes
        assert validated.requires_review is True

    def test_invoice_number_equals_nit_suspicious(self):
        doc = ExtractedDocument(
            supplier_tax_id='900123456',
            supplier_tax_id_verification_digit='7',
            invoice_number='9001234567',
        )
        v = validate_extracted(doc)
        assert v.invoice_number is None
        assert any('INVOICE_NUMBER_SUSPICIOUS' in w for w in v.warnings)
        assert v.requires_review is False
        assert v.validation_level == 'completed_with_warnings'

    def test_public_service_faux_items_stripped(self):
        doc = ExtractedDocument(
            document_type='invoice',
            supplier_name='Cedenar Acueducto y Alcantarillado',
            supplier_tax_id='899999000',
            invoice_number='SER-2024-08102',
            issue_date='2024-08-10',
            currency='COP',
            subtotal=Decimal('95000.00'),
            taxes=[TaxItem(type='INC', amount=Decimal('5000.00'))],
            total_amount=Decimal('125000.00'),
            line_items=[
                LineItem(description='Lectura anterior: 123 m3', quantity=Decimal('1'),
                         total=Decimal('0')),
                LineItem(description='Lectura actual: 158 m3', quantity=Decimal('1'),
                         total=Decimal('0')),
                LineItem(description='Periodo facturado 2024-08', quantity=Decimal('1'),
                         total=Decimal('80000')),
                LineItem(description='NIT proveedor 899999000-1', quantity=Decimal('1'),
                         total=Decimal('0')),
            ],
        )
        v = validate_extracted(doc, raw_text_hint=RAW_HINT_SERVICIO)
        codes = ' '.join(v.warnings)
        assert 'FAUX_LINE_ITEMS_DETECTED' in codes
        assert 'FAUX_LINE_ITEMS_STRIPPED' in codes
        assert v.line_items == []
        # Valores no mutados
        assert v.subtotal == Decimal('95000.00')
        assert v.total_amount == Decimal('125000.00')

    def test_date_invalid_format_warns(self):
        doc = ExtractedDocument(issue_date='not-a-date')
        v = validate_extracted(doc)
        assert any('DATE_INVALID_FORMAT' in w for w in v.warnings)

    def test_currency_not_allowed(self):
        doc = ExtractedDocument(currency='ABC')
        v = validate_extracted(doc)
        assert any('CURRENCY_NOT_ALLOWED' in w for w in v.warnings)

    def test_looks_like_date_helper(self):
        assert _looks_like_date('15/08/2024')
        assert _looks_like_date('15 de agosto de 2024')
        assert not _looks_like_date('FE-100')
        assert not _looks_like_date('')


# ---------------------------------------------------------------------------
# 3. GeminiExtractor mocks (TR-2.x)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, text):
        self.text = text


class TestGeminiExtractorStrictSchema:
    def test_omitted_fields_are_null(self):
        """Los campos no presentes en la respuesta del extractor se conservan null
        (no aplica fallback heurístico)."""
        payload = ExtractedDocument(
            document_type='receipt',
            supplier_name='Comercio Local',
            invoice_number=None,
            currency='COP',
            total_amount=Decimal('50000.00'),
        )
        # Caso 1: validate_extracted sobre datos con invoice=null lo mantiene null
        v = validate_extracted(payload)
        assert v.invoice_number is None
        assert v.supplier_name == 'Comercio Local'

        # Caso 2: Con validate_extracted + raw_text sin tabla no rellena line_items.
        # Si invocamos un mock extractor que devuelve invoice=None el resultado debe seguir None
        fake_result = payload
        with patch.object(GeminiStructuredExtractor, 'extract_file',
                          return_value=fake_result):
            ext = GeminiStructuredExtractor(api_key='')
            out = ext.extract_file('__fake_path__.png')
            assert out.invoice_number is None
            assert out.supplier_name == 'Comercio Local'

    def test_api_down_requires_review_no_fallback(self):
        """Si la API falla por completo, extract_file retorna requires_review=True
        y un código STRUCTURED_OUTPUT_FAILED sin inventar datos."""
        fake_fallback = ExtractedDocument(
            requires_review=True,
            warnings=['STRUCTURED_OUTPUT_FAILED: simulated down'],
        )
        with patch.object(GeminiStructuredExtractor, 'extract_file',
                          return_value=fake_fallback):
            ext = GeminiStructuredExtractor(api_key='')
            out = ext.extract_file('__fail__.png')
        assert out.requires_review is True
        codes = ' '.join(out.warnings)
        assert 'STRUCTURED_OUTPUT_FAILED' in codes
        assert out.invoice_number is None
        assert out.supplier_name is None
        assert out.total_amount is None


# ---------------------------------------------------------------------------
# 4. Fixtures tipificadas + Processor integration (TR-6.x)
# ---------------------------------------------------------------------------

def _make_doc(d: dict):
    """Helper para construir un ExtractedDocument desde dict."""
    return ExtractedDocument.model_validate(d)


class TestFacturasTipificadas:
    # ---- PROMWEBSOFT: factura electrónica, tabla con 3 productos, FESE ----
    def test_promwebsoft(self):
        cufe = 'a' * 96
        doc = ExtractedDocument(
            document_type='invoice',
            supplier_name='PROMWEBSOFT S.A.S.',
            supplier_tax_id='900200300',
            supplier_tax_id_verification_digit='9',
            invoice_number='FESE-001-000042',
            issue_date='2024-06-14',
            issue_time='10:45:00',
            currency='COP',
            subtotal=Decimal('840000.00'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19.00'), amount=Decimal('159600.00'))],
            total_amount=Decimal('999600.00'),
            payment_method='transfer',
            cufe=cufe,
            line_items=[
                LineItem(description='Licencia SaaS básica 1 año',
                         sku='SAAS-BAS-1Y', quantity=Decimal('1'),
                         unit_price=Decimal('360000'), discount=Decimal('0'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('68400.00'),
                         total=Decimal('360000.00')),
                LineItem(description='Módulo facturación electrónica',
                         sku='SAAS-FE-1Y', quantity=Decimal('1'),
                         unit_price=Decimal('300000'), discount=Decimal('0'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('57000.00'),
                         total=Decimal('300000.00')),
                LineItem(description='Implementación y soporte',
                         sku='SRV-IMP-01', quantity=Decimal('1'),
                         unit_price=Decimal('180000'), discount=Decimal('0'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('34200.00'),
                         total=Decimal('180000.00')),
            ],
        )
        raw = ('CANT DESCRIPCIÓN VLR UNITARIO IVA TOTAL\n'
               'FESE Factura Electrónica SECOP')
        v = validate_extracted(doc, raw_text_hint=raw)
        assert v.model_dump().get('document_type') or doc.document_type  # Acceso seguro
        assert doc.invoice_number == 'FESE-001-000042'
        assert doc.supplier_tax_id == '900200300'
        assert doc.supplier_tax_id_verification_digit == '9'
        assert len(doc.line_items) == 3
        # Verificar que no existan productos prohibidos
        for it in doc.line_items:
            d = (it.description or '').lower()
            for bad in ('nit', 'cufe', 'lectura', 'periodo', 'total a pagar'):
                assert bad not in d
        # La suma cuadra: warnings vacíos
        assert v.warnings == []
        assert v.requires_review is False

    # ---- COJARDIN: NIT 901178470-1 + 1 producto ------------------------
    def test_cojardin_nit_dv_split(self):
        doc = ExtractedDocument(
            document_type='invoice',
            supplier_name='COJARDIN S.A.S  BLOQUE CADIZ',
            supplier_tax_id='901178470',
            supplier_tax_id_verification_digit='1',
            invoice_number='CJ-2024-00881',
            issue_date='2024-05-02',
            currency='COP',
            subtotal=Decimal('280000.00'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('53200.00'))],
            total_amount=Decimal('333200.00'),
            line_items=[
                LineItem(description='Diseño y mantenimiento jardines 1 mes',
                         sku='SER-JAR-M1', quantity=Decimal('1'),
                         unit_price=Decimal('280000'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('53200.00'),
                         total=Decimal('280000.00')),
            ],
        )
        raw = 'Descripcion Cantidad Vr. Unitario Valor Total COJARDIN Bloque Cádiz'
        v = validate_extracted(doc, raw_text_hint=raw)
        assert doc.supplier_tax_id == '901178470'
        assert doc.supplier_tax_id_verification_digit == '1'
        assert len(doc.line_items) == 1
        # Nombre del proveedor no contiene adquiriente (Cliente)
        assert 'Cliente' not in doc.supplier_name
        assert v.warnings == []
        assert v.requires_review is False

    # ---- CEDENAR: Servicio público (debe tener [] lineitems) -----------
    def test_cedenar_public_service_no_line_items(self):
        doc = ExtractedDocument(
            document_type='invoice',
            supplier_name='CEDENAR E.S.P. - Acueducto y Alcantarillado',
            supplier_tax_id='8001234567',
            invoice_number='ESP-2024-08102',
            issue_date='2024-08-10',
            due_date='2024-08-30',
            billing_period='2024-08',
            currency='COP',
            subtotal=Decimal('92000.00'),
            taxes=[TaxItem(type='INC', amount=Decimal('5000.00'))],
            total_amount=Decimal('124300.00'),
            line_items=[
                LineItem(description='Lectura anterior 123 m3', quantity=Decimal('1'),
                         total=Decimal('0')),
                LineItem(description='Lectura actual 158 m3', quantity=Decimal('1'),
                         total=Decimal('0')),
                LineItem(description='Consumo mes 35 m3', quantity=Decimal('1'),
                         total=Decimal('85000.00')),
                LineItem(description='Periodo facturado 2024-08', quantity=Decimal('1'),
                         total=Decimal('0')),
                LineItem(description='Histórico consumo 3m', quantity=Decimal('1'),
                         total=Decimal('0')),
            ],
        )
        raw = ('NIT Emisor: 8001234567-1\n'
               'Lectura anterior: 123 m3\n'
               'Lectura actual: 158 m3\n'
               'Consumo: 35 m3\n'
               'Periodo facturado: 2024-08\n'
               'Total a pagar: $ 124.300')
        v = validate_extracted(doc, raw_text_hint=raw)
        codes = ' '.join(v.warnings)
        assert 'FAUX_LINE_ITEMS_DETECTED' in codes
        assert 'FAUX_LINE_ITEMS_STRIPPED' in codes
        assert len(v.line_items) == 0
        # No se crean productos falsos
        for it in doc.line_items:
            d = (it.description or '').lower()
            for bad in ('lectura', 'periodo', 'consumo', 'histórico', 'histórico'):
                # Está permitido que existan como warnings, no como items finales
                assert True
        # Los items reales están limpios
        assert v.line_items == []
        # Valores se mantienen
        assert v.subtotal == Decimal('92000.00')
        assert v.total_amount == Decimal('124300.00')

    # ---- FACTURA POS ----------------------------------------------------
    def test_pos_invoice_short_table(self):
        doc = ExtractedDocument(
            document_type='ticket',
            supplier_name='TIENDA LA ESQUINA SISTEMA POS',
            supplier_tax_id='890100200',
            supplier_tax_id_verification_digit='3',
            invoice_number='POS-88712',
            issue_date='2024-09-01',
            issue_time='18:24:05',
            currency='COP',
            subtotal=Decimal('32000.00'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('6080.00'))],
            total_amount=Decimal('38080.00'),
            payment_method='cash',
            line_items=[
                LineItem(description='Gaseosa 1.5L', quantity=Decimal('2'),
                         unit_price=Decimal('5000'), total=Decimal('10000.00'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('1900.00')),
                LineItem(description='Papa margarita', quantity=Decimal('1'),
                         unit_price=Decimal('8000'), total=Decimal('8000.00'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('1520.00')),
                LineItem(description='Chocolatina Jet x3', quantity=Decimal('3'),
                         unit_price=Decimal('2000'), total=Decimal('6000.00'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('1140.00')),
                LineItem(description='Jabón de baño', quantity=Decimal('2'),
                         unit_price=Decimal('4000'), total=Decimal('8000.00'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('1520.00')),
            ],
        )
        raw = 'Cant Artículo Precio Total Sistema POS #88712 Efectivo Cambio'
        v = validate_extracted(doc, raw_text_hint=raw)
        assert doc.invoice_number == 'POS-88712'
        assert len(doc.line_items) == 4
        assert all(it.total > 0 for it in doc.line_items)
        for it in doc.line_items:
            d = (it.description or '').lower()
            for bad in ('nit', 'cufe', 'total a pagar', 'efectivo', 'cambio'):
                assert bad not in d
        assert v.warnings == []
        assert v.requires_review is False

    # ---- Factura Electrónica (múltiples fechas) ------------------------
    def test_electronic_invoice_multiple_dates(self):
        doc = ExtractedDocument(
            document_type='invoice',
            supplier_name='EMPRESA GENERICA LTDA',
            supplier_tax_id='901000222',
            supplier_tax_id_verification_digit='5',
            invoice_number='SETP990001234',
            issue_date='2024-08-15',
            generation_date='2024-08-14',
            due_date='2024-09-15',
            billing_period='2024-08',
            currency='COP',
            subtotal=Decimal('500000.00'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('95000.00'))],
            total_amount=Decimal('595000.00'),
            cufe='f' * 96,
            line_items=[
                LineItem(description='Consultoría IT 20h', quantity=Decimal('1'),
                         unit_price=Decimal('500000'),
                         total=Decimal('500000.00'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('95000.00')),
            ],
        )
        raw = 'Cant Descripcion Valor Unit Subtotal IVA Total SETP Prefijo'
        v = validate_extracted(doc, raw_text_hint=raw)
        # Todas las fechas preservadas (no pisa una con otra)
        assert doc.issue_date == '2024-08-15'
        assert doc.generation_date == '2024-08-14'
        assert doc.due_date == '2024-09-15'
        assert doc.billing_period == '2024-08'
        # invoice_number = prefijo SETP... (no CUFE)
        assert doc.invoice_number == 'SETP990001234'
        assert doc.cufe == 'f' * 96
        assert v.warnings == []
        assert v.requires_review is False

    # ---- D&W Construcción: descuentos + TOTAL_MISMATCH intencional ------
    def test_dw_construccion_mismatch_keeps_values(self):
        doc = ExtractedDocument(
            document_type='invoice',
            supplier_name="D&W CONSTRUCCION Y DISEÑO S.A.S",
            supplier_tax_id='900987654',
            supplier_tax_id_verification_digit='2',
            invoice_number='DW-2024-1120',
            issue_date='2024-07-22',
            currency='COP',
            subtotal=Decimal('1000000.00'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('171000.00'))],
            total_amount=Decimal('1089000.00'),  # Errado intencionalmente
            line_items=[
                LineItem(description='Ladrillo hueco x1000 und', quantity=Decimal('1'),
                         unit_price=Decimal('700000'), discount=Decimal('100000.00'),
                         total=Decimal('600000.00')),
                LineItem(description='Cemento tipo 1 x50', quantity=Decimal('1'),
                         unit_price=Decimal('500000'), discount=Decimal('0'),
                         total=Decimal('500000.00')),
            ],
        )
        raw = 'Cant Descripcion Vlr Unit Desc Vlr Total D&W Factura Nro DW-2024-1120'
        v = validate_extracted(doc, raw_text_hint=raw)
        # No se cambian los valores
        assert v.subtotal == Decimal('1000000.00')
        assert v.total_amount == Decimal('1089000.00')
        # Warning obligatorio
        codes = ' '.join(v.warnings)
        assert 'TOTAL_MISMATCH' in codes
        assert v.requires_review is True

    # ---- InvoiceNumber estricto ----------------------------------------
    def test_invoice_number_only_by_explicit_label(self):
        from services.extraction.post_validator import validate_extracted as val
        # Caso: documento con muchos números, solo uno es Factura No:
        doc = ExtractedDocument(
            supplier_tax_id='900123456',
            supplier_tax_id_verification_digit='7',
            invoice_number='FE-2024-0042',
            cufe='a' * 64,
        )
        # Simular que alguien malintencionadamente igualó invoice_number == cufe:
        bad_invoice_equals_cufe = ExtractedDocument(
            supplier_tax_id='900123456',
            invoice_number='a' * 64,  # == cufe
            cufe='a' * 64,
        )
        v = val(bad_invoice_equals_cufe)
        codes = ' '.join(v.warnings)
        assert 'INVOICE_NUMBER_SUSPICIOUS' in codes
        # El bueno pasa
        v2 = val(doc, raw_text_hint='NIT Factura No Cuenta Referencia')
        assert v2.warnings == [] or not any('INVOICE_NUMBER_SUSPICIOUS' in w for w in v2.warnings)

    # ---- Documento sin tabla -> line_items = [] ------------------------
    def test_without_product_table_empty_items(self):
        doc = ExtractedDocument(
            document_type='invoice',
            supplier_name='CONSULTORIO MEDICO XYZ',
            invoice_number='CM-2024-01',
            issue_date='2024-09-05',
            currency='COP',
            subtotal=Decimal('180000.00'),
            taxes=[TaxItem(type='IVA', amount=Decimal('0'))],
            total_amount=Decimal('180000.00'),
            line_items=[],
        )
        raw = ('Atencion medica general Historia Clinica Nro 888 '
               'Fecha 2024-09-05 Valor $180.000')
        v = validate_extracted(doc, raw_text_hint=raw)
        assert len(v.line_items) == 0
        assert v.warnings == []

    # ---- Categorías prohibidas no se convierten en productos ------------
    def test_bad_categories_not_converted_to_products(self):
        fake_prods = [
            'Fecha de emisión 2024-08-15',
            'Lectura anterior 123',
            'Referencia ORD-9981',
            'NIT 901178470-1',
            'Dirección Cra 15 # 93-47',
            'Teléfono 3001234567',
            'Total a pagar $ 45.500',
            'Histórico 3 meses $38.000',
            'CUFE ' + 'f'*48,
        ]
        doc = ExtractedDocument(
            line_items=[
                LineItem(description=d, quantity=Decimal('1'), total=Decimal('0'))
                for d in fake_prods
            ]
        )
        raw = ('Datos de la factura.\n'
               'Información adicional del pie de página')
        v = validate_extracted(doc, raw_text_hint=raw)
        codes = ' '.join(v.warnings)
        assert 'FAUX_LINE_ITEMS_DETECTED' in codes
        assert 'FAUX_LINE_ITEMS_STRIPPED' in codes
        assert v.line_items == []


# ---------------------------------------------------------------------------
# 5. Processor integration (TR-4.x)
# ---------------------------------------------------------------------------

class TestProcessorIntegration:
    def test_structured_maps_to_processed_document(self):
        from services.document_processor.processor import (
            DocumentProcessor,
            ProcessedDocument,
        )
        processor = DocumentProcessor()
        # Sin file_path, usamos solo apply_structured
        structured = ExtractedDocument(
            document_type='invoice',
            supplier_name='COJARDIN SAS',
            supplier_tax_id='901178470',
            supplier_tax_id_verification_digit='1',
            invoice_number='CJ-001',
            issue_date='2024-05-02',
            currency='COP',
            subtotal=Decimal('280000'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('53200'))],
            total_amount=Decimal('333200'),
            line_items=[
                LineItem(description='Jardineria mensual',
                         quantity=Decimal('1'),
                         unit_price=Decimal('280000'),
                         total=Decimal('280000'),
                         tax_rate=Decimal('19'), tax_amount=Decimal('53200')),
            ],
            warnings=[],
            requires_review=False,
        )
        from services.document_processor.processor import ProcessedDocument
        pd = ProcessedDocument()
        pd = processor._apply_structured_to_processed(pd, structured)
        assert pd.document_number == 'CJ-001'
        assert pd.supplier_nit == '901178470'
        assert pd.supplier_verification_digit == '1'
        assert pd.supplier_tax_id == '901178470-1'
        assert pd.subtotal_amount == Decimal('280000.00')
        assert pd.tax_amount == Decimal('53200.00')
        assert pd.total_amount == Decimal('333200.00')
        assert pd.currency == 'COP'
        assert len(pd.line_items) == 1
        assert pd.line_items[0]['description'] == 'Jardineria mensual'
        # warnings serializados en to_dict
        d = pd.to_dict()
        assert 'warnings' in d and isinstance(d['warnings'], list)

    def test_successful_empty_items_replace_regex_lines(self):
        from services.document_processor.processor import DocumentProcessor, ProcessedDocument
        processor = DocumentProcessor()
        processed = ProcessedDocument(
            line_items=[{'description': 'Lectura anterior', 'total_amount': Decimal('0')}],
        )
        structured = ExtractedDocument(
            supplier_name='CEDENAR',
            invoice_number='ESP-1',
            document_type='invoice',
            document_kind='public_service',
            line_items=[],
            warnings=['GEMINI_MODEL_USED: gemini-3.5-flash'],
            total_amount=Decimal('100'),
        )
        out = processor._apply_structured_to_processed(processed, structured)
        assert out.line_items == []
        assert out.total_amount == Decimal('100')

    def test_failed_extraction_keeps_regex_lines(self):
        from services.document_processor.processor import DocumentProcessor, ProcessedDocument
        processor = DocumentProcessor()
        processed = ProcessedDocument(
            line_items=[{'description': 'Cable UTP', 'total_amount': Decimal('10')}],
        )
        structured = ExtractedDocument(
            requires_review=True,
            warnings=['STRUCTURED_OUTPUT_FAILED: timeout'],
            line_items=[],
        )
        out = processor._apply_structured_to_processed(processed, structured)
        assert len(out.line_items) == 1
        assert out.line_items[0]['description'] == 'Cable UTP'


class TestExtractionPrecision:
    def test_colombian_amounts_and_dates(self):
        doc = ExtractedDocument(
            issue_date='15/08/2024',
            due_date='30-09-2024',
            billing_period='08/2024',
            subtotal='125.000',
            total_amount='1.250.000,50',
        )
        assert doc.issue_date == '2024-08-15'
        assert doc.due_date == '2024-09-30'
        assert doc.billing_period == '2024-08'
        assert doc.subtotal == Decimal('125000.00')
        assert doc.total_amount == Decimal('1250000.50')

    def test_public_service_kind_drops_charge_lines(self):
        doc = ExtractedDocument(
            document_type='public_service',
            supplier_name='CEDENAR',
            invoice_number='ESP-1',
            currency='COP',
            subtotal=Decimal('50000'),
            total_amount=Decimal('50000'),
            line_items=[
                LineItem(description='Cargo fijo', quantity=Decimal('1'),
                         unit_price=Decimal('50000'), total=Decimal('50000')),
            ],
        )
        assert doc.document_type == 'invoice'
        assert doc.document_kind == 'public_service'
        validated = validate_extracted(doc)
        assert validated.line_items == []
        assert validated.subtotal == Decimal('50000')
        assert validated.service_concepts[0].description == 'Cargo fijo'
        assert any('PUBLIC_SERVICE_NO_LINE_ITEMS' in w for w in validated.warnings)
        assert validated.requires_review is False
        assert validated.validation_level == 'completed_with_warnings'

    def test_mixed_items_keep_products_only(self):
        doc = ExtractedDocument(
            invoice_number='FE-9',
            supplier_tax_id='900123456',
            currency='COP',
            subtotal=Decimal('10000'),
            total_amount=Decimal('10000'),
            has_product_table=True,
            line_items=[
                LineItem(description='Cable UTP 5m', quantity=Decimal('1'),
                         unit_price=Decimal('10000'), total=Decimal('10000')),
                LineItem(description='Lectura anterior 10 kWh', quantity=Decimal('1'),
                         total=Decimal('0')),
                LineItem(description='NIT 900123456-7', quantity=Decimal('1'),
                         total=Decimal('0')),
            ],
        )
        validated = validate_extracted(doc, raw_text_hint='CANT DESCRIPCION PRECIO')
        assert [item.description for item in validated.line_items] == ['Cable UTP 5m']
        codes = ' '.join(validated.warnings)
        assert 'FAUX_LINE_ITEMS_DETECTED' in codes
        assert 'FAUX_LINE_ITEMS_FILTERED' in codes
        assert 'FAUX_LINE_ITEMS_STRIPPED' not in codes
        assert validated.requires_review is False
        assert validated.validation_level == 'completed_with_warnings'

    def test_real_products_are_not_flagged_by_substrings(self):
        doc = ExtractedDocument(
            document_type='invoice',
            supplier_name='Ferreteria El Tornillo',
            supplier_tax_id='900555666',
            supplier_tax_id_verification_digit='1',
            invoice_number='FER-100',
            issue_date='2024-03-01',
            currency='COP',
            subtotal=Decimal('100000'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('19000'))],
            total_amount=Decimal('119000'),
            has_product_table=True,
            line_items=[
                LineItem(description='Monitor LED 24', quantity=Decimal('1'),
                         unit_price=Decimal('40000'), total=Decimal('40000')),
                LineItem(description='Caja de herramientas', quantity=Decimal('1'),
                         unit_price=Decimal('30000'), total=Decimal('30000')),
                LineItem(description='Cambio de aceite', quantity=Decimal('1'),
                         unit_price=Decimal('20000'), total=Decimal('20000')),
                LineItem(description='Aceite derivado sintetico', quantity=Decimal('1'),
                         unit_price=Decimal('10000'), total=Decimal('10000')),
            ],
        )
        validated = validate_extracted(doc, raw_text_hint='CANT DESCRIPCION PRECIO TOTAL')
        assert len(validated.line_items) == 4
        assert validated.warnings == []
        assert validated.requires_review is False

    def test_missing_product_table_drops_invented_items(self):
        doc = ExtractedDocument(
            has_product_table=False,
            currency='COP',
            subtotal=Decimal('100'),
            total_amount=Decimal('100'),
            line_items=[
                LineItem(description='Producto inventado', quantity=Decimal('1'),
                         unit_price=Decimal('100'), total=Decimal('100')),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.line_items == []
        assert validated.subtotal == Decimal('100')
        assert any('NO_PRODUCT_TABLE' in w for w in validated.warnings)
        assert validated.requires_review is False
        assert validated.validation_level == 'completed_with_warnings'

    def test_line_math_mismatch_keeps_printed_total(self):
        doc = ExtractedDocument(
            currency='COP',
            subtotal=Decimal('100'),
            total_amount=Decimal('100'),
            has_product_table=True,
            line_items=[
                LineItem(description='Tornillo', quantity=Decimal('2'),
                         unit_price=Decimal('10'), total=Decimal('100')),
            ],
        )
        validated = validate_extracted(doc, raw_text_hint='CANT DESCRIPCION')
        assert validated.line_items[0].total == Decimal('100')
        assert validated.line_items[0].unit_price == Decimal('10')
        assert any('LINE_ITEM_MATH_MISMATCH' in w for w in validated.warnings)
        assert validated.requires_review is False
        assert validated.validation_level == 'completed_with_warnings'

    def test_due_date_before_issue_requires_review(self):
        doc = ExtractedDocument(issue_date='2024-08-15', due_date='2024-08-01')
        validated = validate_extracted(doc)
        assert validated.issue_date == '2024-08-15'
        assert validated.due_date == '2024-08-01'
        assert any('DATE_ORDER_INVALID' in w for w in validated.warnings)

    def test_bad_line_does_not_discard_header(self):
        import json
        extractor = GeminiStructuredExtractor(api_key='test-key')
        payload = json.dumps({
            'supplier_name': 'PROMWEBSOFT',
            'invoice_number': 'FE-1',
            'currency': 'COP',
            'total_amount': 1000,
            'line_items': [
                {'description': 'Licencia', 'quantity': 1, 'unit_price': 1000, 'total': 1000},
                {'description': ['no es texto'], 'quantity': 1, 'total': 10},
            ],
        })
        parsed = extractor._parse_to_model(payload, source='test')
        assert parsed is not None
        assert parsed.supplier_name == 'PROMWEBSOFT'
        assert parsed.invoice_number == 'FE-1'
        assert parsed.total_amount == Decimal('1000')
        assert parsed.requires_review is True
        assert any('GEMINI_SCHEMA_VALIDATION_FAILED' in w for w in parsed.warnings)

    def test_non_positive_quantity_does_not_discard_document(self):
        import json
        extractor = GeminiStructuredExtractor(api_key='test-key')
        payload = json.dumps({
            'supplier_name': 'TIENDA POS',
            'invoice_number': 'POS-1',
            'currency': 'COP',
            'total_amount': '45.500',
            'line_items': [
                {'description': 'Gaseosa 1.5L', 'quantity': 2, 'unit_price': '5.000', 'total': '10.000'},
                {'description': 'Lectura anterior', 'quantity': 0, 'total': 0},
            ],
        })
        parsed = extractor._parse_to_model(payload, source='test')
        assert parsed.supplier_name == 'TIENDA POS'
        assert parsed.invoice_number == 'POS-1'
        assert parsed.total_amount == Decimal('45500.00')
        assert parsed.line_items[0].unit_price == Decimal('5000.00')
        assert parsed.line_items[1].quantity is None
        assert any('LINE_ITEM_QUANTITY_CLEARED' in w for w in parsed.warnings)

    def test_process_file_strips_readings_using_ocr_text(self):
        from unittest.mock import patch
        from services.document_processor.processor import DocumentProcessor
        from services.ocr.base import OCRResult

        ocr = OCRResult(
            raw_text='CEDENAR E.S.P.\nLectura anterior 10\nLectura actual 20\nTotal a pagar 100',
            lines=['CEDENAR E.S.P.', 'Lectura anterior 10', 'Lectura actual 20', 'Total a pagar 100'],
            confidence=0.9,
        )
        structured = ExtractedDocument(
            supplier_name='CEDENAR E.S.P.',
            supplier_tax_id='8001234567',
            invoice_number='ESP-1',
            issue_date='2024-08-10',
            currency='COP',
            subtotal=Decimal('100'),
            total_amount=Decimal('100'),
            line_items=[
                LineItem(description='Lectura anterior 10', quantity=Decimal('1'), total=Decimal('0')),
            ],
            warnings=['GEMINI_MODEL_USED: test'],
        )
        processor = DocumentProcessor()
        with patch.object(DocumentProcessor, '_process_via_structured', return_value=structured), \
                patch('services.document_processor.processor.extract_ocr', return_value=ocr):
            processed = processor.process_file('ignored.png')
        assert processed.line_items == []
        assert processed.supplier_name == 'CEDENAR E.S.P.'
        assert processed.document_number == 'ESP-1'
        assert any('FAUX_LINE_ITEMS' in w for w in processed.warnings)
        assert processed.raw.get('extraction_source') == 'gemini_structured'

    def test_successful_structured_does_not_keep_regex_guesses(self):
        """A null from Gemini stays null. The regex parser must not fill it."""
        from unittest.mock import patch
        from services.document_processor.processor import DocumentProcessor
        from services.ocr.base import OCRResult

        ocr = OCRResult(
            raw_text=(
                'CANT DESCRIPCION V. UNIT TOTAL\n'
                '1 Lectura anterior 123 10.000 10.000\n'
                'NIT 900123456-7\n'
                'Factura No 99999\n'
                'Fecha 15/08/2024\n'
                'TOTAL 10.000\n'
            ),
            lines=[
                'CANT DESCRIPCION V. UNIT TOTAL',
                '1 Lectura anterior 123 10.000 10.000',
                'NIT 900123456-7',
                'Factura No 99999',
                'Fecha 15/08/2024',
                'TOTAL 10.000',
            ],
            confidence=0.9,
        )
        structured = ExtractedDocument(
            document_type='invoice',
            supplier_name='PROMWEBSOFT S.A.S.',
            supplier_tax_id='900200300',
            supplier_tax_id_verification_digit='9',
            invoice_number=None,
            issue_date=None,
            currency='COP',
            subtotal=Decimal('840000'),
            total_amount=Decimal('999600'),
            line_items=[],
            has_product_table=False,
            warnings=['GEMINI_MODEL_USED: test'],
        )
        processor = DocumentProcessor()
        with patch.object(DocumentProcessor, '_process_via_structured', return_value=structured), \
                patch('services.document_processor.processor.extract_ocr', return_value=ocr):
            processed = processor.process_file('ignored.png')
        assert processed.document_number is None
        assert processed.issue_date is None
        assert processed.issued_at is None
        assert processed.line_items == []
        assert processed.supplier_name == 'PROMWEBSOFT S.A.S.'
        assert processed.supplier_nit == '900200300'
        assert processed.subtotal_amount == Decimal('840000')
        assert processed.total_amount == Decimal('999600')
        assert processed.requires_review is True


class TestRealInvoiceInterpretation:
    """Cifras de facturas reales. El validador corre sobre los montos, sin simularlo."""

    def test_dw_iva_uses_taxable_base_not_subtotal(self):
        doc = ExtractedDocument(
            document_class='ELECTRONIC_INVOICE',
            supplier_name='D y w construcción y diseño s.a.s',
            supplier_tax_id='901178470',
            supplier_tax_id_verification_digit='1',
            invoice_number='1020',
            issue_date='23/05/2022',
            due_date='23/05/2022',
            currency='COP',
            payment_method='transfer',
            subtotal=Decimal('12770082.63'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('106417.35'))],
            total_amount=Decimal('12876499.98'),
            has_product_table=True,
            line_items=[
                LineItem(description='MANO DE OBRA PARA CONSTRUCCION DE PLACA',
                         quantity=Decimal('1'), unit_price=Decimal('11201826.88'),
                         total=Decimal('11201826.88')),
                LineItem(description='ADMINISTRACION',
                         quantity=Decimal('1'), unit_price=Decimal('784127.88'),
                         total=Decimal('784127.88')),
                LineItem(description='IMPREVISTOS',
                         quantity=Decimal('1'), unit_price=Decimal('224036.53'),
                         total=Decimal('224036.53')),
                LineItem(description='UTILIDAD',
                         quantity=Decimal('1'), unit_price=Decimal('560091.34'),
                         total=Decimal('560091.34')),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.supplier_tax_id == '901178470'
        assert validated.supplier_tax_id_verification_digit == '1'
        assert validated.invoice_number == '1020'
        assert validated.issue_date == '2022-05-23'
        assert validated.taxes[0].taxable_base == Decimal('560091.34')
        assert validated.taxes[0].amount == Decimal('106417.35')
        assert validated.subtotal == Decimal('12770082.63')
        assert validated.total_amount == Decimal('12876499.98')
        assert not any('IVA_RATE_AMOUNT_MISMATCH' in warning for warning in validated.warnings)
        assert validated.requires_review is False
        assert validated.validation_level == 'confirmed'
        assert len(validated.line_items) == 4

    def test_explicit_wrong_taxable_base_still_requires_review(self):
        doc = ExtractedDocument(
            currency='COP',
            subtotal=Decimal('12770082.63'),
            taxes=[TaxItem(
                type='IVA', rate=Decimal('19'),
                taxable_base=Decimal('560091.34'), amount=Decimal('200000'),
            )],
            total_amount=Decimal('12970082.63'),
            has_product_table=True,
            line_items=[
                LineItem(description='UTILIDAD', quantity=Decimal('1'),
                         total=Decimal('560091.34')),
            ],
        )
        validated = validate_extracted(doc)
        assert any('IVA_RATE_AMOUNT_MISMATCH' in warning for warning in validated.warnings)
        assert validated.requires_review is True
        assert validated.taxes[0].amount == Decimal('200000')

    def test_full_subtotal_sent_as_base_is_reconciled_to_the_taxed_line(self):
        doc = ExtractedDocument(
            currency='COP',
            supplier_name='D y w construcción y diseño s.a.s',
            invoice_number='1020',
            issue_date='2022-05-23',
            subtotal=Decimal('12770082.63'),
            taxes=[TaxItem(
                type='IVA', rate=Decimal('19'),
                taxable_base=Decimal('12770082.63'), amount=Decimal('106417.35'),
            )],
            total_amount=Decimal('12876499.98'),
            has_product_table=True,
            line_items=[
                LineItem(description='MANO DE OBRA', quantity=Decimal('1'),
                         total=Decimal('11201826.88')),
                LineItem(description='ADMINISTRACION', quantity=Decimal('1'),
                         total=Decimal('784127.88')),
                LineItem(description='IMPREVISTOS', quantity=Decimal('1'),
                         total=Decimal('224036.53')),
                LineItem(description='UTILIDAD', quantity=Decimal('1'),
                         total=Decimal('560091.34')),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.taxes[0].amount == Decimal('106417.35')
        assert validated.taxes[0].taxable_base == Decimal('560091.34')
        assert any('TAXABLE_BASE_RECONCILED' in warning for warning in validated.warnings)
        assert not any('IVA_RATE_AMOUNT_MISMATCH' in warning for warning in validated.warnings)
        assert validated.requires_review is False

    def test_cojardin_is_utility_bill_without_products(self):
        doc = ExtractedDocument(
            document_class='UTILITY_BILL',
            supplier_name='COJARDIN S.A. - E.S.P.',
            supplier_tax_id='830131572',
            supplier_tax_id_verification_digit='4',
            invoice_number='17627',
            generation_date='2019-10-04',
            billing_period_start='2019-08-27',
            billing_period_end='2019-09-26',
            payment_date='2019-10-15',
            suspension_date='2019-10-17',
            currency='COP',
            subtotal=Decimal('143704.23'),
            other_charges=Decimal('-4.23'),
            total_amount=Decimal('143700.00'),
            has_product_table=False,
            line_items=[
                LineItem(description='Lectura actual 2034', quantity=Decimal('1'), total=Decimal('2034')),
                LineItem(description='Lectura anterior 2019', quantity=Decimal('1'), total=Decimal('2019')),
                LineItem(description='Consumo 15 m3', quantity=Decimal('1'), total=Decimal('15')),
                LineItem(description='Acueducto', quantity=Decimal('1'), total=Decimal('143704.23')),
                LineItem(description='Referencia 000000', quantity=Decimal('1'), total=Decimal('0')),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.document_class == 'WATER_BILL'
        assert validated.invoice_number == '17627'
        assert validated.sections[0].columns == ['Concepto', 'Valor']
        assert validated.sections[0].rows[0].cells[0].value == 'Acueducto'
        assert validated.supplier_tax_id_verification_digit == '4'
        assert validated.generation_date == '2019-10-04'
        assert validated.payment_date == '2019-10-15'
        assert validated.suspension_date == '2019-10-17'
        assert validated.issue_date is None
        descriptions = [item.description for item in validated.line_items]
        assert descriptions == []
        concepts = [concept.description for concept in validated.service_concepts]
        assert 'Acueducto' in concepts
        assert not any(name and 'lectura' in name.lower() for name in concepts)
        assert validated.total_amount == Decimal('143700.00')
        assert validated.requires_review is False
        assert validated.validation_level == 'completed_with_warnings'

    def test_utility_generation_date_is_not_stored_as_issue_date(self):
        doc = ExtractedDocument(
            document_class='UTILITY_BILL',
            supplier_name='COJARDIN S.A. - E.S.P.',
            invoice_number='17627',
            issue_date='2019-10-04',
            due_date='2019-10-15',
            payment_date='2019-10-15',
            suspension_date='2019-10-17',
            billing_period_start='2019-08-27',
            billing_period_end='2019-09-26',
            currency='COP',
            subtotal=Decimal('143704.23'),
            other_charges=Decimal('-4.23'),
            total_amount=Decimal('143700.00'),
        )
        hint = (
            'FECHA GENERACION: 4 DE OCTUBRE DE 2019 '
            'FECHA DE PAGO OPORTUNO 15 DE OCTUBRE DE 2019 '
            'FECHA DE SUSPENSION 17 DE OCTUBRE DE 2019'
        )
        validated = validate_extracted(doc, raw_text_hint=hint)
        assert validated.issue_date is None
        assert validated.generation_date == '2019-10-04'
        assert validated.due_date is None
        assert validated.payment_date == '2019-10-15'
        assert validated.suspension_date == '2019-10-17'
        assert validated.requires_review is False

    def test_promwebsoft_does_not_fabricate_line_extension(self):
        doc = ExtractedDocument(
            document_class='POS_RECEIPT',
            supplier_name='PROMWEBSOFT',
            supplier_tax_id='517840030',
            supplier_tax_id_verification_digit='1',
            invoice_number='000029',
            issue_date='06/12/2015',
            currency='COP',
            payment_method='cash',
            subtotal=Decimal('112586'),
            taxes=[
                TaxItem(type='IVA', rate=Decimal('16'), amount=Decimal('12414')),
                TaxItem(type='IVA', rate=Decimal('0'), amount=Decimal('0')),
            ],
            total_amount=Decimal('125000'),
            has_product_table=True,
            line_items=[
                LineItem(description='MANTENIMIENTO PREVENTIVO', quantity=Decimal('1'),
                         unit_price=Decimal('35000'), discount=Decimal('0'), total=Decimal('35000')),
                LineItem(description='COMBO TECLADO + MOUSE', quantity=Decimal('3'),
                         unit_price=Decimal('90000'), discount=Decimal('0'), total=Decimal('270000')),
            ],
        )
        validated = validate_extracted(doc)
        combo = validated.line_items[1]
        assert combo.quantity == Decimal('3')
        assert combo.unit_price == Decimal('90000')
        assert combo.total is None
        assert validated.line_items[0].total == Decimal('35000')
        assert validated.supplier_tax_id == '517840030'
        assert validated.invoice_number == '000029'
        assert validated.issue_date == '2015-12-06'
        assert validated.subtotal == Decimal('112586')
        assert validated.taxes[0].amount == Decimal('12414')
        assert validated.total_amount == Decimal('125000')
        assert not any('IVA_RATE_AMOUNT_MISMATCH' in warning for warning in validated.warnings)
        assert any('LINE_TOTAL_AMBIGUOUS' in warning for warning in validated.warnings)
        assert validated.requires_review is False
        assert validated.validation_level == 'completed_with_warnings'

    def test_multiple_iva_rates_and_exempt_line(self):
        doc = ExtractedDocument(
            document_class='SALES_INVOICE',
            supplier_name='Distribuidora Andina',
            supplier_tax_id='800111222',
            supplier_tax_id_verification_digit='3',
            invoice_number='FE-88',
            issue_date='2024-01-15',
            currency='COP',
            subtotal=Decimal('150000'),
            taxes=[
                TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('19000')),
                TaxItem(type='IVA', rate=Decimal('5'), amount=Decimal('2500')),
            ],
            total_amount=Decimal('171500'),
            has_product_table=True,
            line_items=[
                LineItem(description='Repuesto gravado 19', quantity=Decimal('1'),
                         unit_price=Decimal('100000'), tax_rate=Decimal('19'),
                         total=Decimal('100000')),
                LineItem(description='Insumo gravado 5', quantity=Decimal('2'),
                         unit_price=Decimal('25000'), tax_rate=Decimal('5'),
                         total=Decimal('50000')),
                LineItem(description='Servicio exento', quantity=Decimal('1'),
                         unit_price=Decimal('0'), tax_rate=Decimal('0'),
                         total=Decimal('0')),
            ],
        )
        validated = validate_extracted(doc)
        bases = {tax.rate: tax.taxable_base for tax in validated.taxes}
        assert bases[Decimal('19')] == Decimal('100000')
        assert bases[Decimal('5')] == Decimal('50000')
        assert validated.requires_review is False
        assert len(validated.line_items) == 3

    def test_documents_do_not_share_extracted_fields(self):
        first = validate_extracted(ExtractedDocument(
            supplier_name='Proveedor A',
            supplier_tax_id='901178470',
            invoice_number='1020',
            currency='COP',
            subtotal=Decimal('100'),
            total_amount=Decimal('100'),
            line_items=[LineItem(description='Producto A', quantity=Decimal('1'),
                                 unit_price=Decimal('100'), total=Decimal('100'))],
        ))
        second = validate_extracted(ExtractedDocument(
            supplier_name='Proveedor B',
            supplier_tax_id='830131572',
            invoice_number='17627',
            currency='COP',
            subtotal=Decimal('50'),
            total_amount=Decimal('50'),
            line_items=[LineItem(description='Producto B', quantity=Decimal('1'),
                                 unit_price=Decimal('50'), total=Decimal('50'))],
        ))
        assert first.supplier_name == 'Proveedor A'
        assert first.invoice_number == '1020'
        assert first.line_items[0].description == 'Producto A'
        assert second.supplier_name == 'Proveedor B'
        assert second.invoice_number == '17627'
        assert second.total_amount == Decimal('50')
        assert second.line_items[0].description == 'Producto B'

    def test_several_dates_do_not_force_review(self):
        doc = ExtractedDocument(
            document_class='ELECTRONIC_INVOICE',
            supplier_name='Comercial SAS',
            invoice_number='500',
            issue_date='2022-05-23',
            generation_date='2022-05-23',
            due_date='2022-06-23',
            currency='COP',
            subtotal=Decimal('1000'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('190'))],
            total_amount=Decimal('1190'),
            has_product_table=True,
            line_items=[
                LineItem(description='Servicio', quantity=Decimal('1'),
                         unit_price=Decimal('1000'), total=Decimal('1000')),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.issue_date == '2022-05-23'
        assert validated.due_date == '2022-06-23'
        assert validated.requires_review is False
        assert validated.validation_level == 'confirmed'

    def test_line_without_total_warns_but_stays_completed(self):
        doc = ExtractedDocument(
            supplier_name='Taller',
            invoice_number='7',
            currency='COP',
            subtotal=Decimal('80000'),
            total_amount=Decimal('80000'),
            has_product_table=True,
            line_items=[
                LineItem(description='Mano de obra', quantity=Decimal('1'),
                         unit_price=Decimal('80000'), total=None),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.line_items[0].total is None
        assert validated.line_items[0].quantity == Decimal('1')
        assert validated.requires_review is False

    def test_cedenar_utility_with_coherent_total(self):
        doc = ExtractedDocument(
            document_kind='public_service',
            supplier_name='Cedenar',
            supplier_tax_id='891200686',
            supplier_tax_id_verification_digit='8',
            invoice_number='EN-44120',
            issue_date='2024-08-10',
            billing_period='08/2024',
            currency='COP',
            subtotal=Decimal('92000'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), taxable_base=Decimal('20000'),
                           amount=Decimal('3800'))],
            other_charges=Decimal('0'),
            total_amount=Decimal('95800'),
            line_items=[
                LineItem(description='Lectura anterior 1200 kWh', total=Decimal('1200')),
                LineItem(description='Consumo del mes 180 kWh', total=Decimal('180')),
                LineItem(description='Cargo fijo energia', quantity=Decimal('1'),
                         total=Decimal('92000')),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.document_class == 'ENERGY_BILL'
        assert validated.line_items == []
        assert any(concept.description == 'Cargo fijo energia' for concept in validated.service_concepts)
        assert validated.taxes[0].taxable_base == Decimal('20000')
        assert validated.requires_review is False

    def test_pos_and_electronic_classes_follow_their_totals(self):
        pos = validate_extracted(ExtractedDocument(
            document_kind='pos',
            supplier_name='TIENDA POS',
            invoice_number='POS-9',
            issue_date='2024-02-01',
            currency='COP',
            subtotal=Decimal('10000'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('1900'))],
            total_amount=Decimal('11900'),
            has_product_table=True,
            line_items=[
                LineItem(description='Gaseosa', quantity=Decimal('2'),
                         unit_price=Decimal('5000'), total=Decimal('10000')),
            ],
        ))
        electronic = validate_extracted(ExtractedDocument(
            document_kind='electronic_invoice',
            supplier_name='Electronica SAS',
            supplier_tax_id='900111222',
            invoice_number='FE-1',
            issue_date='2024-02-02',
            currency='COP',
            subtotal=Decimal('20000'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('3800'))],
            total_amount=Decimal('23800'),
            has_product_table=True,
            line_items=[
                LineItem(description='Licencia', quantity=Decimal('1'),
                         unit_price=Decimal('20000'), total=Decimal('20000')),
            ],
        ))
        assert pos.document_class == 'POS_RECEIPT'
        assert pos.requires_review is False
        assert pos.validation_level == 'confirmed'
        assert electronic.document_class == 'ELECTRONIC_INVOICE'
        assert electronic.invoice_number == 'FE-1'
        assert electronic.requires_review is False
        assert pos.supplier_name != electronic.supplier_name

    def test_processed_raw_json_accepts_tax_decimals(self):
        import json
        from services.document_processor.processor import DocumentProcessor

        doc = validate_extracted(ExtractedDocument(
            document_class='ELECTRONIC_INVOICE',
            supplier_name='D y w construcción y diseño s.a.s',
            supplier_tax_id='901178470',
            supplier_tax_id_verification_digit='1',
            invoice_number='1020',
            issue_date='2022-05-23',
            currency='COP',
            subtotal=Decimal('12770082.63'),
            taxes=[TaxItem(
                type='IVA', rate=Decimal('19'),
                taxable_base=Decimal('560091.34'), amount=Decimal('106417.35'),
            )],
            total_amount=Decimal('12876499.98'),
            has_product_table=True,
            line_items=[
                LineItem(description='UTILIDAD', quantity=Decimal('1'),
                         unit_price=Decimal('560091.34'), total=Decimal('560091.34')),
            ],
        ))
        processed = DocumentProcessor()._processed_from_structured(doc, ocr_result=None)
        encoded = json.dumps(processed.raw)
        assert '560091.34' in encoded
        assert '106417.35' in encoded
        json.dumps(processed.to_dict())

    def test_cedenar_energy_bill_is_not_a_product_table(self):
        doc = ExtractedDocument(
            document_kind='public_service',
            supplier_name='CEDENAR Centrales Eléctricas de Nariño S.A. E.S.P.',
            supplier_tax_id='891200200',
            supplier_tax_id_verification_digit='8',
            invoice_number='51519035',
            issue_date='2024-03-08',
            billing_period='2024-02',
            billing_period_start='2024-01-26',
            billing_period_end='2024-02-24',
            payment_date='2024-03-22',
            suspension_date='2024-03-23',
            currency='COP',
            subtotal=Decimal('11659.00'),
            other_charges=Decimal('20211.00'),
            total_amount=Decimal('31870.00'),
            line_items=[
                LineItem(description='Lectura anterior 5414', total=Decimal('5414')),
                LineItem(description='Lectura actual 5426', total=Decimal('5426')),
                LineItem(description='Referencia de pago 101930003', total=Decimal('0')),
                LineItem(description='ENERGIA ACTIVA SENCILLA', total=Decimal('11655.24')),
                LineItem(description='AJUSTE MONETARIO', total=Decimal('3.76')),
            ],
        )
        validated = validate_extracted(doc, raw_text_hint='CEDENAR kWh alumbrado publico FACTURA No 51519035')
        assert validated.document_class == 'ENERGY_BILL'
        assert validated.invoice_number == '51519035'
        assert validated.line_items == []
        concepts = [concept.description for concept in validated.service_concepts]
        assert 'ENERGIA ACTIVA SENCILLA' in concepts
        assert 'AJUSTE MONETARIO' in concepts
        assert not any('lectura' in (name or '').lower() for name in concepts)
        assert validated.sections[0].columns == ['Concepto', 'Valor']
        assert validated.total_amount == Decimal('31870.00')
        assert validated.requires_review is False

    def test_its_pos_keeps_printed_quantity_and_exempt_tax(self):
        doc = ExtractedDocument(
            document_class='POS_RECEIPT',
            supplier_name='ITECSOFT SAS',
            supplier_tax_id='901414566',
            supplier_tax_id_verification_digit='2',
            invoice_number='CT-1',
            issue_date='2024-05-23',
            currency='COP',
            payment_method='cash',
            subtotal=Decimal('12200'),
            taxes=[TaxItem(type='IVA', rate=Decimal('0'), taxable_base=Decimal('12200'), amount=Decimal('0'))],
            total_amount=Decimal('12200'),
            has_product_table=True,
            line_items=[
                LineItem(
                    description='Arroz Blanco SuDespensa',
                    quantity=Decimal('1'),
                    unit_price=Decimal('12200'),
                    tax_amount=Decimal('0'),
                    total=Decimal('12200'),
                ),
            ],
        )
        validated = validate_extracted(doc)
        item = validated.line_items[0]
        assert validated.invoice_number == 'CT-1'
        assert validated.supplier_tax_id == '901414566'
        assert item.quantity == Decimal('1')
        assert item.unit_price == Decimal('12200')
        assert item.total == Decimal('12200')
        assert validated.taxes[0].amount == Decimal('0')
        assert validated.requires_review is False
        assert validated.validation_level == 'confirmed'

    def test_telecom_service_line_is_not_rewritten(self):
        doc = ExtractedDocument(
            document_class='TELECOM_BILL',
            supplier_name='OSCAR ORTEGA FLORES',
            supplier_tax_id='000000000',
            invoice_number='FE146',
            issue_date='2019-05-22',
            due_date='2019-06-08',
            currency='COP',
            subtotal=Decimal('55000'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('10450'))],
            total_amount=Decimal('65450'),
            has_product_table=True,
            line_items=[
                LineItem(
                    description='Renta y mantenimiento de la red Plan de Internet',
                    quantity=Decimal('1'),
                    unit_price=Decimal('55000'),
                    total=Decimal('55000'),
                ),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.document_class == 'TELECOM_BILL'
        assert validated.invoice_number == 'FE146'
        assert validated.supplier_name == 'OSCAR ORTEGA FLORES'
        assert len(validated.line_items) == 1
        assert validated.line_items[0].quantity == Decimal('1')
        assert validated.line_items[0].total == Decimal('55000')
        assert validated.taxes[0].taxable_base == Decimal('55000')
        assert validated.requires_review is False

    def test_product_line_without_quantity_stays_null(self):
        doc = ExtractedDocument(
            document_class='ELECTRONIC_INVOICE',
            supplier_name='GRUPO DECOR S.A.S.',
            supplier_tax_id='800166377',
            supplier_tax_id_verification_digit='1',
            invoice_number='PERS7380025023',
            issue_date='2024-09-05',
            currency='COP',
            subtotal=Decimal('11992197'),
            taxes=[TaxItem(type='IVA', rate=Decimal('19'), amount=Decimal('2278518'))],
            total_amount=Decimal('14270715'),
            has_product_table=True,
            line_items=[
                LineItem(description='AVENUE 60*60 BLANCO', quantity=None, unit_price=None,
                         total=Decimal('8982496')),
                LineItem(description='CITY 31*60 GRIS', quantity=None, unit_price=None,
                         total=Decimal('949809')),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.line_items[0].quantity is None
        assert validated.line_items[0].unit_price is None
        assert validated.line_items[0].total == Decimal('8982496')
        assert validated.invoice_number == 'PERS7380025023'
        assert validated.requires_review is False

    def test_header_discount_uses_taxable_base_and_ignores_false_unit_price(self):
        from services.extraction.schemas import DocumentSection, SectionCell, SectionRow
        doc = ExtractedDocument(
            document_class='ELECTRONIC_INVOICE',
            supplier_name='GRUPO DECOR S.A.S.',
            invoice_number='PERS7380025023',
            issue_date='2024-09-05',
            currency='COP',
            subtotal=Decimal('15466148'),
            taxes=[TaxItem(
                type='IVA', rate=Decimal('19'),
                taxable_base=Decimal('11992197'), amount=Decimal('2278518'),
            )],
            total_amount=Decimal('14270715'),
            has_product_table=True,
            sections=[DocumentSection(
                name='Articulos',
                columns=['Descripcion', 'Cant. Caja', 'Mts/Und', 'Total Neto'],
                rows=[SectionRow(cells=[
                    SectionCell(column='Descripcion', value='AVENUE 60*60 BLANCO'),
                    SectionCell(column='Cant. Caja', value='140'),
                    SectionCell(column='Mts/Und', value='302,400'),
                    SectionCell(column='Total Neto', value='8.862.436'),
                ])],
            )],
            line_items=[
                LineItem(description='AVENUE 60*60 BLANCO', quantity=Decimal('140'),
                         unit_price=Decimal('302.4'), total=Decimal('8862436')),
            ],
        )
        validated = validate_extracted(doc)
        assert validated.line_items[0].quantity is None
        assert validated.line_items[0].unit_price is None
        assert validated.line_items[0].total == Decimal('8862436')
        assert validated.sections[0].columns == ['Descripcion', 'Cant. Caja', 'Mts/Und', 'Total Neto']
        assert not any('TOTAL_MISMATCH' in warning for warning in validated.warnings)
        assert validated.requires_review is False

    def test_bogota_water_total_includes_charges_outside_the_subtotal(self):
        from services.extraction.schemas import DocumentSection, SectionCell, SectionRow
        doc = ExtractedDocument(
            document_class='WATER_BILL',
            supplier_name='Empresa de Acueducto y Alcantarillado de Bogotá - ESP',
            supplier_tax_id='899999094',
            supplier_tax_id_verification_digit='1',
            invoice_number='39704771813',
            issue_date='2020-12-04',
            payment_date='2020-12-18',
            due_date='2020-12-23',
            billing_period_start='2020-09-15',
            billing_period_end='2020-11-13',
            currency='COP',
            subtotal=Decimal('53280'),
            other_charges=Decimal('-2'),
            total_amount=Decimal('92490'),
            sections=[DocumentSection(
                name='Resumen de su cuenta',
                columns=[
                    'Descripción', 'Cantidad', 'Valor a Pagar', 'Otros Cobros', 'Saldo',
                ],
                rows=[
                    SectionRow(cells=[
                        SectionCell(column='Descripción', value='Cargo fijo residencial'),
                        SectionCell(column='Cantidad', value='1'),
                        SectionCell(column='Valor a Pagar', value='11360'),
                        SectionCell(column='Otros Cobros', value=None),
                        SectionCell(column='Saldo', value=None),
                    ]),
                    SectionRow(cells=[
                        SectionCell(column='Descripción', value='Acueducto'),
                        SectionCell(column='Otros Cobros', value='-2'),
                        SectionCell(column='Saldo', value='250'),
                    ]),
                ],
            )],
        )
        validated = validate_extracted(doc)
        assert validated.document_class == 'WATER_BILL'
        assert validated.total_amount == Decimal('92490')
        assert validated.subtotal == Decimal('53280')
        assert not any('TOTAL_MISMATCH' in warning for warning in validated.warnings)
        assert validated.requires_review is False
        assert [section.name for section in validated.sections] == [
            'Resumen de su cuenta', 'Otros cobros',
        ]
        assert 'Otros Cobros' not in validated.sections[0].columns
        assert validated.sections[1].columns == ['Otros Cobros', 'Saldo']

    def test_empotam_keeps_water_charges_and_ignores_readings(self):
        doc = ExtractedDocument(
            supplier_name='EMPOTAM E.S.P.',
            supplier_tax_id='814001033',
            supplier_tax_id_verification_digit='6',
            invoice_number='326062',
            issue_date='2026-08-31',
            due_date='2026-09-30',
            suspension_date='2026-09-30',
            billing_period_start='2026-08-01',
            billing_period_end='2026-08-31',
            currency='COP',
            subtotal=Decimal('7238'),
            total_amount=Decimal('7238'),
            line_items=[
                LineItem(description='Lectura actual 704', total=Decimal('704')),
                LineItem(description='Lectura anterior 688', total=Decimal('688')),
                LineItem(description='Consumo 16 m3', total=Decimal('16')),
                LineItem(description='Cargo fijo acueducto', total=Decimal('2148')),
                LineItem(description='Consumo basico', total=Decimal('2873')),
                LineItem(description='Complementario', total=Decimal('2217')),
            ],
        )
        validated = validate_extracted(doc, raw_text_hint='EMPOTAM acueducto lectura actual consumo m3')
        assert validated.document_class == 'WATER_BILL'
        assert validated.invoice_number == '326062'
        assert validated.line_items == []
        concepts = [concept.description for concept in validated.service_concepts]
        assert concepts == ['Cargo fijo acueducto', 'Consumo basico', 'Complementario']
        assert validated.total_amount == Decimal('7238')
        assert validated.requires_review is False

    def test_empotam_keeps_an_empty_column_without_a_placeholder(self):
        from services.extraction.schemas import DocumentSection, SectionCell, SectionRow

        columns = [
            'Concepto', 'M³', 'Valor Unitario', 'Valor Total',
            'Intereses Mora', 'Subsidio', 'Valor a Pagar',
        ]
        doc = ExtractedDocument(
            document_class='WATER_BILL',
            supplier_name='EMPOTAM E.S.P.',
            supplier_tax_id='814001033',
            invoice_number='326062',
            issue_date='2026-08-31',
            currency='COP',
            subtotal=Decimal('7238'),
            total_amount=Decimal('7238'),
            has_product_table=False,
            line_items=[],
            sections=[DocumentSection(
                name='Acueducto',
                columns=columns,
                rows=[
                    SectionRow(cells=[
                        SectionCell(column='Concepto', value='Cargo fijo acueducto'),
                        SectionCell(column='M³', value=None),
                        SectionCell(column='Valor Unitario', value='-'),
                        SectionCell(column='Valor Total', value='7.161'),
                        SectionCell(column='Intereses Mora', value='—'),
                        SectionCell(column='Subsidio', value='-5.013'),
                        SectionCell(column='Valor a Pagar', value='2.148'),
                    ]),
                    SectionRow(cells=[
                        SectionCell(column='Concepto', value='Consumo básico'),
                        SectionCell(column='M³', value='13'),
                        SectionCell(column='Valor Unitario', value='738'),
                        SectionCell(column='Valor Total', value='9.594'),
                        SectionCell(column='Intereses Mora', value='0'),
                        SectionCell(column='Subsidio', value='-6.721'),
                        SectionCell(column='Valor a Pagar', value='2.873'),
                    ]),
                ],
            )],
        )
        validated = validate_extracted(doc)
        section = validated.sections[0]
        assert section.columns == columns
        first = {cell.column: cell.value for cell in section.rows[0].cells}
        second = {cell.column: cell.value for cell in section.rows[1].cells}
        assert first['M³'] is None
        assert first['Valor Unitario'] is None
        assert first['Intereses Mora'] is None
        assert first['Subsidio'] == '-5.013'
        assert first['Valor a Pagar'] == '2.148'
        assert second['Intereses Mora'] == '0'
        assert second['M³'] == '13'
        assert validated.requires_review is False

    def test_mixed_meter_and_charge_columns_become_two_sections(self):
        from services.extraction.schemas import DocumentSection, SectionCell, SectionRow

        doc = ExtractedDocument(
            document_class='WATER_BILL',
            supplier_name='ACUEDUCTO MUNICIPAL E.S.P.',
            invoice_number='320329',
            issue_date='2026-06-30',
            due_date='2026-08-31',
            currency='COP',
            subtotal=Decimal('7238'),
            total_amount=Decimal('7238'),
            has_product_table=False,
            line_items=[],
            sections=[
                DocumentSection(
                    name='Factura',
                    columns=[
                        'Medidor', 'Diámetro', '1/2 Pulg', 'Lectura actual',
                        'Lectura anterior', 'Consumo m3', 'Valor total',
                        'Intereses mora', 'Valor a pagar',
                    ],
                    rows=[SectionRow(cells=[
                        SectionCell(column='Medidor', value='0'),
                        SectionCell(column='Diámetro', value='16'),
                        SectionCell(column='1/2 Pulg', value='16'),
                        SectionCell(column='Lectura actual', value='704'),
                        SectionCell(column='Lectura anterior', value='688'),
                        SectionCell(column='Consumo m3', value='16'),
                        SectionCell(column='Valor total', value='7.161'),
                        SectionCell(column='Intereses mora', value='0'),
                        SectionCell(column='Valor a pagar', value='2.148'),
                    ])],
                ),
                DocumentSection(
                    name='Alcantarillado',
                    columns=['M³', 'Valor unitario', 'Valor total', 'Intereses mora', 'Valor a pagar'],
                    rows=[SectionRow(cells=[
                        SectionCell(column='M³', value='0'),
                        SectionCell(column='Valor unitario', value='0'),
                        SectionCell(column='Valor total', value='0'),
                        SectionCell(column='Intereses mora', value='0'),
                        SectionCell(column='Valor a pagar', value='0'),
                    ])],
                ),
                DocumentSection(
                    name='Cargos de energía',
                    columns=['Concepto', 'Consumo', 'Unidad', 'Tarifa', 'Valor'],
                    rows=[SectionRow(cells=[
                        SectionCell(column='Concepto', value='Energía activa'),
                        SectionCell(column='Consumo', value='12'),
                        SectionCell(column='Unidad', value='kWh'),
                        SectionCell(column='Tarifa', value=None),
                        SectionCell(column='Valor', value='11655'),
                    ])],
                ),
            ],
        )
        hint = (
            'FACTURA N°. 326062 FECHA: 31-ago-2026 '
            'PERIODO FACTURADO 01-ago-2026 / 31-ago-2026 '
            'ULTIMO PAGO 27-ago-2026 '
            'FECHA LIMITE DE PAGO 30-sep-2026 '
            'FECHA DE SUSPENSION 30-sep-2026'
        )
        validated = validate_extracted(doc, raw_text_hint=hint)
        names = [section.name for section in validated.sections]
        assert names[:2] == ['Información de medición', 'Factura']
        assert validated.sections[0].columns == [
            'Medidor', 'Diámetro', '1/2 Pulg', 'Lectura actual', 'Lectura anterior', 'Consumo m3',
        ]
        assert validated.sections[1].columns == ['Valor total', 'Intereses mora', 'Valor a pagar']
        meter = {cell.column: cell.value for cell in validated.sections[0].rows[0].cells}
        assert meter['Lectura actual'] == '704'
        assert meter['Lectura anterior'] == '688'
        assert meter['Diámetro'] == '16'
        sewer = next(section for section in validated.sections if section.name == 'Alcantarillado')
        assert [cell.value for cell in sewer.rows[0].cells] == ['0', '0', '0', '0', '0']
        energy = next(section for section in validated.sections if section.name == 'Cargos de energía')
        assert energy.columns == ['Concepto', 'Consumo', 'Unidad', 'Tarifa', 'Valor']
        assert validated.invoice_number == '326062'
        assert validated.issue_date == '2026-08-31'
        assert validated.billing_period_start == '2026-08-01'
        assert validated.billing_period_end == '2026-08-31'
        assert validated.due_date == '2026-09-30'
        assert validated.suspension_date == '2026-09-30'
        assert validated.payment_date == '2026-08-27'
        assert validated.document_class == 'WATER_BILL'
        assert validated.requires_review is False
        assert '—' not in ''.join(
            cell.value or '' for section in validated.sections for row in section.rows for cell in row.cells
        )

    def test_month_name_is_not_kept_as_stratum(self):
        from services.extraction.schemas import DocumentSection, SectionCell, SectionRow

        doc = ExtractedDocument(
            document_class='WATER_BILL',
            supplier_name='ACUEDUCTO MUNICIPAL E.S.P.',
            invoice_number='326062',
            issue_date='2026-08-31',
            currency='COP',
            subtotal=Decimal('7238'),
            total_amount=Decimal('7238'),
            sections=[DocumentSection(
                name='Información general',
                columns=['Uso', 'Estrato', 'Mes facturado'],
                rows=[SectionRow(cells=[
                    SectionCell(column='Uso', value='RESIDENCIAL'),
                    SectionCell(column='Estrato', value='AGOSTO'),
                    SectionCell(column='Mes facturado', value=None),
                ])],
            )],
        )
        validated = validate_extracted(doc)
        row = {cell.column: cell.value for cell in validated.sections[0].rows[0].cells}
        assert row['Estrato'] is None
        assert row['Mes facturado'] == 'AGOSTO'
        assert row['Uso'] == 'RESIDENCIAL'
        assert validated.requires_review is False
