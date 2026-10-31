import logging

from django.conf import settings


from .google_vision_service import GoogleVisionOCRService
from .gemini_service import GeminiOCRService
from .base import OCRResult, BaseOCRService, registry as _registry

logger = logging.getLogger(__name__)

__all__ = [
    'OCRResult',
    'BaseOCRService',
    'GoogleVisionOCRService',
    'GeminiOCRService',
    'extract_ocr',
    'registry',
]

registry = _registry


def extract_ocr(file_path: str, service_name: str = None) -> OCRResult:
    if not service_name:
        service_name = getattr(settings, 'OCR_SERVICE', 'gemini')
    svc: BaseOCRService = registry.get(service_name)
    return svc.extract(file_path)

