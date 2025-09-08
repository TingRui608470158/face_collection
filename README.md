# Face Collection Service

以 FastAPI 建置的簡易人臉影像收集服務。前端使用瀏覽器攝影機擷取影像，每 500ms 上傳一張到後端，後端以 OpenCV 進行人臉偵測並裁切存檔。同時於上傳與最終確認（Django finalize）階段加入 insightface 驗證，若未偵測到人臉特徵將拒收並提示重新選擇。

## 需求
- Python 3.9+
- 建議使用虛擬環境

## 安裝
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install fastapi "uvicorn[standard]" opencv-python-headless python-multipart aiofiles
# 人臉特徵（CPU 預設）
pip install insightface onnxruntime
```

## 啟動
```bash
uvicorn webapp.asgi:application --host 0.0.0.0 --port 8443 --ssl-certfile ./face.syncrobotic.ai/fullchain.crt --ssl-keyfile ./face.syncrobotic.ai/private.key
```

啟動後瀏覽：
- `http://<伺服器IP或網域>:8000/` → 前端頁面
- `POST /upload?label=<姓名>` → 上傳單張影像（`file` 欄位，image/jpeg）
  - 若未偵測到人臉特徵，回覆 `{ "error": "no-face" }` 與 400
- `GET /count?label=<姓名>` → 取得該標籤累計影像數
- `GET /labels` → 取得所有標籤清單

## 資料儲存
- 影像存於 `data/<label>` 底下（已裁切/縮放的臉部區域）

## 注意
- 本專案使用 OpenCV Haar Cascade 進行快速人臉偵測，速度快、精度中等；另以 insightface 進一步驗證人臉特徵。若需更高準確率可換用 DNN/RetinaFace 等方法。
- 前端採每 500ms 上傳一張，請依網路與性能調整。

## 快速清理與重啟（8443 埠）

- 清理被佔用的 8443 埠：
```bash
scripts/clean_8443.sh
```

- 清理後直接重啟 HTTPS 服務：
```bash
scripts/restart_https.sh
```
