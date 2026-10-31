from __future__ import annotations

import hashlib
import logging
from typing import Optional

from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.utils import timezone

from .models import Document, ExpenseItem

logger = logging.getLogger(__name__)


def compute_file_sha256(uploaded_file: UploadedFile, chunk_size: int = 65536) -> str:
    sha = hashlib.sha256()
    pos = uploaded_file.tell()
    try:
        uploaded_file.seek(0)
        while True:
            data = uploaded_file.read(chunk_size)
            if not data:
                break
            sha.update(data)
    finally:
        try:
            uploaded_file.seek(pos)
        except Exception:
            pass
    return sha.hexdigest()


class DocumentService:
    @staticmethod
    def create_document(user, file_obj: UploadedFile, **extra) -> Document:
        checksum = None
        try:
            checksum = compute_file_sha256(file_obj)
        except Exception as exc:
            logger.warning('Could not compute checksum for file %s: %s', file_obj.name, exc)

        document_type = extra.get('document_type') or Document.DOCUMENT_TYPE_RECEIPT
        title = (extra.get('title') or file_obj.name or '').strip()

        with transaction.atomic():
            document = Document.objects.create(
                user=user,
                file=file_obj,
                original_filename=file_obj.name or '',
                file_size=getattr(file_obj, 'size', None),
                mime_type=getattr(file_obj, 'content_type', '') or '',
                checksum=checksum or '',
                document_type=document_type,
                title=title,
                notes=extra.get('notes', '') or '',
                status=Document.STATUS_PENDING,
            )
        return document

    @staticmethod
    def set_line_items(document: Document, items: list) -> list:
        if not items:
            document.line_items.all().delete()
            document.has_line_items = False
            document.save(update_fields=['has_line_items', 'updated_at'])
            return []

        created_items = []
        with transaction.atomic():
            document.line_items.all().delete()
            for idx, raw in enumerate(items, start=1):
                extra = raw.get('extra', {}) or {}
                from_structured = extra.get('source') == 'gemini_structured'
                description = (raw.get('description') or '').strip()
                if from_structured and not description:
                    continue
                if not from_structured:
                    description = description or 'Producto'
                quantity = raw.get('quantity', 1)
                unit_price = raw.get('unit_price', 0)
                tax_amount = raw.get('tax_amount', 0)
                discount_amount = raw.get('discount_amount', 0)
                total_amount = raw.get('total_amount')
                if from_structured:
                    if quantity in (None, ''):
                        quantity = 1
                        extra = dict(extra)
                        extra['quantity_missing'] = True
                    if unit_price in (None, ''):
                        unit_price = 0
                        extra = dict(extra)
                        extra['unit_price_missing'] = True
                    if total_amount in (None, ''):
                        total_amount = 0
                        extra = dict(extra)
                        extra['total_missing'] = True
                    if tax_amount in (None, ''):
                        tax_amount = 0
                        extra = dict(extra)
                        extra['tax_amount_missing'] = True
                    if discount_amount in (None, ''):
                        discount_amount = 0
                else:
                    quantity = quantity or 1
                    unit_price = unit_price or 0
                    tax_amount = tax_amount or 0
                    discount_amount = discount_amount or 0
                    if total_amount in (None, ''):
                        subtotal = float(quantity) * float(unit_price)
                        total_amount = round(subtotal + float(tax_amount) - float(discount_amount), 2)
                        if total_amount < 0:
                            total_amount = 0
                item = ExpenseItem.objects.create(
                    document=document,
                    sequence=raw.get('sequence', idx),
                    sku=raw.get('sku', '') or '',
                    description=description[:500],
                    quantity=quantity,
                    unit_price=unit_price,
                    unit=raw.get('unit', '') or '',
                    tax_rate=raw.get('tax_rate', None),
                    tax_amount=tax_amount,
                    discount_amount=discount_amount,
                    total_amount=total_amount,
                    notes=raw.get('notes', '') or '',
                    extra=extra,
                )
                created_items.append(item)
            document.has_line_items = bool(created_items)
            document.save(update_fields=['has_line_items', 'updated_at'])
        return created_items

    @staticmethod
    def delete_document_safe(document: Document) -> None:
        with transaction.atomic():
            if document.file:
                try:
                    document.file.delete(save=False)
                except Exception as exc:
                    logger.warning('Could not delete file for document %s: %s', document.id, exc)
            document.line_items.all().delete()
            document.delete()  # cascades to the expense registered from it


document_service = DocumentService
