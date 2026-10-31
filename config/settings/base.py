import os
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    EMAIL_USE_TLS=(bool, True),
    EMAIL_VERIFICATION_REQUIRED=(bool, False),
)

environ.Env.read_env(os.path.join(BASE_DIR, '.env'))

SECRET_KEY = env('DJANGO_SECRET_KEY')
DEBUG = env('DJANGO_DEBUG')
ALLOWED_HOSTS = env.list('DJANGO_ALLOWED_HOSTS', default=['localhost', '127.0.0.1'])
FRONTEND_URL = env('APP_FRONTEND_URL', default='http://localhost:3000')

DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'django_filters',
    'corsheaders',
    'django_celery_results',
    'drf_spectacular',
    'storages',
]

LOCAL_APPS = [
    'apps.users',
    'apps.documents',
    'apps.expenses',
    'apps.categories',
    'apps.suppliers',
    'apps.reports',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('DB_NAME'),
        'USER': env('DB_USER'),
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST'),
        'PORT': env('DB_PORT'),
        'OPTIONS': {
            'connect_timeout': 10,
            'sslmode': env('DB_SSLMODE', default='require'),
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 8},
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
    'django.contrib.auth.hashers.ScryptPasswordHasher',
]

LANGUAGE_CODE = 'es'
TIME_ZONE = 'America/Bogota'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static_collected'
STATICFILES_DIRS = [BASE_DIR / 'static']

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

MAX_UPLOAD_SIZE_MB = env.int('MAX_UPLOAD_SIZE_MB', default=20)
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = env.list(
    'ALLOWED_IMAGE_EXTENSIONS', default=['jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff', 'pdf', 'webp']
)
ALLOWED_DOCUMENT_EXTENSIONS = env.list(
    'ALLOWED_DOCUMENT_EXTENSIONS', default=['pdf', 'jpg', 'jpeg', 'png', 'webp']
)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'users.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_RENDERER_CLASSES': (
        'rest_framework.renderers.JSONRenderer',
    ),
    'DEFAULT_PARSER_CLASSES': (
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
    ),
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'EXCEPTION_HANDLER': 'apps.common.exceptions.custom_exception_handler',
    'TEST_REQUEST_DEFAULT_FORMAT': 'json',
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(
        minutes=env.int('JWT_ACCESS_TOKEN_LIFETIME_MINUTES', default=30)
    ),
    'REFRESH_TOKEN_LIFETIME': timedelta(
        days=env.int('JWT_REFRESH_TOKEN_LIFETIME_DAYS', default=7)
    ),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'TOKEN_TYPE_CLAIM': 'token_type',
}

CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[
    'http://localhost:3000',
])
CORS_ALLOW_CREDENTIALS = True
CORS_EXPOSE_HEADERS = ['Content-Disposition', 'X-Total-Count']
CSRF_TRUSTED_ORIGINS = CORS_ALLOWED_ORIGINS

EMAIL_BACKEND = env('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = env('EMAIL_HOST', default='')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env('EMAIL_USE_TLS')
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='ReceiptFlow <no-reply@receiptflow.com>')

EMAIL_VERIFICATION_REQUIRED = env('EMAIL_VERIFICATION_REQUIRED')
EMAIL_VERIFICATION_EXPIRE_HOURS = 24
PASSWORD_RESET_EXPIRE_HOURS = 1

GOOGLE_CLOUD_PROJECT_ID = env('GOOGLE_CLOUD_PROJECT_ID', default='')
GOOGLE_APPLICATION_CREDENTIALS = env('GOOGLE_APPLICATION_CREDENTIALS', default='')
GOOGLE_VISION_TIMEOUT = env.int('GOOGLE_VISION_TIMEOUT', default=60)

# Google Gemini & OCR Service
GEMINI_API_KEY = env('GEMINI_API_KEY', default='')
GEMINI_MODEL = env('GEMINI_MODEL', default='gemini-3.5-flash')
OCR_SERVICE = env('OCR_SERVICE', default='gemini' if GEMINI_API_KEY else 'google_vision')



LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': env('DJANGO_LOG_LEVEL', default='INFO'),
            'propagate': False,
        },
        'django.security.*': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'apps': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'services': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
        'celery': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}

# ------------------------------------------------------------------------------
# Cache / Redis
# ------------------------------------------------------------------------------
REDIS_URL = env('REDIS_URL', default='redis://localhost:6379/0')
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
        'TIMEOUT': 300,
        'OPTIONS': {
            'socket_connect_timeout': 5,
            'socket_timeout': 5,
        },
    },
    'celery-broker': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': REDIS_URL,
    },
}
SESSION_ENGINE = 'django.contrib.sessions.backends.cached_db'

# ------------------------------------------------------------------------------
# Celery
# ------------------------------------------------------------------------------
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default=REDIS_URL)
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='django-db')
if CELERY_RESULT_BACKEND == 'django-db':
    CELERY_RESULT_BACKEND = 'django-db'
CELERY_CACHE_BACKEND = 'celery-broker'
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

# ------------------------------------------------------------------------------
# Gemini Structured Output Extraction
# ------------------------------------------------------------------------------
GEMINI_STRUCTURED_OUTPUT_ENABLED = env.bool('GEMINI_STRUCTURED_OUTPUT_ENABLED', default=True)
GEMINI_USE_OCR_FALLBACK = env.bool('GEMINI_USE_OCR_FALLBACK', default=True)
GEMINI_DEFAULT_OCR_SERVICE = env('OCR_SERVICE', default='gemini')
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 20
CELERY_TASK_SOFT_TIME_LIMIT = 60 * 15
CELERY_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 500
CELERY_RESULT_EXTENDED = True

PROCESSING_TIMEOUT_MINUTES = env.int('PROCESSING_TIMEOUT_MINUTES', default=15)
PROCESSING_MAX_ATTEMPTS = env.int('PROCESSING_MAX_ATTEMPTS', default=3)
CELERY_BEAT_ENABLED = env.bool('CELERY_BEAT_ENABLED', default=True)

# ------------------------------------------------------------------------------
# Rate Limiting (django-ratelimit)
# ------------------------------------------------------------------------------
RATELIMIT_ENABLE = env.bool('RATELIMIT_ENABLE', default=True)
RATELIMIT_USE_CACHE = 'default'
RATELIMIT_VIEW = 'apps.common.responses.rate_limited_view'
RATELIMIT_AUTH_LOGIN = env('RATELIMIT_AUTH_LOGIN', default='10/m')
RATELIMIT_AUTH_REGISTER = env('RATELIMIT_AUTH_REGISTER', default='5/m')
RATELIMIT_AUTH_PASSWORD_RESET = env('RATELIMIT_AUTH_PASSWORD_RESET', default='5/m')
RATELIMIT_AUTH_VERIFY_EMAIL = env('RATELIMIT_AUTH_VERIFY_EMAIL', default='10/m')
RATELIMIT_DOCUMENTS_CREATE = env('RATELIMIT_DOCUMENTS_CREATE', default='20/m')
RATELIMIT_DOCUMENTS_PROCESS = env('RATELIMIT_DOCUMENTS_PROCESS', default='15/m')

# ------------------------------------------------------------------------------
# drf-spectacular - OpenAPI / Swagger
# ------------------------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    'TITLE': 'ReceiptFlow API',
    'DESCRIPTION': (
        'API REST para ReceiptFlow: carga y procesamiento de recibos y facturas '
        'mediante OCR con Google Cloud Vision. Incluye autenticación JWT, gestión '
        'de gastos, categorías, proveedores y reportes de dashboard.'
    ),
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SCHEMA_PATH_PREFIX': '/api/',
    'SCHEMA_COERCE_PATH_PK_SUFFIX': True,
    'TAGS': [
        {'name': 'Authentication', 'description': 'Registro, inicio de sesión, JWT, contraseñas y verificación de correo.'},
        {'name': 'Users', 'description': 'Gestión de perfil de usuario y administración.'},
        {'name': 'Documents', 'description': 'Subida, procesamiento OCR, actualización y eliminación de documentos.'},
        {'name': 'Expenses', 'description': 'Gestión de gastos, items, filtros y estadísticas.'},
        {'name': 'Categories', 'description': 'Categorías de gasto (sistema y personalizadas).'},
        {'name': 'Suppliers', 'description': 'Proveedores con deduplicación inteligente y favoritos.'},
        {'name': 'Reports', 'description': 'Reportes agregados y endpoints de dashboard.'},
    ],
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
        'displayOperationId': True,
    },
    'REDOC_UI_SETTINGS': {
        'hideDownloadButton': False,
        'theme': {
            'colors': {'primary': {'main': '#3b82f6'}},
        },
    },
    'COMPONENT_SPLIT_REQUEST': True,
    'ENUM_GENERATE_CHOICE_DESCRIPTION': True,
    'AUTHENTICATION_WHITELIST': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'PREPROCESSING_HOOKS': [],
    'POSTPROCESSING_HOOKS': [],
    'SECURITY': [
        {'JWT': []},
    ],
    'EXTENSIONS_INFO': {
        'x-logo': {
            'url': '/static/logo.png',
            'altText': 'ReceiptFlow',
        },
    },
}

# ------------------------------------------------------------------------------
# Storage backend abstraction (local filesystem vs Google Cloud Storage)
# ------------------------------------------------------------------------------
STORAGE_BACKEND = env('STORAGE_BACKEND', default='filesystem').lower()
if STORAGE_BACKEND == 'gcs':
    STORAGES = {
        'default': {
            'BACKEND': 'storages.backends.gcloud.GoogleCloudStorage',
            'OPTIONS': {
                'bucket_name': env('GS_BUCKET_NAME', default=''),
                'project_id': env('GS_PROJECT_ID', default=GOOGLE_CLOUD_PROJECT_ID),
                'default_acl': env('GS_DEFAULT_ACL', default='private'),
                'cache_control': env('GS_CACHE_CONTROL', default='public, max-age=3600'),
                'blob_chunk_size': 5 * 1024 * 1024,
                'file_overwrite': False,
                'expiration': 60 * 60 * 24 * 7,
            },
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }
else:
    STORAGES = {
        'default': {
            'BACKEND': 'django.core.files.storage.FileSystemStorage',
        },
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    }

# ------------------------------------------------------------------------------
# Security & Maintenance
# ------------------------------------------------------------------------------
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

MAINTENANCE_STUCK_PROCESSING_HOURS = env.int('MAINTENANCE_STUCK_PROCESSING_HOURS', default=PROCESSING_TIMEOUT_MINUTES // 60 or 1)
MAINTENANCE_ORPHAN_FILES_DAYS = env.int('MAINTENANCE_ORPHAN_FILES_DAYS', default=3)
MAINTENANCE_FAILED_DOCS_DAYS = env.int('MAINTENANCE_FAILED_DOCS_DAYS', default=30)
MAINTENANCE_TOKEN_BLACKLIST_DAYS = env.int('MAINTENANCE_TOKEN_BLACKLIST_DAYS', default=30)

# Extra MIME type strict verification (python-magic)
STRICT_MIME_VERIFICATION = True

# Temporary file upload permission (prevents path traversal via name)
FILE_UPLOAD_PERMISSIONS = 0o640
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o750
FILE_UPLOAD_TEMP_DIR = None
