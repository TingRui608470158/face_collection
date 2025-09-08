from django import forms
from .models import Profile
from django.utils import timezone


class NameRoleForm(forms.Form):
    name = forms.CharField(label='名稱', max_length=120)
    role = forms.ChoiceField(label='角色', choices=Profile.ROLE_CHOICES)
    employee_id = forms.CharField(label='員工編號', max_length=120, required=False)
    plate_number = forms.CharField(label='車牌號碼', max_length=32, required=False)

    def clean_name(self):
        # 僅做基本清理，不再在本地做唯一性驗證，避免與雲端資料不同步
        name = (self.cleaned_data.get('name') or '').strip()
        return name

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        # 僅在角色為「員工」時，要求員工編號；車牌非必填，訪客不需員工編號
        if role == Profile.ROLE_EMPLOYEE:
            if not (cleaned.get('employee_id') or '').strip():
                self.add_error('employee_id', '員工編號為必填')
        return cleaned

    def save_profile(self) -> Profile:
        name = self.cleaned_data['name'].strip()
        role = self.cleaned_data['role']
        expires_at = None
        if role == Profile.ROLE_VISITOR:
            # 設定至當天結束
            now = timezone.now()
            end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)
            expires_at = end_of_day
        return Profile.objects.create(name=name, role=role, expires_at=expires_at)



