with open(r'E:\New folder\AI agency tool kit\New folder\api key\mockapi\backend\app.py', 'r') as f:
    lines = f.readlines()

# Verify structure
assert lines[31] == 'def \n', f"Line 32 unexpected: {lines[31]!r}"
assert lines[461] == 'init_db():\n', f"Line 462 unexpected: {lines[461]!r}"
assert 'ADMIN PANEL' in lines[1051], f"Line 1052 unexpected: {lines[1051]!r}"

# Reconstruct:
#   A: lines[0:31]          -> imports + config (before broken 'def ')
#   + 'def init_db():\n'
#   + lines[462:1051]       -> original init_db body (463-1051)
#   + lines[1051:]          -> second admin block + init_db() call + app run
result = lines[0:31] + ['def init_db():\n'] + lines[462:1051] + lines[1051:]

with open(r'E:\New folder\AI agency tool kit\New folder\api key\mockapi\backend\app.py', 'w') as f:
    f.writelines(result)

print('Reconstructed. New total lines:', len(result))
