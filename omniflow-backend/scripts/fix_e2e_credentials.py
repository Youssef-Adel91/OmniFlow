"""
scripts/fix_e2e_credentials.py — E2E Test Credential Fixer

Emergency repair script:
  1. Checks for the E2E test user (agent@eliteprops.sa)
  2. Generates a proper bcrypt hash for "OmniFlow@2025!"
  3. Updates the hash directly via psycopg2 (synchronous, no PgBouncer/SSL issues)
  4. Verifies login works by calling verify_password

Run:
    cd omniflow-backend
    $env:PYTHONPATH = "."
    $env:PYTHONUTF8 = "1"
    .venv\Scripts\python.exe scripts/fix_e2e_credentials.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEV_EMAIL    = "agent@eliteprops.sa"
DEV_PASSWORD = "OmniFlow@2025!"

# ── Import the project's own hashing utilities ────────────────────────────────
from src.shared.security.password import hash_password, verify_password

print("[*] Hashing password with bcrypt...")
hashed = hash_password(DEV_PASSWORD)
print(f"[*] Hash prefix: {hashed[:15]}...")

# ── Verify immediately before writing ────────────────────────────────────────
assert verify_password(DEV_PASSWORD, hashed), "BUG: hash-verify round-trip failed!"
print("[OK] verify_password() round-trip: PASSED")

# ── Write to PostgreSQL using psycopg2 (sync, no SSL, no PgBouncer) ──────────
import os
try:
    import psycopg2
except ImportError:
    print("[!] psycopg2 not available — trying psycopg2-binary install hint")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "psycopg2-binary", "-q"])
    import psycopg2

conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST", "127.0.0.1"),  # force IPv4
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    dbname=os.getenv("POSTGRES_DB", "omniflow_db"),
    user=os.getenv("POSTGRES_USER", "omniflow"),
    password=os.getenv("POSTGRES_PASSWORD", "omniflow_dev_secret_change_me"),
    sslmode="disable",
)
conn.autocommit = True
cur = conn.cursor()

# Check if user exists
cur.execute("SELECT user_id, email, role FROM tenant_users WHERE email = %s", (DEV_EMAIL,))
row = cur.fetchone()

if not row:
    print(f"[!] User {DEV_EMAIL!r} NOT found in tenant_users!")
    print("    → Run: python scripts/seed_mock_data.py --skip-qdrant first")
    cur.close(); conn.close(); sys.exit(1)

user_id, email, role = row
print(f"[*] Found user: {email}  role={role}  id={user_id}")

# Update hash
cur.execute(
    "UPDATE tenant_users SET hashed_password = %s, updated_at = NOW() WHERE email = %s",
    (hashed, DEV_EMAIL),
)
print(f"[OK] hashed_password updated — rows affected: {cur.rowcount}")

# Re-read and verify
cur.execute("SELECT hashed_password FROM tenant_users WHERE email = %s", (DEV_EMAIL,))
stored = cur.fetchone()[0]
assert stored == hashed, "DB round-trip mismatch!"
assert verify_password(DEV_PASSWORD, stored), "verify_password against DB hash FAILED!"
print("[OK] DB round-trip verify: PASSED")

cur.close()
conn.close()

print()
print("═" * 60)
print("  ✅  E2E credentials are ready!")
print(f"  Email   : {DEV_EMAIL}")
print(f"  Password: {DEV_PASSWORD}")
print("═" * 60)
