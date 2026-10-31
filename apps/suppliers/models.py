from __future__ import annotations

import re
import unicodedata
from typing import Optional

from django.conf import settings
from django.db import models
from django.db.models import Q


class SupplierManager(models.Manager):
    def _normalize(self, value: str) -> str:
        if not value:
            return ''
        nfkd = unicodedata.normalize('NFKD', value)
        no_accent = ''.join(c for c in nfkd if not unicodedata.combining(c))
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', no_accent).lower()
        return cleaned.strip()

    def find_match(
        self,
        user,
        name: Optional[str] = None,
        tax_id: Optional[str] = None,
    ) -> Optional['Supplier']:
        if not name and not tax_id:
            return None
        qs = self.filter(user=user)
        if tax_id:
            tax_clean = re.sub(r'[^A-Za-z0-9]', '', tax_id).upper()
            candidate = qs.filter(tax_id__iexact=tax_clean).first()
            if candidate:
                return candidate
        if name:
            norm_name = self._normalize(name)
            if not norm_name:
                return None
            for candidate in qs.iterator():
                if self._normalize(candidate.name) == norm_name:
                    return candidate
        return None

    def get_or_create_for_user(
        self,
        user,
        name: str,
        tax_id: str = '',
        address: str = '',
        phone: str = '',
        email: str = '',
    ) -> tuple['Supplier', bool]:
        existing = self.find_match(user=user, name=name, tax_id=tax_id)
        if existing:
            updated = False
            if tax_id and not existing.tax_id:
                existing.tax_id = re.sub(r'[^A-Za-z0-9]', '', tax_id).upper()
                updated = True
            if address and not existing.address:
                existing.address = address
                updated = True
            if phone and not existing.phone:
                existing.phone = phone
                updated = True
            if email and not existing.email:
                existing.email = email.lower()
                updated = True
            if updated:
                existing.save(update_fields=['tax_id', 'address', 'phone', 'email', 'updated_at'])
            return existing, False
        return self.create(
            user=user,
            name=name.strip(),
            tax_id=re.sub(r'[^A-Za-z0-9]', '', tax_id).upper(),
            address=address.strip(),
            phone=phone.strip(),
            email=email.lower().strip(),
        ), True


class Supplier(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='suppliers',
        verbose_name='Usuario',
    )
    name = models.CharField('Nombre/Razón social', max_length=200, db_index=True)
    tax_id = models.CharField(
        'RFC/CIF/NIF', max_length=50, blank=True, default='', db_index=True,
    )
    address = models.CharField('Dirección', max_length=300, blank=True, default='')
    email = models.EmailField('Correo electrónico', blank=True, default='')
    phone = models.CharField('Teléfono', max_length=50, blank=True, default='')
    website = models.URLField('Sitio web', max_length=255, blank=True, default='')
    notes = models.TextField('Notas', blank=True, default='')
    is_favorite = models.BooleanField('Favorito', default=False)
    default_category = models.ForeignKey(
        'categories.Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='default_for_suppliers',
        verbose_name='Categoría aprendida',
    )
    created_at = models.DateTimeField('Fecha de creación', auto_now_add=True)
    updated_at = models.DateTimeField('Última actualización', auto_now=True)

    objects = SupplierManager()

    class Meta:
        verbose_name = 'Proveedor'
        verbose_name_plural = 'Proveedores'
        ordering = ['name']
        indexes = [
            models.Index(fields=['user', 'name']),
            models.Index(fields=['user', 'tax_id']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'name'],
                name='unique_user_supplier_name',
            ),
            models.UniqueConstraint(
                fields=['user', 'tax_id'],
                condition=~models.Q(tax_id=''),
                name='unique_user_supplier_tax_id_when_set',
            ),
        ]

    def __str__(self):
        if self.tax_id:
            return f'{self.name} ({self.tax_id})'
        return self.name

    @property
    def document_count(self) -> int:
        return getattr(self, '_document_count', None) or self.documents.count()

    @property
    def expense_count(self) -> int:
        return getattr(self, '_expense_count', None) or self.expenses.count()
