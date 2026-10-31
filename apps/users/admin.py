from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = [
        'email', 'first_name', 'last_name', 'is_email_verified',
        'is_staff', 'is_active', 'last_login', 'created_at',
    ]
    list_filter = [
        'is_staff', 'is_active', 'is_superuser',
        'is_email_verified', 'created_at', 'last_login',
    ]
    search_fields = ['email', 'first_name', 'last_name', 'username']
    ordering = ['-created_at']
    readonly_fields = [
        'last_login', 'date_joined', 'created_at', 'updated_at',
        'email_verified_at', 'email_verification_sent_at', 'password_reset_sent_at',
        'last_activity_at',
    ]
    raw_id_fields = []

    fieldsets = (
        (None, {'fields': ('username', 'email', 'password')}),
        ('Información personal', {
            'fields': ('first_name', 'last_name', 'avatar'),
        }),
        ('Verificación y seguridad', {
            'fields': (
                'is_email_verified',
                'email_verified_at',
                'email_verification_token',
                'email_verification_sent_at',
                'password_reset_token',
                'password_reset_sent_at',
            ),
        }),
        ('Permisos', {
            'fields': (
                'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions',
            ),
        }),
        ('Fechas importantes', {
            'fields': (
                'last_login', 'last_activity_at', 'date_joined', 'created_at', 'updated_at',
            ),
        }),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'password1', 'password2'),
        }),
    )
