"""Scheduled maintenance without a Celery worker (Render's free plan has none).

An external scheduler (e.g. cron-job.org, free) calls POST /api/internal/run-scheduled-tasks/
once a day with the header X-Cron-Secret. The same work is available as
`python manage.py run_scheduled_tasks` for a Render Cron Job or a local run.
"""
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
            results[name] = task()  # direct call: runs now, in this process
        except Exception:  # one failing task must not stop the others
            logger.exception('Scheduled task %s failed', name)
            results[name] = 'error'
    return results


@csrf_exempt
@require_POST
def scheduled_tasks_view(request):
    expected = getattr(settings, 'CRON_SECRET', '')
    received = request.headers.get('X-Cron-Secret', '')
    # 404 (not 403) so the endpoint does not reveal itself without the right secret.
    if not expected or not hmac.compare_digest(received.encode(), expected.encode()):
        return JsonResponse({'detail': 'Not found.'}, status=404)
    return JsonResponse({'status': 'ok', 'results': run_scheduled_tasks()})


@require_GET
def health_view(request):
    """Liveness check for Render: answers without touching the database."""
    return JsonResponse({'status': 'ok'})
