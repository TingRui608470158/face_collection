from django.db import models
from django.utils import timezone
import uuid


class DatabaseConfig(models.Model):
    ENGINE_SQLITE = 'django.db.backends.sqlite3'
    ENGINE_POSTGRES = 'django.db.backends.postgresql'
    ENGINE_MYSQL = 'django.db.backends.mysql'

    alias = models.CharField(max_length=64, unique=True, help_text='Django DB alias (e.g., tenant_acme)')
    engine = models.CharField(max_length=64, default=ENGINE_SQLITE)
    name = models.CharField(max_length=256, help_text='Database name or file path for sqlite')
    user = models.CharField(max_length=128, blank=True, default='')
    password = models.CharField(max_length=256, blank=True, default='')
    host = models.CharField(max_length=128, blank=True, default='')
    port = models.CharField(max_length=16, blank=True, default='')
    options = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # type: ignore[override]
        return f"{self.alias} -> {self.engine}:{self.name}"


class Company(models.Model):
    name = models.CharField(max_length=120, unique=True)
    db_config = models.ForeignKey(DatabaseConfig, on_delete=models.PROTECT, related_name='companies')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # type: ignore[override]
        return self.name


class AccountProfile(models.Model):
    # 與 Django auth.User 一對一，存放公司與權限
    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='account_profile')
    company = models.ForeignKey(Company, on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
    is_company_admin = models.BooleanField(default=False)

    def __str__(self) -> str:  # type: ignore[override]
        return f"AccountProfile({self.user.username})"


class Profile(models.Model):
    ROLE_EMPLOYEE = 'employee'
    ROLE_VISITOR = 'visitor'
    ROLE_CHOICES = [
        (ROLE_EMPLOYEE, '員工'),
        (ROLE_VISITOR, '訪客'),
    ]

    name = models.CharField(max_length=120)
    company = models.ForeignKey('collector.Company', on_delete=models.PROTECT, null=True, blank=True, related_name='profiles')
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_EMPLOYEE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:  # type: ignore[override]
        return f"{self.name} ({self.get_role_display()})"

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() > self.expires_at)

    class Meta:
        verbose_name = '人臉對象'
        verbose_name_plural = '人臉對象'


def capture_upload_path(instance: 'Capture', filename: str) -> str:
    return f"captures/{instance.profile.name}/{instance.batch_id}/{filename}"


def profile_image_upload_path(instance: 'Profile', filename: str) -> str:
    # 提供給舊遷移使用的上傳路徑（單人單資料夾）
    safe_name = (getattr(instance, 'name', '') or 'unknown').replace('/', '_')
    return f"profiles/{safe_name}/{filename}"


class Capture(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='captures')
    batch_id = models.CharField(max_length=64, db_index=True)
    image = models.ImageField(upload_to=capture_upload_path)
    image_sha256 = models.CharField(max_length=64, blank=True, default='')
    image_size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    selected = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']
        verbose_name = '影像'
        verbose_name_plural = '影像'


# 自動為新使用者建立 AccountProfile
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='auth.User')
def _ensure_account_profile(sender, instance, created, **kwargs):  # type: ignore[no-redef]
    try:
        from django.contrib.auth.models import User  # import here to avoid early import
        if not isinstance(instance, User):
            return
    except Exception:
        return
    if created:
        AccountProfile.objects.get_or_create(user=instance)

