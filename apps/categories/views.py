from django.db.models import Count, Q
from django_filters import rest_framework as filters
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import StandardPagination
from apps.common.permissions import IsOwnerOrAdmin

from .models import Category
from .serializers import CategorySerializer


class CategoryFilter(filters.FilterSet):
    scope = filters.CharFilter(field_name='scope')
    is_active = filters.BooleanFilter(field_name='is_active')

    class Meta:
        model = Category
        fields = ['scope', 'is_active']


class CategoryViewSet(viewsets.ModelViewSet):
    serializer_class = CategorySerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    pagination_class = StandardPagination
    filter_backends = list(viewsets.ModelViewSet.filter_backends) + [
        filters.DjangoFilterBackend,
    ]
    filterset_class = CategoryFilter
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'sort_order', '-created_at', 'created_at']
    ordering = ['sort_order', 'name']
    lookup_field = 'pk'

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Category.objects.none()
        user = self.request.user
        qs = Category.objects.filter(
            Q(scope=Category.SCOPE_SYSTEM) | Q(user=user)
        ).select_related('user').annotate(
            usage_count=Count('expenses', distinct=True) + Count('documents', distinct=True)
        )
        return qs

    def perform_create(self, serializer):
        from django.utils.text import slugify
        name = serializer.validated_data.get('name', '')
        slug = slugify(name)[:100] if name else ''
        serializer.save(
            user=self.request.user,
            scope=Category.SCOPE_USER,
            slug=slug,
        )

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if not instance.is_editable and not request.user.is_staff:
            return Response(
                {'status': 'error', 'message': 'No puedes modificar categorías del sistema.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if not instance.is_editable and not request.user.is_staff:
            return Response(
                {'status': 'error', 'message': 'No puedes eliminar categorías del sistema.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='available')
    def available(self, request):
        user = request.user
        qs = Category.objects.filter(
            Q(scope=Category.SCOPE_SYSTEM, is_active=True) |
            Q(user=user, scope=Category.SCOPE_USER, is_active=True)
        ).order_by('scope', 'sort_order', 'name')
        data = [
            {
                'id': cat.id,
                'name': cat.name,
                'icon': cat.icon,
                'color': cat.color,
                'scope': cat.scope,
                'is_system': cat.is_system,
            }
            for cat in qs
        ]
        return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)
