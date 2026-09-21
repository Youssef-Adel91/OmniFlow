"""
Direct seed via psycopg2 synchronous driver — bypasses asyncpg SSL issues.
Generates a fresh bcrypt hash and updates via docker-accessible connection.
"""
import subprocess
import sys
import os

# Get hash from passlib
sys.path.insert(0, ".")
from src.shared.security.password import hash_password, verify_password

DEV_PASSWORD = "OmniFlow@2025!"

print(f"[*] Hashing '{DEV_PASSWORD}' with bcrypt rounds=12...")
new_hash = hash_password(DEV_PASSWORD)
print(f"[*] Generated hash: {new_hash}")
print(f"[*] Hash length: {len(new_hash)} chars (expected 60)")

# Verify hash is correct before storing it
assert verify_password(DEV_PASSWORD, new_hash), "Hash verification failed!"
print("[OK] Self-verification passed.")

# Write the SQL to a temp file to avoid shell escaping issues
sql = f"UPDATE tenant_users SET hashed_password = $tag${new_hash}$tag$;"
sql_file = "scripts/_temp_seed.sql"
with open(sql_file, "w", encoding="utf-8") as f:
    f.write(sql + "\n")
    f.write("SELECT email, length(hashed_password) as hash_len FROM tenant_users;\n")

print(f"[*] SQL written to {sql_file}")
print("[*] Running via docker exec...")

result = subprocess.run(
    ["docker", "exec", "-i", "omniflow-postgres", "psql", "-U", "omniflow", "-d", "omniflow_db"],
    input=open(sql_file, "rb").read(),
    capture_output=True,
)
print(result.stdout.decode())
if result.returncode != 0:
    print("[ERR]", result.stderr.decode())
else:
    print(f"[✅] Password '{DEV_PASSWORD}' seeded successfully via Docker!")
    print("[i]  You can now log in at http://localhost:3001/ar/login")

os.remove(sql_file)
