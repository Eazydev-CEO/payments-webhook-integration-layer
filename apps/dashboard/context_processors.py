from django.conf import settings


def site_context(request):
    """Expose global template values (site name, demo mode banner)."""
    return {
        "SITE_NAME": settings.SITE_NAME,
        "DEMO_MODE": settings.DEMO_MODE,
    }
