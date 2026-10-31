from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import generics, status, views, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from apps.common.permissions import IsVerifiedUser
from apps.common.pagination import StandardPagination
from apps.common.ratelimit import (
    ratelimit_auth_login,
    ratelimit_auth_password_reset,
    ratelimit_auth_register,
    ratelimit_auth_verify_email,
)

from .serializers import (
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    EmailVerificationSerializer,
    LogoutSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ResendVerificationEmailSerializer,
    UserAdminSerializer,
    UserMeSerializer,
    UserRegistrationSerializer,
    UserUpdateSerializer,
)

User = get_user_model()


sensitive = method_decorator(
    sensitive_post_parameters(
        'password', 'password_confirm', 'current_password', 'new_password',
        'confirm_password', 'refresh',
    ),
    name='dispatch',
)


@sensitive
@ratelimit_auth_register()
class RegisterView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = UserRegistrationSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        payload = {
            'status': 'success',
            'message': 'Registro exitoso.',
            'user': UserMeSerializer(instance=user, context={'request': request}).data,
        }

        if settings.EMAIL_VERIFICATION_REQUIRED and not user.is_email_verified:
            # No se emiten tokens: la cuenta debe verificar el correo antes de ingresar.
            payload['message'] = (
                'Te enviamos un correo de verificación. Revisa tu bandeja de entrada '
                'y haz clic en el enlace para activar tu cuenta antes de iniciar sesión.'
            )
            payload['requires_email_verification'] = True
            payload['tokens'] = None
            return Response(payload, status=status.HTTP_201_CREATED)

        refresh = RefreshToken.for_user(user)
        payload['tokens'] = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
        }
        return Response(payload, status=status.HTTP_201_CREATED)


@sensitive
@ratelimit_auth_login()
class LoginView(generics.GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = CustomTokenObtainPairSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated = serializer.validated_data
        return Response(
            {
                'status': 'success',
                'message': 'Inicio de sesión exitoso.',
                'user': validated['user'],
                'tokens': {
                    'access': validated['access'],
                    'refresh': validated['refresh'],
                },
            },
            status=status.HTTP_200_OK,
        )


@sensitive
class LogoutView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = LogoutSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data['refresh'])
            token.blacklist()
        except Exception:
            pass
        return Response(
            {'status': 'success', 'message': 'Sesión cerrada correctamente.'},
            status=status.HTTP_200_OK,
        )


@sensitive
class TokenRefreshCustomView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == 200:
            data = response.data
            response.data = {
                'status': 'success',
                'message': 'Tokens actualizados.',
                'tokens': data,
            }
        return response


class MeView(generics.RetrieveUpdateAPIView):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return UserUpdateSerializer
        return UserMeSerializer

    def get_object(self):
        user = self.request.user
        if hasattr(user, 'touch_activity'):
            try:
                user.touch_activity()
            except Exception:
                pass
        return user

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        return Response(
            {'status': 'success', 'message': 'Perfil de usuario.', 'data': serializer.data},
            status=status.HTTP_200_OK,
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(
            {
                'status': 'success',
                'message': 'Perfil actualizado correctamente.',
                'data': UserMeSerializer(instance=instance, context={'request': request}).data,
            },
            status=status.HTTP_200_OK,
        )

    def perform_update(self, serializer):
        serializer.save()


@sensitive
class ChangePasswordView(generics.UpdateAPIView):
    permission_classes = [IsAuthenticated, IsVerifiedUser]
    serializer_class = ChangePasswordSerializer
    http_method_names = ['put', 'patch']

    def get_object(self):
        return self.request.user

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        serializer = self.get_serializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'status': 'success', 'message': 'Contraseña actualizada correctamente.'},
            status=status.HTTP_200_OK,
        )


@sensitive
@ratelimit_auth_password_reset()
class PasswordResetRequestView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = PasswordResetRequestSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {
                'status': 'success',
                'message': (
                    'Si existe una cuenta activa con ese correo electrónico, '
                    'hemos enviado las instrucciones para restablecer la contraseña.'
                ),
            },
            status=status.HTTP_200_OK,
        )


@sensitive
class PasswordResetConfirmView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = PasswordResetConfirmSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {'status': 'success', 'message': 'Contraseña restablecida correctamente.'},
            status=status.HTTP_200_OK,
        )


@ratelimit_auth_verify_email()
class EmailVerificationView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = EmailVerificationSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                'status': 'success',
                'message': 'Correo electrónico verificado correctamente.',
                'data': {
                    'is_email_verified': True,
                    'email': user.email,
                },
            },
            status=status.HTTP_200_OK,
        )


class ResendVerificationEmailView(generics.CreateAPIView):
    permission_classes = [AllowAny]
    serializer_class = ResendVerificationEmailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            {
                'status': 'success',
                'message': (
                    'Si existe una cuenta pendiente de verificación, '
                    'hemos reenviado el correo de confirmación.'
                ),
            },
            status=status.HTTP_200_OK,
        )


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsAdminUser]
    serializer_class = UserAdminSerializer
    pagination_class = StandardPagination
    search_fields = ['=email', 'first_name', 'last_name', '=username']
    ordering_fields = ['-date_joined', 'date_joined', '-last_login', 'email']
    ordering = ['-date_joined']
    lookup_field = 'pk'

    def get_queryset(self):
        return User.objects.all().order_by('-date_joined')

    @action(detail=True, methods=['post'], url_path='toggle-active')
    def toggle_active(self, request, pk=None):
        user = self.get_object()
        if user.pk == request.user.pk:
            return Response(
                {'status': 'error', 'message': 'No puedes desactivar tu propia cuenta.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.is_active = not user.is_active
        user.save(update_fields=['is_active', 'updated_at'])
        return Response(
            {
                'status': 'success',
                'message': f"Cuenta {'activada' if user.is_active else 'desactivada'} correctamente.",
                'data': {'is_active': user.is_active},
            },
            status=status.HTTP_200_OK,
        )
