from django.db import models
from django.utils import timezone
import uuid


class Profile(models.Model):
    ROLE_EMPLOYEE = 'employee'
    ROLE_VISITOR = 'visitor'
    ROLE_CHOICES = [
        (ROLE_EMPLOYEE, '員工'),
        (ROLE_VISITOR, '訪客'),
    ]

    name = models.CharField(max_length=120)
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_EMPLOYEE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:  # type: ignore[override]
        return f"{self.name} ({self.get_role_display()})"

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and timezone.now() > self.expires_at)


def capture_upload_path(instance: 'Capture', filename: str) -> str:
    return f"captures/{instance.profile.name}/{instance.batch_id}/{filename}"


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

