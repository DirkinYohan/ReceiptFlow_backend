from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .dashboard_views import DashboardViewSet

app_name = 'reports'

router = DefaultRouter()
router.register(r'dashboard', DashboardViewSet, basename='dashboard')

urlpatterns = [
    path('', include(router.urls)),
]
