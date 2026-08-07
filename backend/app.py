from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
from datetime import date
import json
import sqlite3
import uuid
import os
import hashlib
import secrets
import logging
from pathlib import Path

app = Flask(__name__, static_folder='../frontend', static_url_path='')

BASE_DIR = Path(__file__).resolve().parent
_database_path = os.environ.get("DATABASE_PATH", "").strip()
DB_NAME = Path(_database_path) if _database_path else BASE_DIR / "mockapi.db"
_upload_dir = os.environ.get("UPLOAD_DIR", "").strip()
UPLOAD_DIR = Path(_upload_dir) if _upload_dir else BASE_DIR / "uploads" / "images"

# Prefer a stable SECRET_KEY from the environment. If none is set (or the
# well-known placeholder is left in place) persist an auto-generated key next
# to the database so all workers and restarts share the same session secret.
# The file lives beside the DB (i.e. on the same persistent volume), so a
# Railway/container restart does not log every user out.
_secret_key = os.environ.get('SECRET_KEY', '').strip()
if not _secret_key or _secret_key == 'mockapi-secret-key-change-in-production':
    try:
        _secret_file = DB_NAME.parent / ".secret_key"
        if _secret_file.exists():
            _secret_key = _secret_file.read_text(encoding="utf-8").strip()
        if not _secret_key:
            DB_NAME.parent.mkdir(parents=True, exist_ok=True)
            _secret_key = secrets.token_hex(32)
            _secret_file.write_text(_secret_key, encoding="utf-8")
            print("[CONFIG] SECRET_KEY not set: generated and persisted to "
                  f"{_secret_file}. Set SECRET_KEY in production for best practice.")
    except OSError:
        pass
if not _secret_key:
    _secret_key = secrets.token_hex(32)
    print("[CONFIG] SECRET_KEY not set and could not be persisted; using an "
          "ephemeral random secret. Set a stable SECRET_KEY in production.")
app.secret_key = _secret_key

# Safety ceiling on request body size to mitigate denial-of-service via huge
# payloads. The image-upload endpoint enforces a tighter 10MB limit itself;
# 11MB leaves headroom for multipart encoding so legitimate uploads are unaffected.
app.config['MAX_CONTENT_LENGTH'] = 11 * 1024 * 1024

CORS(app, supports_credentials=True)

oauth = OAuth(app)
oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID', ''),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', ''),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()

ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'gif'}
MAX_IMAGE_SIZE = 10 * 1024 * 1024

def init_db():
    os.makedirs(DB_NAME.parent, exist_ok=True)
    os.makedirs(UPLOAD_DIR.parent, exist_ok=True)
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id TEXT PRIMARY KEY,
                  email TEXT UNIQUE NOT NULL,
                  password_hash TEXT,
                  auth_provider TEXT DEFAULT 'email',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS mock_apis
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id TEXT NOT NULL,
                  endpoint TEXT,
                  method TEXT,
                  response TEXT,
                  status_code INTEGER,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS rest_mocks
                 (id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  name TEXT NOT NULL,
                  endpoint TEXT NOT NULL,
                  status_code INTEGER DEFAULT 200,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS rest_records
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  mock_id TEXT NOT NULL,
                  data TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (mock_id) REFERENCES rest_mocks(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS api_keys
                 (id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  key_hash TEXT NOT NULL,
                  key_prefix TEXT NOT NULL,
                  name TEXT DEFAULT '',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  last_used_at TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS request_usage
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  api_key_id TEXT NOT NULL,
                  date TEXT NOT NULL,
                  request_count INTEGER DEFAULT 0,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (api_key_id) REFERENCES api_keys(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS images
                 (id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  filename TEXT NOT NULL,
                  original_name TEXT NOT NULL,
                  mime_type TEXT NOT NULL,
                  size INTEGER NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(id))''')

    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # Safe migrations - add columns if they don't exist
    migrations = [
        ("is_admin", "INTEGER NOT NULL DEFAULT 0"),
        ("role", "TEXT NOT NULL DEFAULT 'free'"),
        ("daily_limit", "INTEGER DEFAULT 250"),
        ("is_active", "INTEGER NOT NULL DEFAULT 1"),
    ]
    for col_name, col_def in migrations:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
        except sqlite3.OperationalError:
            pass

    # Backfill role based on is_admin for existing users
    try:
        c.execute("UPDATE users SET role='admin' WHERE is_admin=1 AND (role IS NULL OR role='free')")
        c.execute("UPDATE users SET role='free' WHERE role IS NULL")
    except sqlite3.OperationalError:
        pass

    # Migration: dedupe mock_apis so only one row exists per (user_id, endpoint, method)
    c.execute('''DELETE FROM mock_apis
                 WHERE id NOT IN (SELECT MIN(id) FROM mock_apis GROUP BY user_id, endpoint, method)''')
    # Enforce uniqueness going forward; prevents duplicate method registrations
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_mock_apis_unique ON mock_apis (user_id, endpoint, method)')
    # Enforce one REST mock per (user_id, endpoint)
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_rest_mocks_unique ON rest_mocks (user_id, endpoint)')

    conn.commit()
    conn.close()
    ensure_admin()

def get_user_id():
    return session.get('user_id')

def ensure_admin():
    if not ADMIN_EMAIL:
        return
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("UPDATE users SET is_admin=1, role='admin', daily_limit=0 WHERE email=? AND (is_admin=0 OR role!='admin')", (ADMIN_EMAIL,))
    if c.rowcount > 0:
        print(f"[ADMIN] Promoted {ADMIN_EMAIL} to admin")
    conn.commit()
    conn.close()

def get_user_daily_limit(user_id):
    """Get the daily limit for a user from the database.
    0 means unlimited. Admin, Pro and Enterprise are always unlimited;
    Free users use their configured daily_limit."""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT daily_limit, role FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 250  # default
    daily_limit = row[0]
    role = row[1]
    if role in ('admin', 'pro', 'enterprise'):
        return 0  # unlimited
    if daily_limit is None or daily_limit <= 0:
        return 250  # free default
    return daily_limit

def get_user_role(user_id):
    """Get the role for a user from the database."""
    if not user_id:
        return 'free'
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT role FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 'free'

def get_current_user():
    user_id = get_user_id()
    if not user_id:
        return None
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, email, role, daily_limit, is_active, is_admin, created_at FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    if not user:
        return None
    return {
        "id": user[0],
        "email": user[1],
        "role": user[2],
        "daily_limit": user[3],
        "is_active": user[4],
        "is_admin": user[5],
        "created_at": user[6]
    }

def is_admin_user(user_id):
    if not user_id:
        return False
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT role FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row and row[0] == 'admin'

def is_user_active(user_id):
    if not user_id:
        return False
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT is_active FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row and row[0] == 1

def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized"}), 401
        user_id = session['user_id']
        if not is_admin_user(user_id):
            return jsonify({"error": "Forbidden: Admin access required"}), 403
        return f(*args, **kwargs)
    return decorated

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized"}), 401
        user_id = session['user_id']
        conn = sqlite3.connect(DB_NAME, timeout=10)
        c = conn.cursor()
        c.execute("SELECT id, is_active FROM users WHERE id=?", (user_id,))
        user = c.fetchone()
        conn.close()
        if not user:
            session.pop('user_id', None)
            return jsonify({"error": "Unauthorized"}), 401
        if not user[1]:
            session.pop('user_id', None)
            return jsonify({"error": "Account disabled"}), 403
        return f(*args, **kwargs)
    return decorated

def generate_api_key():
    raw = f"mk_live_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    key_prefix = raw[:16]
    return raw, key_hash, key_prefix

DAILY_LIMIT = 250

def validate_api_key(api_key_raw):
    key_hash = hashlib.sha256(api_key_raw.encode()).hexdigest()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, user_id FROM api_keys WHERE key_hash=?", (key_hash,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None, None
    return row[0], row[1]

def get_today():
    return date.today().isoformat()

def get_usage(api_key_id):
    today = get_today()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT request_count FROM request_usage WHERE api_key_id=? AND date=?",
              (api_key_id, today))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def increment_usage(api_key_id):
    today = get_today()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, request_count FROM request_usage WHERE api_key_id=? AND date=?",
              (api_key_id, today))
    row = c.fetchone()
    if row:
        c.execute("UPDATE request_usage SET request_count=? WHERE id=?",
                  (row[1] + 1, row[0]))
    else:
        c.execute("INSERT INTO request_usage (api_key_id, date, request_count) VALUES (?, ?, 1)",
                  (api_key_id, today))
    c.execute("UPDATE api_keys SET last_used_at=CURRENT_TIMESTAMP WHERE id=?", (api_key_id,))
    conn.commit()
    conn.close()

@app.route('/api/signup', methods=['POST'])
def signup():
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400

    user_id = str(uuid.uuid4())
    password_hash = generate_password_hash(password)

    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    try:
        is_admin = 1 if email == ADMIN_EMAIL else 0
        role = 'admin' if email == ADMIN_EMAIL else 'free'
        daily_limit = 0 if email == ADMIN_EMAIL else 250
        c.execute("INSERT INTO users (id, email, password_hash, is_admin, role, daily_limit, is_active) VALUES (?, ?, ?, ?, ?, ?, 1)",
                  (user_id, email, password_hash, is_admin, role, daily_limit))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "Email already exists"}), 409

    session['user_id'] = user_id
    conn.close()
    return jsonify({"success": True, "user_id": user_id, "email": email})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400

    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, password_hash, is_active FROM users WHERE email=?", (email,))
    user = c.fetchone()
    conn.close()

    if not user or not check_password_hash(user[1], password):
        return jsonify({"error": "Invalid email or password"}), 401

    if not user[2]:
        return jsonify({"error": "Account disabled"}), 403

    session['user_id'] = user[0]
    return jsonify({"success": True, "user_id": user[0], "email": email})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})

@app.route('/logout')
def logout_page():
    session.clear()
    return redirect('/')

@app.route('/api/keys', methods=['GET'])
@login_required
def list_api_keys():
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, key_prefix, name, created_at, last_used_at FROM api_keys WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    keys = []
    for r in rows:
        keys.append({
            "id": r[0],
            "key_preview": f"{r[1]}...{'*' * 20}",
            "name": r[2],
            "created_at": r[3],
            "last_used_at": r[4],
        })
    return jsonify({"keys": keys, "total": len(keys)})

@app.route('/api/keys', methods=['POST'])
@login_required
def create_api_key():
    user_id = get_user_id()
    data = request.json or {}
    name = data.get("name", "").strip()

    raw, key_hash, key_prefix = generate_api_key()
    key_id = str(uuid.uuid4())

    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("INSERT INTO api_keys (id, user_id, key_hash, key_prefix, name) VALUES (?, ?, ?, ?, ?)",
              (key_id, user_id, key_hash, key_prefix, name))
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "id": key_id,
        "key": raw,
        "key_preview": f"{key_prefix}...{'*' * 20}",
        "name": name,
    })

@app.route('/api/keys/<key_id>', methods=['DELETE'])
@login_required
def delete_api_key(key_id):
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("DELETE FROM api_keys WHERE id=? AND user_id=?", (key_id, user_id))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted:
        return jsonify({"success": True})
    return jsonify({"error": "Key not found"}), 404

@app.route('/api/keys/<key_id>/regenerate', methods=['POST'])
@login_required
def regenerate_api_key(key_id):
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id FROM api_keys WHERE id=? AND user_id=?", (key_id, user_id))
    if not c.fetchone():
        conn.close()
        return jsonify({"error": "Key not found"}), 404

    raw, new_hash, new_prefix = generate_api_key()
    c.execute("UPDATE api_keys SET key_hash=?, key_prefix=? WHERE id=? AND user_id=?",
              (new_hash, new_prefix, key_id, user_id))
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "id": key_id,
        "key": raw,
        "key_preview": f"{new_prefix}...{'*' * 20}",
    })

@app.route('/login/google')
def login_google():
    if not oauth.google.client_id:
        return jsonify({"error": "Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."}), 503
    redirect_uri = url_for('login_google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri)

@app.route('/login/google/authorized')
def login_google_callback():
    if not oauth.google.client_id:
        return redirect('/login')
    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
        if not user_info:
            return redirect('/login')
    except Exception:
        return redirect('/login')

    email = user_info.get('email', '').strip().lower()
    if not email:
        return redirect('/login')

    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()

    c.execute("SELECT id FROM users WHERE email=?", (email,))
    existing = c.fetchone()

    if existing:
        session['user_id'] = existing[0]
        conn.close()
        return redirect('/dashboard')

    user_id = str(uuid.uuid4())
    is_admin = 1 if email == ADMIN_EMAIL else 0
    role = 'admin' if email == ADMIN_EMAIL else 'free'
    daily_limit = 0 if email == ADMIN_EMAIL else 250
    c.execute("INSERT INTO users (id, email, auth_provider, is_admin, role, daily_limit, is_active) VALUES (?, ?, 'google', ?, ?, ?, 1)",
              (user_id, email, is_admin, role, daily_limit))
    conn.commit()
    conn.close()

    session['user_id'] = user_id
    return redirect('/dashboard')

@app.route('/api/me', methods=['GET'])
def me():
    user_id = get_user_id()
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401

    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, email, role, daily_limit, is_admin, is_active, created_at FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    conn.close()

    if not user:
        session.pop('user_id', None)
        return jsonify({"error": "User not found"}), 401

    return jsonify({
        "user_id": user[0],
        "email": user[1],
        "role": user[2],
        "daily_limit": user[3],
        "is_admin": bool(user[4]),
        "is_active": bool(user[5]),
        "created_at": user[6]
    })

@app.route('/')
def index():
    return send_from_directory('../frontend', 'index.html')

@app.route('/login')
def login_page():
    return send_from_directory('../frontend', 'login.html')

@app.route('/signup')
def signup_page():
    return send_from_directory('../frontend', 'signup.html')

@app.route('/dashboard')
def dashboard_page():
    if 'user_id' not in session:
        return send_from_directory('../frontend', 'login.html')
    return send_from_directory('../frontend', 'dashboard.html')

@app.route('/images')
def images_page():
    if 'user_id' not in session:
        return send_from_directory('../frontend', 'login.html')
    return send_from_directory('../frontend', 'images.html')

@app.route('/create')
def create_page():
    if 'user_id' not in session:
        return send_from_directory('../frontend', 'login.html')
    return send_from_directory('../frontend', 'create.html')

@app.route('/pricing')
def pricing_page():
    return send_from_directory('../frontend', 'pricing.html')

@app.route('/docs')
def docs_page():
    return send_from_directory('../frontend', 'docs.html')

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('../frontend', path)

@app.route('/api/create', methods=['POST'])
@login_required
def create_mock_api():
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()

    if not is_admin_user(user_id):
        c.execute("SELECT COUNT(*) FROM mock_apis WHERE user_id=?", (user_id,))
        count = c.fetchone()[0]
        if count >= 5:
            conn.close()
            return jsonify({"error": "Free limit reached (5 APIs per user)"}), 429
    data = request.json
    endpoint = data.get('endpoint', '').strip()
    method = data.get('method', 'GET').upper()
    response = data.get('response', '{}')
    status_code = data.get('status_code', 200)
    if not endpoint.startswith('/'):
        endpoint = '/' + endpoint
    try:
        json.loads(response)
    except:
        conn.close()
        return jsonify({"error": "Invalid JSON response"}), 400
    c.execute("""INSERT INTO mock_apis (user_id, endpoint, method, response, status_code)
                 VALUES (?, ?, ?, ?, ?)
                 ON CONFLICT(user_id, endpoint, method)
                 DO UPDATE SET response=excluded.response, status_code=excluded.status_code""",
              (user_id, endpoint, method, response, status_code))
    c.execute("SELECT id FROM mock_apis WHERE user_id=? AND endpoint=? AND method=?", (user_id, endpoint, method))
    api_id = c.fetchone()[0]
    conn.commit()
    conn.close()
    api_url = f"{request.host_url}mock/{user_id}{endpoint}"
    return jsonify({"success": True, "api_id": api_id, "url": api_url, "method": method, "status_code": status_code})

@app.route('/api/list')
@login_required
def list_apis():
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, endpoint, method, status_code, created_at FROM mock_apis WHERE user_id=? ORDER BY endpoint, method", (user_id,))
    apis = c.fetchall()
    conn.close()

    grouped = {}
    for a in apis:
        ep = a[1]
        if ep not in grouped:
            grouped[ep] = []
        grouped[ep].append({"id": a[0], "method": a[2], "status_code": a[3], "created_at": a[4]})

    result = []
    for endpoint, methods in grouped.items():
        result.append({"endpoint": endpoint, "methods": methods, "total_methods": len(methods)})

    return jsonify({"apis": result, "total": len(result), "limit": 5})

@app.route('/api/delete/<int:api_id>', methods=['DELETE'])
@login_required
def delete_api(api_id):
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("DELETE FROM mock_apis WHERE id=? AND user_id=?", (api_id, user_id))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted:
        return jsonify({"success": True})
    return jsonify({"error": "API not found"}), 404

def _gen_rest_id():
    return f"rm_{uuid.uuid4().hex}"

def _rest_count_for_user(user_id):
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM rest_mocks WHERE user_id=?", (user_id,))
    n = c.fetchone()[0]
    conn.close()
    return n

@app.route('/api/rest/create', methods=['POST'])
@login_required
def create_rest_mock():
    user_id = get_user_id()
    if not is_admin_user(user_id):
        if _rest_count_for_user(user_id) >= 5:
            return jsonify({"error": "Free limit reached (5 REST APIs per user)"}), 429
    data = request.json or {}
    name = (data.get('name') or '').strip()
    endpoint = (data.get('endpoint') or '').strip()
    status_code = data.get('status_code', 200)
    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not endpoint:
        return jsonify({"error": "Endpoint is required"}), 400
    if not endpoint.startswith('/'):
        endpoint = '/' + endpoint
    mock_id = _gen_rest_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO rest_mocks (id, user_id, name, endpoint, status_code) VALUES (?, ?, ?, ?, ?)",
                  (mock_id, user_id, name, endpoint, status_code))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": f"REST mock for {endpoint} already exists"}), 409
    conn.close()
    return jsonify({"success": True, "rest_id": mock_id, "name": name, "endpoint": endpoint, "status_code": status_code})

@app.route('/api/rest/list')
@login_required
def list_rest_mocks():
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, name, endpoint, status_code FROM rest_mocks WHERE user_id=? ORDER BY created_at", (user_id,))
    mocks = c.fetchall()
    result = []
    for m in mocks:
        c.execute("SELECT COUNT(*) FROM rest_records WHERE mock_id=?", (m[0],))
        rec_count = c.fetchone()[0]
        result.append({"rest_id": m[0], "name": m[1], "endpoint": m[2], "status_code": m[3], "records": rec_count})
    conn.close()
    return jsonify({"rest_apis": result, "total": len(result), "limit": 5})

@app.route('/api/rest/delete/<rest_id>', methods=['DELETE'])
@login_required
def delete_rest_mock(rest_id):
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("DELETE FROM rest_records WHERE mock_id=? AND mock_id IN (SELECT id FROM rest_mocks WHERE id=? AND user_id=?)",
              (rest_id, rest_id, user_id))
    c.execute("DELETE FROM rest_mocks WHERE id=? AND user_id=?", (rest_id, user_id))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted:
        return jsonify({"success": True})
    return jsonify({"error": "REST API not found"}), 404

@app.route('/api/rest/records/<rest_id>', methods=['GET'])
@login_required
def list_rest_records(rest_id):
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id FROM rest_mocks WHERE id=? AND user_id=?", (rest_id, user_id))
    if not c.fetchone():
        conn.close()
        return jsonify({"error": "REST API not found"}), 404
    c.execute("SELECT id, data FROM rest_records WHERE mock_id=? ORDER BY id", (rest_id,))
    records = [{"id": r[0], "data": json.loads(r[1])} for r in c.fetchall()]
    conn.close()
    return jsonify({"records": records, "total": len(records)})

@app.route('/api/usage', methods=['GET'])
@login_required
def get_usage_summary():
    user_id = get_user_id()
    daily_limit = get_user_daily_limit(user_id)
    role = get_user_role(user_id)

    if daily_limit == 0:  # unlimited
        return jsonify({"today": 0, "limit": "Unlimited", "remaining": "Unlimited", "is_admin": role == 'admin', "unlimited": True, "role": role})

    today = get_today()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id FROM api_keys WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    key_ids = [row[0] for row in rows]
    total = 0
    for kid in key_ids:
        c.execute("SELECT request_count FROM request_usage WHERE api_key_id=? AND date=?",
                  (kid, today))
        row = c.fetchone()
        if row:
            total += row[0]
    conn.close()
    remaining = max(0, daily_limit - total)
    return jsonify({"today": total, "limit": daily_limit, "remaining": remaining, "is_admin": role == 'admin', "unlimited": False, "role": role})

@app.route('/mock/<user_id>/<path:endpoint>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def mock_response(user_id, endpoint):
    api_key = request.headers.get('x-api-key') or request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    if not api_key:
        return jsonify({"error": "API key required. Pass x-api-key header or Authorization: Bearer <key>"}), 401

    key_id, key_owner = validate_api_key(api_key)
    if not key_id:
        return jsonify({"error": "Invalid API key"}), 401

    if not is_user_active(key_owner):
        return jsonify({"error": "Account disabled"}), 403

    if not is_admin_user(key_owner):
        # Get the daily limit for the key owner
        owner_limit = get_user_daily_limit(key_owner)
        if owner_limit > 0:  # not unlimited
            count = get_usage(key_id)
            if count >= owner_limit:
                return jsonify({"error": "Daily request limit reached. Upgrade to Pro."}), 429
        increment_usage(key_id)

    method = request.method
    full_path = '/' + endpoint
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()

    # --- REST Mock dispatch (stateful) ---
    c.execute("SELECT id, endpoint, status_code FROM rest_mocks WHERE user_id=? ORDER BY endpoint", (user_id,))
    rest_hits = []
    for rid, r_ep, r_sc in c.fetchall():
        if full_path == r_ep:
            rest_hits.append((rid, r_ep, r_sc, None))
        elif full_path.startswith(r_ep + '/'):
            rest_hits.append((rid, r_ep, r_sc, full_path[len(r_ep) + 1:]))
    if rest_hits:
        rest_hits.sort(key=lambda x: len(x[1]), reverse=True)
        rest_id, rest_ep, rest_sc, rest_key = rest_hits[0]
        conn.close()
        return _rest_execute(user_id, key_owner, rest_id, rest_ep, rest_sc, rest_key, method)

    c.execute("SELECT response, status_code FROM mock_apis WHERE user_id=? AND endpoint=? AND method=?",
              (user_id, full_path, method))
    result = c.fetchone()
    if result is None:
        c.execute("SELECT DISTINCT method FROM mock_apis WHERE user_id=? AND endpoint=? ORDER BY method", (user_id, full_path))
        existing = c.fetchall()
        conn.close()
        if existing:
            methods = [m[0] for m in existing]
            return jsonify({"error": f"{method} mock for {full_path} has not been created. Available methods: {', '.join(methods)}"}), 404
        return jsonify({"error": f"Mock for {full_path} has not been created"}), 404
    conn.close()
    return jsonify(json.loads(result[0])), result[1]


def _rest_execute(user_id, key_owner, rest_id, rest_ep, rest_sc, rest_key, method):
    """Execute a stateful REST mock operation.

    rest_key is None for the base resource path; otherwise it is the record id.
    GET  <ep>          -> list all records
    GET  <ep>/<id>     -> return one record
    POST <ep>          -> create a record from the JSON body
    PUT  <ep>/<id>     -> replace a record from the JSON body
    DELETE <ep>/<id>   -> delete one record
    DELETE <ep>        -> delete all records for the resource
    """
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()

    if method in ('PUT', 'DELETE') and rest_key is None:
        conn.close()
        return jsonify({"error": f"{method} on {rest_ep} requires a record id, e.g. {rest_ep}/{{id}}"}), 400

    # Resolve payload for POST/PUT
    payload = None
    if method in ('POST', 'PUT'):
        try:
            payload = request.get_json(silent=True)
        except Exception:
            payload = None
        if payload is None:
            conn.close()
            return jsonify({"error": "Request body must be valid JSON"}), 400
        if not isinstance(payload, (dict, list)):
            conn.close()
            return jsonify({"error": "Request body must be a JSON object or array"}), 400

    if method == 'GET':
        if rest_key is None:
            c.execute("SELECT id, data FROM rest_records WHERE mock_id=? ORDER BY id", (rest_id,))
            records = [json.loads(r[1]) for r in c.fetchall()]
            conn.close()
            return jsonify(records), 200
        c.execute("SELECT data FROM rest_records WHERE mock_id=? AND id=?", (rest_id, int(rest_key)))
        row = c.fetchone()
        conn.close()
        if not row:
            return jsonify({"error": f"Record {rest_key} not found"}), 404
        return jsonify(json.loads(row[0])), 200

    if method == 'POST':
        c.execute("INSERT INTO rest_records (mock_id, data) VALUES (?, ?)", (rest_id, json.dumps(payload)))
        new_id = c.lastrowid
        conn.commit()
        conn.close()
        return jsonify({"id": new_id, "data": payload}), 201

    if method == 'PUT':
        c.execute("UPDATE rest_records SET data=?, updated_at=CURRENT_TIMESTAMP WHERE mock_id=? AND id=?",
                  (json.dumps(payload), rest_id, int(rest_key)))
        updated = c.rowcount
        conn.commit()
        conn.close()
        if not updated:
            return jsonify({"error": f"Record {rest_key} not found"}), 404
        return jsonify({"id": int(rest_key), "data": payload}), 200

    if method == 'DELETE':
        c.execute("DELETE FROM rest_records WHERE mock_id=? AND id=?", (rest_id, int(rest_key)))
        deleted = c.rowcount
        conn.commit()
        conn.close()
        if not deleted:
            return jsonify({"error": f"Record {rest_key} not found"}), 404
        return jsonify({"success": True, "deleted_id": int(rest_key)}), 200

    conn.close()
    return jsonify({"error": f"Method {method} not supported for this REST mock"}), 405


# ==================== IMAGE API ====================

def image_requester():
    """Return (user_id, api_key_id) authenticated via session or API key.
    Session auth returns (user_id, None); API key auth returns (owner, key_id).
    Returns (None, None) when unauthenticated."""
    user_id = get_user_id()
    if user_id:
        return user_id, None
    api_key = request.headers.get('x-api-key') or request.headers.get('Authorization', '').replace('Bearer ', '').strip()
    if not api_key:
        return None, None
    key_id, key_owner = validate_api_key(api_key)
    if not key_id or not is_user_active(key_owner):
        return None, None
    return key_owner, key_id


def image_usage_check(user_id, key_id):
    """Enforce daily limit and record usage for API-key-authenticated requests.
    Session-authenticated (dashboard) requests are not metered.
    Returns (ok, error_response)."""
    if not key_id or is_admin_user(user_id):
        return True, None
    owner_limit = get_user_daily_limit(user_id)
    if owner_limit > 0 and get_usage(key_id) >= owner_limit:
        return False, (jsonify({"error": "Daily request limit reached. Upgrade to Pro."}), 429)
    increment_usage(key_id)
    return True, None


def image_mime_type(ext):
    return {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'webp': 'image/webp',
        'gif': 'image/gif',
    }.get(ext, 'application/octet-stream')


def validate_image_file(file, filename):
    """Return normalized extension if the file is a valid image, else None."""
    ext = Path(filename).suffix.lower().lstrip('.')
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        return None
    ext = 'jpg' if ext == 'jpeg' else ext
    header = file.read(16)
    file.seek(0)
    if ext == 'jpg':
        ok = header[:3] == b'\xff\xd8\xff'
    elif ext == 'png':
        ok = header[:8] == b'\x89PNG\r\n\x1a\n'
    elif ext == 'gif':
        ok = header[:6] in (b'GIF87a', b'GIF89a')
    elif ext == 'webp':
        ok = header[:4] == b'RIFF' and header[8:12] == b'WEBP'
    else:
        ok = False
    return ext if ok else None


def delete_image_file(conn, image_id, user_id, admin_allowed=True):
    """Delete an image owned by user_id (or any if admin_allowed and admin).
    Returns (success, status, payload)."""
    c = conn.cursor()
    c.execute("SELECT id, user_id, filename FROM images WHERE id=?", (image_id,))
    row = c.fetchone()
    if not row:
        return False, 404, {"error": "Image not found"}
    owner = row[1]
    if owner != user_id and not (admin_allowed and is_admin_user(user_id)):
        return False, 404, {"error": "Image not found"}
    try:
        (UPLOAD_DIR / row[2]).unlink()
    except OSError:
        pass
    c.execute("DELETE FROM images WHERE id=?", (image_id,))
    conn.commit()
    return True, 200, {"success": True}


@app.route('/api/images', methods=['POST'])
def upload_image():
    user_id, key_id = image_requester()
    if not user_id:
        return jsonify({"error": "Authentication required. Log in or pass an API key via x-api-key."}), 401

    ok, err = image_usage_check(user_id, key_id)
    if not ok:
        return err

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({"error": "No file provided"}), 400

    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    if size > MAX_IMAGE_SIZE:
        return jsonify({"error": "File too large. Maximum size is 10MB"}), 413

    ext = validate_image_file(file, file.filename)
    if not ext:
        return jsonify({"error": "Invalid image. Allowed types: jpg, jpeg, png, webp, gif"}), 400

    image_id = str(uuid.uuid4())
    stored_name = f"{image_id}.{ext}"
    file.save(UPLOAD_DIR / stored_name)

    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("INSERT INTO images (id, user_id, filename, original_name, mime_type, size) VALUES (?, ?, ?, ?, ?, ?)",
              (image_id, user_id, stored_name, file.filename, image_mime_type(ext), size))
    conn.commit()
    conn.close()

    return jsonify({
        "success": True,
        "id": image_id,
        "filename": stored_name,
        "original_name": file.filename,
        "mime_type": image_mime_type(ext),
        "size": size,
        "url": f"/api/images/{image_id}",
    }), 201


@app.route('/api/images', methods=['GET'])
def list_images():
    user_id, key_id = image_requester()
    if not user_id:
        return jsonify({"error": "Authentication required. Log in or pass an API key via x-api-key."}), 401

    ok, err = image_usage_check(user_id, key_id)
    if not ok:
        return err

    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, filename, original_name, mime_type, size, created_at FROM images WHERE user_id=? ORDER BY created_at DESC",
              (user_id,))
    rows = c.fetchall()
    conn.close()
    images = [{
        "id": r[0],
        "filename": r[1],
        "original_name": r[2],
        "mime_type": r[3],
        "size": r[4],
        "created_at": r[5],
        "url": f"/api/images/{r[0]}",
    } for r in rows]
    return jsonify({"images": images, "total": len(images)})


@app.route('/api/images/<image_id>', methods=['GET'])
def get_image(image_id):
    user_id, key_id = image_requester()
    if not user_id:
        return jsonify({"error": "Authentication required. Log in or pass an API key via x-api-key."}), 401

    ok, err = image_usage_check(user_id, key_id)
    if not ok:
        return err

    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id, user_id, filename, original_name, mime_type, size, created_at FROM images WHERE id=?", (image_id,))
    row = c.fetchone()
    conn.close()
    if not row or (row[1] != user_id and not is_admin_user(user_id)):
        return jsonify({"error": "Image not found"}), 404
    if not (UPLOAD_DIR / row[2]).exists():
        return jsonify({"error": "Image file missing"}), 404
    return send_from_directory(UPLOAD_DIR, row[2])


@app.route('/api/images/<image_id>', methods=['DELETE'])
def delete_image(image_id):
    user_id, key_id = image_requester()
    if not user_id:
        return jsonify({"error": "Authentication required. Log in or pass an API key via x-api-key."}), 401

    ok, err = image_usage_check(user_id, key_id)
    if not ok:
        return err

    conn = sqlite3.connect(DB_NAME, timeout=10)
    success, status, payload = delete_image_file(conn, image_id, user_id, admin_allowed=True)
    conn.close()
    return jsonify(payload), status


# ==================== ADMIN PANEL ====================

def get_usage_for_user(user_id, date_str=None):
    """Get total request usage for a user (all keys)."""
    today = date_str or get_today()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id FROM api_keys WHERE user_id=?", (user_id,))
    key_ids = [row[0] for row in c.fetchall()]
    total = 0
    for kid in key_ids:
        c.execute("SELECT request_count FROM request_usage WHERE api_key_id=? AND date=?", (kid, today))
        row = c.fetchone()
        if row:
            total += row[0]
    conn.close()
    return total


def get_lifetime_usage_for_user(user_id):
    """Get lifetime request usage for a user (all keys, all dates)."""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id FROM api_keys WHERE user_id=?", (user_id,))
    key_ids = [row[0] for row in c.fetchall()]
    total = 0
    for kid in key_ids:
        c.execute("SELECT COALESCE(SUM(request_count), 0) FROM request_usage WHERE api_key_id=?", (kid,))
        row = c.fetchone()
        if row and row[0]:
            total += row[0]
    conn.close()
    return total


@app.route('/admin')
def admin_page():
    user_id = get_user_id()
    if not user_id or not is_admin_user(user_id):
        return send_from_directory('../frontend', 'dashboard.html') if user_id else send_from_directory('../frontend', 'login.html')
    return send_from_directory('../frontend', 'admin.html')


@app.route('/admin/dashboard', methods=['GET'])
@admin_required
def admin_dashboard_stats():
    today = get_today()
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM mock_apis")
    total_apis = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM api_keys")
    total_keys = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    admin_users = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM users WHERE is_active=1")
    active_users = c.fetchone()[0]

    c.execute("SELECT COALESCE(SUM(request_count), 0) FROM request_usage WHERE date=?", (today,))
    requests_today = c.fetchone()[0]

    c.execute("SELECT COALESCE(SUM(request_count), 0) FROM request_usage")
    requests_lifetime = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM images")
    total_images = c.fetchone()[0]

    c.execute("SELECT COALESCE(SUM(size), 0) FROM images")
    storage_used = c.fetchone()[0]

    conn.close()

    return jsonify({
        "total_users": total_users,
        "total_mock_apis": total_apis,
        "total_api_keys": total_keys,
        "requests_today": requests_today,
        "requests_lifetime": requests_lifetime,
        "admin_users": admin_users,
        "active_users": active_users,
        "total_images": total_images,
        "storage_used": storage_used,
    })


@app.route('/admin/users', methods=['GET'])
@admin_required
def admin_list_users():
    search = request.args.get('search', '').strip().lower()
    role = request.args.get('role', '').strip().lower()
    status = request.args.get('status', '').strip().lower()
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(50, max(1, int(request.args.get('per_page', 20))))
    today = get_today()

    query = """SELECT u.id, u.email, u.role, u.daily_limit, u.is_active, u.created_at,
               (SELECT COUNT(*) FROM api_keys k WHERE k.user_id = u.id) as key_count,
               (SELECT COUNT(*) FROM mock_apis m WHERE m.user_id = u.id) as api_count,
               (SELECT COALESCE(SUM(r.request_count), 0) FROM request_usage r
                JOIN api_keys k ON r.api_key_id = k.id
                WHERE k.user_id = u.id AND r.date = ?) as requests_today
               FROM users u WHERE 1=1"""
    params = [today]

    if search:
        query += " AND (LOWER(u.email) LIKE ? OR LOWER(u.email) LIKE ?)"
        params.append(f"%{search}%")
        params.append(f"%{search}%")
    if role:
        query += " AND u.role = ?"
        params.append(role)
    if status in ('active', 'disabled'):
        active_val = 1 if status == 'active' else 0
        query += " AND u.is_active = ?"
        params.append(active_val)

    query += " ORDER BY u.created_at DESC LIMIT ? OFFSET ?"
    params.append(per_page)
    params.append((page - 1) * per_page)

    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute(query, params)
    rows = c.fetchall()

    # Count total matching for pagination
    count_query = query.replace("""SELECT u.id, u.email, u.role, u.daily_limit, u.is_active, u.created_at,
               (SELECT COUNT(*) FROM api_keys k WHERE k.user_id = u.id) as key_count,
               (SELECT COUNT(*) FROM mock_apis m WHERE m.user_id = u.id) as api_count,
               (SELECT COALESCE(SUM(r.request_count), 0) FROM request_usage r
                JOIN api_keys k ON r.api_key_id = k.id
                WHERE k.user_id = u.id AND r.date = ?) as requests_today
               FROM users u WHERE 1=1""", "SELECT COUNT(*) FROM users u WHERE 1=1")
    # remove LIMIT/OFFSET for count
    count_query = count_query.split(" ORDER BY")[0]
    c.execute(count_query, params[1:-2])
    total_count = c.fetchone()[0]
    conn.close()

    users = []
    for r in rows:
        users.append({
            "id": r[0],
            "email": r[1],
            "role": r[2],
            "daily_limit": r[3],
            "is_active": bool(r[4]),
            "created_at": r[5],
            "api_keys": r[6],
            "mock_apis": r[7],
            "requests_today": r[8],
        })

    return jsonify({
        "users": users,
        "total": total_count,
        "page": page,
        "per_page": per_page,
    })


@app.route('/admin/users/<user_id>', methods=['GET'])
@admin_required
def admin_get_user(user_id):
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("""SELECT u.id, u.email, u.role, u.daily_limit, u.is_active, u.created_at,
               (SELECT COUNT(*) FROM api_keys k WHERE k.user_id = u.id) as key_count,
               (SELECT COUNT(*) FROM mock_apis m WHERE m.user_id = u.id) as api_count
               FROM users u WHERE u.id = ?""", (user_id,))
    user = c.fetchone()
    if not user:
        conn.close()
        return jsonify({"error": "User not found"}), 404
    conn.close()

    today_requests = get_usage_for_user(user_id)
    lifetime_requests = get_lifetime_usage_for_user(user_id)
    daily_limit = get_user_daily_limit(user_id)
    remaining = "Unlimited" if daily_limit == 0 else max(0, daily_limit - today_requests)

    return jsonify({
        "id": user[0],
        "email": user[1],
        "role": user[2],
        "daily_limit": daily_limit,
        "is_active": bool(user[4]),
        "created_at": user[5],
        "api_keys": user[6],
        "mock_apis": user[7],
        "usage": {
            "today": today_requests,
            "lifetime": lifetime_requests,
            "remaining": remaining,
        },
    })


@app.route('/admin/users/<user_id>', methods=['PATCH'])
@admin_required
def admin_update_user(user_id):
    data = request.json or {}
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()

    c.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if not c.fetchone():
        conn.close()
        return jsonify({"error": "User not found"}), 404

    # Prevent admin from demoting themselves
    if user_id == get_user_id():
        conn.close()
        return jsonify({"error": "Cannot modify your own account from admin panel"}), 400

    allowed_roles = ('free', 'pro', 'enterprise', 'admin')
    updates = []
    params = []

    if 'role' in data:
        role = data['role'].strip().lower()
        if role not in allowed_roles:
            conn.close()
            return jsonify({"error": "Invalid role"}), 400
        updates.append("role=?")
        params.append(role)
        updates.append("is_admin=?")
        params.append(1 if role == 'admin' else 0)
        if role in ('admin', 'pro', 'enterprise'):
            updates.append("daily_limit=?")
            params.append(0)  # unlimited
        elif role == 'free' and 'daily_limit' not in data:
            updates.append("daily_limit=?")
            params.append(250)  # free default

    if 'daily_limit' in data:
        dl = data['daily_limit']
        if dl in ('Unlimited', 'unlimited', ''):
            dl = 0
        else:
            try:
                dl = int(dl)
                if dl < 0:
                    raise ValueError()
            except (ValueError, TypeError):
                conn.close()
                return jsonify({"error": "Invalid daily limit"}), 400
        updates.append("daily_limit=?")
        params.append(dl)

    if 'is_active' in data:
        is_active = 1 if bool(data['is_active']) else 0
        updates.append("is_active=?")
        params.append(is_active)

    if not updates:
        conn.close()
        return jsonify({"error": "No updates provided"}), 400

    params.append(user_id)
    c.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", params)
    conn.commit()
    conn.close()

    return jsonify({"success": True})


@app.route('/admin/users/<user_id>/disable', methods=['POST'])
@admin_required
def admin_disable_user(user_id):
    if user_id == get_user_id():
        return jsonify({"error": "Cannot disable your own account"}), 400
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/admin/users/<user_id>/enable', methods=['POST'])
@admin_required
def admin_enable_user(user_id):
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("UPDATE users SET is_active=1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/admin/users/<user_id>', methods=['DELETE'])
@admin_required
def admin_delete_user(user_id):
    if user_id == get_user_id():
        return jsonify({"error": "Cannot delete your own account"}), 400
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    try:
        conn.execute("BEGIN TRANSACTION")
        # Delete request usage (via api keys of the user)
        c.execute("DELETE FROM request_usage WHERE api_key_id IN (SELECT id FROM api_keys WHERE user_id=?)", (user_id,))
        # Delete API keys
        c.execute("DELETE FROM api_keys WHERE user_id=?", (user_id,))
        # Delete mock APIs
        c.execute("DELETE FROM mock_apis WHERE user_id=?", (user_id,))
        # Delete images (and their files)
        c.execute("SELECT filename FROM images WHERE user_id=?", (user_id,))
        for (filename,) in c.fetchall():
            try:
                (UPLOAD_DIR / filename).unlink()
            except OSError:
                pass
        c.execute("DELETE FROM images WHERE user_id=?", (user_id,))
        # Delete user
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 500
    conn.close()
    return jsonify({"success": True})


@app.route('/admin/keys', methods=['GET'])
@admin_required
def admin_list_all_keys():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("""SELECT k.id, k.key_prefix, k.name, k.created_at, k.last_used_at, u.email
               FROM api_keys k JOIN users u ON k.user_id = u.id
               ORDER BY k.created_at DESC""")
    rows = c.fetchall()
    conn.close()
    keys = []
    for r in rows:
        keys.append({
            "id": r[0],
            "key_preview": f"{r[1]}...{'*' * 20}",
            "name": r[2],
            "created_at": r[3],
            "last_used_at": r[4],
            "user_email": r[5],
        })
    return jsonify({"keys": keys, "total": len(keys)})


@app.route('/admin/keys/<key_id>', methods=['DELETE'])
@admin_required
def admin_delete_key(key_id):
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted:
        return jsonify({"success": True})
    return jsonify({"error": "Key not found"}), 404


@app.route('/admin/keys/<key_id>/regenerate', methods=['POST'])
@admin_required
def admin_regenerate_key(key_id):
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT id FROM api_keys WHERE id=?", (key_id,))
    if not c.fetchone():
        conn.close()
        return jsonify({"error": "Key not found"}), 404
    raw, new_hash, new_prefix = generate_api_key()
    c.execute("UPDATE api_keys SET key_hash=?, key_prefix=? WHERE id=?", (new_hash, new_prefix, key_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "id": key_id, "key": raw, "key_preview": f"{new_prefix}...{'*' * 20}"})


@app.route('/admin/apis', methods=['GET'])
@admin_required
def admin_list_all_apis():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("""SELECT m.id, m.endpoint, m.method, m.status_code, m.created_at, u.email
               FROM mock_apis m JOIN users u ON m.user_id = u.id
               ORDER BY m.created_at DESC""")
    rows = c.fetchall()
    conn.close()
    apis = []
    for r in rows:
        apis.append({
            "id": r[0],
            "endpoint": r[1],
            "method": r[2],
            "status_code": r[3],
            "created_at": r[4],
            "user_email": r[5],
        })
    return jsonify({"apis": apis, "total": len(apis)})


@app.route('/admin/apis/<int:api_id>', methods=['DELETE'])
@admin_required
def admin_delete_api(api_id):
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("DELETE FROM mock_apis WHERE id=?", (api_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted:
        return jsonify({"success": True})
    return jsonify({"error": "API not found"}), 404


@app.route('/admin/apis/<int:api_id>/disable', methods=['POST'])
@admin_required
def admin_disable_api(api_id):
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    try:
        c.execute("ALTER TABLE mock_apis ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    c.execute("UPDATE mock_apis SET is_active=0 WHERE id=?", (api_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/admin/apis/<int:api_id>/enable', methods=['POST'])
@admin_required
def admin_enable_api(api_id):
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("UPDATE mock_apis SET is_active=1 WHERE id=?", (api_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route('/admin/images', methods=['GET'])
@admin_required
def admin_list_all_images():
    search = request.args.get('search', '').strip().lower()
    query = """SELECT i.id, i.filename, i.original_name, i.mime_type, i.size, i.created_at, u.email
               FROM images i JOIN users u ON i.user_id = u.id"""
    params = []
    if search:
        query += " WHERE LOWER(u.email) LIKE ? OR LOWER(i.original_name) LIKE ?"
        params = [f"%{search}%", f"%{search}%"]
    query += " ORDER BY i.created_at DESC"
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    images = [{
        "id": r[0],
        "filename": r[1],
        "original_name": r[2],
        "mime_type": r[3],
        "size": r[4],
        "created_at": r[5],
        "user_email": r[6],
        "url": f"/api/images/{r[0]}",
    } for r in rows]
    return jsonify({"images": images, "total": len(images)})


@app.route('/admin/images/<image_id>', methods=['DELETE'])
@admin_required
def admin_delete_image(image_id):
    conn = sqlite3.connect(DB_NAME, timeout=10)
    success, status, payload = delete_image_file(conn, image_id, get_user_id(), admin_allowed=True)
    conn.close()
    return jsonify(payload), status


@app.errorhandler(500)
def handle_internal_error(exc):
    logging.getLogger(__name__).exception("Unhandled server error")
    return jsonify({"error": "Internal server error"}), 500


init_db()

from images import images_bp, models as image_models
image_models.init_image_table()
app.register_blueprint(images_bp)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
