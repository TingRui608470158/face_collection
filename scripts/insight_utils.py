from __future__ import annotations

from typing import Tuple

import threading
import numpy as np
from pathlib import Path
import cv2


_app = None
_lock = threading.Lock()


def _lazy_load_app(det_size: Tuple[int, int] = (640, 640)):
    global _app
    if _app is None:
        with _lock:
            if _app is None:
                from insightface.app import FaceAnalysis  # type: ignore
                app = FaceAnalysis(name='buffalo_l')
                app.prepare(ctx_id=-1, det_size=det_size)
                _try_warmup(app)
                _app = app
    return _app


def has_face_features(image_rgb: np.ndarray) -> bool:
    if image_rgb is None or image_rgb.ndim != 3 or image_rgb.shape[2] != 3:
        return False
    app = _lazy_load_app()
    faces = app.get(image_rgb)
    if not faces:
        return False
    for f in faces:
        if getattr(f, 'embedding', None) is not None:
            return True
    return True


def _try_warmup(app) -> None:
    try:
        base = Path(__file__).resolve().parents[1]
        img_path = base / 'static' / 'assets' / 'Lena.png'
        if not img_path.exists():
            return
        image_bgr = cv2.imread(str(img_path))
        if image_bgr is None or image_bgr.size == 0:
            return
        _ = app.get(image_bgr)
    except Exception:
        pass


def warmup_if_needed() -> None:
    """Public warmup entry to be called at app startup."""
    _lazy_load_app()


