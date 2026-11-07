import os
import re

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


def get_mime_type(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower().lstrip('.')
    return MIME_TYPE_MAP.get(ext, 'image/jpeg')


def clean_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith('```'):
        match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if match:
            text = match.group(1).strip()
    return text
