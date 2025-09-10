from __future__ import annotations

from django.utils.deprecation import MiddlewareMixin

from .tenant import set_current_db, clear_current_db, ensure_database_connection


class TenantDatabaseMiddleware(MiddlewareMixin):
    def process_request(self, request):  # type: ignore[override]
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            clear_current_db()
            return None
        # Lazy import to avoid app registry ready issues
        from .models import AccountProfile

        try:
            ap = AccountProfile.objects.select_related('company__db_config').get(user=user)
        except AccountProfile.DoesNotExist:
            clear_current_db()
            return None
        if not ap.company:
            clear_current_db()
            return None
        alias = ap.company.db_config.alias
        ensure_database_connection(alias)
        set_current_db(alias)
        return None

    def process_response(self, request, response):  # type: ignore[override]
        clear_current_db()
        return response

