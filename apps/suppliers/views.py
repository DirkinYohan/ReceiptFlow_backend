from django.db import models as django_models
from django.db.models import Count
from django_filters import rest_framework as filters
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.common.pagination import StandardPagination
from apps.common.permissions import IsOwnerOrAdmin

from .models import Supplier
from .serializers import (
    SupplierAutocompleteSerializer,
    SupplierFilter,
    SupplierSerializer,
)


class SupplierViewSet(viewsets.ModelViewSet):
    serializer_class = SupplierSerializer
    permission_classes = [IsAuthenticated, IsOwnerOrAdmin]
    pagination_class = StandardPagination
    filter_backends = list(viewsets.ModelViewSet.filter_backends) + [
        filters.DjangoFilterBackend,
    ]
    filterset_class = SupplierFilter
    search_fields = ['name', 'tax_id', 'email', 'phone', 'address']
    ordering_fields = [
        'name', '-created_at', 'created_at', '-updated_at', 'updated_at',
        '-document_count', '-expense_count',
    ]
    ordering = ['name']
    lookup_field = 'pk'

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Supplier.objects.none()
        return Supplier.objects.filter(user=self.request.user).annotate(
            _document_count=Count('documents', distinct=True),
            _expense_count=Count('expenses', distinct=True),
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'], url_path='autocomplete')
    def autocomplete(self, request):
        query = (request.query_params.get('q') or '').strip()
        qs = self.get_queryset()
        if query:
            qs = qs.filter(
                django_models.Q(name__icontains=query)
                | django_models.Q(tax_id__icontains=query)
                | django_models.Q(email__icontains=query)
            )
        qs = qs.order_by('name')[:25]
        data = SupplierAutocompleteSerializer(qs, many=True).data
        return Response({'status': 'success', 'data': data}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='toggle-favorite')
    def toggle_favorite(self, request, pk=None):
        supplier = self.get_object()
        supplier.is_favorite = not supplier.is_favorite
        supplier.save(update_fields=['is_favorite', 'updated_at'])
        return Response(
            {
                'status': 'success',
                'message': 'Favorito actualizado.',
                'data': {'is_favorite': supplier.is_favorite},
            },
            status=status.HTTP_200_OK,
        )
