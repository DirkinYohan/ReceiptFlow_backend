from django.db.models import Q
from rest_framework import serializers, status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .budgets import budget_status
from .models import Category, CategoryBudget


class CategoryBudgetSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    category_color = serializers.CharField(source='category.color', read_only=True)
    status = serializers.SerializerMethodField()

    class Meta:
        model = CategoryBudget
        fields = [
            'id', 'category', 'category_name', 'category_color',
            'monthly_amount', 'currency', 'status', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'category_name', 'category_color', 'status', 'created_at', 'updated_at']

    def get_status(self, obj):
        return budget_status(obj)

    def validate_category(self, value):
        user = self.context['request'].user
        allowed = Category.objects.filter(Q(scope=Category.SCOPE_SYSTEM) | Q(user=user), pk=value.pk)
        if not allowed.exists():
            raise serializers.ValidationError('Categoría no encontrada.')
        return value

    def validate_monthly_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError('El presupuesto debe ser mayor que cero.')
        return value


class CategoryBudgetViewSet(viewsets.ModelViewSet):
    serializer_class = CategoryBudgetSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return CategoryBudget.objects.none()
        return CategoryBudget.objects.filter(user=self.request.user).select_related('category', 'user')

    def create(self, request, *args, **kwargs):
        # One budget per category: posting again for the same category updates it.
        existing = self.get_queryset().filter(category_id=request.data.get('category')).first()
        serializer = self.get_serializer(existing, data=request.data, partial=existing is not None)
        serializer.is_valid(raise_exception=True)
        budget = serializer.save(user=request.user)
        return Response(
            {'status': 'success', 'data': self.get_serializer(budget).data},
            status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED,
        )

    def list(self, request, *args, **kwargs):
        data = self.get_serializer(self.get_queryset(), many=True).data
        return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)
