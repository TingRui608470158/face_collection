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
import logging
from datetime import timezone as dt_timezone, timedelta

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
                    # 訪客：預先建立雲端訪客並保存 index 於 session，後續 finalize 直接沿用
                    try:
                        pre = cloud.pre_register_visitor()
                        index = (
                            pre.get('index')
                            or (pre.get('data') or {}).get('index')
                            or (pre.get('result') or {}).get('index')
                        )
                        if index:
                            request.session['visitor_index'] = index
                    except Exception:
                        # 不阻斷本地流程
                        pass
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
            # 訪客：使用 session 中的 visitor_index（若無則現場建立），避免分裂多筆紀錄
            try:
                index = (request.session.get('visitor_index') or '').strip()
                if not index:
                    pre = cloud.pre_register_visitor()
                    index = (
                        pre.get('index')
                        or (pre.get('data') or {}).get('index')
                        or (pre.get('result') or {}).get('index')
                    )
                    if index:
                        request.session['visitor_index'] = index
                if index:
                    # 基本必填：姓名 + 來訪時段（UTC/Z 格式）
                    now_utc = timezone.now().astimezone(dt_timezone.utc)
                    visit_start = now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
                    visit_end = (now_utc + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
                    info_payload = {
                        'name': profile.name,
                        'visit_start': visit_start,
                        'visit_end': visit_end,
                        'purpose': 'face_registration',
                        'company': 'NA',
                        'plate_number': 'NA',
                        'phone_number': 'NA',
                        'email': 'NA',
                    }
                    # 先嘗試更新主要欄位，避免 fill-info 覆寫為空
                    try:
                        cloud.update_visitor(index, info_payload)
                    except Exception as e:
                        logging.exception('cloud.update_visitor failed: %s', e)
                    try:
                        cloud.fill_info_visitor(index, info_payload)
                    except Exception as e:
                        logging.exception('cloud.fill_info_visitor failed: %s', e)
                    try:
                        # 參考員工上傳格式：使用 face_image 為 data URL，並附 face_feature
                        face_payload = {
                            'face_image': data_url,
                            'face_feature': None,
                        }
                        fc = cloud.face_capture_visitor(index, face_payload)
                        try:
                            logging.info('cloud.face_capture_visitor response: %s', str(fc)[:300])
                        except Exception:
                            pass
                    except Exception as e:
                        logging.exception('cloud.face_capture_visitor failed: %s', e)
            except Exception as e:
                # 雲端不中斷本地流程，但記錄詳細錯誤以便排查
                logging.exception('cloud pre_register/flow failed: %s', e)
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
def console_employee_detail(request: HttpRequest, employee_id: str):
    item = None
    error = None
    image_src = None
    try:
        item = cloud.get_employee(employee_id)
        if not item:
            error = 'not-found'
        else:
            image_src = (
                (item.get('face_image') if isinstance(item, dict) else None)
                or (item.get('data', {}) if isinstance(item, dict) else {}).get('face_image')
                or (item.get('result', {}) if isinstance(item, dict) else {}).get('face_image')
            )
    except Exception as e:
        error = str(e)
    return render(request, 'collector/console_employee_detail.html', {
        'item': item,
        'image_src': image_src,
        'error': error,
        'active_tab': 'employees',
    })


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


@login_required
def console_visitor_detail(request: HttpRequest, visitor_index: str):
    item = None
    error = None
    image_src = None
    has_face = False
    try:
        item = cloud.get_visitor(visitor_index)
        if not item:
            error = 'not-found'
        else:
            # Normalize image selection
            face = (
                item.get('face_image')
                or (item.get('data') or {}).get('face_image')
                or (item.get('result') or {}).get('face_image')
            )
            qr = (
                item.get('qrcode_base64')
                or (item.get('data') or {}).get('qrcode_base64')
                or (item.get('result') or {}).get('qrcode_base64')
            )
            image_src = face or qr
            has_face = bool(face)
    except Exception as e:
        error = str(e)
    return render(
        request,
        'collector/console_visitor_detail.html',
        {
            'item': item,
            'image_src': image_src,
            'has_face': has_face,
            'error': error,
            'active_tab': 'visitors',
        },
    )

