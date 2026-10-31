from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
from django.conf import settings
from django.urls import reverse
from django.utils.crypto import get_random_string


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        extra_fields.setdefault('username', email.split('@')[0])
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_email_verified', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        extra_fields.setdefault('username', email.split('@')[0])
        extra_fields.setdefault('first_name', 'Admin')
        extra_fields.setdefault('last_name', 'ReceiptFlow')
        return self._create_user(email, password, **extra_fields)


def generate_email_token():
    return get_random_string(64)


def generate_password_reset_token():
    return get_random_string(64)


class User(AbstractUser):
    first_name = models.CharField('Nombres', max_length=150)
    last_name = models.CharField('Apellidos', max_length=150)
    email = models.EmailField('Correo electrónico', unique=True, db_index=True)
    is_email_verified = models.BooleanField('Correo verificado', default=False)
    email_verified_at = models.DateTimeField('Fecha verificación email', null=True, blank=True)
    email_verification_token = models.CharField(
        max_length=128, blank=True, default=generate_email_token, db_index=True
    )
    email_verification_sent_at = models.DateTimeField(null=True, blank=True)
    password_reset_token = models.CharField(max_length=128, blank=True, db_index=True)
    password_reset_sent_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField('Última actividad', null=True, blank=True)
    avatar = models.ImageField(
        upload_to='avatars/', null=True, blank=True, max_length=255
    )
    created_at = models.DateTimeField('Fecha de creación', auto_now_add=True)
    updated_at = models.DateTimeField('Última actualización', auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = 'Usuario'
        verbose_name_plural = 'Usuarios'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['email_verification_token']),
            models.Index(fields=['password_reset_token']),
        ]

    def __str__(self):
        return f'{self.full_name} ({self.email})'

    @property
    def full_name(self):
        return f'{self.first_name} {self.last_name}'.strip() or self.email

    @property
    def initials(self):
        first = (self.first_name or '').strip()[:1]
        last = (self.last_name or '').strip()[:1]
        return (first + last).upper() or self.email[:1].upper()

    def mark_email_verified(self):
        self.is_email_verified = True
        self.email_verified_at = timezone.now()
        self.email_verification_token = ''
        self.save(update_fields=[
            'is_email_verified', 'email_verified_at', 'email_verification_token', 'updated_at'
        ])

    def regenerate_email_verification_token(self):
        self.email_verification_token = generate_email_token()
        self.email_verification_sent_at = timezone.now()
        self.save(update_fields=[
            'email_verification_token', 'email_verification_sent_at', 'updated_at'
        ])
        return self.email_verification_token

    def regenerate_password_reset_token(self):
        self.password_reset_token = generate_password_reset_token()
        self.password_reset_sent_at = timezone.now()
        self.save(update_fields=[
            'password_reset_token', 'password_reset_sent_at', 'updated_at'
        ])
        return self.password_reset_token

    def clear_password_reset_token(self):
        self.password_reset_token = ''
        self.password_reset_sent_at = None
        self.save(update_fields=['password_reset_token', 'password_reset_sent_at', 'updated_at'])

    def touch_activity(self):
        self.last_activity_at = timezone.now()
        self.save(update_fields=['last_activity_at'])

    def email_verification_has_expired(self) -> bool:
        if not self.email_verification_sent_at:
            return True
        delta = timezone.now() - self.email_verification_sent_at
        return delta.total_seconds() > settings.EMAIL_VERIFICATION_EXPIRE_HOURS * 3600

    def password_reset_has_expired(self) -> bool:
        if not self.password_reset_sent_at:
            return True
        delta = timezone.now() - self.password_reset_sent_at
        return delta.total_seconds() > settings.PASSWORD_RESET_EXPIRE_HOURS * 3600
