from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST
from django.core.files.base import ContentFile
from django.utils import timezone
import base64
import uuid
import cv2
import numpy as np
import hashlib

from .forms import NameRoleForm
from .models import Profile, Capture
from .cloud_api import client as cloud
from scripts.insight_utils import has_face_features


def name_role_form(request: HttpRequest):
    if not request.user.is_authenticated:
        return redirect('login')

    # 清理過期訪客（每日）
    Profile.objects.filter(role=Profile.ROLE_VISITOR, expires_at__lt=timezone.now()).delete()

    if request.method == 'POST':
        form = NameRoleForm(request.POST)
        if form.is_valid():
            profile = form.save_profile()
            request.session['profile_id'] = profile.id
            request.session['batch_id'] = uuid.uuid4().hex
            # 若為員工，暫存雲端所需欄位以便 finalize 上傳
            try:
                if form.cleaned_data.get('role') == Profile.ROLE_EMPLOYEE:
                    request.session['employee_id'] = (form.cleaned_data.get('employee_id') or '').strip()
                else:
                    request.session.pop('employee_id', None)
            except Exception:
                pass
            return redirect('collect')
    else:
        form = NameRoleForm()
    return render(request, 'collector/name_role_form.html', {'form': form, 'active_tab': 'collect'})


@login_required
def collect(request: HttpRequest):
    profile_id = request.session.get('profile_id')
    batch_id = request.session.get('batch_id')
    if not profile_id or not batch_id:
        return redirect('name_role_form')
    profile = Profile.objects.get(id=profile_id)
    return render(request, 'collector/collect.html', {'profile': profile, 'batch_id': batch_id, 'active_tab': 'collect'})


@login_required
@require_POST
def upload_frame(request: HttpRequest):
    profile_id = request.session.get('profile_id')
    batch_id = request.session.get('batch_id')
    if not profile_id or not batch_id:
        return JsonResponse({'error': 'no-session'}, status=400)
    profile = Profile.objects.get(id=profile_id)

    # 期待 dataURL base64: data:image/jpeg;base64,...
    data_url = request.POST.get('image')
    if not data_url or not data_url.startswith('data:image'):
        return JsonResponse({'error': 'bad-image'}, status=400)
    header, b64data = data_url.split(',', 1)
    binary = base64.b64decode(b64data)

    filename = f"{timezone.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    sha256 = hashlib.sha256(binary).hexdigest()
    cap = Capture(profile=profile, batch_id=batch_id, image_sha256=sha256, image_size=len(binary))
    cap.image.save(filename, ContentFile(binary))
    cap.save()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def reset_batch(request: HttpRequest):
    # 並不刪舊資料，只是產生新批次
    request.session['batch_id'] = uuid.uuid4().hex
    return JsonResponse({'ok': True, 'batch_id': request.session['batch_id']})


@login_required
@require_POST
def finalize(request: HttpRequest):
    """接收前端選中的單張圖（dataURL），僅儲存這一張並標記 selected=True。"""
    profile_id = request.session.get('profile_id')
    batch_id = request.session.get('batch_id')
    if not profile_id:
        return JsonResponse({'error': 'no-session'}, status=400)
    if not batch_id:
        batch_id = uuid.uuid4().hex
        request.session['batch_id'] = batch_id

    profile = Profile.objects.get(id=profile_id)

    data_url = request.POST.get('image')
    if not data_url or not data_url.startswith('data:image'):
        return JsonResponse({'error': 'bad-image'}, status=400)
    header, b64data = data_url.split(',', 1)
    binary = base64.b64decode(b64data)

    # 使用 insightface 檢查是否含有人臉特徵
    try:
        np_arr = np.frombuffer(binary, dtype=np.uint8)
        image_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if image_bgr is None:
            return JsonResponse({'error': 'bad-image'}, status=400)
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        if not has_face_features(image_rgb):
            return JsonResponse({'error': 'no-face'}, status=400)
    except Exception:
        return JsonResponse({'error': 'insightface-error'}, status=500)

    # 儲存單張並設為 selected=True
    filename = f"{timezone.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    sha256 = hashlib.sha256(binary).hexdigest()
    cap = Capture(profile=profile, batch_id=batch_id, selected=True, image_sha256=sha256, image_size=len(binary))
    cap.image.save(filename, ContentFile(binary))
    cap.save()

    # 若為員工，將代表照同步到雲端建立員工
    try:
        if profile.role == Profile.ROLE_EMPLOYEE:
            employee_id = (request.session.get('employee_id') or profile.name).strip()
            payload = {
                'name': profile.name,
                'employee_id': employee_id or profile.name,
                'plate_number': 'NA',
                'face_image': data_url,
                'face_feature': None,
            }
            try:
                cloud.create_employee(payload)
            except Exception:
                # 若已存在，嘗試改為更新資料（如補上最新代表照）
                try:
                    cloud.update_employee(employee_id or profile.name, {
                        'name': profile.name,
                        'face_image': data_url,
                        'face_feature': None,
                    })
                except Exception:
                    pass
        else:
            # 訪客：走雲端訪客註冊三步驟
            try:
                pre = cloud.pre_register_visitor()
                # 柔性解析 index 欄位
                index = (
                    pre.get('index')
                    or (pre.get('data') or {}).get('index')
                    or (pre.get('result') or {}).get('index')
                )
                if index:
                    # 先嘗試更新主要欄位，避免 fill-info 覆寫為空
                    try:
                        cloud.update_visitor(index, {'name': profile.name})
                    except Exception:
                        pass
                    cloud.fill_info_visitor(index, {'name': profile.name})
                    cloud.face_capture_visitor(index, {
                        'face_image': data_url,
                        'face_feature': None,
                    })
            except Exception:
                # 雲端不中斷本地流程
                pass
    except Exception:
        # 不阻斷本地流程
        pass

    return JsonResponse({'ok': True})


@login_required
def select_image(request: HttpRequest):
    profile_id = request.session.get('profile_id')
    batch_id = request.session.get('batch_id')
    if not profile_id or not batch_id:
        return redirect('name_role_form')
    profile = Profile.objects.get(id=profile_id)
    images = Capture.objects.filter(profile=profile, batch_id=batch_id)
    if request.method == 'POST':
        chosen = request.POST.get('image_id')
        if chosen:
            Capture.objects.filter(profile=profile, batch_id=batch_id).update(selected=False)
            Capture.objects.filter(id=chosen).update(selected=True)
            return redirect('complete')
    return render(request, 'collector/select.html', {'images': images, 'profile': profile})


@login_required
def complete(request: HttpRequest):
    profile_id = request.session.get('profile_id')
    batch_id = request.session.get('batch_id')
    if not profile_id or not batch_id:
        return redirect('name_role_form')
    profile = Profile.objects.get(id=profile_id)
    images = Capture.objects.filter(profile=profile, batch_id=batch_id)
    selected = images.filter(selected=True).first()
    return render(request, 'collector/complete.html', {'profile': profile, 'selected': selected})


# --- Console pages backed by cloud API ---

@login_required
def console_employees(request: HttpRequest):
    query_skip = int(request.GET.get('skip', '0') or '0')
    query_limit = int(request.GET.get('limit', '20') or '20')
    try:
        data = cloud.list_employees(skip=query_skip, limit=query_limit)
    except Exception as e:
        data = {'total': 0, 'items': [], 'error': str(e)}
    return render(request, 'collector/console_employees.html', {'data': data, 'active_tab': 'employees'})


@login_required
@require_POST
def console_employee_delete(request: HttpRequest, employee_id: str):
    try:
        cloud.delete_employee(employee_id)
        # Prefer staying on the same page (PRG pattern)
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('console_employees')
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@login_required
def console_visitors(request: HttpRequest):
    query_skip = int(request.GET.get('skip', '0') or '0')
    query_limit = int(request.GET.get('limit', '20') or '20')
    try:
        data = cloud.list_visitors(skip=query_skip, limit=query_limit)
    except Exception as e:
        data = {'total': 0, 'items': [], 'error': str(e)}
    return render(request, 'collector/console_visitors.html', {'data': data, 'active_tab': 'visitors'})


@login_required
@require_POST
def console_visitor_delete(request: HttpRequest, visitor_index: str):
    try:
        cloud.delete_visitor(visitor_index)
        # Prefer staying on the same page (PRG pattern)
        referer = request.META.get('HTTP_REFERER')
        if referer:
            return redirect(referer)
        return redirect('console_visitors')
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

