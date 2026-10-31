import io
import logging
import os
from typing import Optional

from services.google_vision.client import vision_client
from services.ocr.base import OCRResult, BaseOCRService, registry

logger = logging.getLogger(__name__)


def _is_pdf(file_path: str) -> bool:
    ext = os.path.splitext(file_path)[1].lower().lstrip('.')
    return ext == 'pdf'


def _pdf_to_images(file_path: str, max_pages: int = 3):
    try:
        from pdf2image import convert_from_path  # type: ignore
    except ImportError:
        logger.debug('pdf2image not installed, falling back to raw Vision for PDF.')
        return []
    try:
        images = convert_from_path(file_path, first_page=1, last_page=max_pages, dpi=300)
        buffers = []
        for image in images:
            buf = io.BytesIO()
            image.save(buf, format='PNG')
            buffers.append(buf.getvalue())
        return buffers
    except Exception as exc:
        logger.warning('pdf2image conversion failed for %s: %s', file_path, exc)
        return []


class GoogleVisionOCRService(BaseOCRService):
    name = 'google_vision'

    def extract(self, file_path: str) -> OCRResult:
        if not vision_client.is_configured:
            raise RuntimeError(
                'Google Cloud Vision API is not configured. '
                'Check GOOGLE_APPLICATION_CREDENTIALS and GOOGLE_CLOUD_PROJECT_ID environment variables.'
            )
        if not os.path.exists(file_path):
            raise FileNotFoundError(f'File not found: {file_path}')

        aggregated_lines = []
        aggregated_words = []
        aggregated_blocks = []
        texts = []

        if _is_pdf(file_path):
            page_buffers = _pdf_to_images(file_path)
            if not page_buffers:
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(file_path)
                    pdf_lines = []
                    for page in reader.pages[:5]:
                        extracted = page.extract_text() or ''
                        for line in extracted.splitlines():
                            if line.strip():
                                pdf_lines.append(line.strip())
                    if pdf_lines:
                        return OCRResult(
                            raw_text='\n'.join(pdf_lines),
                            lines=pdf_lines,
                            words=[{'text': w} for l in pdf_lines for w in l.split()],
                            blocks=[],
                            confidence=0.99,
                            extra={'source': 'pypdf-text-layer', 'pages': len(reader.pages)},
                        )
                except Exception as pypdf_exc:
                    logger.debug('pypdf extraction failed: %s', pypdf_exc)

                with open(file_path, 'rb') as fh:
                    content = fh.read()
                result = vision_client.detect_document_text(content)
                return OCRResult(
                    raw_text=result.get('text', ''),
                    lines=result.get('lines', []),
                    words=result.get('words', []),
                    blocks=result.get('blocks', []),
                    confidence=None,
                    extra={'source': 'pdf-raw', 'pages': 1},
                )
            for idx, buffer in enumerate(page_buffers, start=1):
                try:
                    result = vision_client.detect_document_text(buffer)
                    texts.append(result.get('text', ''))
                    aggregated_lines.extend(result.get('lines', []))
                    aggregated_words.extend(result.get('words', []))
                    aggregated_blocks.extend(result.get('blocks', []))
                except Exception as exc:
                    logger.warning('Google Vision failed for PDF page %s: %s', idx, exc)
            return OCRResult(
                raw_text='\n'.join(t for t in texts if t),
                lines=aggregated_lines,
                words=aggregated_words,
                blocks=aggregated_blocks,
                confidence=None,
                extra={'source': 'pdf-pages', 'pages': len(page_buffers)},
            )

        with open(file_path, 'rb') as fh:
            content = fh.read()
        try:
            result = vision_client.detect_document_text(content)
        except Exception:
            logger.info('document_text_detection failed; falling back to text_detection.')
            result = vision_client.detect_text(content)
        return OCRResult(
            raw_text=result.get('text', ''),
            lines=result.get('lines', []),
            words=result.get('words', []),
            blocks=result.get('blocks', []),
            confidence=None,
            extra={'source': 'image'},
        )


registry.register(GoogleVisionOCRService.name, GoogleVisionOCRService)
