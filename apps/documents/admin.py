from django.contrib import admin

from .models import Document, ExpenseItem


class ExpenseItemInline(admin.TabularInline):
    model = ExpenseItem
    extra = 0
    fields = [
        'sequence', 'sku', 'description', 'quantity',
        'unit_price', 'unit', 'tax_rate', 'tax_amount',
        'discount_amount', 'total_amount', 'notes',
    ]


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'user',
        'document_type',
        'status',
        'title',
        'original_filename',
        'total_amount',
        'currency',
        'issued_at',
        'created_at',
    ]
    list_filter = [
        'document_type', 'status', 'currency',
        'payment_method', 'created_at', 'issued_at',
    ]
    search_fields = [
        'title', 'original_filename', 'document_number',
        'supplier__name', 'category__name', 'notes', 'user__email',
    ]
    readonly_fields = [
        'original_filename', 'file_size', 'mime_type',
        'checksum', 'processing_attempts',
        'processing_started_at', 'processed_at',
        'raw_ocr_data', 'structured_data',
        'processing_error', 'processing_time_ms',
        'has_line_items', 'created_at', 'updated_at',
    ]
    raw_id_fields = ['user', 'supplier', 'category']
    inlines = [ExpenseItemInline]


@admin.register(ExpenseItem)
class ExpenseItemAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'document', 'expense', 'sequence',
        'description', 'quantity', 'unit_price', 'total_amount',
    ]
    list_filter = ['created_at']
    search_fields = ['description', 'sku', 'notes']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['document', 'expense']
