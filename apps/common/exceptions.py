from rest_framework.views import exception_handler
from rest_framework.exceptions import APIException
from rest_framework import status
from django.db import IntegrityError, DatabaseError
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
import logging

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        data = {
            'status': 'error',
            'code': response.status_code,
            'message': _get_exception_message(exc),
            'errors': _format_errors(exc, response),
        }
        response.data = data
        return response

    try:
        from django_ratelimit.exceptions import Ratelimited
        if isinstance(exc, Ratelimited):
            from rest_framework.response import Response
            return Response(
                {
                    'status': 'error',
                    'code': status.HTTP_429_TOO_MANY_REQUESTS,
                    'message': (
                        'Has excedido el número máximo de solicitudes permitidas. '
                        'Por favor, inténtalo de nuevo más tarde.'
                    ),
                    'errors': [],
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
    except ImportError:
        pass

    if isinstance(exc, (IntegrityError, DatabaseError)):
        logger.exception('Database error in %s', context.get('view'))
        from rest_framework.response import Response
        return Response(
            {
                'status': 'error',
                'code': status.HTTP_409_CONFLICT,
                'message': 'Error de integridad en la base de datos.',
                'errors': [],
            },
            status=status.HTTP_409_CONFLICT,
        )

    if isinstance(exc, DjangoValidationError):
        from rest_framework.response import Response
        return Response(
            {
                'status': 'error',
                'code': status.HTTP_400_BAD_REQUEST,
                'message': 'Error de validación.',
                'errors': exc.messages if hasattr(exc, 'messages') else [str(exc)],
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    logger.exception('Unhandled exception in %s: %s', context.get('view'), exc)
    from rest_framework.response import Response
    return Response(
        {
            'status': 'error',
            'code': status.HTTP_500_INTERNAL_SERVER_ERROR,
            'message': 'Ocurrió un error interno en el servidor.',
            'errors': [],
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


def _get_exception_message(exc) -> str:
    if isinstance(exc, APIException):
        default_detail = getattr(exc, 'default_detail', str(exc))
        detail = getattr(exc, 'detail', default_detail)
        if isinstance(detail, (list, dict)):
            return 'Error de validación en los datos enviados.'
        return str(detail)
    if isinstance(exc, PermissionDenied):
        return 'No tienes permiso para realizar esta acción.'
    return str(exc) if exc else 'Error desconocido.'


def _format_errors(exc, response) -> list:
    errors = []
    detail = getattr(exc, 'detail', None) if isinstance(exc, APIException) else None
    if detail is None:
        return errors
    if isinstance(detail, list):
        for idx, item in enumerate(detail):
            errors.append({'field': f'non_field_errors_{idx}', 'message': str(item)})
    elif isinstance(detail, dict):
        for field, messages in detail.items():
            if isinstance(messages, list):
                for msg in messages:
                    errors.append({'field': field, 'message': str(msg)})
            else:
                errors.append({'field': field, 'message': str(messages)})
    else:
        errors.append({'field': 'non_field_errors', 'message': str(detail)})
    return errors
