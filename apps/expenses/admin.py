from django.contrib import admin
from apps.documents.admin import ExpenseItemInline

from .models import Expense


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'user',
        'description',
        'category',
        'supplier',
        'amount',
        'tax_amount',
        'currency',
        'payment_method',
        'incurred_at',
        'is_recurring',
        'is_reimbursable',
        'created_at',
    ]
    list_filter = [
        'currency', 'payment_method', 'is_recurring', 'is_reimbursable',
        'incurred_at', 'created_at',
    ]
    search_fields = [
        'description', 'notes',
        'supplier__name', 'category__name', 'user__email',
    ]
    readonly_fields = ['has_line_items', 'created_at', 'updated_at']
    raw_id_fields = ['user', 'document', 'category', 'supplier']
    inlines = [ExpenseItemInline]
