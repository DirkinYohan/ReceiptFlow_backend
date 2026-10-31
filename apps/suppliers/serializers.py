from django.db import models as django_models
from django.db.models import Count
from django_filters import rest_framework as filters
from rest_framework import serializers

from apps.common.validators import ValidatedFileField

from .models import Supplier


class SupplierFilter(filters.FilterSet):
    is_favorite = filters.BooleanFilter(field_name='is_favorite')

    class Meta:
        model = Supplier
        fields = ['is_favorite']


class SupplierSerializer(serializers.ModelSerializer):
    document_count = serializers.IntegerField(read_only=True)
    expense_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Supplier
        fields = [
            'id',
            'name',
            'tax_id',
            'address',
            'email',
            'phone',
            'website',
            'notes',
            'is_favorite',
            'default_category',
            'document_count',
            'expense_count',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'document_count',
            'expense_count',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {
            'name': {'required': True, 'allow_blank': False},
            'email': {'allow_blank': True, 'required': False},
            'tax_id': {'allow_blank': True, 'required': False},
        }

    def validate_email(self, value):
        return (value or '').lower().strip()

    def validate_tax_id(self, value):
        import re
        return re.sub(r'[^A-Za-z0-9]', '', value or '').upper()

    def validate_name(self, value):
        return value.strip()

    def validate_default_category(self, value):
        if value is None:
            return value
        request = self.context.get('request')
        if not value.is_system and (request is None or value.user_id != request.user.pk):
            raise serializers.ValidationError('Categoría no encontrada.')
        return value


class SupplierAutocompleteSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = [
            'id',
            'name',
            'tax_id',
            'address',
            'email',
            'phone',
        ]
