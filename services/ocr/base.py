import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class OCRResult:
    raw_text: str = ''
    lines: list = field(default_factory=list)
    words: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    confidence: Optional[float] = None
    extra: dict = field(default_factory=dict)


class BaseOCRService:
    name = 'base'

    def extract(self, file_path: str) -> OCRResult:
        raise NotImplementedError('Las subclases deben implementar el método extract.')


class OCRServiceRegistry:
    _services: dict = {}

    @classmethod
    def register(cls, name: str, service_cls):
        cls._services[name] = service_cls

    @classmethod
    def get(cls, name: str) -> BaseOCRService:
        if name not in cls._services:
            raise KeyError(f'Servicio OCR "{name}" no registrado.')
        return cls._services[name]()

    @classmethod
    def available(cls) -> list:
        return list(cls._services.keys())


registry = OCRServiceRegistry
