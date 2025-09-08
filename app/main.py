from fastapi import FastAPI, UploadFile, File, Query
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import RedirectResponse, JSONResponse
import cv2
import numpy as np
from pathlib import Path
import time
from typing import Optional, Dict
from scripts.insight_utils import has_face_features, warmup_if_needed


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATIC_DIR = BASE_DIR / "static"

DATA_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Face Collection Service")
warmup_if_needed()

# CORS - 視需要開放，同源訪問即可
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _load_face_detector() -> cv2.CascadeClassifier:
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)
    if detector.empty():
        raise RuntimeError(f"Failed to load Haar cascade at: {cascade_path}")
    return detector


FACE_DETECTOR = _load_face_detector()


@app.get("/")
async def root_redirect():
    return RedirectResponse(url="/static/index.html")


@app.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    label: Optional[str] = Query(default="unknown", description="使用者標籤，例如姓名"),
):
    try:
        file_bytes = await file.read()
        if not file_bytes:
            return JSONResponse(status_code=400, content={"error": "空檔案"})

        np_arr = np.frombuffer(file_bytes, dtype=np.uint8)
        image_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if image_bgr is None:
            return JSONResponse(status_code=400, content={"error": "影像解碼失敗"})

        # 使用 insightface 驗證是否有人臉特徵
        try:
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            if not has_face_features(image_rgb):
                return JSONResponse(status_code=400, content={"error": "no-face"})
        except Exception as _exc:
            return JSONResponse(status_code=500, content={"error": "insightface-error"})

        gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
        faces = FACE_DETECTOR.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(60, 60),
        )

        label_name = (label or "unknown").strip() or "unknown"
        label_dir = DATA_DIR / label_name
        label_dir.mkdir(parents=True, exist_ok=True)

        timestamp = int(time.time() * 1000)
        saved = 0
        h, w = gray.shape[:2]

        for idx, (x, y, fw, fh) in enumerate(faces):
            # 簡單加邊界
            margin = 0.2
            mx = int(max(0, x - fw * margin))
            my = int(max(0, y - fh * margin))
            mx2 = int(min(w, x + fw * (1 + margin)))
            my2 = int(min(h, y + fh * (1 + margin)))

            face_roi = image_bgr[my:my2, mx:mx2]
            if face_roi.size == 0:
                continue

            # 可選：統一尺寸
            try:
                face_resized = cv2.resize(face_roi, (160, 160))
            except Exception:
                face_resized = face_roi

            out_path = label_dir / f"{timestamp}_{idx}.jpg"
            cv2.imwrite(str(out_path), face_resized)
            saved += 1

        return {
            "label": label_name,
            "faces_detected": int(len(faces)),
            "faces_saved": int(saved),
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/count")
async def count_images(label: Optional[str] = Query(default=None)) -> Dict[str, int]:
    if label:
        dir_path = DATA_DIR / label
        if not dir_path.exists():
            return {label: 0}
        count = sum(1 for p in dir_path.iterdir() if p.is_file())
        return {label: count}

    # 全部標籤
    result: Dict[str, int] = {}
    for p in DATA_DIR.iterdir():
        if p.is_dir():
            result[p.name] = sum(1 for fp in p.iterdir() if fp.is_file())
    return result


@app.get("/labels")
async def list_labels():
    labels = [p.name for p in DATA_DIR.iterdir() if p.is_dir()]
    return {"labels": sorted(labels)}



