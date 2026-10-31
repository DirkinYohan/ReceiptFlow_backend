from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .budget_views import CategoryBudgetViewSet

app_name = 'budgets'

router = DefaultRouter()
router.register(r'', CategoryBudgetViewSet, basename='budget')

urlpatterns = [
    path('', include(router.urls)),
]
