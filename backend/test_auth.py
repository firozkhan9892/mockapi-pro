import requests, sys
BASE = "http://127.0.0.1:5000"
p = f = 0
def T(n, c):
    global p, f
    if c:
        print(f"  PASS  {n}"); p += 1
    else:
        print(f"  FAIL  {n}"); f += 1

s = requests.Session()
print("=== AUTH TESTS ===")
print()
print("[1] Signup")
r = s.post(f"{BASE}/api/signup", json={"email": "", "password": ""})
T("Reject empty fields", r.status_code == 400)
r = s.post(f"{BASE}/api/signup", json={"email": "t@t.com", "password": "123"})
T("Reject short pw", r.status_code == 400)
r = s.post(f"{BASE}/api/signup", json={"email": "t@t.com", "password": "pass1234"})
T("Signup ok", r.status_code == 200 and r.json().get("success"))
r = s.post(f"{BASE}/api/signup", json={"email": "t@t.com", "password": "pass1234"})
T("Reject dup", r.status_code == 409)

print()
print("[2] Session")
r = s.get(f"{BASE}/api/me")
T("Session persists", r.status_code == 200 and r.json().get("email") == "t@t.com")

print()
print("[3] Logout")
r = s.post(f"{BASE}/api/logout")
T("Logout ok", r.status_code == 200)
r = s.get(f"{BASE}/api/me")
T("Session cleared", r.status_code == 401)

print()
print("[4] Login")
s2 = requests.Session()
r = s2.post(f"{BASE}/api/login", json={"email": "t@t.com", "password": "wrong"})
T("Bad pw reject", r.status_code == 401)
r = s2.post(f"{BASE}/api/login", json={"email": "t@t.com", "password": "pass1234"})
T("Login ok", r.status_code == 200 and r.json().get("success"))
r = s2.get(f"{BASE}/api/me")
T("Session after login", r.status_code == 200)

print()
print("[5] Protected")
r = requests.get(f"{BASE}/api/list")
T("List blocked", r.status_code == 401)
r = requests.post(f"{BASE}/api/create", json={})
T("Create blocked", r.status_code == 401)

print()
print("[6] CRUD")
r = s2.post(f"{BASE}/api/create", json={"endpoint": "/t", "method": "GET", "response": '{"a":1}', "status_code": 200})
T("Create", r.status_code == 200 and r.json().get("success"))
aid = r.json()["api_id"]
url = r.json()["url"]
r = s2.get(f"{BASE}/api/list")
T("List", r.status_code == 200 and r.json().get("total", 0) >= 1)
kr = s2.post(f"{BASE}/api/keys", json={})
ak = kr.json()["key"]
r = requests.get(url, headers={"x-api-key": ak}, timeout=3)
T("Mock hit", r.status_code == 200 and r.json().get("a") == 1)
r = s2.delete(f"{BASE}/api/delete/{aid}")
T("Delete", r.status_code == 200 and r.json().get("success"))
r = s2.get(f"{BASE}/api/list")
T("List empty", r.json().get("total", -1) == 0)

print()
print(f"=== {p} passed, {f} failed ===")
sys.exit(1 if f else 0)
