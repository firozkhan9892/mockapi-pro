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
from pathlib import Path

app = Flask(__name__, static_folder='../frontend', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', 'mockapi-secret-key-change-in-production')
CORS(app, supports_credentials=True)

oauth = OAuth(app)
oauth.register(
    name='google',
    client_id=os.environ.get('GOOGLE_CLIENT_ID', ''),
    client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', ''),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)

BASE_DIR = Path(__file__).resolve().parent
DB_NAME = BASE_DIR / "mockapi.db"

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()

def init_db():
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
    """Get the daily limit for a user from the database."""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT daily_limit, role FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return 250  # default
    daily_limit = row[0]
    role = row[1]
    if role == 'admin' or daily_limit is None or daily_limit == 0:
        return 0  # 0 means unlimited
    return daily_limit

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
    c.execute("INSERT INTO mock_apis (user_id, endpoint, method, response, status_code) VALUES (?, ?, ?, ?, ?)",
              (user_id, endpoint, method, response, status_code))
    api_id = c.lastrowid
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

@app.route('/api/usage', methods=['GET'])
@login_required
def get_usage_summary():
    user_id = get_user_id()
    daily_limit = get_user_daily_limit(user_id)

    if daily_limit == 0:  # unlimited
        return jsonify({"today": 0, "limit": "Unlimited", "remaining": "Unlimited", "is_admin": True})

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
    return jsonify({"today": total, "limit": daily_limit, "remaining": remaining, "is_admin": False})

@app.route('/mock/<user_id>/<path:endpoint>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def mock_response(user_id, endpoint):
    api_key = request.headers.get('x-api-key') or request.headers.get('Authorization', '').removeprefix('Bearer ').strip()
    if not api_key:
        return jsonify({"error": "API key required. Pass x-api-key header or Authorization: Bearer <key>"}), 401

    key_id, key_owner = validate_api_key(api_key)
    if not key_id:
        return jsonify({"error": "Invalid API key"}), 401

    if not is_admin_user(key_owner):
        # Get the daily limit for the key owner
        owner_limit = get_user_daily_limit(key_owner)
        if owner_limit > 0:  # not unlimited
            count = get_usage(key_id)
            if count >= owner_limit:
                return jsonify({"error": "Daily request limit reached. Upgrade to Pro."}), 429
        increment_usage(key_id)

    method = request.method
    endpoint = '/' + endpoint
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    c.execute("SELECT response, status_code FROM mock_apis WHERE user_id=? AND endpoint=? AND method=?",
              (user_id, endpoint, method))
    result = c.fetchone()
    if result is None:
        c.execute("SELECT method FROM mock_apis WHERE user_id=? AND endpoint=?", (user_id, endpoint))
        existing = c.fetchall()
        conn.close()
        if existing:
            methods = [m[0] for m in existing]
            return jsonify({"error": f"{method} mock for {endpoint} has not been created. Available methods: {', '.join(methods)}"}), 404
        return jsonify({"error": f"Mock for {endpoint} has not been created"}), 404
    conn.close()
    return jsonify(json.loads(result[0])), result[1]



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

    conn.close()

    return jsonify({
        "total_users": total_users,
        "total_mock_apis": total_apis,
        "total_api_keys": total_keys,
        "requests_today": requests_today,
        "requests_lifetime": requests_lifetime,
        "admin_users": admin_users,
        "active_users": active_users,
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
    daily_limit = user[3]
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
        if role == 'admin':
            updates.append("daily_limit=?")
            params.append(0)

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

init_db()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
