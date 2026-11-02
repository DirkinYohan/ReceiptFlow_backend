from .base import *  # noqa: F401,F403

DEBUG = False

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)  # noqa: F405
SECURE_REDIRECT_EXEMPT = [r'^healthz/?$']
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

_render_host = env('RENDER_EXTERNAL_HOSTNAME', default='')  # noqa: F405
if _render_host and _render_host not in ALLOWED_HOSTS:  # noqa: F405
    ALLOWED_HOSTS.append(_render_host)  # noqa: F405

CORS_ALLOWED_ORIGIN_REGEXES = env.list('CORS_ALLOWED_ORIGIN_REGEXES', default=[])  # noqa: F405

MIDDLEWARE.insert(  # noqa: F405
    MIDDLEWARE.index('django.middleware.security.SecurityMiddleware') + 1,  # noqa: F405
    'whitenoise.middleware.WhiteNoiseMiddleware',
)
STORAGES['staticfiles'] = {  # noqa: F405
    'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
}

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': env('DJANGO_LOG_LEVEL', default='INFO'),  # noqa: F405
            'propagate': False,
        },
    },
}
