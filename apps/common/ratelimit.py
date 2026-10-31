from __future__ import annotations

from typing import Callable
from django.conf import settings
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit


def get_ratelimit_rate(setting_key: str, default: str) -> Callable:
    """Return a callable that fetches the rate limit dynamically from settings."""
    def _rate_getter(group, request):
        return getattr(settings, setting_key, default)
    return _rate_getter


def ratelimit_auth_login():
    return method_decorator(
        ratelimit(
            key='ip',
            rate=get_ratelimit_rate('RATELIMIT_AUTH_LOGIN', '10/m'),
            method=['POST'],
            block=True,
        ),
        name='post',
    )


def ratelimit_auth_register():
    return method_decorator(
        ratelimit(
            key='ip',
            rate=get_ratelimit_rate('RATELIMIT_AUTH_REGISTER', '5/m'),
            method=['POST'],
            block=True,
        ),
        name='post',
    )


def ratelimit_auth_password_reset():
    return method_decorator(
        ratelimit(
            key='ip',
            rate=get_ratelimit_rate('RATELIMIT_AUTH_PASSWORD_RESET', '5/m'),
            method=['POST'],
            block=True,
        ),
        name='post',
    )


def ratelimit_auth_verify_email():
    return method_decorator(
        ratelimit(
            key='ip',
            rate=get_ratelimit_rate('RATELIMIT_AUTH_VERIFY_EMAIL', '10/m'),
            method=['POST'],
            block=True,
        ),
        name='post',
    )


def ratelimit_documents_create():
    return ratelimit(
        key='user_or_ip',
        rate=get_ratelimit_rate('RATELIMIT_DOCUMENTS_CREATE', '20/m'),
        method=['POST'],
        block=True,
    )


def ratelimit_documents_process():
    return ratelimit(
        key='user_or_ip',
        rate=get_ratelimit_rate('RATELIMIT_DOCUMENTS_PROCESS', '15/m'),
        method=['POST'],
        block=True,
    )
