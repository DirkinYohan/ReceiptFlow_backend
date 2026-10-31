from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.static import serve

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path('admin/', admin.site.urls),

    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path(
        'api/docs/',
        SpectacularSwaggerView.as_view(url_name='schema'),
        name='swagger-ui',
    ),
    path(
        'api/redoc/',
        SpectacularRedocView.as_view(url_name='schema'),
        name='redoc',
    ),

    path('api/auth/', include('apps.users.urls', namespace='auth')),
    path('api/v1/users/', include(('apps.users.urls', 'users'), namespace='users')),
    path('api/v1/documents/', include('apps.documents.urls', namespace='documents')),
    path('api/v1/expenses/', include('apps.expenses.urls', namespace='expenses')),
    path('api/v1/categories/', include('apps.categories.urls', namespace='categories')),
    path('api/v1/budgets/', include('apps.categories.budget_urls', namespace='budgets')),
    path('api/v1/suppliers/', include('apps.suppliers.urls', namespace='suppliers')),
    path('api/v1/reports/', include('apps.reports.urls', namespace='reports')),
]

def serve_media(request, path):
    # Django sends X-Frame-Options: DENY by default; the frontend must be able to embed the PDF.
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    response['Content-Security-Policy'] = f"frame-ancestors 'self' {settings.FRONTEND_URL}"
    return response


if settings.DEBUG:
    urlpatterns += [
        re_path(
            rf'^{settings.MEDIA_URL.lstrip("/")}(?P<path>.*)$',
            xframe_options_exempt(serve_media),
        ),
    ]
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

    try:
        import debug_toolbar
        urlpatterns = [
            path('__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns
    except ImportError:
        pass
