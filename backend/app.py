from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth
import json
import sqlite3
import uuid
import os

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

DB_NAME = 'mockapi.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
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
    conn.commit()
    conn.close()

def get_user_id():
    return session.get('user_id')

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({"error": "Unauthorized"}), 401
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE id=?", (session['user_id'],))
        user = c.fetchone()
        conn.close()
        if not user:
            session.pop('user_id', None)
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

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

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (id, email, password_hash) VALUES (?, ?, ?)",
                  (user_id, email, password_hash))
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

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, password_hash FROM users WHERE email=?", (email,))
    user = c.fetchone()
    conn.close()

    if not user or not check_password_hash(user[1], password):
        return jsonify({"error": "Invalid email or password"}), 401

    session['user_id'] = user[0]
    return jsonify({"success": True, "user_id": user[0], "email": email})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True})

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

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("SELECT id FROM users WHERE email=?", (email,))
    existing = c.fetchone()

    if existing:
        session['user_id'] = existing[0]
        conn.close()
        return redirect('/dashboard')

    user_id = str(uuid.uuid4())
    c.execute("INSERT INTO users (id, email, auth_provider) VALUES (?, ?, 'google')",
              (user_id, email))
    conn.commit()
    conn.close()

    session['user_id'] = user_id
    return redirect('/dashboard')

@app.route('/api/me', methods=['GET'])
def me():
    user_id = get_user_id()
    if not user_id:
        return jsonify({"error": "Not logged in"}), 401

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT id, email, created_at FROM users WHERE id=?", (user_id,))
    user = c.fetchone()
    conn.close()

    if not user:
        session.pop('user_id', None)
        return jsonify({"error": "User not found"}), 401

    return jsonify({"user_id": user[0], "email": user[1], "created_at": user[2]})

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

@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('../frontend', path)

@app.route('/api/create', methods=['POST'])
@login_required
def create_mock_api():
    user_id = get_user_id()
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
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
    conn = sqlite3.connect(DB_NAME)
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
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM mock_apis WHERE id=? AND user_id=?", (api_id, user_id))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    if deleted:
        return jsonify({"success": True})
    return jsonify({"error": "API not found"}), 404

@app.route('/mock/<user_id>/<path:endpoint>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def mock_response(user_id, endpoint):
    method = request.method
    endpoint = '/' + endpoint
    conn = sqlite3.connect(DB_NAME)
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
        conn.close()
        return jsonify({"error": f"Mock for {endpoint} has not been created"}), 404
    conn.close()
    return jsonify(json.loads(result[0])), result[1]

if __name__ == '__main__':
    init_db()
    app.run(debug=True, port=5000)
