import re

with open(r'E:\New folder\AI agency tool kit\New folder\api key\mockapi\backend\app.py', 'r') as f:
    content = f.read()

old_func = '''    if not is_admin_user(key_owner):
        count = get_usage(key_id)
        if count >= DAILY_LIMIT:
            return jsonify({"error": "Daily request limit reached. Upgrade to Pro."}), 429
        increment_usage(key_id)'''

new_func = '''    if not is_admin_user(key_owner):
        # Get the daily limit for the key owner
        owner_limit = get_user_daily_limit(key_owner)
        if owner_limit > 0:  # not unlimited
            count = get_usage(key_id)
            if count >= owner_limit:
                return jsonify({"error": "Daily request limit reached. Upgrade to Pro."}), 429
        increment_usage(key_id)'''

if old_func in content:
    content = content.replace(old_func, new_func)
    with open(r'E:\New folder\AI agency tool kit\New folder\api key\mockapi\backend\app.py', 'w') as f:
        f.write(content)
    print('Replaced successfully')
else:
    print('Old function not found')