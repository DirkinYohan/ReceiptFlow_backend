import json
import logging
import os

from django.conf import settings

from services.ocr.base import OCRResult, BaseOCRService, registry

from services.gemini_common import CANDIDATE_MODELS, MIME_TYPE_MAP  # noqa: F401
from services.gemini_common import clean_json_text as _clean_json_text
from services.gemini_common import get_mime_type as _get_mime_type

logger = logging.getLogger(__name__)


PROMPT = """Eres el motor de reconocimiento óptico de caracteres (OCR) y análisis documental inteligente de ReceiptFlow.
Tu misión es analizar la imagen o documento proporcionado (recibo, tirilla POS, factura comercial, factura electrónica o factura de servicios públicos colombiana) y extraer el texto verbatim y los datos que sí aparecen.

No inventes datos. Si un campo no está visible, usa null. No uses el NIT como número de factura.
Separa fecha de emisión, fecha de generación, fecha de vencimiento y periodo facturado; si una no está rotulada, null.
line_items solo con productos o servicios de una tabla clara. En servicios públicos (lecturas, consumos, históricos, medidor) line_items es [].
No conviertas fechas, NIT, CUFE, referencias, direcciones, teléfonos, impuestos, subtotales, totales, efectivo ni cambio en productos.
Montos como números JSON sin separador de miles (125000.5, no 125.000,50). No calcules un total que no esté impreso.

Responde ÚNICAMENTE con un objeto JSON válido (sin explicaciones previas ni bloques de texto adicionales, solo el JSON) con las siguientes propiedades:
{
  "raw_text": "Todo el texto del documento transcrito exactamente tal cual aparece, manteniendo saltos de línea y el orden de lectura",
  "document_type": "invoice" | "receipt" | "ticket",
  "document_number": "Número del documento o factura (ejemplo: FE-1234, POS-567, Factura No. 9982)",
  "supplier_name": "Nombre o razón social del emisor / establecimiento comercial",
  "supplier_nit": "NIT o número de identificación tributaria (solo dígitos, sin dígito de verificación ni guiones)",
  "supplier_verification_digit": "Dígito de verificación DIAN (0-9) si aparece, o null",
  "supplier_tax_id": "NIT completo con dígito de verificación si aplica (ejemplo: 900123456-7)",
  "supplier_address": "Dirección física del establecimiento",
  "supplier_phone": "Teléfono o celular de contacto",
  "supplier_email": "Correo electrónico de contacto",
  "supplier_website": "Sitio web del emisor",
  "issue_date": "Fecha de emisión en formato YYYY-MM-DD",
  "issue_time": "Hora de emisión en formato HH:MM:SS (o null si no figura)",
  "cufe": "Código Único de Factura Electrónica (CUFE) si es factura electrónica colombiana (cadena hexadecimal de 64 a 100 caracteres)",
  "currency": "COP" | "USD" | "EUR" | "MXN" (predeterminado COP si es Colombia),
  "subtotal_amount": número decimal o null,
  "tax_amount": número decimal con el total de impuestos o null,
  "iva_amount": número decimal con el total de IVA (19%, 5%, etc.) o null,
  "discount_amount": número decimal de descuento o 0,
  "tip_amount": número decimal de propina voluntaria o 0,
  "total_amount": número decimal del monto total a pagar,
  "payment_method": "cash" | "card" | "transfer" | "digital" | "other",
  "suggested_category": "Alimentación" | "Transporte" | "Servicios" | "Tecnología" | "Suministros" | "Salud" | "Entretenimiento" | "Hospedaje" | "Otros",
  "line_items": []
}

Si hay productos, cada elemento de line_items usa: description (string), quantity (número o null), unit_price (número o null), tax_rate (número o null), total_amount (número o null). Si no hay tabla de productos, line_items queda [].
"""


class GeminiOCRService(BaseOCRService):
    name = 'gemini'

    def __init__(self):
        self._api_key = getattr(settings, 'GEMINI_API_KEY', '') or os.environ.get('GEMINI_API_KEY', '')
        self._default_model = getattr(settings, 'GEMINI_MODEL', 'gemini-3.5-flash')

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def extract(self, file_path: str) -> OCRResult:
        if not self._api_key:
            raise RuntimeError(
                'GEMINI_API_KEY is not configured in settings or environment. '
                'Please set GEMINI_API_KEY in your .env file.'
            )
        if not os.path.exists(file_path):
            raise FileNotFoundError(f'File not found: {file_path}')

        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError(
                'google-genai is not installed. Run "pip install google-genai" to enable Gemini OCR.'
            ) from exc

        client = genai.Client(api_key=self._api_key)
        mime_type = _get_mime_type(file_path)

        with open(file_path, 'rb') as fh:
            file_bytes = fh.read()

        file_part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)

        models_to_try = [self._default_model]
        for m in CANDIDATE_MODELS:
            if m not in models_to_try:
                models_to_try.append(m)

        last_error = None
        response_text = ''
        chosen_model = self._default_model

        for model_name in models_to_try:
            try:
                logger.info('Calling Gemini OCR with model: %s on %s', model_name, file_path)
                response = client.models.generate_content(
                    model=model_name,
                    contents=[file_part, PROMPT],
                )
                if response and response.text:
                    response_text = response.text
                    chosen_model = model_name
                    break
            except Exception as exc:
                logger.warning('Gemini model %s failed: %s', model_name, exc)
                last_error = exc
                continue

        if not response_text:
            raise RuntimeError(f'All Gemini OCR models failed. Last error: {last_error}')

        parsed = {}
        cleaned = _clean_json_text(response_text)
        try:
            parsed = json.loads(cleaned)
        except Exception as json_err:
            logger.warning('Failed to parse Gemini response as JSON: %s. Using raw response text.', json_err)
            parsed = {}

        raw_text = parsed.get('raw_text') if isinstance(parsed, dict) else ''
        if not raw_text:
            raw_text = response_text

        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        words = [{'text': w} for line in lines for w in line.split()]

        return OCRResult(
            raw_text=raw_text,
            lines=lines,
            words=words,
            blocks=[],
            confidence=0.98,
            extra={
                'source': 'gemini',
                'model': chosen_model,
                'gemini_structured': parsed if isinstance(parsed, dict) else {},
            },
        )


registry.register(GeminiOCRService.name, GeminiOCRService)
