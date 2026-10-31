from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.users.views import UserViewSet

from .views import (
    ChangePasswordView,
    EmailVerificationView,
    LoginView,
    LogoutView,
    MeView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RegisterView,
    ResendVerificationEmailView,
    TokenRefreshCustomView,
)

app_name = 'users'

router = DefaultRouter()
router.register(r'admin', UserViewSet, basename='user-admin')

profile_urlpatterns = [
    path('me/', MeView.as_view(), name='me'),
    path('me/change-password/', ChangePasswordView.as_view(), name='change-password'),
]

auth_urlpatterns = [
    path('register/', RegisterView.as_view(), name='auth-register'),
    path('login/', LoginView.as_view(), name='auth-login'),
    path('logout/', LogoutView.as_view(), name='auth-logout'),
    path('refresh/', TokenRefreshCustomView.as_view(), name='auth-refresh'),
    path('verify-email/', EmailVerificationView.as_view(), name='auth-verify-email'),
    path(
        'resend-verification-email/',
        ResendVerificationEmailView.as_view(),
        name='auth-resend-verification',
    ),
    path(
        'password-reset/',
        PasswordResetRequestView.as_view(),
        name='auth-password-reset',
    ),
    path(
        'password-reset/request/',
        PasswordResetRequestView.as_view(),
        name='auth-password-reset-request',
    ),
    path(
        'password-reset/confirm/',
        PasswordResetConfirmView.as_view(),
        name='auth-password-reset-confirm',
    ),
]

urlpatterns = (
    auth_urlpatterns
    + profile_urlpatterns
    + [path('', include(router.urls))]
)
