import uuid

from django.conf import settings
from django.db import models


def document_upload_path(instance, filename):
    user_id = instance.user.pk if instance.user else 'unknown'
    ext = filename.split('.')[-1].lower() if '.' in filename else 'bin'
    safe_name = f'{uuid.uuid4().hex}.{ext}'
    return f'documents/user_{user_id}/{instance.document_type}/{safe_name}'


class Document(models.Model):
    DOCUMENT_TYPE_RECEIPT = 'receipt'
    DOCUMENT_TYPE_INVOICE = 'invoice'
    DOCUMENT_TYPE_TICKET = 'ticket'
    DOCUMENT_TYPE_OTHER = 'other'

    DOCUMENT_TYPE_CHOICES = [
        (DOCUMENT_TYPE_RECEIPT, 'Recibo'),
        (DOCUMENT_TYPE_INVOICE, 'Factura'),
        (DOCUMENT_TYPE_TICKET, 'Ticket'),
        (DOCUMENT_TYPE_OTHER, 'Otro'),
    ]

    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_COMPLETED = 'completed'
    STATUS_REVIEW_REQUIRED = 'review_required'
    STATUS_FAILED = 'failed'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendiente'),
        (STATUS_PROCESSING, 'Procesando'),
        (STATUS_COMPLETED, 'Procesado'),
        (STATUS_REVIEW_REQUIRED, 'Revisión requerida'),
        (STATUS_FAILED, 'Error'),
    ]

    PAYMENT_METHOD_CASH = 'cash'
    PAYMENT_METHOD_CARD = 'card'
    PAYMENT_METHOD_TRANSFER = 'transfer'
    PAYMENT_METHOD_DIGITAL = 'digital'
    PAYMENT_METHOD_OTHER = 'other'

    PAYMENT_METHOD_CHOICES = [
        (PAYMENT_METHOD_CASH, 'Efectivo'),
        (PAYMENT_METHOD_CARD, 'Tarjeta'),
        (PAYMENT_METHOD_TRANSFER, 'Transferencia'),
        (PAYMENT_METHOD_DIGITAL, 'Pago digital'),
        (PAYMENT_METHOD_OTHER, 'Otro'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='documents',
        verbose_name='Usuario',
    )
    title = models.CharField('Título', max_length=200, blank=True, default='')
    document_number = models.CharField(
        'Número de documento', max_length=100, blank=True, default='', db_index=True,
    )
    document_type = models.CharField(
        'Tipo de documento',
        max_length=20,
        choices=DOCUMENT_TYPE_CHOICES,
        default=DOCUMENT_TYPE_RECEIPT,
        db_index=True,
    )
    file = models.FileField(
        'Archivo', upload_to=document_upload_path, max_length=500,
    )
    original_filename = models.CharField(
        'Nombre de archivo original', max_length=255, blank=True, default='',
    )
    file_size = models.PositiveBigIntegerField(
        'Tamaño del archivo (bytes)', null=True, blank=True,
    )
    mime_type = models.CharField(
        'Tipo MIME', max_length=100, blank=True, default='',
    )
    checksum = models.CharField(
        'Checksum (SHA-256)', max_length=64, blank=True, default='', db_index=True,
    )
    status = models.CharField(
        'Estado de procesamiento',
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    processing_started_at = models.DateTimeField(
        'Inicio de procesamiento', null=True, blank=True,
    )
    processed_at = models.DateTimeField(
        'Fecha de procesamiento', null=True, blank=True,
    )
    processing_attempts = models.PositiveSmallIntegerField(
        'Intentos de procesamiento', default=0,
    )
    raw_ocr_data = models.JSONField(
        'Datos OCR crudos', null=True, blank=True, default=dict,
    )
    structured_data = models.JSONField(
        'Datos estructurados extraídos', null=True, blank=True, default=dict,
    )
    processing_error = models.TextField('Error de procesamiento', blank=True, default='')
    processing_time_ms = models.PositiveIntegerField(
        'Tiempo de procesamiento (ms)', null=True, blank=True,
    )
    supplier = models.ForeignKey(
        'suppliers.Supplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
        verbose_name='Proveedor',
    )
    category = models.ForeignKey(
        'categories.Category',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='documents',
        verbose_name='Categoría',
    )
    issued_at = models.DateTimeField(
        'Fecha de emisión', null=True, blank=True, db_index=True,
    )
    due_date = models.DateTimeField(
        'Fecha de vencimiento', null=True, blank=True,
    )
    subtotal_amount = models.DecimalField(
        'Subtotal', max_digits=12, decimal_places=2, null=True, blank=True,
    )
    total_amount = models.DecimalField(
        'Monto total', max_digits=12, decimal_places=2, null=True, blank=True,
    )
    tax_amount = models.DecimalField(
        'Impuestos', max_digits=12, decimal_places=2, null=True, blank=True,
    )
    discount_amount = models.DecimalField(
        'Descuentos', max_digits=12, decimal_places=2, null=True, blank=True, default=0,
    )
    tip_amount = models.DecimalField(
        'Propina', max_digits=12, decimal_places=2, null=True, blank=True, default=0,
    )
    currency = models.CharField(
        'Moneda', max_length=3, blank=True, default='COP', db_index=True,
    )
    payment_method = models.CharField(
        'Método de pago',
        max_length=30,
        choices=PAYMENT_METHOD_CHOICES,
        blank=True,
        default='',
    )
    is_paid = models.BooleanField('Pagado', default=False, db_index=True)
    last_due_reminder_at = models.DateTimeField(
        'Último aviso de vencimiento', null=True, blank=True,
    )
    has_line_items = models.BooleanField(
        'Tiene items (productos/servicios)', default=False,
    )
    notes = models.TextField('Notas', blank=True, default='')
    created_at = models.DateTimeField(
        'Fecha de creación', auto_now_add=True, db_index=True,
    )
    updated_at = models.DateTimeField('Última actualización', auto_now=True)

    class Meta:
        verbose_name = 'Documento'
        verbose_name_plural = 'Documentos'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['user', 'document_type']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['user', 'issued_at']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(processing_attempts__gte=0),
                name='document_processing_attempts_non_negative',
            ),
        ]

    def __str__(self):
        return f'{self.get_document_type_display()} - {self.title or self.original_filename or self.id}'

    @property
    def is_processed(self) -> bool:
        return self.status == self.STATUS_COMPLETED

    @property
    def requires_review(self) -> bool:
        return self.status == self.STATUS_REVIEW_REQUIRED

    @property
    def file_extension(self) -> str:
        if not self.original_filename:
            return ''
        return (self.original_filename.split('.')[-1] if '.' in self.original_filename else '').lower()

    def mark_processing(self):
        from django.utils import timezone
        self.status = self.STATUS_PROCESSING
        self.processing_started_at = timezone.now()
        self.processing_attempts += 1
        self.save(
            update_fields=[
                'status', 'processing_started_at',
                'processing_attempts', 'updated_at',
            ]
        )

    def mark_completed(self, processing_time_ms=None):
        from django.utils import timezone
        self.status = self.STATUS_COMPLETED
        self.processed_at = timezone.now()
        if processing_time_ms is not None:
            self.processing_time_ms = int(processing_time_ms)
        self.processing_error = ''
        save_fields = ['status', 'processed_at', 'processing_error', 'updated_at']
        if processing_time_ms is not None:
            save_fields.append('processing_time_ms')
        self.save(update_fields=save_fields)

    def mark_review_required(self, processing_error: str = ''):
        from django.utils import timezone
        self.status = self.STATUS_REVIEW_REQUIRED
        self.processed_at = timezone.now()
        if processing_error:
            self.processing_error = processing_error[:500]
        self.save(
            update_fields=['status', 'processed_at', 'processing_error', 'updated_at']
        )

    def mark_failed(self, processing_error: str = ''):
        from django.utils import timezone
        self.status = self.STATUS_FAILED
        self.processed_at = timezone.now()
        if processing_error:
            self.processing_error = processing_error[:500]
        self.save(
            update_fields=['status', 'processed_at', 'processing_error', 'updated_at']
        )


class ExpenseItem(models.Model):
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name='line_items',
        verbose_name='Documento',
        null=True,
        blank=True,
    )
    expense = models.ForeignKey(
        'expenses.Expense',
        on_delete=models.CASCADE,
        related_name='line_items',
        verbose_name='Gasto',
        null=True,
        blank=True,
    )
    sequence = models.PositiveIntegerField('Orden', default=1)
    sku = models.CharField('SKU/Código', max_length=100, blank=True, default='')
    description = models.CharField('Descripción', max_length=500)
    quantity = models.DecimalField(
        'Cantidad', max_digits=14, decimal_places=4, default=1,
    )
    unit_price = models.DecimalField(
        'Precio unitario', max_digits=12, decimal_places=2, default=0,
    )
    unit = models.CharField('Unidad', max_length=30, blank=True, default='')
    tax_rate = models.DecimalField(
        'Tasa de impuesto (%)', max_digits=6, decimal_places=3, null=True, blank=True,
    )
    tax_amount = models.DecimalField(
        'Impuesto', max_digits=12, decimal_places=2, default=0,
    )
    discount_amount = models.DecimalField(
        'Descuento', max_digits=12, decimal_places=2, default=0,
    )
    total_amount = models.DecimalField(
        'Total de línea', max_digits=14, decimal_places=2, default=0,
    )
    notes = models.CharField('Notas', max_length=255, blank=True, default='')
    extra = models.JSONField('Datos adicionales', default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Item de gasto/documento'
        verbose_name_plural = 'Items de gasto/documento'
        ordering = ['document_id', 'expense_id', 'sequence', 'id']
        indexes = [
            models.Index(fields=['document', 'sequence']),
            models.Index(fields=['expense', 'sequence']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gte=0),
                name='expense_item_quantity_non_negative',
            ),
            models.CheckConstraint(
                check=models.Q(unit_price__gte=0),
                name='expense_item_unit_price_non_negative',
            ),
            models.CheckConstraint(
                check=models.Q(total_amount__gte=0),
                name='expense_item_total_non_negative',
            ),
        ]

    def __str__(self):
        return f'{self.sequence}. {self.description} x {self.quantity}'

    def save(self, *args, **kwargs):
        structured = isinstance(self.extra, dict) and self.extra.get('source') == 'gemini_structured'
        if not structured and (not self.total_amount or self.total_amount <= 0):
            subtotal = float(self.quantity) * float(self.unit_price)
            line_total = subtotal + float(self.tax_amount) - float(self.discount_amount)
            if line_total < 0:
                line_total = 0
            self.total_amount = round(line_total, 2)
        super().save(*args, **kwargs)
