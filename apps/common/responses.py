from __future__ import annotations

from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views import View

from rest_framework import status


@method_decorator(csrf_exempt, name='dispatch')
class RateLimitedView(View):
    http_method_names = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options']

    def dispatch(self, request, *args, **kwargs):
        return JsonResponse(
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


def rate_limited_view(request, exception=None):
    return JsonResponse(
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
