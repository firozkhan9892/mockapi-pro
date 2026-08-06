import re

with open(r'E:\New folder\AI agency tool kit\New folder\api key\mockapi\backend\app.py', 'r') as f:
    content = f.read()

admin_endpoints = '''

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
    c.execute(count_query, params[:-2])
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

'''

# Insert before init_db()
if 'init_db()' in content:
    content = content.replace('init_db()', admin_endpoints + 'init_db()')
    with open(r'E:\New folder\AI agency tool kit\New folder\api key\mockapi\backend\app.py', 'w') as f:
        f.write(content)
    print('Admin endpoints added successfully')
else:
    print('init_db() not found')