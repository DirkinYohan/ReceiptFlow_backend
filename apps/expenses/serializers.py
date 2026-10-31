from django.db.models import Count, Sum
from django_filters import rest_framework as filters
from rest_framework import serializers

from apps.documents.serializers import ExpenseItemSerializer

from .models import Expense


class ExpenseFilter(filters.FilterSet):
    category = filters.NumberFilter(field_name='category_id')
    supplier = filters.NumberFilter(field_name='supplier_id')
    document = filters.NumberFilter(field_name='document_id')
    currency = filters.CharFilter(field_name='currency')
    payment_method = filters.CharFilter(field_name='payment_method')
    is_recurring = filters.BooleanFilter(field_name='is_recurring')
    is_reimbursable = filters.BooleanFilter(field_name='is_reimbursable')
    incurred_from = filters.DateTimeFilter(field_name='incurred_at', lookup_expr='gte')
    incurred_to = filters.DateTimeFilter(field_name='incurred_at', lookup_expr='lte')
    created_from = filters.DateTimeFilter(field_name='created_at', lookup_expr='gte')
    created_to = filters.DateTimeFilter(field_name='created_at', lookup_expr='lte')
    min_amount = filters.NumberFilter(field_name='amount', lookup_expr='gte')
    max_amount = filters.NumberFilter(field_name='amount', lookup_expr='lte')

    class Meta:
        model = Expense
        fields = [
            'category', 'supplier', 'document', 'currency',
            'payment_method', 'is_recurring', 'is_reimbursable',
        ]


class ExpenseSerializer(serializers.ModelSerializer):
    total_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True,
    )
    line_items_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Expense
        fields = [
            'id',
            'document',
            'category',
            'supplier',
            'description',
            'amount',
            'tax_amount',
            'discount_amount',
            'tip_amount',
            'total_amount',
            'currency',
            'payment_method',
            'incurred_at',
            'is_recurring',
            'recurrence_frequency',
            'is_reimbursable',
            'has_line_items',
            'line_items_count',
            'notes',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'total_amount',
            'has_line_items',
            'line_items_count',
            'created_at',
            'updated_at',
        ]
        extra_kwargs = {
            'description': {'required': True, 'allow_blank': False},
            'incurred_at': {'required': True},
            'category': {'required': True},
        }


class ExpenseDetailSerializer(ExpenseSerializer):
    line_items = ExpenseItemSerializer(many=True, read_only=True, source='line_items.all')

    class Meta(ExpenseSerializer.Meta):
        fields = ExpenseSerializer.Meta.fields + ['line_items']


class ExpenseCreateFromDocumentSerializer(serializers.Serializer):
    document_id = serializers.IntegerField(required=True)
    category_id = serializers.IntegerField(required=False, allow_null=True)
    supplier_id = serializers.IntegerField(required=False, allow_null=True)
    description = serializers.CharField(max_length=300, required=False, allow_blank=True)
    payment_method = serializers.ChoiceField(
        choices=Expense.PAYMENT_METHOD_CHOICES, required=False,
    )
    notes = serializers.CharField(required=False, allow_blank=True)


class ExpenseStatsSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    total_amount = serializers.DecimalField(max_digits=16, decimal_places=2)
    average_amount = serializers.DecimalField(max_digits=16, decimal_places=2)
    max_amount = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    min_amount = serializers.DecimalField(max_digits=16, decimal_places=2, allow_null=True)
    recurring_count = serializers.IntegerField()
    reimbursable_total = serializers.DecimalField(max_digits=16, decimal_places=2)
