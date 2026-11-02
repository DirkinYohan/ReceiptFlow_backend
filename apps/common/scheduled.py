import hmac
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

logger = logging.getLogger(__name__)


def run_scheduled_tasks() -> dict:
    from apps.documents.tasks import (
        cleanup_old_token_blacklist,
        mark_stuck_processing_documents,
        send_due_reminders,
    )

    results = {}
    for name, task in (
        ('stuck_documents', mark_stuck_processing_documents),
        ('due_reminders', send_due_reminders),
        ('token_blacklist', cleanup_old_token_blacklist),
    ):
        try:
            results[name] = task()
        except Exception:
            logger.exception('Scheduled task %s failed', name)
            results[name] = 'error'
    return results


@csrf_exempt
@require_POST
def scheduled_tasks_view(request):
    expected = getattr(settings, 'CRON_SECRET', '')
    received = request.headers.get('X-Cron-Secret', '')
    if not expected or not hmac.compare_digest(received.encode(), expected.encode()):
        return JsonResponse({'detail': 'Not found.'}, status=404)
    return JsonResponse({'status': 'ok', 'results': run_scheduled_tasks()})


@require_GET
def health_view(request):
    return JsonResponse({'status': 'ok'})
