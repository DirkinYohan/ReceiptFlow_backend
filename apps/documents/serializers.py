from django.db.models import Count, Sum
from django_filters import rest_framework as filters
from rest_framework import serializers

from apps.common.validators import ValidatedFileField
from apps.suppliers.serializers import SupplierAutocompleteSerializer

from .models import Document, ExpenseItem


class DocumentFilter(filters.FilterSet):
    status = filters.CharFilter(field_name='status')
    document_type = filters.CharFilter(field_name='document_type')
    category = filters.NumberFilter(field_name='category_id')
    supplier = filters.NumberFilter(field_name='supplier_id')
    currency = filters.CharFilter(field_name='currency')
    payment_method = filters.CharFilter(field_name='payment_method')
    is_paid = filters.BooleanFilter(field_name='is_paid')
    issued_at_from = filters.DateTimeFilter(field_name='issued_at', lookup_expr='gte')
    issued_at_to = filters.DateTimeFilter(field_name='issued_at', lookup_expr='lte')
    created_from = filters.DateTimeFilter(field_name='created_at', lookup_expr='gte')
    created_to = filters.DateTimeFilter(field_name='created_at', lookup_expr='lte')
    min_total = filters.NumberFilter(field_name='total_amount', lookup_expr='gte')
    max_total = filters.NumberFilter(field_name='total_amount', lookup_expr='lte')

    class Meta:
        model = Document
        fields = [
            'status', 'document_type', 'category', 'supplier',
            'currency', 'payment_method', 'is_paid',
        ]


class ExpenseItemSerializer(serializers.ModelSerializer):
    def to_representation(self, instance):
        data = super().to_representation(instance)
        extra = data.get('extra') or {}
        if extra.get('quantity_missing'):
            data['quantity'] = None
        if extra.get('unit_price_missing'):
            data['unit_price'] = None
        if extra.get('total_missing'):
            data['total_amount'] = None
        if extra.get('tax_amount_missing'):
            data['tax_amount'] = None
        return data

    class Meta:
        model = ExpenseItem
        fields = [
            'id',
            'sequence',
            'sku',
            'description',
            'quantity',
            'unit_price',
            'unit',
            'tax_rate',
            'tax_amount',
            'discount_amount',
            'total_amount',
            'notes',
            'extra',
        ]
        read_only_fields = ['id']
        extra_kwargs = {
            'description': {'required': True, 'allow_blank': False},
        }


class DocumentSerializer(serializers.ModelSerializer):
    line_items_count = serializers.IntegerField(read_only=True)
    expense_id = serializers.SerializerMethodField()
    supplier_name = serializers.CharField(source='supplier.name', read_only=True, default=None)
    category_name = serializers.CharField(source='category.name', read_only=True, default=None)

    def get_expense_id(self, obj):
        expense = obj.expenses.only('id').first()
        return expense.pk if expense else None

    class Meta:
        model = Document
        fields = [
            'id',
            'title',
            'document_number',
            'document_type',
            'original_filename',
            'file_size',
            'mime_type',
            'status',
            'processing_attempts',
            'processing_error',
            'supplier',
            'supplier_name',
            'category',
            'category_name',
            'issued_at',
            'due_date',
            'subtotal_amount',
            'total_amount',
            'tax_amount',
            'discount_amount',
            'tip_amount',
            'currency',
            'payment_method',
            'is_paid',
            'has_line_items',
            'notes',
            'line_items_count',
            'expense_id',
            'processed_at',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id',
            'document_number',
            'original_filename',
            'file_size',
            'mime_type',
            'status',
            'processing_attempts',
            'processing_error',
            'processed_at',
            'has_line_items',
            'line_items_count',
            'created_at',
            'updated_at',
        ]


class DocumentCreateSerializer(serializers.ModelSerializer):
    file = ValidatedFileField(required=True)

    class Meta:
        model = Document
        fields = [
            'id',
            'title',
            'document_type',
            'file',
            'notes',
        ]
        read_only_fields = ['id']

    def validate(self, attrs):
        return attrs


class DocumentDetailSerializer(DocumentSerializer):
    file_url = serializers.SerializerMethodField()
    line_items = ExpenseItemSerializer(many=True, read_only=True)
    supplier_data = SupplierAutocompleteSerializer(source='supplier', read_only=True)
    is_processed = serializers.BooleanField(read_only=True)
    requires_review = serializers.BooleanField(read_only=True)

    class Meta(DocumentSerializer.Meta):
        fields = DocumentSerializer.Meta.fields + [
            'file',
            'file_url',
            'raw_ocr_data',
            'structured_data',
            'processing_started_at',
            'processing_time_ms',
            'line_items',
            'supplier_data',
            'is_processed',
            'requires_review',
            'checksum',
        ]
        read_only_fields = DocumentSerializer.Meta.read_only_fields + [
            'file',
            'file_url',
            'raw_ocr_data',
            'structured_data',
            'processing_started_at',
            'processing_time_ms',
            'line_items',
            'supplier_data',
            'is_processed',
            'requires_review',
            'checksum',
        ]

    def get_file_url(self, obj):
        if obj.file and hasattr(obj.file, 'url'):
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.file.url)
            return obj.file.url
        return None


class DocumentUpdateExtractedDataSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False, allow_blank=True)
    document_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    supplier_id = serializers.IntegerField(required=False, allow_null=True)
    supplier_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    category_id = serializers.IntegerField(required=False, allow_null=True)
    is_paid = serializers.BooleanField(required=False)
    issued_at = serializers.DateTimeField(required=False, allow_null=True)
    due_date = serializers.DateTimeField(required=False, allow_null=True)
    subtotal_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    total_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    tax_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    tip_amount = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True,
    )
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True)
    payment_method = serializers.CharField(max_length=30, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    status = serializers.ChoiceField(
        choices=Document.STATUS_CHOICES, required=False,
    )
    line_items = ExpenseItemSerializer(many=True, required=False)

    def validate_status(self, value):
        valid_statuses = {
            Document.STATUS_PENDING,
            Document.STATUS_COMPLETED,
            Document.STATUS_REVIEW_REQUIRED,
        }
        if value not in valid_statuses:
            raise serializers.ValidationError(
                'No puedes establecer el estado solicitado manualmente.'
            )
        return value


class DocumentStatsSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    pending = serializers.IntegerField()
    processing = serializers.IntegerField()
    completed = serializers.IntegerField()
    review_required = serializers.IntegerField()
    failed = serializers.IntegerField()
    paid = serializers.IntegerField()
    unpaid = serializers.IntegerField()
    total_amount_sum = serializers.DecimalField(max_digits=16, decimal_places=2)
