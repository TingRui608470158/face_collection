"""
ASGI config for webapp project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from scripts.insight_utils import warmup_if_needed

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'webapp.settings')


application = get_asgi_application()
warmup_if_needed()
