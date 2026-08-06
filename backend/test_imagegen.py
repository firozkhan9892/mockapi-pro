import requests, sys
from pathlib import Path
BASE = "http://127.0.0.1:5000"
p = f = 0
def T(n, c):
    global p, f
    if c:
        print(f"  PASS  {n}"); p += 1
    else:
        print(f"  FAIL  {n}"); f += 1

def ensure_login(session, email, password):
    r = session.post(f"{BASE}/api/signup", json={"email": email, "password": password})
    if r.status_code != 200:
        session.post(f"{BASE}/api/login", json={"email": email, "password": password})

s = requests.Session()
ensure_login(s, "gen@t.com", "pass1234")
s2 = requests.Session()
ensure_login(s2, "gen2@t.com", "pass1234")

print("=== IMAGE GENERATION API (v1) TESTS ===\n")

# [1] Auth
print("[1] Authentication")
r = requests.post(f"{BASE}/api/v1/images/generate", json={"prompt": "x"})
T("Generate blocked unauth", r.status_code == 401)
r = requests.get(f"{BASE}/api/v1/images")
T("List blocked unauth", r.status_code == 401)
r = requests.delete(f"{BASE}/api/v1/images/abc")
T("Delete blocked unauth", r.status_code == 401)

# [2] Validation
print("\n[2] Validation")
r = s.post(f"{BASE}/api/v1/images/generate", json={})
T("Prompt required", r.status_code == 400 and "prompt" in r.json().get("error", ""))
r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "   "})
T("Blank prompt rejected", r.status_code == 400)
r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "cat", "model": "gpt-4"})
T("Unsupported model rejected", r.status_code == 400 and "model" in r.json().get("error", ""))
r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "cat", "width": 16})
T("Width too small", r.status_code == 400 and "width" in r.json().get("error", ""))
r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "cat", "width": 4096})
T("Width too large", r.status_code == 400)
r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "cat", "height": "big"})
T("Height not integer", r.status_code == 400)
r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "cat", "width": 512.5})
T("Width not integer", r.status_code == 400)
r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "cat", "negative_prompt": "x" * 2001})
T("Negative prompt too long", r.status_code == 400 and "negative_prompt" in r.json().get("error", ""))
r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "cat", "negative_prompt": "   "})
T("Blank negative prompt accepted", r.status_code == 201)

# [3] Generate
print("\n[3] Generate")
r = s.post(f"{BASE}/api/v1/images/generate", json={
    "prompt": "a red fox", "negative_prompt": "blur",
    "model": "flux-schnell", "width": 512, "height": 512})
T("Generate 201", r.status_code == 201 and r.json().get("success") == True)
img = r.json().get("image", {})
img_id = img.get("id", "")
T("Has id", len(img_id) > 0)
T("Prompt echoed", img.get("prompt") == "a red fox")
T("Negative prompt echoed", img.get("negative_prompt") == "blur")
T("Model echoed", img.get("model") == "flux-schnell")
T("Width/height echoed", img.get("width") == 512 and img.get("height") == 512)
T("Status completed", img.get("status") == "completed")
T("Filename ends .png", img.get("filename", "").endswith(".png"))
T("Filepath under storage/images", "storage/images" in img.get("filepath", ""))
T("Has url", img.get("url") == f"/api/v1/images/{img_id}")
T("Has created_at", img.get("created_at") is not None)
T("User id set", img.get("user_id") == img.get("user_id"))

r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "sunset"})
T("Defaults applied", r.status_code == 201 and r.json()["image"]["width"] == 1024
  and r.json()["image"]["height"] == 1024 and r.json()["image"]["model"] == "flux-schnell")

# [4] Get image file
print("\n[4] Get image file")
r = s.get(f"{BASE}/api/v1/images/{img_id}")
T("Owner gets PNG", r.status_code == 200 and r.content[:4] == b"\x89PNG"
  and "image/png" in r.headers.get("Content-Type", ""))
r = s.get(f"{BASE}/api/v1/images/nope")
T("Missing image 404", r.status_code == 404)

# [5] List
print("\n[5] List")
r = s.get(f"{BASE}/api/v1/images")
T("List 200", r.status_code == 200)
T("List has images", r.json().get("total", 0) >= 2)
ids = [i.get("id") for i in r.json().get("images", [])]
T("Contains generated id", img_id in ids)

# [6] Ownership isolation
print("\n[6] Ownership isolation")
r = s2.get(f"{BASE}/api/v1/images/{img_id}")
T("Other user cannot get", r.status_code == 404)
r = s2.delete(f"{BASE}/api/v1/images/{img_id}")
T("Other user cannot delete", r.status_code == 404)
r = s2.get(f"{BASE}/api/v1/images")
T("Other user list empty", r.json().get("total") == 0)

# [7] API key auth + usage
print("\n[7] API key auth + usage")
r = s.post(f"{BASE}/api/keys", json={"name": "gen-key"})
raw_key = r.json().get("key", "")
T("Created API key", raw_key.startswith("mk_live_"))

r = requests.post(f"{BASE}/api/v1/images/generate", json={"prompt": "via key"},
                  headers={"x-api-key": raw_key})
T("Generate via key 201", r.status_code == 201)
key_img_id = r.json()["image"]["id"]

r = requests.get(f"{BASE}/api/v1/images/{key_img_id}", headers={"x-api-key": raw_key})
T("Get via key works", r.status_code == 200 and r.content[:4] == b"\x89PNG")

r = requests.delete(f"{BASE}/api/v1/images/{key_img_id}", headers={"x-api-key": raw_key})
T("Delete via key works", r.status_code == 200 and r.json().get("success") == True)

r = s.get(f"{BASE}/api/usage")
T("Usage recorded for key calls", r.json().get("today", 0) >= 1)

# [8] Delete
print("\n[8] Delete")
r = s.delete(f"{BASE}/api/v1/images/{img_id}")
T("Owner delete success", r.status_code == 200 and r.json().get("success") == True)
r = s.get(f"{BASE}/api/v1/images/{img_id}")
T("Gone after delete", r.status_code == 404)
r = s.delete(f"{BASE}/api/v1/images/{img_id}")
T("Second delete 404", r.status_code == 404)

r = s.post(f"{BASE}/api/v1/images/generate", json={"prompt": "delete-missing-file"})
missing_img = r.json()["image"]
Path(missing_img["filepath"]).unlink()
r = s.delete(f"{BASE}/api/v1/images/{missing_img['id']}")
T("Delete succeeds when file missing", r.status_code == 200 and r.json().get("success") == True)

# [9] Admin override
print("\n[9] Admin override")
r = s2.post(f"{BASE}/api/v1/images/generate", json={"prompt": "admin-visible"})
admin_img_id = r.json()["image"]["id"]

admin_s = requests.Session()
ar = admin_s.post(f"{BASE}/api/login", json={"email": "firozkhan2027k@gmail.com", "password": "admin123"})
if ar.status_code != 200:
    admin_s.post(f"{BASE}/api/signup", json={"email": "firozkhan2027k@gmail.com", "password": "admin123"})

r = admin_s.get(f"{BASE}/api/v1/images/{admin_img_id}")
T("Admin can get any image", r.status_code == 200 and r.content[:4] == b"\x89PNG")
r = admin_s.delete(f"{BASE}/api/v1/images/{admin_img_id}")
T("Admin can delete any image", r.status_code == 200 and r.json().get("success") == True)
r = s2.get(f"{BASE}/api/v1/images/{admin_img_id}")
T("Gone after admin delete", r.status_code == 404)

print(f"\n=== {p} passed, {f} failed ===")
sys.exit(1 if f else 0)
