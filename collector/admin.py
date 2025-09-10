from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.core.management import call_command

from .models import DatabaseConfig, Company, AccountProfile, Profile, Capture
from .tenant import ensure_database_connection


@admin.register(DatabaseConfig)
class DatabaseConfigAdmin(admin.ModelAdmin):
    list_display = ('alias', 'engine', 'name', 'host', 'port', 'created_at')
    search_fields = ('alias', 'name', 'host')
    list_filter = ('engine',)

    def save_model(self, request, obj, form, change):  # type: ignore[override]
        super().save_model(request, obj, form, change)
        # 儲存後自動初始化/遷移該資料庫
        try:
            ensure_database_connection(obj.alias)
            call_command('migrate', database=obj.alias, interactive=False, verbosity=1)
            messages.success(request, f"資料庫 {obj.alias} 已初始化/遷移完成")
        except Exception as e:
            messages.error(request, f"初始化資料庫 {obj.alias} 失敗：{e}")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'db_config', 'created_at')
    search_fields = ('name',)

    def save_model(self, request, obj, form, change):  # type: ignore[override]
        super().save_model(request, obj, form, change)
        # 確保公司所屬資料庫已遷移
        try:
            if obj.db_config and obj.db_config.alias:
                ensure_database_connection(obj.db_config.alias)
                call_command('migrate', database=obj.db_config.alias, interactive=False, verbosity=1)
                messages.success(request, f"公司 {obj.name} 對應資料庫 {obj.db_config.alias} 已遷移完成")
        except Exception as e:
            messages.error(request, f"遷移公司 {obj.name} 資料庫失敗：{e}")


class AccountProfileInline(admin.StackedInline):
    model = AccountProfile
    can_delete = False
    fk_name = 'user'
    extra = 0


class UserAdmin(BaseUserAdmin):
    inlines = (AccountProfileInline,)
    list_display = BaseUserAdmin.list_display + ('is_superuser',)
    list_filter = BaseUserAdmin.list_filter + ('is_superuser',)

    # 避免新增使用者時同時透過 inline 再建立一次 AccountProfile，造成唯一鍵衝突
    def get_inline_instances(self, request, obj=None):  # type: ignore[override]
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)


# 重新註冊 User，加入 Profile inline
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'role', 'created_at', 'expires_at')
    list_filter = ('role',)
    search_fields = ('name',)

    def get_queryset(self, request):  # type: ignore[override]
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        try:
            company = request.user.account_profile.company
        except Exception:
            company = None
        return qs.filter(company=company)

    def save_model(self, request, obj, form, change):  # type: ignore[override]
        if not request.user.is_superuser and not obj.company:
            try:
                obj.company = request.user.account_profile.company
            except Exception:
                pass
        return super().save_model(request, obj, form, change)


@admin.register(Capture)
class CaptureAdmin(admin.ModelAdmin):
    list_display = ('id', 'profile', 'batch_id', 'selected', 'created_at')
    list_filter = ('selected',)
    search_fields = ('profile__name', 'batch_id')

    def get_queryset(self, request):  # type: ignore[override]
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        try:
            company = request.user.account_profile.company
        except Exception:
            company = None
        return qs.filter(profile__company=company)
