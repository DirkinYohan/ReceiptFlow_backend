from django.contrib import admin

from .models import Category, CategoryBudget


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'scope', 'user', 'is_active', 'sort_order', 'color', 'created_at']
    list_filter = ['scope', 'is_active', 'created_at']
    search_fields = ['name', 'description', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['user']


@admin.register(CategoryBudget)
class CategoryBudgetAdmin(admin.ModelAdmin):
    list_display = ['user', 'category', 'monthly_amount', 'currency', 'updated_at']
    raw_id_fields = ['user', 'category']
