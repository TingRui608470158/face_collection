from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import ensure_csrf_cookie
from django.core.files.base import ContentFile
from django.utils import timezone
import base64
import uuid
import cv2
import numpy as np
import hashlib
import logging
from datetime import timezone as dt_timezone, timedelta
import mimetypes

from .forms import NameRoleForm
from .models import Profile, Capture, Company
from django.conf import settings
from .cloud_api import client as cloud
from scripts.insight_utils import has_face_features


@ensure_csrf_cookie
def name_role_form(request: HttpRequest):
    if not request.user.is_authenticated:
        return redirect('login')

    # 若帶 reset=1，清除先前暫存的作業狀態
    if request.GET.get('reset') == '1':
        for k in ['profile_id', 'batch_id', 'employee_id', 'visitor_index']:
            try:
                request.session.pop(k, None)
            except Exception:
                pass

    # 清理過期訪客（每日）— 若資料表尚未建立或租戶 DB 尚未初始化，避免阻斷流程
    try:
        Profile.objects.filter(role=Profile.ROLE_VISITOR, expires_at__lt=timezone.now()).delete()
    except Exception:
        # 允許略過（例如新租戶尚未 migrate 完成）
        pass

    if request.method == 'POST':
        form = NameRoleForm(request.POST)
        if form.is_valid():
            # 單資料庫模式：依使用者所屬公司建立 Profile
            user_company = None
            try:
                account_profile = getattr(request.user, 'account_profile', None)
                if account_profile:
                    user_company = account_profile.company
            except Exception as e:
                logging.warning(f'無法取得使用者公司資訊: {e}')
                user_company = None
            
            # 確保有取得公司資訊
            if not user_company:
                logging.error(f'使用者 {request.user.username} 沒有關聯的公司，無法建立 Profile')
                form.add_error(None, '您的帳號尚未關聯到公司，請聯絡管理員')
                return render(request, 'collector/name_role_form.html', {'form': form, 'active_tab': 'collect'})
            
            profile = form.save_profile(company=user_company)
            logging.info(f'已為公司 {user_company.name} 建立 Profile: {profile.name} ({profile.role})')
            request.session['profile_id'] = profile.id
            request.session['batch_id'] = uuid.uuid4().hex
            # 若為員工，暫存雲端所需欄位以便 finalize 上傳
            if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
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
@ensure_csrf_cookie
def collect(request: HttpRequest):
    profile_id = request.session.get('profile_id')
    batch_id = request.session.get('batch_id')
    if not profile_id or not batch_id:
        return redirect('name_role_form')
    # 僅從使用者公司範圍讀取
    user_company = getattr(getattr(request.user, 'account_profile', None), 'company', None)
    profile = Profile.objects.get(id=profile_id, company=user_company)
    return render(request, 'collector/collect.html', {'profile': profile, 'batch_id': batch_id, 'active_tab': 'collect'})


@login_required
@require_POST
def upload_frame(request: HttpRequest):
    profile_id = request.session.get('profile_id')
    batch_id = request.session.get('batch_id')
    if not profile_id or not batch_id:
        return JsonResponse({'error': 'no-session'}, status=400)
    user_company = getattr(getattr(request.user, 'account_profile', None), 'company', None)
    profile = Profile.objects.get(id=profile_id, company=user_company)

    # 期待 dataURL base64: data:image/jpeg;base64,...
    data_url = request.POST.get('image')
    if not data_url or not data_url.startswith('data:image'):
        return JsonResponse({'error': 'bad-image'}, status=400)
    header, b64data = data_url.split(',', 1)
    binary = base64.b64decode(b64data)

    # 僅保存含人臉的影像，避免非人臉殘留
    try:
        np_arr = np.frombuffer(binary, dtype=np.uint8)
        image_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if image_bgr is None:
            return JsonResponse({'ok': False, 'error': 'bad-image'}, status=400)
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        if not has_face_features(image_rgb):
            return JsonResponse({'ok': False, 'error': 'no-face'}, status=400)
    except Exception:
        return JsonResponse({'ok': False, 'error': 'insightface-error'}, status=500)

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

    user_company = getattr(getattr(request.user, 'account_profile', None), 'company', None)
    profile = Profile.objects.get(id=profile_id, company=user_company)

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

    # 僅保留本次代表照，清理同批次其它影像，避免非人臉或舊影像殘留
    try:
        Capture.objects.filter(profile=profile, batch_id=batch_id).exclude(id=cap.id).delete()
    except Exception:
        pass

    if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
        # 若為員工/訪客，執行雲端同步；若關閉則完全跳過
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
                    try:
                        cloud.update_employee(employee_id or profile.name, {
                            'name': profile.name,
                            'face_image': data_url,
                            'face_feature': None,
                        })
                    except Exception:
                        pass
            else:
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
                        try:
                            cloud.update_visitor(index, info_payload)
                        except Exception as e:
                            logging.exception('cloud.update_visitor failed: %s', e)
                        try:
                            cloud.fill_info_visitor(index, info_payload)
                        except Exception as e:
                            logging.exception('cloud.fill_info_visitor failed: %s', e)
                        try:
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
                    logging.exception('cloud pre_register/flow failed: %s', e)
        except Exception:
            pass

    return JsonResponse({'ok': True})


@login_required
@ensure_csrf_cookie
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
@ensure_csrf_cookie
def console_employees(request: HttpRequest):
    query_skip = int(request.GET.get('skip', '0') or '0')
    query_limit = int(request.GET.get('limit', '20') or '20')
    if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
        try:
            data = cloud.list_employees(skip=query_skip, limit=query_limit)
        except Exception as e:
            data = {'total': 0, 'items': [], 'error': str(e)}
    else:
        user_company = getattr(getattr(request.user, 'account_profile', None), 'company', None)
        qs = Profile.objects.filter(role=Profile.ROLE_EMPLOYEE, company=user_company).order_by('-created_at')
        total = qs.count()
        items = [
            {
                'employee_id': str(p.id),
                'name': p.name,
                'created_at': p.created_at,
            }
            for p in qs[query_skip:query_skip + query_limit]
        ]
        data = {'total': total, 'items': items}
    return render(request, 'collector/console_employees.html', {'data': data, 'active_tab': 'employees'})


@login_required
@ensure_csrf_cookie
def console_employee_create(request: HttpRequest):
    if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
        return redirect('console_employees')
    # 取得使用者所屬公司
    user_company = None
    try:
        account_profile = getattr(request.user, 'account_profile', None)
        if account_profile:
            user_company = account_profile.company
    except Exception:
        user_company = None
    
    if not user_company:
        return render(request, 'collector/console_employee_create.html', {'error': '您的帳號尚未關聯到公司，請聯絡管理員'})
    
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        emp_id = (request.POST.get('employee_id') or '').strip()
        if not name or not emp_id:
            return render(request, 'collector/console_employee_create.html', {'error': '姓名與員工編號必填'})
        p = Profile.objects.create(name=name, role=Profile.ROLE_EMPLOYEE, company=user_company)
        logging.info(f'已為公司 {user_company.name} 建立員工 Profile: {p.name}')
        return redirect('console_employees')
    return render(request, 'collector/console_employee_create.html')


@login_required
@require_POST
def console_employee_delete(request: HttpRequest, employee_id: str):
    if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
        try:
            cloud.delete_employee(employee_id)
            referer = request.META.get('HTTP_REFERER')
            if referer:
                return redirect(referer)
            return redirect('console_employees')
        except Exception as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    else:
        try:
            pid = int(employee_id)
            user_company = getattr(getattr(request.user, 'account_profile', None), 'company', None)
            Profile.objects.filter(id=pid, role=Profile.ROLE_EMPLOYEE, company=user_company).delete()
            referer = request.META.get('HTTP_REFERER')
            if referer:
                return redirect(referer)
            return redirect('console_employees')
        except Exception as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@login_required
def console_employee_detail(request: HttpRequest, employee_id: str):
    if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
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
    else:
        error = None
        image_src = None
        try:
            pid = int(employee_id)
            user_company = getattr(getattr(request.user, 'account_profile', None), 'company', None)
            p = Profile.objects.get(id=pid, role=Profile.ROLE_EMPLOYEE, company=user_company)
            cap = Capture.objects.filter(profile=p, selected=True).first() or Capture.objects.filter(profile=p).order_by('-created_at').first()
            if cap and getattr(cap.image, 'url', None):
                image_src = cap.image.url
            item = {'name': p.name}
        except Exception as e:
            item = None
            error = str(e)
        return render(request, 'collector/console_employee_detail.html', {
            'item': item,
            'image_src': image_src,
            'error': error,
            'active_tab': 'employees',
        })


@login_required
@ensure_csrf_cookie
def console_visitors(request: HttpRequest):
    query_skip = int(request.GET.get('skip', '0') or '0')
    query_limit = int(request.GET.get('limit', '20') or '20')
    if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
        try:
            data = cloud.list_visitors(skip=query_skip, limit=query_limit)
        except Exception as e:
            data = {'total': 0, 'items': [], 'error': str(e)}
    else:
        user_company = getattr(getattr(request.user, 'account_profile', None), 'company', None)
        qs = Profile.objects.filter(role=Profile.ROLE_VISITOR, company=user_company).order_by('-created_at')
        total = qs.count()
        items = [
            {
                'index': str(p.id),
                'name': p.name,
                'created_at': p.created_at,
            }
            for p in qs[query_skip:query_skip + query_limit]
        ]
        data = {'total': total, 'items': items}
    return render(request, 'collector/console_visitors.html', {'data': data, 'active_tab': 'visitors'})


@login_required
@ensure_csrf_cookie
def console_visitor_create(request: HttpRequest):
    if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
        return redirect('console_visitors')
    # 取得使用者所屬公司
    user_company = None
    try:
        account_profile = getattr(request.user, 'account_profile', None)
        if account_profile:
            user_company = account_profile.company
    except Exception:
        user_company = None
    
    if not user_company:
        return render(request, 'collector/console_visitor_create.html', {'error': '您的帳號尚未關聯到公司，請聯絡管理員'})
    
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            return render(request, 'collector/console_visitor_create.html', {'error': '姓名必填'})
        # 設定當天到期
        now = timezone.now()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        p = Profile.objects.create(name=name, role=Profile.ROLE_VISITOR, expires_at=end_of_day, company=user_company)
        logging.info(f'已為公司 {user_company.name} 建立訪客 Profile: {p.name}')
        return redirect('console_visitors')
    return render(request, 'collector/console_visitor_create.html')


@login_required
@require_POST
def console_visitor_delete(request: HttpRequest, visitor_index: str):
    if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
        try:
            cloud.delete_visitor(visitor_index)
            referer = request.META.get('HTTP_REFERER')
            if referer:
                return redirect(referer)
            return redirect('console_visitors')
        except Exception as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)
    else:
        try:
            pid = int(visitor_index)
            user_company = getattr(getattr(request.user, 'account_profile', None), 'company', None)
            Profile.objects.filter(id=pid, role=Profile.ROLE_VISITOR, company=user_company).delete()
            referer = request.META.get('HTTP_REFERER')
            if referer:
                return redirect(referer)
            return redirect('console_visitors')
        except Exception as e:
            return JsonResponse({'ok': False, 'error': str(e)}, status=400)


@login_required
def console_visitor_detail(request: HttpRequest, visitor_index: str):
    if getattr(settings, 'CLOUD_SYNC_ENABLED', True):
        item = None
        error = None
        image_src = None
        has_face = False
        try:
            item = cloud.get_visitor(visitor_index)
            if not item:
                error = 'not-found'
            else:
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
    else:
        # CLOUD_SYNC_ENABLED=False：從本地資料庫讀取訪客資料
        item = None
        error = None
        image_src = None
        has_face = False
        try:
            pid = int(visitor_index)
            user_company = getattr(getattr(request.user, 'account_profile', None), 'company', None)
            p = Profile.objects.get(id=pid, role=Profile.ROLE_VISITOR, company=user_company)
            cap = Capture.objects.filter(profile=p, selected=True).first() or Capture.objects.filter(profile=p).order_by('-created_at').first()
            if cap and getattr(cap.image, 'url', None):
                image_src = cap.image.url
                has_face = True
            item = {'name': p.name}
        except Exception as e:
            item = None
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


# --- Company-scoped read-only APIs ---

def _get_company_by_name_or_404(company_name: str) -> Company:
    try:
        return Company.objects.get(name=company_name)
    except Company.DoesNotExist:
        raise Exception('not-found')


def api_employees(request: HttpRequest, company_name: str):
    if request.method != 'GET':
        return JsonResponse({'error': 'method-not-allowed'}, status=405)
    try:
        company = _get_company_by_name_or_404(company_name)
        skip = int(request.GET.get('skip', '0') or '0')
        limit = int(request.GET.get('limit', '50') or '50')
        # 僅列出已完成（有代表照）的對象
        qs = (
            Profile.objects
            .filter(company=company, role=Profile.ROLE_EMPLOYEE, captures__selected=True)
            .order_by('-created_at')
            .distinct()
        )
        items = [
            {
                'id': p.id,
                'name': p.name,
                'created_at': p.created_at.isoformat(),
            }
            for p in qs[skip:skip+limit]
        ]
        return JsonResponse({'total': qs.count(), 'items': items})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=404)


def api_employee_detail(request: HttpRequest, company_name: str, employee_id: int):
    if request.method != 'GET':
        return JsonResponse({'error': 'method-not-allowed'}, status=405)
    try:
        company = _get_company_by_name_or_404(company_name)
        p = Profile.objects.get(company=company, role=Profile.ROLE_EMPLOYEE, id=employee_id)
        cap = Capture.objects.filter(profile=p, selected=True).first()
        if not cap:
            return JsonResponse({'error': 'not-completed'}, status=404)
        data = {
            'id': p.id,
            'name': p.name,
            'image': _imagefile_to_data_url(cap.image) if (cap and getattr(cap.image, 'name', None)) else None,
            'created_at': p.created_at.isoformat(),
        }
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=404)


def api_visitors(request: HttpRequest, company_name: str):
    if request.method != 'GET':
        return JsonResponse({'error': 'method-not-allowed'}, status=405)
    try:
        company = _get_company_by_name_or_404(company_name)
        skip = int(request.GET.get('skip', '0') or '0')
        limit = int(request.GET.get('limit', '50') or '50')
        # 僅列出已完成（有代表照）的對象
        qs = (
            Profile.objects
            .filter(company=company, role=Profile.ROLE_VISITOR, captures__selected=True)
            .order_by('-created_at')
            .distinct()
        )
        items = [
            {
                'id': p.id,
                'name': p.name,
                'created_at': p.created_at.isoformat(),
            }
            for p in qs[skip:skip+limit]
        ]
        return JsonResponse({'total': qs.count(), 'items': items})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=404)


def api_visitor_detail(request: HttpRequest, company_name: str, visitor_id: int):
    if request.method != 'GET':
        return JsonResponse({'error': 'method-not-allowed'}, status=405)
    try:
        company = _get_company_by_name_or_404(company_name)
        p = Profile.objects.get(company=company, role=Profile.ROLE_VISITOR, id=visitor_id)
        cap = Capture.objects.filter(profile=p, selected=True).first()
        if not cap:
            return JsonResponse({'error': 'not-completed'}, status=404)
        data = {
            'id': p.id,
            'name': p.name,
            'image': _imagefile_to_data_url(cap.image) if (cap and getattr(cap.image, 'name', None)) else None,
            'created_at': p.created_at.isoformat(),
        }
        return JsonResponse(data)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=404)


def _imagefile_to_data_url(file_field) -> str | None:
    try:
        path = getattr(file_field, 'path', None)
        if not path:
            return None
        with open(path, 'rb') as f:
            binary = f.read()
        mime, _ = mimetypes.guess_type(getattr(file_field, 'name', '') or path)
        mime = mime or 'image/jpeg'
        b64 = base64.b64encode(binary).decode('utf-8')
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None

