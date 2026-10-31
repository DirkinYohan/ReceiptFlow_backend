import logging
import re

from django.conf import settings
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from .models import User as UserModel
from .services import email_service

User = get_user_model()

logger = logging.getLogger(__name__)


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = User.USERNAME_FIELD

    def validate(self, attrs):
        credentials = {
            'email': attrs.get('email'),
            'password': attrs.get('password'),
        }
        if not credentials['email'] or not credentials['password']:
            raise serializers.ValidationError(
                {'email': 'El correo y la contraseña son requeridos.'}
            )

        try:
            user = User.objects.get(email=credentials['email'])
        except User.DoesNotExist:
            raise AuthenticationFailed(
                'No existe una cuenta con este correo electrónico.'
            )

        if not user.is_active:
            raise AuthenticationFailed(
                'Tu cuenta se encuentra desactivada.'
            )

        if not user.check_password(credentials['password']):
            raise AuthenticationFailed(
                'Contraseña incorrecta.'
            )

        if settings.EMAIL_VERIFICATION_REQUIRED and not user.is_email_verified:
            raise AuthenticationFailed(
                'Debes verificar tu correo electrónico antes de iniciar sesión. '
                'Revisa tu bandeja de entrada y haz clic en el enlace de verificación.'
            )

        data = super().validate(attrs)
        user.touch_activity()
        data['user'] = UserMeSerializer(instance=self.user, context=self.context).data
        return data

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['email'] = user.email
        token['name'] = user.full_name
        token['is_verified'] = user.is_email_verified
        return token


class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, style={'input_type': 'password'}
    )
    password_confirm = serializers.CharField(
        write_only=True, required=True, style={'input_type': 'password'}
    )
    accept_terms = serializers.BooleanField(write_only=True, required=True)

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'password',
            'password_confirm',
            'first_name',
            'last_name',
            'username',
            'accept_terms',
        ]
        read_only_fields = ['id', 'username']
        extra_kwargs = {
            'first_name': {'required': True, 'allow_blank': False},
            'last_name': {'required': True, 'allow_blank': False},
        }

    def validate_email(self, value):
        email = value.lower().strip()
        if User.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError(
                'Ya existe un usuario registrado con este correo electrónico.'
            )
        return email

    def validate_first_name(self, value):
        return value.strip()

    def validate_last_name(self, value):
        return value.strip()

    def validate_accept_terms(self, value):
        if not value:
            raise serializers.ValidationError('Debes aceptar los términos y condiciones.')
        return value

    def validate(self, data):
        if data.get('password') != data.get('password_confirm'):
            raise serializers.ValidationError(
                {'password_confirm': 'Las contraseñas no coinciden.'}
            )
        try:
            validate_password(data['password'])
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({'password': exc.messages})
        return data

    def create(self, validated_data):
        validated_data.pop('password_confirm', None)
        validated_data.pop('accept_terms', None)
        email = validated_data['email']
        username_base = email.split('@')[0]
        username = username_base[:150]
        counter = 1
        while User.objects.filter(username=username).exists():
            suffix = f'{counter}'
            username = f'{username_base[:150 - len(suffix)]}{suffix}'
            counter += 1
        validated_data['username'] = username
        validated_data['is_active'] = True
        from django.conf import settings as dj_settings
        if not dj_settings.EMAIL_VERIFICATION_REQUIRED:
            validated_data['is_email_verified'] = True
            validated_data['email_verified_at'] = timezone.now()
            validated_data['email_verification_token'] = ''
        user = User.objects.create_user(**validated_data)
        if dj_settings.EMAIL_VERIFICATION_REQUIRED:
            try:
                email_service.send_email_verification(user)
            except Exception:
                logger.exception(
                    'No se pudo enviar el correo de verificación a %s', user.email
                )
        else:
            try:
                email_service.send_welcome(user)
            except Exception:
                logger.exception(
                    'No se pudo enviar el correo de bienvenida a %s', user.email
                )
        return user


class UserMeSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    initials = serializers.CharField(read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'username',
            'first_name',
            'last_name',
            'full_name',
            'initials',
            'avatar',
            'avatar_url',
            'is_active',
            'is_staff',
            'is_superuser',
            'is_email_verified',
            'email_verified_at',
            'last_login',
            'last_activity_at',
            'date_joined',
            'created_at',
            'updated_at',
        ]
        read_only_fields = [
            'id', 'email', 'is_staff', 'is_superuser', 'is_email_verified',
            'email_verified_at', 'last_login', 'last_activity_at',
            'date_joined', 'created_at', 'updated_at', 'username',
        ]

    def get_avatar_url(self, obj):
        if obj.avatar and hasattr(obj.avatar, 'url'):
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.avatar.url)
            return obj.avatar.url
        return None


class UserUpdateSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField(read_only=True)
    full_name = serializers.CharField(read_only=True)
    initials = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id',
            'first_name',
            'last_name',
            'avatar',
            'full_name',
            'initials',
            'avatar_url',
        ]
        read_only_fields = ['id', 'full_name', 'initials', 'avatar_url']
        extra_kwargs = {
            'first_name': {'required': True, 'allow_blank': False},
            'last_name': {'required': True, 'allow_blank': False},
            'avatar': {'required': False, 'allow_null': True},
        }

    def get_avatar_url(self, obj):
        if obj.avatar and hasattr(obj.avatar, 'url'):
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.avatar.url)
            return obj.avatar.url
        return None


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, required=True)
    new_password = serializers.CharField(write_only=True, required=True)
    confirm_password = serializers.CharField(write_only=True, required=True)

    def validate_current_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError('La contraseña actual es incorrecta.')
        return value

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError(
                {'confirm_password': 'Las contraseñas nuevas no coinciden.'}
            )
        if data['current_password'] == data['new_password']:
            raise serializers.ValidationError(
                {'new_password': 'La nueva contraseña debe ser diferente a la actual.'}
            )
        try:
            validate_password(data['new_password'], user=self.context['request'].user)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({'new_password': exc.messages})
        return data

    def save(self, **kwargs):
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save(update_fields=['password', 'updated_at'])
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        return value.lower().strip()

    def save(self, **kwargs):
        email = self.validated_data['email']
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            return None
        email_service.send_password_reset(user)
        return user


class PasswordResetConfirmSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    token = serializers.CharField(required=True, max_length=128)
    new_password = serializers.CharField(write_only=True, required=True)
    confirm_password = serializers.CharField(write_only=True, required=True)

    def validate_email(self, value):
        return value.lower().strip()

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError(
                {'confirm_password': 'Las contraseñas no coinciden.'}
            )
        try:
            user = User.objects.get(
                email__iexact=data['email'],
                password_reset_token=data['token'],
                is_active=True,
            )
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {'token': 'Token de recuperación inválido.'}
            )
        if user.password_reset_has_expired():
            raise serializers.ValidationError(
                {'token': 'El token de recuperación ha expirado.'}
            )
        try:
            validate_password(data['new_password'], user=user)
        except serializers.ValidationError as exc:
            raise serializers.ValidationError({'new_password': exc.messages})
        self._user = user
        return data

    def save(self, **kwargs):
        user = self._user
        user.set_password(self.validated_data['new_password'])
        user.clear_password_reset_token()
        return user


class EmailVerificationSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    token = serializers.CharField(required=True, max_length=128)

    def validate_email(self, value):
        return value.lower().strip()

    def validate(self, data):
        try:
            user = User.objects.get(
                email__iexact=data['email'],
                email_verification_token=data['token'],
                is_active=True,
            )
        except User.DoesNotExist:
            raise serializers.ValidationError(
                {'token': 'Token de verificación inválido.'}
            )
        if user.is_email_verified:
            self._user = user
            return data
        if user.email_verification_has_expired():
            raise serializers.ValidationError(
                {'token': 'El token de verificación ha expirado.'}
            )
        self._user = user
        return data

    def save(self, **kwargs):
        user = self._user
        if not user.is_email_verified:
            user.mark_email_verified()
            try:
                email_service.send_welcome(user)
            except Exception:
                logger.exception(
                    'No se pudo enviar el correo de bienvenida a %s', user.email
                )
        return user


class ResendVerificationEmailSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        return value.lower().strip()

    def save(self, **kwargs):
        email = self.validated_data['email']
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
        except User.DoesNotExist:
            return None
        if user.is_email_verified:
            return user
        email_service.send_email_verification(user)
        return user


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=True)

    def validate_refresh(self, value):
        if not value or not isinstance(value, str):
            raise serializers.ValidationError('Refresh token es requerido.')
        return value


class UserAdminSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'email', 'first_name', 'last_name', 'full_name',
            'is_active', 'is_staff', 'is_email_verified', 'date_joined',
            'last_login', 'created_at',
        ]
        read_only_fields = ['id', 'date_joined', 'last_login', 'created_at']
