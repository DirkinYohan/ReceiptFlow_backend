from django.conf import settings
from django.db import models


class Expense(models.Model):
    PAYMENT_METHOD_CASH = 'cash'
    PAYMENT_METHOD_CARD = 'card'
    PAYMENT_METHOD_TRANSFER = 'transfer'
    PAYMENT_METHOD_DIGITAL = 'digital'
    PAYMENT_METHOD_CHECK = 'check'
    PAYMENT_METHOD_CREDIT = 'credit'
    PAYMENT_METHOD_OTHER = 'other'

    PAYMENT_METHOD_CHOICES = [
        (PAYMENT_METHOD_CASH, 'Efectivo'),
        (PAYMENT_METHOD_CARD, 'Tarjeta'),
        (PAYMENT_METHOD_TRANSFER, 'Transferencia'),
        (PAYMENT_METHOD_DIGITAL, 'Pago digital'),
        (PAYMENT_METHOD_CHECK, 'Cheque'),
        (PAYMENT_METHOD_CREDIT, 'Crédito'),
        (PAYMENT_METHOD_OTHER, 'Otro'),
    ]

    RECURRENCE_DAILY = 'daily'
    RECURRENCE_WEEKLY = 'weekly'
    RECURRENCE_BIWEEKLY = 'biweekly'
    RECURRENCE_MONTHLY = 'monthly'
    RECURRENCE_QUARTERLY = 'quarterly'
    RECURRENCE_YEARLY = 'yearly'

    RECURRENCE_CHOICES = [
        ('', 'No recurrente'),
        (RECURRENCE_DAILY, 'Diario'),
        (RECURRENCE_WEEKLY, 'Semanal'),
        (RECURRENCE_BIWEEKLY, 'Quincenal'),
        (RECURRENCE_MONTHLY, 'Mensual'),
        (RECURRENCE_QUARTERLY, 'Trimestral'),
        (RECURRENCE_YEARLY, 'Anual'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='expenses',
        verbose_name='Usuario',
        db_index=True,
    )
    # The expense exists because of the receipt: deleting the receipt deletes it too,
    # otherwise re-uploading a receipt leaves the old expense counted twice.
    document = models.ForeignKey(
        'documents.Document',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='expenses',
        verbose_name='Documento asociado',
    )
    category = models.ForeignKey(
        'categories.Category',
        on_delete=models.PROTECT,
        related_name='expenses',
        verbose_name='Categoría',
        db_index=True,
    )
    supplier = models.ForeignKey(
        'suppliers.Supplier',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses',
        verbose_name='Proveedor',
        db_index=True,
    )
    description = models.CharField('Descripción', max_length=300)
    amount = models.DecimalField('Monto', max_digits=12, decimal_places=2, db_index=True)
    tax_amount = models.DecimalField(
        'Impuestos', max_digits=12, decimal_places=2, default=0,
    )
    discount_amount = models.DecimalField(
        'Descuentos', max_digits=12, decimal_places=2, default=0,
    )
    tip_amount = models.DecimalField(
        'Propina', max_digits=12, decimal_places=2, default=0,
    )
    currency = models.CharField(
        'Moneda', max_length=3, default='COP', db_index=True,
    )
    payment_method = models.CharField(
        'Método de pago',
        max_length=30,
        choices=PAYMENT_METHOD_CHOICES,
        default=PAYMENT_METHOD_OTHER,
        db_index=True,
    )
    incurred_at = models.DateTimeField(
        'Fecha del gasto', db_index=True,
    )
    is_recurring = models.BooleanField('Es recurrente', default=False, db_index=True)
    recurrence_frequency = models.CharField(
        'Frecuencia de recurrencia',
        max_length=20,
        choices=RECURRENCE_CHOICES,
        blank=True,
        default='',
    )
    has_line_items = models.BooleanField(
        'Tiene items (productos/servicios)', default=False,
    )
    is_reimbursable = models.BooleanField('Es reembolsable', default=False)
    notes = models.TextField('Notas', blank=True, default='')
    created_at = models.DateTimeField('Fecha de creación', auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField('Última actualización', auto_now=True)

    class Meta:
        verbose_name = 'Gasto'
        verbose_name_plural = 'Gastos'
        ordering = ['-incurred_at']
        indexes = [
            models.Index(fields=['user', '-incurred_at']),
            models.Index(fields=['user', 'category', '-incurred_at']),
            models.Index(fields=['user', 'supplier', '-incurred_at']),
            models.Index(fields=['user', 'payment_method']),
            models.Index(fields=['user', 'is_recurring']),
            models.Index(fields=['user', 'currency']),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gte=0), name='expense_amount_non_negative'),
            models.CheckConstraint(check=models.Q(tax_amount__gte=0), name='expense_tax_non_negative'),
            models.CheckConstraint(check=models.Q(discount_amount__gte=0), name='expense_discount_non_negative'),
            models.CheckConstraint(check=models.Q(tip_amount__gte=0), name='expense_tip_non_negative'),
        ]

    def __str__(self):
        return f'{self.description} - {self.amount} {self.currency}'

    @property
    def total_amount(self):
        return (
            (self.amount or 0)
            + (self.tax_amount or 0)
            - (self.discount_amount or 0)
            + (self.tip_amount or 0)
        )

    def line_items_total(self):
        total = sum(
            (li.total_amount or 0)
            for li in self.line_items.all()
        )
        return total
