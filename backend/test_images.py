import requests, sys
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

PNG_BYTES = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\rIHDR' + b'\x00' * 30
GIF_BYTES = b'GIF89a' + b'\x01\x00\x01\x00' + b'\x00' * 30

s = requests.Session()
ensure_login(s, "imgtest@t.com", "pass1234")

s2 = requests.Session()
ensure_login(s2, "imgtest2@t.com", "pass1234")

print("=== IMAGE API TESTS ===\n")

# [1] Initial state
print("[1] Initial state")
r = s.get(f"{BASE}/api/images")
T("List empty", r.status_code == 200 and r.json().get("total") == 0)

# [2] Upload valid image
print("\n[2] Upload valid image")
r = s.post(f"{BASE}/api/images", files={"file": ("test.png", PNG_BYTES, "image/png")})
T("Upload success 201", r.status_code == 201 and r.json().get("success") == True)
img_id = r.json().get("id", "")
T("Has id", len(img_id) > 0)
T("Has url", r.json().get("url") == f"/api/images/{img_id}")
T("Mime is image/png", r.json().get("mime_type") == "image/png")
T("Size recorded", r.json().get("size") == len(PNG_BYTES))

# [3] Upload rejects invalid types
print("\n[3] Reject invalid images")
r = s.post(f"{BASE}/api/images", files={"file": ("notes.txt", b"not an image", "text/plain")})
T("Reject txt ext", r.status_code == 400)

r = s.post(f"{BASE}/api/images", files={"file": ("fake.png", b"not an image", "image/png")})
T("Reject fake magic bytes", r.status_code == 400)

big = b'\x89PNG\r\n\x1a\n' + b'\x00' * (10 * 1024 * 1024)
r = s.post(f"{BASE}/api/images", files={"file": ("huge.png", big, "image/png")})
T("Reject over 10MB", r.status_code == 413)

r = s.post(f"{BASE}/api/images", files={})
T("Reject no file", r.status_code == 400)

# [4] List shows image
print("\n[4] List after upload")
r = s.get(f"{BASE}/api/images")
T("List has 1", r.status_code == 200 and r.json().get("total") == 1)
listed = r.json()["images"][0]
T("Image id matches", listed.get("id") == img_id)
T("Original name kept", listed.get("original_name") == "test.png")
T("Stored name random", listed.get("filename") != "test.png" and listed.get("filename", "").endswith(".png"))

# [5] Get by id
print("\n[5] Get image")
r = s.get(f"{BASE}/api/images/{img_id}")
T("Owner can fetch", r.status_code == 200 and r.content == PNG_BYTES)
T("Content type png", "image/png" in r.headers.get("Content-Type", ""))

r = s.get(f"{BASE}/api/images/does-not-exist")
T("Missing image 404", r.status_code == 404)

# [6] Cross-user isolation
print("\n[6] Cross-user isolation")
r = s2.get(f"{BASE}/api/images/{img_id}")
T("Other user cannot fetch", r.status_code == 404)
r = s2.get(f"{BASE}/api/images")
T("Other user list empty", r.status_code == 200 and r.json().get("total") == 0)
r = s2.delete(f"{BASE}/api/images/{img_id}")
T("Other user cannot delete", r.status_code == 404)

# [7] Upload via API key + usage tracking
print("\n[7] API key auth + usage")
r = s.post(f"{BASE}/api/keys", json={"name": "img-key"})
raw_key = r.json().get("key", "")
T("Created API key", r.status_code == 200 and raw_key.startswith("mk_live_"))

usage_before = s.get(f"{BASE}/api/usage").json()
r = s.post(f"{BASE}/api/images",
           files={"file": ("gif.gif", GIF_BYTES, "image/gif")},
           headers={"x-api-key": raw_key})
T("Upload via key 201", r.status_code == 201)
gif_id = r.json().get("id", "")

r = requests.get(f"{BASE}/api/images/{gif_id}", headers={"x-api-key": raw_key})
T("Get via key works", r.status_code == 200 and r.content == GIF_BYTES)

r = requests.get(f"{BASE}/api/images")
T("Unauth list blocked", r.status_code == 401)
r = requests.post(f"{BASE}/api/images", files={"file": ("a.png", PNG_BYTES, "image/png")})
T("Unauth upload blocked", r.status_code == 401)
r = requests.get(f"{BASE}/api/images/{img_id}")
T("Unauth get blocked", r.status_code == 401)

# [8] Owner delete
print("\n[8] Delete")
r = s2.post(f"{BASE}/api/images", files={"file": ("own.png", PNG_BYTES, "image/png")})
own_id = r.json().get("id", "")
r = s2.delete(f"{BASE}/api/images/{own_id}")
T("Owner delete success", r.status_code == 200 and r.json().get("success") == True)
r = s2.get(f"{BASE}/api/images/{own_id}")
T("Gone after delete", r.status_code == 404)
r = s2.delete(f"{BASE}/api/images/{own_id}")
T("Second delete 404", r.status_code == 404)

# [9] Admin panel
print("\n[9] Admin panel")
r = requests.get(f"{BASE}/admin/images")
T("Admin list blocked unauth", r.status_code == 401)

r = s.get(f"{BASE}/admin/images")
T("Non-admin blocked", r.status_code == 403)

# keep an image from imgtest2 for the cross-user admin check
r = s2.post(f"{BASE}/api/images", files={"file": ("keep.png", PNG_BYTES, "image/png")})
keep_id = r.json().get("id", "")

# admin login
admin_s = requests.Session()
ar = admin_s.post(f"{BASE}/api/login", json={"email": "firozkhan2027k@gmail.com", "password": "admin123"})
if ar.status_code != 200:
    admin_s.post(f"{BASE}/api/signup", json={"email": "firozkhan2027k@gmail.com", "password": "admin123"})

r = admin_s.get(f"{BASE}/admin/images")
T("Admin list all", r.status_code == 200 and r.json().get("total") >= 3)
emails = [i.get("user_email") for i in r.json()["images"]]
T("Sees both users' images", "imgtest@t.com" in emails and "imgtest2@t.com" in emails)

r = admin_s.get(f"{BASE}/admin/images?search=imgtest@")
T("Admin search works", r.status_code == 200 and all(i.get("user_email") == "imgtest@t.com" for i in r.json()["images"]))

r = admin_s.get(f"{BASE}/admin/dashboard")
T("Admin stats include images", r.status_code == 200 and "total_images" in r.json() and "storage_used" in r.json())
T("Storage used > 0", r.json()["storage_used"] > 0)

r = admin_s.get(f"{BASE}/api/images/{gif_id}")
T("Admin can fetch any image", r.status_code == 200 and r.content == GIF_BYTES)

r = admin_s.delete(f"{BASE}/admin/images/{img_id}")
T("Admin delete success", r.status_code == 200 and r.json().get("success") == True)
r = admin_s.get(f"{BASE}/api/images/{img_id}")
T("Gone after admin delete", r.status_code == 404)

r = admin_s.delete(f"{BASE}/admin/images/{img_id}")
T("Admin second delete 404", r.status_code == 404)

# [10] Dashboard stats updated
print("\n[10] Cleanup")
r = s.delete(f"{BASE}/api/images/{gif_id}")
T("Owner cleanup delete", r.status_code == 200)

r = s2.delete(f"{BASE}/api/images/{keep_id}")
T("imgtest2 cleanup delete", r.status_code == 200)

r = s.get(f"{BASE}/api/usage")
usage = r.json()
T("Usage recorded for key calls", usage.get("today", 0) >= 1)

print(f"\n=== {p} passed, {f} failed ===")
sys.exit(1 if f else 0)
