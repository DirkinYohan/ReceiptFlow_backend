import logging
from typing import Optional

from django.conf import settings
from google.api_core.client_options import ClientOptions

logger = logging.getLogger(__name__)


class GoogleVisionClient:
    def __init__(self):
        self._client = None
        credentials_path = settings.GOOGLE_APPLICATION_CREDENTIALS
        project_id = settings.GOOGLE_CLOUD_PROJECT_ID
        self._configured = bool(credentials_path and project_id)
        self._credentials_path = credentials_path or ''
        self._project_id = project_id or ''
        self._timeout = getattr(settings, 'GOOGLE_VISION_TIMEOUT', 60)

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._configured:
            raise RuntimeError(
                'Google Cloud Vision API is not configured. '
                'Set GOOGLE_APPLICATION_CREDENTIALS and GOOGLE_CLOUD_PROJECT_ID in your .env file.'
            )
        try:
            from google.cloud import vision  # type: ignore
            if self._credentials_path:
                self._client = vision.ImageAnnotatorClient.from_service_account_file(
                    self._credentials_path,
                    client_options=ClientOptions(
                        quota_project_id=self._project_id or None,
                    ),
                )
            else:
                self._client = vision.ImageAnnotatorClient()
        except ImportError as exc:
            raise ImportError(
                'google-cloud-vision is not installed. '
                'Please run: pip install google-cloud-vision'
            ) from exc
        return self._client

    @property
    def is_configured(self) -> bool:
        return self._configured

    @property
    def timeout(self) -> int:
        return self._timeout

    def _image_from_bytes(self, content: bytes):
        from google.cloud import vision  # type: ignore
        return vision.Image(content=content)

    @staticmethod
    def _read_image_bytes(file_path: str) -> bytes:
        with open(file_path, 'rb') as fh:
            return fh.read()

    def detect_text(self, image_content: bytes) -> dict:
        client = self._get_client()
        image = self._image_from_bytes(image_content)
        response = client.text_detection(image=image, timeout=self._timeout)
        annotations = response.text_annotations
        full_text = ''
        lines = []
        words = []
        if annotations:
            full_text = annotations[0].description
            for annotation in annotations[1:]:
                words.append({
                    'text': annotation.description,
                    'bounding_poly': [
                        (v.x, v.y) for v in getattr(annotation.bounding_poly, 'vertices', [])
                    ],
                })
            for line in full_text.splitlines():
                if line.strip():
                    lines.append(line.strip())
        return {
            'text': full_text,
            'lines': lines,
            'words': words,
            'locale': getattr(annotations[0], 'locale', '') if annotations else '',
            'raw': type(response).__name__,
        }

    def detect_document_text(self, image_content: bytes) -> dict:
        client = self._get_client()
        image = self._image_from_bytes(image_content)
        response = client.document_text_detection(image=image, timeout=self._timeout)
        full_text = ''
        pages = []
        lines = []
        words = []
        blocks_text = []
        if response.full_text_annotation:
            full_text = response.full_text_annotation.text
            for page in response.full_text_annotation.pages:
                page_data = {
                    'width': page.width,
                    'height': page.height,
                    'confidence': getattr(page, 'confidence', None),
                    'languages': [
                        lang.language_code
                        for block in page.blocks
                        for par in block.paragraphs
                        for word in par.words
                        for lang in (getattr(word, 'detected_languages', []) or [])
                    ] or [],
                    'blocks': [],
                }
                for block in page.blocks:
                    block_text_parts = []
                    for paragraph in block.paragraphs:
                        for word_obj in paragraph.words:
                            word_text = ''.join(symbol.text for symbol in word_obj.symbols)
                            page_words = {
                                'text': word_text,
                                'confidence': getattr(word_obj, 'confidence', None),
                                'bounding_poly': [
                                    (v.x, v.y) for v in getattr(word_obj.bounding_box, 'vertices', [])
                                ],
                            }
                            words.append(page_words)
                            block_text_parts.append(word_text)
                    block_content = ' '.join(block_text_parts).strip()
                    if block_content:
                        blocks_text.append(block_content)
                        page_data['blocks'].append({
                            'text': block_content,
                            'type': getattr(block, 'block_type', None),
                            'confidence': getattr(block, 'confidence', None),
                        })
                pages.append(page_data)
                lines = [line.strip() for line in full_text.splitlines() if line.strip()]
        return {
            'text': full_text,
            'lines': lines,
            'words': words,
            'blocks': blocks_text,
            'pages': pages,
            'locale': (
                pages[0]['languages'][0] if pages and pages[0]['languages'] else ''
            ),
        }

    def extract_receipt_fields(self, image_content: bytes) -> dict:
        ocr = self.detect_document_text(image_content)
        full_text = ocr.get('text', '')
        lines = ocr.get('lines', [])
        words = ocr.get('words', [])
        blocks = ocr.get('blocks', [])
        return {
            'text': full_text,
            'lines': lines,
            'words': words,
            'blocks': blocks,
        }


vision_client = GoogleVisionClient()
