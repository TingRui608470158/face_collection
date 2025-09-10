from __future__ import annotations

from typing import Optional

from .tenant import get_current_db


TENANT_APP_LABELS = {'collector'}
TENANT_LOCAL_MODELS = {'Profile', 'Capture'}  # 存放在租戶資料庫
GLOBAL_MODELS = {'DatabaseConfig', 'Company', 'AccountProfile'}  # 存放在 default


class TenantRouter:
    def _is_global_model(self, model) -> bool:
        return model._meta.app_label == 'collector' and model.__name__ in GLOBAL_MODELS

    def _is_tenant_model(self, model) -> bool:
        return model._meta.app_label in TENANT_APP_LABELS and model.__name__ in TENANT_LOCAL_MODELS

    def db_for_read(self, model, **hints) -> Optional[str]:  # type: ignore[override]
        if self._is_global_model(model):
            return 'default'
        if self._is_tenant_model(model):
            alias = get_current_db()
            return alias or 'default'
        return None

    def db_for_write(self, model, **hints) -> Optional[str]:  # type: ignore[override]
        if self._is_global_model(model):
            return 'default'
        if self._is_tenant_model(model):
            alias = get_current_db()
            return alias or 'default'
        return None

    def allow_relation(self, obj1, obj2, **hints):  # type: ignore[override]
        return True

    def allow_migrate(self, db: str, app_label: str, model_name: Optional[str] = None, **hints):  # type: ignore[override]
        model_name_cap = (model_name or '').capitalize()
        if app_label == 'collector' and model_name_cap in GLOBAL_MODELS:
            return db == 'default'
        if app_label in TENANT_APP_LABELS and model_name_cap in TENANT_LOCAL_MODELS:
            # 租戶資料表，允許在非 default 的資料庫遷移；若是 default 也允許（便於開發）
            return True
        return None

