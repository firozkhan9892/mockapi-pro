import requests, sys
from pathlib import Path
BASE = "http://127.0.0.1:5000"
DB_PATH = Path(__file__).resolve().parent / "mockapi.db"
p = f = 0
def T(n, c):
    global p, f
    if c:
        print(f"  PASS  {n}"); p += 1
    else:
        print(f"  FAIL  {n}"); f += 1

# Setup: create user + mock endpoint + API key
s = requests.Session()
r = s.post(f"{BASE}/api/signup", json={"email": "usage@t.com", "password": "pass1234"})
if r.status_code != 200:
    s.post(f"{BASE}/api/login", json={"email": "usage@t.com", "password": "pass1234"})

# Get user_id
me = s.get(f"{BASE}/api/me").json()
uid = me["user_id"]

# Create mock endpoint
r = s.post(f"{BASE}/api/create", json={
    "endpoint": "/usage-test", "method": "GET",
    "response": '{"ok":true}', "status_code": 200
})
mock_url = r.json()["url"]

# Create API key
r = s.post(f"{BASE}/api/keys", json={"name": "test"})
api_key = r.json()["key"]

print("=== USAGE & RATE LIMIT TESTS ===\n")

# [1] Valid API key
print("[1] Valid API key")
r = requests.get(mock_url, headers={"x-api-key": api_key})
T("Valid key returns 200", r.status_code == 200 and r.json().get("ok") == True)

# [2] Invalid API key
print("\n[2] Invalid API key")
r = requests.get(mock_url, headers={"x-api-key": "mk_live_invalidkey123"})
T("Invalid key returns 401", r.status_code == 401)

r = requests.get(mock_url)
T("No key returns 401", r.status_code == 401)

# [3] Bearer auth
print("\n[3] Bearer auth")
r = requests.get(mock_url, headers={"Authorization": f"Bearer {api_key}"})
T("Bearer token works", r.status_code == 200)

# [4] Usage increment
print("\n[4] Usage increment")
r = s.get(f"{BASE}/api/usage")
data = r.json()
T("Usage endpoint returns today", "today" in data)
T("Usage endpoint returns limit", data.get("limit") == 250)
T("Usage > 0 after requests", data.get("today", 0) > 0)
T("Remaining = limit - today", data.get("remaining") == data["limit"] - data["today"])

# [5] Usage increments on each request
print("\n[5] Usage increments")
before = requests.get(f"{BASE}/api/usage", headers={"x-api-key": api_key}).json() if False else s.get(f"{BASE}/api/usage").json()
before_count = before.get("today", 0)
requests.get(mock_url, headers={"x-api-key": api_key})
requests.get(mock_url, headers={"x-api-key": api_key})
after = s.get(f"{BASE}/api/usage").json()
T("Count increased by 2", after.get("today", 0) == before_count + 2)

# [6] Daily limit (250)
print("\n[6] Daily limit")
# Manually set count to 250 in DB
import sqlite3
conn = sqlite3.connect(DB_PATH, timeout=10)
c = conn.cursor()
c.execute("SELECT id FROM api_keys WHERE key_hash=?", (
    __import__('hashlib').sha256(api_key.encode()).hexdigest(),))
kid = c.fetchone()[0]
from datetime import date
today = date.today().isoformat()
c.execute("UPDATE request_usage SET request_count=250 WHERE api_key_id=? AND date=?", (kid, today))
conn.commit()
conn.close()

r = requests.get(mock_url, headers={"x-api-key": api_key})
T("429 when limit reached", r.status_code == 429)
T("Error message correct", "Daily request limit reached" in r.json().get("error", ""))

# [7] Usage endpoint shows limit
print("\n[7] Usage at limit")
r = s.get(f"{BASE}/api/usage")
data = r.json()
T("Usage shows 250", data.get("today") == 250)
T("Remaining is 0", data.get("remaining") == 0)

# [8] Daily reset
print("\n[8] Daily reset")
conn = sqlite3.connect(DB_PATH, timeout=10)
c = conn.cursor()
c.execute("DELETE FROM request_usage WHERE api_key_id=? AND date=?", (kid, today))
conn.commit()
conn.close()

r = s.get(f"{BASE}/api/usage")
data = r.json()
T("Usage resets to 0", data.get("today") == 0)
T("Remaining back to 250", data.get("remaining") == 250)

# Request works again after reset
r = requests.get(mock_url, headers={"x-api-key": api_key})
T("Request works after reset", r.status_code == 200)

# [9] Unauth usage endpoint
print("\n[9] Unauth usage endpoint")
r = requests.get(f"{BASE}/api/usage")
T("Usage blocked unauth", r.status_code == 401)

print(f"\n=== {p} passed, {f} failed ===")
sys.exit(1 if f else 0)
