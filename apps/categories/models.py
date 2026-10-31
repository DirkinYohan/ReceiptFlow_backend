from django.conf import settings
from django.db import models


class Category(models.Model):
    SCOPE_SYSTEM = 'system'
    SCOPE_USER = 'user'
    SCOPE_CHOICES = [
        (SCOPE_SYSTEM, 'Sistema'),
        (SCOPE_USER, 'Usuario'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='categories',
        verbose_name='Usuario',
    )
    name = models.CharField('Nombre', max_length=100)
    slug = models.SlugField('Slug', max_length=100, blank=True)
    description = models.TextField('Descripción', blank=True)
    icon = models.CharField('Icono', max_length=50, blank=True)
    color = models.CharField('Color (hex)', max_length=7, blank=True, default='#3B82F6')
    scope = models.CharField(
        'Ámbito',
        max_length=10,
        choices=SCOPE_CHOICES,
        default=SCOPE_USER,
        db_index=True,
    )
    sort_order = models.PositiveIntegerField('Orden', default=0, db_index=True)
    is_active = models.BooleanField('Activa', default=True)
    created_at = models.DateTimeField('Fecha de creación', auto_now_add=True)
    updated_at = models.DateTimeField('Última actualización', auto_now=True)

    class Meta:
        verbose_name = 'Categoría'
        verbose_name_plural = 'Categorías'
        ordering = ['scope', 'sort_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'name'],
                condition=models.Q(scope='user'),
                name='unique_user_category_name',
            ),
            models.UniqueConstraint(
                fields=['name'],
                condition=models.Q(scope='system', user__isnull=True),
                name='unique_system_category_name',
            ),
        ]
        indexes = [
            models.Index(fields=['scope']),
            models.Index(fields=['scope', 'is_active']),
        ]

    def __str__(self):
        prefix = '[Sistema] ' if self.scope == self.SCOPE_SYSTEM else ''
        return f'{prefix}{self.name}'

    @property
    def is_system(self) -> bool:
        return self.scope == self.SCOPE_SYSTEM

    @property
    def is_editable(self) -> bool:
        return self.scope != self.SCOPE_SYSTEM


class CategoryBudget(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='category_budgets',
        verbose_name='Usuario',
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='budgets',
        verbose_name='Categoría',
    )
    monthly_amount = models.DecimalField('Presupuesto mensual', max_digits=12, decimal_places=2)
    currency = models.CharField('Moneda', max_length=3, default='COP')
    # First day of the month in which each alert was already emailed, so it goes out once a month.
    warning_sent_for = models.DateField('Aviso 80% enviado (mes)', null=True, blank=True)
    exceeded_sent_for = models.DateField('Aviso 100% enviado (mes)', null=True, blank=True)
    created_at = models.DateTimeField('Fecha de creación', auto_now_add=True)
    updated_at = models.DateTimeField('Última actualización', auto_now=True)

    class Meta:
        verbose_name = 'Presupuesto por categoría'
        verbose_name_plural = 'Presupuestos por categoría'
        ordering = ['category__sort_order', 'category__name']
        constraints = [
            models.UniqueConstraint(fields=['user', 'category'], name='unique_user_category_budget'),
            models.CheckConstraint(
                check=models.Q(monthly_amount__gt=0), name='category_budget_amount_positive',
            ),
        ]

    def __str__(self):
        return f'{self.category.name}: {self.monthly_amount} {self.currency}'
