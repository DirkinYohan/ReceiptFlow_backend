from django.core.files.uploadedfile import UploadedFile
from django.core.exceptions import ValidationError
from django.conf import settings
from rest_framework import serializers
import os


ALLOWED_IMAGE_MIME_MAP = {
    'jpg': ['image/jpeg'],
    'jpeg': ['image/jpeg'],
    'png': ['image/png'],
    'gif': ['image/gif'],
    'bmp': ['image/bmp'],
    'tiff': ['image/tiff'],
    'pdf': ['application/pdf'],
    'webp': ['image/webp'],
}


def get_file_extension(filename: str) -> str:
    _, ext = os.path.splitext(filename)
    return ext.lower().lstrip('.')


def validate_uploaded_file(
    file_obj: UploadedFile,
    allowed_extensions=None,
    max_size_mb: int | None = None,
) -> None:
    if allowed_extensions is None:
        allowed_extensions = settings.ALLOWED_DOCUMENT_EXTENSIONS
    if max_size_mb is None:
        max_size_mb = settings.MAX_UPLOAD_SIZE_MB

    if not file_obj or not file_obj.name:
        raise ValidationError('No se ha proporcionado ningún archivo.')

    ext = get_file_extension(file_obj.name)
    if not ext:
        raise ValidationError('No se pudo determinar la extensión del archivo.')

    allowed_lower = [e.lower() for e in allowed_extensions]
    if ext not in allowed_lower:
        raise ValidationError(
            f'Tipo de archivo no permitido (.{ext}). Extensiones permitidas: {", ".join(allowed_lower)}.'
        )

    max_bytes = max_size_mb * 1024 * 1024
    size = getattr(file_obj, 'size', None)
    if size is None:
        if hasattr(file_obj, 'file'):
            try:
                file_obj.file.seek(0, os.SEEK_END)
                size = file_obj.file.tell()
                file_obj.file.seek(0)
            except Exception:
                size = 0
        else:
            size = 0
    if size and size > max_bytes:
        raise ValidationError(
            f'El archivo excede el tamaño máximo permitido ({max_size_mb}MB).'
        )

    # Magic bytes verification for allowed types
    if getattr(settings, 'STRICT_MIME_VERIFICATION', True):
        pos = file_obj.tell() if hasattr(file_obj, 'tell') else 0
        header = b''
        try:
            if hasattr(file_obj, 'seek'):
                file_obj.seek(0)
            header = file_obj.read(1024) if hasattr(file_obj, 'read') else b''
        finally:
            if hasattr(file_obj, 'seek'):
                try:
                    file_obj.seek(pos)
                except Exception:
                    pass

        if header:
            is_valid_magic = False
            if ext == 'pdf' and header.startswith(b'%PDF-'):
                is_valid_magic = True
            elif ext == 'png' and header.startswith(b'\x89PNG\r\n\x1a\n'):
                is_valid_magic = True
            elif ext in ('jpg', 'jpeg') and header.startswith(b'\xff\xd8\xff'):
                is_valid_magic = True
            elif ext == 'gif' and (header.startswith(b'GIF87a') or header.startswith(b'GIF89a')):
                is_valid_magic = True
            elif ext == 'bmp' and header.startswith(b'BM'):
                is_valid_magic = True
            elif ext == 'tiff' and (header.startswith(b'II*\x00') or header.startswith(b'MM\x00*')):
                is_valid_magic = True
            elif ext == 'webp' and len(header) >= 12 and header[:4] == b'RIFF' and header[8:12] == b'WEBP':
                is_valid_magic = True

            if not is_valid_magic:
                raise ValidationError(
                    f'El contenido del archivo no corresponde a un formato .{ext} válido o está corrupto.'
                )



class ValidatedFileField(serializers.FileField):
    def __init__(self, *args, allowed_extensions=None, max_size_mb=None, **kwargs):
        self.allowed_extensions = allowed_extensions
        self.max_size_mb = max_size_mb
        super().__init__(*args, **kwargs)

    def to_internal_value(self, data):
        file_obj = super().to_internal_value(data)
        try:
            validate_uploaded_file(
                file_obj,
                allowed_extensions=self.allowed_extensions,
                max_size_mb=self.max_size_mb,
            )
        except ValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return file_obj
