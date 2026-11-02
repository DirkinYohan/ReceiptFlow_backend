"""Pieces needed to run on Render's free plan."""
from unittest.mock import patch

from django.test import override_settings


def test_health_check_needs_no_login(client):
    response = client.get('/healthz/')
    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


@override_settings(CRON_SECRET='s3cret-token')
def test_scheduled_tasks_run_with_the_secret(client, db):
    with patch('apps.common.scheduled.run_scheduled_tasks', return_value={'due_reminders': {'users_notified': 0}}):
        response = client.post('/api/internal/run-scheduled-tasks/', HTTP_X_CRON_SECRET='s3cret-token')
    assert response.status_code == 200
    assert response.json()['results']['due_reminders'] == {'users_notified': 0}


@override_settings(CRON_SECRET='s3cret-token')
def test_scheduled_tasks_hidden_without_the_secret(client):
    assert client.post('/api/internal/run-scheduled-tasks/', HTTP_X_CRON_SECRET='wrong').status_code == 404
    assert client.post('/api/internal/run-scheduled-tasks/').status_code == 404
    assert client.get('/api/internal/run-scheduled-tasks/', HTTP_X_CRON_SECRET='s3cret-token').status_code == 405


@override_settings(CRON_SECRET='')
def test_scheduled_tasks_disabled_when_no_secret_is_configured(client):
    assert client.post('/api/internal/run-scheduled-tasks/', HTTP_X_CRON_SECRET='').status_code == 404


def test_scheduled_tasks_really_run(db):
    from apps.common.scheduled import run_scheduled_tasks
    results = run_scheduled_tasks()
    assert set(results) == {'stuck_documents', 'due_reminders', 'token_blacklist'}
    assert 'error' not in results.values()
