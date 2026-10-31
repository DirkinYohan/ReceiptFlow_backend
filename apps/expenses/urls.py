from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import ExpenseViewSet

app_name = 'expenses'

router = DefaultRouter()
router.register(r'', ExpenseViewSet, basename='expense')

urlpatterns = [
    path('', include(router.urls)),
]
