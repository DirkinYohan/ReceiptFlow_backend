from django.contrib import admin

from .models import Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'user', 'tax_id', 'email', 'phone', 'is_favorite', 'created_at']
    list_filter = ['is_favorite', 'created_at']
    search_fields = ['name', 'tax_id', 'email', 'phone', 'address', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['user']
