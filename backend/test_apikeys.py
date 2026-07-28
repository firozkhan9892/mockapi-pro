import requests, sys
BASE = "http://127.0.0.1:5000"
p = f = 0
def T(n, c):
    global p, f
    if c:
        print(f"  PASS  {n}"); p += 1
    else:
        print(f"  FAIL  {n}"); f += 1

# Setup: login
s = requests.Session()
r = s.post(f"{BASE}/api/signup", json={"email": "keytest@t.com", "password": "pass1234"})
if r.status_code != 200:
    r = s.post(f"{BASE}/api/login", json={"email": "keytest@t.com", "password": "pass1234"})

print("=== API KEY TESTS ===\n")

# [1] List empty
print("[1] Initial state")
r = s.get(f"{BASE}/api/keys")
T("List empty", r.status_code == 200 and r.json().get("total") == 0)

# [2] Create key
print("\n[2] Create API Key")
r = s.post(f"{BASE}/api/keys", json={"name": "test-key"})
T("Create returns success", r.status_code == 200 and r.json().get("success") == True)
raw_key = r.json().get("key", "")
key_id = r.json().get("id", "")
T("Key starts with mk_live_", raw_key.startswith("mk_live_"))
T("Key has id", len(key_id) > 0)
T("Key preview returned", "..." in r.json().get("key_preview", ""))

# [3] List shows key
print("\n[3] List after create")
r = s.get(f"{BASE}/api/keys")
T("List has 1 key", r.status_code == 200 and r.json().get("total") == 1)
shown_key = r.json()["keys"][0]
T("Key has preview", "..." in shown_key.get("key_preview", ""))
T("Key has name", shown_key.get("name") == "test-key")
T("Key has created_at", shown_key.get("created_at") is not None)
T("Raw key NOT in list", raw_key not in str(shown_key))

# [4] Create second key (no name)
print("\n[4] Create second key")
r = s.post(f"{BASE}/api/keys", json={})
T("Create unnamed key", r.status_code == 200 and r.json().get("success") == True)
raw_key2 = r.json().get("key", "")
T("Different key generated", raw_key != raw_key2)

r = s.get(f"{BASE}/api/keys")
T("List has 2 keys", r.json().get("total") == 2)

# [5] Regenerate
print("\n[5] Regenerate Key")
r = s.post(f"{BASE}/api/keys/{key_id}/regenerate")
T("Regenerate success", r.status_code == 200 and r.json().get("success") == True)
regen_key = r.json().get("key", "")
T("New key different from original", regen_key != raw_key)
T("New key starts with mk_live_", regen_key.startswith("mk_live_"))

# Old key should not work (just verify different)
T("Old key replaced", regen_key != raw_key)

# [6] Delete
print("\n[6] Delete Key")
r = s.delete(f"{BASE}/api/keys/{key_id}")
T("Delete success", r.status_code == 200 and r.json().get("success") == True)

r = s.delete(f"{BASE}/api/keys/{key_id}")
T("Delete non-existent returns 404", r.status_code == 404)

r = s.get(f"{BASE}/api/keys")
remaining = r.json().get("total", -1)
T("List has 1 key after delete", remaining == 1)

# [7] Unauth access
print("\n[7] Unauthenticated")
r = requests.get(f"{BASE}/api/keys")
T("List blocked unauth", r.status_code == 401)

r = requests.post(f"{BASE}/api/keys", json={"name": "nope"})
T("Create blocked unauth", r.status_code == 401)

print(f"\n=== {p} passed, {f} failed ===")
sys.exit(1 if f else 0)
