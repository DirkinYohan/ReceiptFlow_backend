from __future__ import annotations

import os
from datetime import timedelta

from celery import Celery
from celery.schedules import crontab

from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.development')

app = Celery('receiptflow')

app.config_from_object('django.conf:settings', namespace='CELERY')

app.autodiscover_tasks(lambda: [a for a in settings.INSTALLED_APPS if a.startswith('apps.')])

app.conf.beat_enabled = getattr(settings, 'CELERY_BEAT_ENABLED', True)
stuck_hours = getattr(settings, 'MAINTENANCE_STUCK_PROCESSING_HOURS', 1)
beat_schedule = {
    'send-due-reminders': {
        'task': 'apps.documents.tasks.send_due_reminders',
        'schedule': crontab(hour=8, minute=0),
        'options': {'expires': 3600},
    },
    'mark-stuck-processing-documents': {
        'task': 'apps.documents.tasks.mark_stuck_processing_documents',
        'schedule': crontab(minute='*/10'),
        'args': (stuck_hours,),
        'options': {'expires': 300},
    },
    'cleanup-old-token-blacklist': {
        'task': 'apps.documents.tasks.cleanup_old_token_blacklist',
        'schedule': crontab(hour=3, minute=17),
        'args': (getattr(settings, 'MAINTENANCE_TOKEN_BLACKLIST_DAYS', 30),),
        'options': {'expires': 3600},
    },
    'cleanup-old-failed-documents': {
        'task': 'apps.documents.tasks.cleanup_old_failed_documents',
        'schedule': crontab(hour=4, minute=23),
        'args': (getattr(settings, 'MAINTENANCE_FAILED_DOCS_DAYS', 30),),
        'options': {'expires': 3600},
    },
    'cleanup-expired-password-reset-tokens': {
        'task': 'apps.documents.tasks.cleanup_expired_password_reset_tokens',
        'schedule': crontab(hour=5, minute=7),
        'options': {'expires': 3600},
    },
    'cleanup-orphan-media-files': {
        'task': 'apps.documents.tasks.cleanup_orphan_media_files',
        'schedule': crontab(hour=2, minute=45),
        'args': (False, 24),
        'options': {'expires': 3600},
    },
    'cleanup-temporary-processing-files': {
        'task': 'apps.documents.tasks.cleanup_temporary_processing_files',
        'schedule': crontab(hour='*/4', minute=15),
        'args': (12,),
        'options': {'expires': 1800},
    },
}
if app.conf.beat_enabled:
    app.conf.beat_schedule = beat_schedule

app.conf.task_routes = {
    'apps.documents.tasks.process_document_task': {'queue': 'ocr'},
    'apps.documents.tasks.mark_stuck_processing_documents': {'queue': 'maintenance'},
    'apps.documents.tasks.cleanup_old_token_blacklist': {'queue': 'maintenance'},
    'apps.documents.tasks.cleanup_old_failed_documents': {'queue': 'maintenance'},
    'apps.documents.tasks.cleanup_expired_password_reset_tokens': {'queue': 'maintenance'},
    'apps.documents.tasks.cleanup_orphan_media_files': {'queue': 'maintenance'},
    'apps.documents.tasks.cleanup_temporary_processing_files': {'queue': 'maintenance'},
}

app.conf.task_default_queue = 'default'

app.conf.timezone = getattr(settings, 'TIME_ZONE', 'UTC')
app.conf.enable_utc = True

@app.task(bind=True, name='receiptflow.healthcheck', ignore_result=True)
def healthcheck(self):
    return {'ok': True, 'task_id': self.request.id}
