import logging
from typing import Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

User = get_user_model()


class EmailService:
    @staticmethod
    def _send(
        subject: str,
        to_email: str,
        template_name: str,
        context: dict,
    ) -> bool:
        try:
            html_message = render_to_string(template_name, context)
            plain_message = strip_tags(html_message)
            email = EmailMultiAlternatives(
                subject=subject,
                body=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[to_email],
            )
            email.attach_alternative(html_message, 'text/html')
            email.send(fail_silently=False)
            return True
        except Exception:
            logger.exception('Error sending email to %s with template %s', to_email, template_name)
            return False

    @classmethod
    def send_due_reminder(cls, user: User, bills: list, missing_recurring: list) -> bool:
        overdue = sum(1 for bill in bills if bill['overdue'])
        subject = (
            f'Tienes {overdue} factura(s) vencida(s) - ReceiptFlow'
            if overdue else 'Facturas por vencer - ReceiptFlow'
        )
        context = {
            'full_name': user.full_name,
            'bills': bills,
            'missing_recurring': missing_recurring,
            'frontend_url': settings.FRONTEND_URL,
        }
        return cls._send(subject, user.email, 'emails/due_reminder.html', context)

    @classmethod
    def send_budget_alert(cls, user: User, budget, status: dict) -> bool:
        exceeded = status['state'] == 'exceeded'
        subject = (
            f'Superaste tu presupuesto de {budget.category.name} - ReceiptFlow'
            if exceeded else
            f'Llevas el {status["percentage"]}% de tu presupuesto de {budget.category.name} - ReceiptFlow'
        )
        context = {
            'full_name': user.full_name,
            'category': budget.category.name,
            'budget': budget.monthly_amount,
            'currency': budget.currency,
            'spent': status['spent'],
            'percentage': status['percentage'],
            'exceeded': exceeded,
            'frontend_url': settings.FRONTEND_URL,
        }
        return cls._send(subject, user.email, 'emails/budget_alert.html', context)

    @classmethod
    def send_welcome(cls, user: User) -> bool:
        subject = '¡Bienvenido a ReceiptFlow!'
        context = {
            'user': user,
            'full_name': user.full_name,
            'frontend_url': settings.FRONTEND_URL,
        }
        return cls._send(subject, user.email, 'emails/welcome.html', context)

    @classmethod
    def send_email_verification(cls, user: User) -> bool:
        token = user.regenerate_email_verification_token()
        verify_url = f'{settings.FRONTEND_URL}/verify-email?token={token}&email={user.email}'
        subject = 'Verifica tu correo electrónico - ReceiptFlow'
        context = {
            'user': user,
            'full_name': user.full_name,
            'verify_url': verify_url,
            'token': token,
            'expire_hours': settings.EMAIL_VERIFICATION_EXPIRE_HOURS,
            'frontend_url': settings.FRONTEND_URL,
        }
        ok = cls._send(subject, user.email, 'emails/email_verification.html', context)
        if not ok:
            logger.warning(
                'Verification email could not be sent. Token generated for %s: %s',
                user.email,
                token[:8] + '...',
            )
        return ok

    @classmethod
    def send_password_reset(cls, user: User) -> bool:
        token = user.regenerate_password_reset_token()
        reset_url = f'{settings.FRONTEND_URL}/reset-password?token={token}&email={user.email}'
        subject = 'Restablece tu contraseña - ReceiptFlow'
        context = {
            'user': user,
            'full_name': user.full_name,
            'reset_url': reset_url,
            'token': token,
            'expire_hours': settings.PASSWORD_RESET_EXPIRE_HOURS,
            'frontend_url': settings.FRONTEND_URL,
        }
        ok = cls._send(subject, user.email, 'emails/password_reset.html', context)
        if not ok:
            logger.warning(
                'Password reset email could not be sent. Token generated for %s: %s',
                user.email,
                token[:8] + '...',
            )
        return ok


email_service = EmailService
