from __future__ import annotations

from threading import local
from typing import Optional, Dict, Any

from django.db import connections


_state = local()


def set_current_db(alias: Optional[str]) -> None:
    _state.current_db = alias


def get_current_db() -> Optional[str]:
    return getattr(_state, 'current_db', None)


def clear_current_db() -> None:
    if hasattr(_state, 'current_db'):
        delattr(_state, 'current_db')


def ensure_database_connection(alias: str) -> None:
    """Dynamically register DB alias into Django connections if not exists."""
    if alias in connections.databases:
        return
    # Lazy import to avoid app registry issues
    from .models import DatabaseConfig

    cfg = DatabaseConfig.objects.filter(alias=alias).first()
    if not cfg:
        raise RuntimeError(f'DatabaseConfig alias not found: {alias}')

    # 帶入 Django 預設必需鍵，避免 settings_dict 缺鍵（如 ATOMIC_REQUESTS）
    db_settings: Dict[str, Any] = {
        'ENGINE': cfg.engine,
        'NAME': cfg.name,
        'USER': cfg.user or '',
        'PASSWORD': cfg.password or '',
        'HOST': cfg.host or '',
        'PORT': cfg.port or '',
        'OPTIONS': cfg.options or {},
        # Defaults aligned with Django settings DATABASES docs
        'AUTOCOMMIT': True,
        'ATOMIC_REQUESTS': False,
        'CONN_MAX_AGE': 0,
        'CONN_HEALTH_CHECKS': True,
        'TIME_ZONE': None,
        'TEST': {},
        'DISABLE_SERVER_SIDE_CURSORS': False,
    }

    connections.databases[alias] = db_settings

