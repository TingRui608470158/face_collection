from django.conf import settings


def globals(request):  # type: ignore[override]
    return {
        'CLOUD_SYNC_ENABLED': getattr(settings, 'CLOUD_SYNC_ENABLED', True),
    }


