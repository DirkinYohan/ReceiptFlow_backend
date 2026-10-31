from __future__ import annotations

import json
import logging
import os
import re
from decimal import Decimal
from typing import Optional

from django.conf import settings

from services.extraction.schemas import ExtractedDocument
from services.money import parse_decimal

logger = logging.getLogger(__name__)

MIME_TYPE_MAP = {
    'pdf': 'application/pdf',
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'webp': 'image/webp',
    'gif': 'image/gif',
    'bmp': 'image/bmp',
    'tiff': 'image/tiff',
    'tif': 'image/tiff',
}

CANDIDATE_MODELS = [
    'gemini-3.5-flash',
    'gemini-3.5-flash-lite',
    'gemini-3.8-flash',
]

_NUMBER = {
    "type": "number",
    "nullable": True,
    "description": (
        "Número JSON sin separador de miles ni símbolo de moneda. "
        "125.000,50 se escribe 125000.5. null si no está impreso. No calcules cifras ausentes."
    ),
}

from services.extraction.schemas import EXPENSE_CATEGORIES

GEMINI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "nullable": True,
            "description": "invoice (factura comercial o electrónica), receipt (recibo), ticket (tirilla POS) u other.",
        },
        "document_kind": {
            "type": "string",
            "nullable": True,
            "description": (
                "commercial_invoice, electronic_invoice, pos, public_service, receipt u other. "
                "Cedenar y demás servicios públicos: public_service. DIAN con CUFE: electronic_invoice. Tirilla: pos."
            ),
        },
        "document_class": {
            "type": "string",
            "nullable": True,
            "description": (
                "Clasifica antes de leer montos: SALES_INVOICE, ELECTRONIC_INVOICE, POS_RECEIPT, "
                "UTILITY_BILL, ENERGY_BILL, WATER_BILL, GAS_BILL, TELECOM_BILL, RECEIPT, "
                "SERVICE_INVOICE u OTHER."
            ),
        },
        "has_product_table": {
            "type": "boolean",
            "nullable": True,
            "description": (
                "true solo si hay una tabla de productos o servicios. "
                "false si el documento solo tiene lecturas, fechas, NIT, CUFE, totales u observaciones."
            ),
        },
        "supplier_name": {
            "type": "string",
            "nullable": True,
            "description": "Razón social del emisor. Nunca el cliente, adquiriente o suscriptor.",
        },
        "supplier_tax_id": {
            "type": "string",
            "nullable": True,
            "description": (
                "NIT del emisor, solo dígitos, sin DV. Solo si está rotulado NIT, RUT, C.C., RFC o Tax ID. "
                "No es el número de factura ni un teléfono. null si el emisor no muestra identificación."
            ),
        },
        "phone_numbers": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Todos los teléfonos impresos (emisor y cliente), tal como aparecen. Lista vacía si no hay.",
        },
        "supplier_tax_id_verification_digit": {
            "type": "string",
            "nullable": True,
            "description": "Un solo dígito de verificación del NIT del emisor, o null.",
        },
        "invoice_number": {
            "type": "string",
            "nullable": True,
            "description": (
                "Solo el consecutivo junto a Factura No, Factura N°, Número de factura, "
                "Factura de venta, Folio o Prefijo. Nunca NIT, CUFE, referencia, medidor ni fecha."
            ),
        },
        "issue_date": {
            "type": "string",
            "nullable": True,
            "description": "Fecha de emisión YYYY-MM-DD. null si esa etiqueta no existe.",
        },
        "issue_time": {"type": "string", "nullable": True, "description": "Hora de emisión HH:MM:SS o null."},
        "generation_date": {
            "type": "string",
            "nullable": True,
            "description": "Fecha de generación DIAN YYYY-MM-DD. null si no está rotulada.",
        },
        "due_date": {
            "type": "string",
            "nullable": True,
            "description": "Fecha de vencimiento YYYY-MM-DD. No la copies en issue_date.",
        },
        "billing_period": {
            "type": "string",
            "nullable": True,
            "description": "Periodo facturado, por ejemplo 2024-08. null si no está.",
        },
        "suspension_date": {
            "type": "string",
            "nullable": True,
            "description": "Fecha de suspensión YYYY-MM-DD. null si no está. No la copies en issue_date.",
        },
        "billing_period_start": {
            "type": "string",
            "nullable": True,
            "description": "Inicio del periodo facturado YYYY-MM-DD. null si no está.",
        },
        "billing_period_end": {
            "type": "string",
            "nullable": True,
            "description": "Fin del periodo facturado YYYY-MM-DD. null si no está.",
        },
        "payment_date": {
            "type": "string",
            "nullable": True,
            "description": "Fecha de pago oportuno YYYY-MM-DD. null si no está. No la copies en issue_date.",
        },
        "currency": {
            "type": "string",
            "nullable": True,
            "description": "COP por defecto en Colombia. Otra moneda solo si está impresa.",
        },
        "subtotal": _NUMBER,
        "other_charges": {
            "type": "number",
            "nullable": True,
            "description": (
                "Otros cargos o ajustes impresos que no son IVA, por ejemplo ajuste a la centena. "
                "Negativo si resta. null si no existe."
            ),
        },
        "withholdings_amount": {
            "type": "number",
            "nullable": True,
            "description": "Suma de retenciones impresas. null si no hay.",
        },
        "taxes": {
            "type": "array",
            "description": "Solo impuestos impresos. Lista vacía si no hay.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "nullable": True, "description": "IVA, INC, RETEFUENTE, RETEICA, RETEIVA u OTRO."},
                    "rate": _NUMBER,
                    "taxable_base": {
                        "type": "number",
                        "nullable": True,
                        "description": (
                            "Base gravable de este impuesto. No uses el subtotal si el impuesto "
                            "solo aplica a algunas líneas. null si no está identificada."
                        ),
                    },
                    "amount": _NUMBER,
                },
            },
        },
        "total_amount": _NUMBER,
        "payment_method": {
            "type": "string",
            "nullable": True,
            "description": "cash, card, transfer, digital u other. null si no está. No es un producto.",
        },
        "cufe": {
            "type": "string",
            "nullable": True,
            "description": "CUFE o CUDE hexadecimal. null si no aparece. Nunca lo uses como invoice_number.",
        },
        "line_items": {
            "type": "array",
            "description": (
                "Vacío si has_product_table es false. Cada elemento es un producto o servicio de la tabla. "
                "Prohibido: fechas, lecturas, consumos, históricos, NIT, CUFE, referencias, direcciones, "
                "teléfonos, subtotales, impuestos, totales, observaciones y medios de pago."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "nullable": True},
                    "sku": {"type": "string", "nullable": True},
                    "quantity": _NUMBER,
                    "unit_price": _NUMBER,
                    "discount": _NUMBER,
                    "tax_rate": _NUMBER,
                    "tax_amount": _NUMBER,
                    "total": _NUMBER,
                },
            },
        },
        "service_concepts": {
            "type": "array",
            "description": (
                "Cargos de una factura de servicios públicos (acueducto, energía, gas). "
                "Lista vacía en facturas comerciales. No incluyas lecturas ni fechas."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "nullable": True},
                    "amount": _NUMBER,
                },
            },
        },
        "sections": {
            "type": "array",
            "description": (
                "Una entrada por cada tabla o bloque con título propio. "
                "No juntes dos tablas en un solo columns. "
                "Cada valor va en la columna cuyo encabezado está encima, en esa misma tabla. "
                "Celda vacía: null. Cero impreso: 0. "
                "No uses Cantidad ni Precio Unitario si el encabezado dice M³ o Valor Unitario."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "nullable": True},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "cells": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "column": {"type": "string", "nullable": True},
                                            "value": {
                                                "type": "string",
                                                "nullable": True,
                                                "description": (
                                                    "Texto impreso en la celda. null si la celda está vacía. "
                                                    "No escribas -, —, N/A ni 0 para rellenar un vacío. "
                                                    "Un cero impreso se conserva como 0."
                                                ),
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
        "suggested_category": {
            "type": "string",
            "nullable": True,
            "format": "enum",
            "enum": list(EXPENSE_CATEGORIES),
            "description": (
                "Categoría del gasto según el proveedor y lo comprado: Food (restaurantes, mercado, "
                "supermercado), Transportation (combustible, peajes, taxis, vuelos, taller), Technology "
                "(software, equipos, internet, telefonía), Health (droguería, médico, óptica), Home "
                "(servicios públicos, arriendo, ferretería, construcción), Entertainment, Education, "
                "Shopping (ropa, tiendas) u Other si ninguna aplica."
            ),
        },
        "requires_review": {
            "type": "boolean",
            "description": (
                "true solo si el total es ilegible o dos montos impresos se contradicen. "
                "false si la factura cuadra aunque haya varias fechas, lecturas o líneas exentas."
            ),
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Motivos breves de duda. Lista vacía si no hay.",
        },
    },
}

STRUCTURED_PROMPT = """Eres el motor de extracción de ReceiptFlow. Lee el documento y llena el esquema.
Si un dato no está impreso con una etiqueta clara, devuelve null. No completes huecos ni calcules totales que no aparezcan.

Clasifica document_kind:
- commercial_invoice: factura de venta con tabla de productos (obra, jardinería, comercio).
- electronic_invoice: factura electrónica DIAN, con CUFE/CUDE y prefijo de facturación.
- pos: tirilla o sistema POS.
- public_service: acueducto, energía, gas, aseo (Cedenar y similares).
- receipt: recibo de caja que no es factura ni tirilla.

has_product_table es true solo cuando hay una tabla o listado de productos/servicios
(descripción, cantidad, valor unitario, total de línea). En caso contrario es false y line_items es [].

En public_service, line_items es siempre []. Lectura anterior, lectura actual, consumo, periodo,
histórico, medidor, suspensión y cargo informativo no son productos.

No conviertas en line_items: fechas, NIT, CUFE, número de factura, referencia, orden, dirección,
teléfono, subtotal, IVA, INC, retenciones, total a pagar, efectivo, cambio, observaciones,
resolución DIAN, código de barras ni QR.

invoice_number sale únicamente del número impreso junto a "Factura No.", "Factura N°",
"Número de factura", "Factura de venta No." o "Nro Factura". El NIT, el DV, el CUFE, una referencia,
el número para pagos, el código del suscriptor, la ruta, la ficha catastral y una fecha nunca son el número.
Si el mismo número se repite en un recuadro de aseo, sigue siendo ese número, no otro distinto.
Si la foto está girada, léela en la orientación en la que el texto queda horizontal.

Primero recorre el documento por secciones, de arriba hacia abajo. Cada título de banda es una section distinta,
con sus columns y sus rows. No armes una sola tabla con columnas de dos bandas.
Ejemplos de bandas que deben quedar separadas cuando existan: información general, información de medición,
acueducto, alcantarillado, energía, alumbrado, otros conceptos, financiación y pagos.
Una celda recibe el valor que está debajo de su encabezado, en la misma fila y en la misma tabla.
Si esa celda está en blanco, el valor es null. No desplaces los números hacia la izquierda para llenar huecos.
Un histórico de varios meses es una fila de la sección de medición, no el diámetro ni la lectura.
La sección de medición no completa la tabla de cargos, y la tabla de cargos no completa la medición.
Si una sección está impresa, inclúyela aunque sus celdas estén en blanco o en cero. No la borres.
Un cero impreso es "0". Una celda en blanco es null.
El resumen de pagos (último pago, entidad, valor pagado, saldo, facturas a pagar) es otra section.
M³ no es Cantidad. Valor Unitario no es Precio Unitario. Usa el encabezado impreso.

supplier_tax_id es el NIT del emisor, no el del cliente. El dígito de verificación va aparte.
Si el valor a pagar es 0 porque el recibo ya muestra el pago aplicado (un concepto y su pago en negativo),
total_amount es lo cobrado antes del pago, no 0.
Un teléfono nunca es NIT: los teléfonos van en phone_numbers. Si el emisor no muestra NIT, supplier_tax_id es null.
supplier_name es el emisor, no el adquiriente ni el suscriptor.

Fechas, cada una en su campo y en YYYY-MM-DD. Si la etiqueta no está, null. No reutilices la primera fecha:
- issue_date: emisión, expedición o fecha de la factura. En un servicio público no uses la fecha de pago.
- generation_date: generación DIAN.
- payment_date: pago oportuno o fecha del último pago. No la uses como vencimiento.
- due_date: vencimiento o fecha límite de pago. No uses el fin del periodo ni el último pago.
- billing_period_start y billing_period_end: inicio y fin del periodo facturado.
- suspension_date: suspensión del servicio.
En información general, el estrato es el número de estrato. Un nombre de mes junto a ese campo es el mes facturado, no el estrato.
En agua, energía o gas, subtotal es el total del servicio visible (acueducto, alcantarillado, energía).
total_amount es el TOTAL A PAGAR. Puede ser mayor porque incluye aseo, terceros o el respaldo.
No fuerces subtotal = total. Una tabla a la izquierda y otra a la derecha son dos sections.
No mezcles lecturas, histórico de consumo ni medidor dentro de la tabla de cargos.
El subsidio y el aporte son columnas de esa tabla, no productos.

Montos en número JSON (840000, no 840.000). Cantidad, precio unitario y total de línea solo si
esa columna se lee con claridad; si no, null. No inventes cantidad 1 ni multipliques precio por
cantidad cuando ese producto supere el subtotal: en ese caso el precio no es unitario y el total
de la línea es null.
IVA, INC y retenciones van en taxes con type, rate, taxable_base y amount. taxable_base es la base
real del impuesto, no el subtotal, cuando solo algunas líneas están gravadas. Líneas exentas no
entran en esa base. Lista vacía si no hay impuesto.
Clasifica document_class antes de leer montos: SALES_INVOICE, ELECTRONIC_INVOICE, POS_RECEIPT,
UTILITY_BILL, ENERGY_BILL, WATER_BILL, GAS_BILL, TELECOM_BILL, RECEIPT, SERVICE_INVOICE u OTHER.
Energía es ENERGY_BILL, acueducto o alcantarillado es WATER_BILL, gas es GAS_BILL,
internet o telefonía es TELECOM_BILL.
En ENERGY_BILL, WATER_BILL, GAS_BILL y UTILITY_BILL, line_items es [] .
Los cargos van en sections, con las columnas impresas (por ejemplo Concepto y Valor).
Las lecturas y el consumo van en otra sección, no como productos.
Si una columna está impresa, déjala en columns aunque una fila, o todas, tengan la celda vacía.
Esa celda es null. No escribas "-", "—", "N/A" ni 0 para completar un vacío.
Un cero impreso se conserva como "0". Un subsidio negativo se conserva, por ejemplo "-5.013".
No inventes una columna que el documento no trae.
No inventes cantidad, precio unitario ni total de línea. Si esa columna no está, el valor es null.
Una factura comercial usa solo las columnas de su tabla: si hay Cantidad, Precio y Total, úsalas;
si solo hay Concepto y Valor, no rellenes las demás.
requires_review true solo si el total es ilegible o los montos impresos se contradicen.

Moneda COP si el documento es colombiano y no dice otra. requires_review true si la suma no cuadra,
sin modificar las cifras impresas.
"""


def _get_mime_type(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower().lstrip('.')
    return MIME_TYPE_MAP.get(ext, 'image/jpeg')


def _clean_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith('```'):
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            text = match.group(1).strip()
    return text


def _as_decimal(value) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value))
        except Exception:
            return None
    if isinstance(value, str):
        return parse_decimal(value, default_currency='COP')
    return None


def _sanitize_payload(data: dict) -> tuple[dict, list[str]]:
    """Drop values that would reject the whole document in Pydantic."""
    warnings: list[str] = []
    items = data.get('line_items')
    if items is None:
        data['line_items'] = []
    elif not isinstance(items, list):
        data['line_items'] = []
        warnings.append('LINE_ITEMS_DROPPED: line_items was not a list')
    else:
        cleaned = []
        cleared_quantity = False
        cleared_money = False
        for item in items:
            if not isinstance(item, dict):
                warnings.append('LINE_ITEM_DROPPED: entry was not an object')
                continue
            item = dict(item)
            quantity = _as_decimal(item.get('quantity'))
            if quantity is not None and quantity <= 0:
                item['quantity'] = None
                cleared_quantity = True
            for key in ('unit_price', 'discount', 'tax_amount', 'total'):
                amount = _as_decimal(item.get(key))
                if amount is not None and amount < 0:
                    item[key] = None
                    cleared_money = True
            cleaned.append(item)
        data['line_items'] = cleaned
        if cleared_quantity:
            warnings.append('LINE_ITEM_QUANTITY_CLEARED: non-positive quantity set to null')
        if cleared_money:
            warnings.append('LINE_ITEM_MONEY_CLEARED: negative amount set to null')
    if not isinstance(data.get('taxes'), list):
        data['taxes'] = []
    if not isinstance(data.get('warnings'), list):
        data['warnings'] = []
    return data, warnings


class GeminiStructuredExtractor:
    """Uses Gemini native structured output with a JSON schema.

    Falls back to a JSON prompt with Pydantic validation if response_schema is not
    supported by the current SDK/model. Always returns an ExtractedDocument.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self._api_key = api_key or getattr(settings, 'GEMINI_API_KEY', '') or os.environ.get('GEMINI_API_KEY', '')
        self._default_model = model or getattr(settings, 'GEMINI_MODEL', 'gemini-3.5-flash')

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def _build_models_to_try(self) -> list:
        models = [self._default_model]
        for candidate in CANDIDATE_MODELS:
            if candidate not in models:
                models.append(candidate)
        return models

    def _generation_config(self, types_module):
        try:
            return types_module.GenerateContentConfig(
                temperature=0,
                response_mime_type='application/json',
                response_schema=GEMINI_RESPONSE_SCHEMA,
            )
        except Exception:
            return None

    def _try_structured(self, client, model_name: str, file_part) -> Optional[ExtractedDocument]:
        try:
            from google.genai import types  # type: ignore
        except Exception:
            types = None

        kwargs = {
            'model': model_name,
            'contents': [file_part, STRUCTURED_PROMPT],
        }
        if types is not None:
            config = self._generation_config(types)
            if config is not None:
                kwargs['config'] = config

        try:
            response = client.models.generate_content(**kwargs)
        except Exception as exc:
            logger.info('Gemini structured call failed for model=%s exc=%s', model_name, exc)
            return None

        if not getattr(response, 'text', None):
            return None

        return self._parse_to_model(response.text, source='structured')

    def _try_text_json(self, client, model_name: str, file_part) -> Optional[ExtractedDocument]:
        schema_json = json.dumps(GEMINI_RESPONSE_SCHEMA, ensure_ascii=False)
        fallback_prompt = (
            STRUCTURED_PROMPT
            + "\n\nDevuelve ÚNICAMENTE JSON que cumpla este esquema:\n"
            + schema_json
            + "\nNo añadas texto, comentarios ni bloques ```. Solo el JSON."
        )
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[file_part, fallback_prompt],
            )
        except Exception as exc:
            logger.warning('Gemini text-json fallback failed for model=%s exc=%s', model_name, exc)
            return None

        if not getattr(response, 'text', None):
            return None
        return self._parse_to_model(response.text, source='text_json')

    def _parse_to_model(self, text: str, source: str) -> Optional[ExtractedDocument]:
        cleaned = _clean_json_text(text)
        try:
            data = json.loads(cleaned)
        except Exception as exc:
            logger.info('Could not parse Gemini JSON %s: %s', source, exc)
            return None
        if not isinstance(data, dict):
            return None

        data, extra_warnings = _sanitize_payload(data)
        model: Optional[ExtractedDocument] = None
        try:
            model = ExtractedDocument.model_validate(data)
        except Exception as exc:
            logger.info('Pydantic validation failed for Gemini %s output: %s', source, exc)
            salvaged = dict(data)
            salvaged['line_items'] = []
            salvaged['requires_review'] = True
            extra_warnings.append(f"GEMINI_SCHEMA_VALIDATION_FAILED: {exc.__class__.__name__}")
            try:
                model = ExtractedDocument.model_validate(salvaged)
            except Exception as second_exc:
                return ExtractedDocument(
                    requires_review=True,
                    warnings=[f"GEMINI_SCHEMA_VALIDATION_FAILED: {second_exc.__class__.__name__}"],
                )

        for warning in extra_warnings:
            model.add_warning(warning)
        if extra_warnings:
            model.requires_review = True
        return model

    def extract_file(self, file_path: str) -> ExtractedDocument:
        if not self.is_configured:
            return ExtractedDocument(
                requires_review=True,
                warnings=["GEMINI_API_KEY_NOT_CONFIGURED"],
            )
        if not os.path.exists(file_path):
            raise FileNotFoundError(f'File not found: {file_path}')

        try:
            from google import genai  # type: ignore
            from google.genai import types  # type: ignore
        except ImportError as exc:
            return ExtractedDocument(
                requires_review=True,
                warnings=[f"GOOGLE_GENAI_NOT_INSTALLED: {exc.__class__.__name__}"],
            )

        client = genai.Client(api_key=self._api_key)
        mime_type = _get_mime_type(file_path)

        with open(file_path, 'rb') as fh:
            file_bytes = fh.read()

        file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

        models_to_try = self._build_models_to_try()
        chosen_model: Optional[str] = None
        parsed: Optional[ExtractedDocument] = None
        last_error: Optional[str] = None

        for model_name in models_to_try:
            try:
                parsed = self._try_structured(client, model_name, file_part)
                if parsed is not None:
                    chosen_model = model_name
                    break
            except Exception as exc:
                last_error = f"{model_name}: {exc.__class__.__name__}"
                logger.warning('Structured extract failed on %s: %s', model_name, exc)
                continue

        if parsed is None:
            for model_name in models_to_try:
                try:
                    parsed = self._try_text_json(client, model_name, file_part)
                    if parsed is not None:
                        chosen_model = model_name
                        break
                except Exception as exc:
                    last_error = f"text-{model_name}: {exc.__class__.__name__}"
                    logger.warning('Text-json extract failed on %s: %s', model_name, exc)
                    continue

        if parsed is None:
            return ExtractedDocument(
                requires_review=True,
                warnings=["STRUCTURED_OUTPUT_FAILED: " + (last_error or "all models failed")],
            )

        if chosen_model:
            parsed.add_warning(f"GEMINI_MODEL_USED: {chosen_model}")

        return parsed
