import re

with open(r'E:\New folder\AI agency tool kit\New folder\api key\mockapi\backend\app.py', 'r') as f:
    content = f.read()

# Find and replace the get_usage_summary function
old_func = '''@app.route('/api/usage', methods=['GET'])
@login_required
def get_usage_summary():
    print(f"[USAGE_DEBUG] session user_id: {session.get('user_id')}")
    print(f"[USAGE_DEBUG] get_user_id(): {get_user_id()}")
    user_id = get_user_id()
    print(f"[USAGE_DEBUG] user_id: {user_id}")

    if is_admin_user(user_id):
        print(f"[USAGE_DEBUG] is_admin_user(user_id) returned: {is_admin_user(user_id)}")
        result = {"today": 0, "limit": "Unlimited", "remaining": "Unlimited", "is_admin": True}
        print(f"[USAGE_DEBUG] admin returning result: {result}")
        return jsonify(result)

    today = get_today()
    print(f"[USAGE_DEBUG] today: {today}")
    conn = sqlite3.connect(DB_NAME, timeout=10)
    c = conn.cursor()
    print(f"[USAGE_DEBUG] Executing: SELECT id FROM api_keys WHERE user_id='{user_id}'")
    c.execute("SELECT id FROM api_keys WHERE user_id=?", (user_id,))
    rows = c.fetchall()
    print(f"[USAGE_DEBUG] SQL rows returned: {rows}")
    key_ids = [row[0] for row in rows]
    total = 0
    for kid in key_ids:
        c.execute("SELECT request_count FROM request_usage WHERE api_key_id=? AND date=?",
                  (kid, today))
        row = c.fetchone()
        if row:
            total += row[0]
    conn.close()
    remaining = max(0, DAILY_LIMIT - total)
    result = {"today": total, "limit": DAILY_LIMIT, "remaining": remaining, "is_admin": False}
    print(f"[USAGE_DEBUG] final non-admin result: {result}")
    return jsonify(result)'''

new_func = '''@app.route('/api/usage', methods=['GET'])
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
    return jsonify({"today": total, "limit": daily_limit, "remaining": remaining, "is_admin": False})'''

if old_func in content:
    content = content.replace(old_func, new_func)
    with open(r'E:\New folder\AI agency tool kit\New folder\api key\mockapi\backend\app.py', 'w') as f:
        f.write(content)
    print('Replaced successfully')
else:
    print('Old function not found - trying with actual content from file')
    # Get the actual function from the file
    idx = content.find("@app.route('/api/usage'")
    if idx >= 0:
        # Find the end of the function (next @app.route or end of file)
        end_idx = content.find("@app.route", idx + 1)
        if end_idx == -1:
            end_idx = len(content)
        actual_func = content[idx:end_idx]
        print("Actual function:")
        print(actual_func[:500])
        print("---")
        print(actual_func[-200:])