from .base import *  # noqa: F401,F403

DEBUG = True

INSTALLED_APPS += [  # noqa: F405
    'debug_toolbar',
    'django_extensions',
]

MIDDLEWARE = [
    'debug_toolbar.middleware.DebugToolbarMiddleware',
] + MIDDLEWARE  # noqa: F405

INTERNAL_IPS = [
    '127.0.0.1',
    'localhost',
]

ALLOWED_HOSTS = ['*']

REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'] += (  # noqa: F405
    'rest_framework.renderers.BrowsableAPIRenderer',
)

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'receiptflow-dev-cache',
    },
    'celery-broker': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'celery-broker-dev-cache',
    },
}

SESSION_ENGINE = 'django.contrib.sessions.backends.db'

RATELIMIT_ENABLE = False

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
