from django.db.models import Count, Q
from django_filters import rest_framework as filters
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import StandardPagination
from apps.common.permissions import IsOwnerOrAdmin

from .models import Category


class CategoryFilter(filters.FilterSet):
    scope = filters.CharFilter(field_name='scope')
    is_active = filters.BooleanFilter(field_name='is_active')
    search = filters.CharFilter(method='search_filter')

    class Meta:
        model = Category
        fields = ['scope', 'is_active']

    def search_filter(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value) | Q(description__icontains=value)
        )


class CategorySerializer(serializers.ModelSerializer):
    is_system = serializers.BooleanField(read_only=True)
    is_editable = serializers.BooleanField(read_only=True)
    usage_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = [
            'id',
            'name',
            'slug',
            'description',
            'icon',
            'color',
            'scope',
            'sort_order',
            'is_active',
            'is_system',
            'is_editable',
            'usage_count',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'scope', 'is_system', 'is_editable', 'usage_count',
            'created_at', 'updated_at', 'slug',
        ]
        extra_kwargs = {
            'name': {'required': True, 'allow_blank': False},
        }




