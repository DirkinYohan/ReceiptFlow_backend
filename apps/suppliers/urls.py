from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import SupplierViewSet

app_name = 'suppliers'

router = DefaultRouter()
router.register(r'', SupplierViewSet, basename='supplier')

urlpatterns = [
    path('', include(router.urls)),
]
