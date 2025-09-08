import os
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.conf import settings


class CloudAPIClient:
    """Simple client for the security system cloud API.

    It keeps a requests.Session inside the Django process and reuses it across calls.
    """

    def __init__(self) -> None:
        base = getattr(settings, 'CLOUD_API_BASE', None) or os.getenv('CLOUD_API_BASE')
        if not base:
            base = 'https://dashboard.syncrobotic.ai/foxlink/security_system_api'
        self.base_url: str = base.rstrip('/')
        self.username: Optional[str] = getattr(settings, 'CLOUD_API_USERNAME', None) or os.getenv('CLOUD_API_USERNAME')
        self.password: Optional[str] = getattr(settings, 'CLOUD_API_PASSWORD', None) or os.getenv('CLOUD_API_PASSWORD')
        verify_ssl = getattr(settings, 'CLOUD_API_VERIFY_SSL', None)
        if verify_ssl is None:
            verify_ssl = os.getenv('CLOUD_API_VERIFY_SSL', 'true').lower() in ('1', 'true', 'yes')
        self.verify_ssl: bool = bool(verify_ssl)

        self._session: Optional[requests.Session] = None

    def _get_session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.verify = self.verify_ssl
        return self._session

    def _ensure_login(self) -> None:
        if not self.username or not self.password:
            # No credentials configured; assume endpoints are public or use IP allowlist
            return
        sess = self._get_session()
        # Quick check: if session already has cookies, skip eager login
        if sess.cookies:
            return
        resp = sess.post(f'{self.base_url}/login', json={
            'username': self.username,
            'password': self.password,
        })
        resp.raise_for_status()

    # Employees
    def list_employees(self, skip: int = 0, limit: int = 50) -> Dict[str, Any]:
        self._ensure_login()
        resp = self._get_session().get(f'{self.base_url}/employees/', params={'skip': skip, 'limit': limit})
        resp.raise_for_status()
        return resp.json()

    def create_employee(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_login()
        resp = self._get_session().post(f'{self.base_url}/employees/', json=payload)
        resp.raise_for_status()
        return resp.json()

    def get_employee(self, employee_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_login()
        resp = self._get_session().get(f'{self.base_url}/employees/{employee_id}')
        if resp.status_code == 200:
            return resp.json()
        return None

    def update_employee(self, employee_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self._ensure_login()
        resp = self._get_session().put(f'{self.base_url}/employees/{employee_id}', json=payload)
        resp.raise_for_status()
        return resp.json()

    def delete_employee(self, employee_id: str) -> Dict[str, Any]:
        self._ensure_login()
        resp = self._get_session().delete(f'{self.base_url}/employees/{employee_id}')
        resp.raise_for_status()
        # Some APIs return an object, some return empty. Normalize.
        return resp.json() if resp.headers.get('Content-Type','').startswith('application/json') and resp.text else {'ok': True}

    # Visitors
    def list_visitors(self, skip: int = 0, limit: int = 50) -> Dict[str, Any]:
        self._ensure_login()
        resp = self._get_session().get(f'{self.base_url}/visitors/', params={'skip': skip, 'limit': limit})
        resp.raise_for_status()
        return resp.json()

    def delete_visitor(self, visitor_index: str) -> Dict[str, Any]:
        self._ensure_login()
        resp = self._get_session().delete(f'{self.base_url}/visitors/{visitor_index}')
        resp.raise_for_status()
        return resp.json() if resp.headers.get('Content-Type','').startswith('application/json') and resp.text else {'ok': True}

    # Visitor registration flow
    def pre_register_visitor(self) -> Dict[str, Any]:
        """Create a new visitor record, returns {index, qrcode_base64, ...}."""
        self._ensure_login()
        resp = self._get_session().post(f'{self.base_url}/visitors/pre-register')
        resp.raise_for_status()
        return resp.json()

    def fill_info_visitor(self, index: str, info: Dict[str, Any]) -> Dict[str, Any]:
        """Fill basic info of a visitor: name, plate_number, company, etc."""
        self._ensure_login()
        resp = self._get_session().post(f'{self.base_url}/visitors/fill-info/{index}', json=info)
        resp.raise_for_status()
        return resp.json()

    def face_capture_visitor(self, index: str, face_data: Dict[str, Any]) -> Dict[str, Any]:
        """Upload face image (data URL) for the visitor to mark completed."""
        self._ensure_login()
        resp = self._get_session().post(f'{self.base_url}/visitors/face-capture/{index}', json=face_data)
        resp.raise_for_status()
        return resp.json()

    def update_visitor(self, index: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Update visitor fields (name, plate_number, etc.)."""
        self._ensure_login()
        resp = self._get_session().put(f'{self.base_url}/visitors/{index}', json=payload)
        resp.raise_for_status()
        return resp.json()


client = CloudAPIClient()


