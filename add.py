import sys
import requests

base = 'https://dashboard.syncrobotic.ai/foxlink/security_system_api'
s = requests.Session()
s.verify = True  # 若為自簽可暫時關閉驗證 s.verify=False

# 刪除員工：python3 add.py delete <employee_id>
if len(sys.argv) >= 3 and sys.argv[1] == 'delete':
  employee_id = sys.argv[2]
  r = s.delete(f'{base}/employees/{employee_id}')
  print(r.status_code, r.text)
  raise SystemExit(0)

# 新增員工
payload = {
  "plate_number": "ABC-1234",
  "name": "王小明",
  "employee_id": "E001",
  "face_image": "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD...",
  "face_feature": None
}
r2 = s.post(f'{base}/employees/', json=payload)
print(r2.status_code, r2.text)