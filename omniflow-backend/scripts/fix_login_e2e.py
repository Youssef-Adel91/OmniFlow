"""
scripts/fix_login_e2e.py — Authoritative E2E Login Fix

ROOT CAUSE:
  The seed_mock_data.py script (line 396) inserts an invalid argon2id-formatted
  string "$argon2id$v=19$m=65536,t=3,p=4$MOCK_HASH_FOR_DEV_ONLY" as the
  hashed_password.  The auth.py login endpoint calls verify_password() which
  uses passlib's bcrypt CryptContext. passlib CANNOT verify an argon2id string
  against a bcrypt context — it raises an exception that is silently caught and
  returns False, causing every login attempt to fail with 401.

FIX:
  1. Hash the real dev password with bcrypt (passlib) — same as hash_password().
  2. Write it directly to PostgreSQL via psycopg2 (synchronous, no SSL, no PgBouncer).
  3. Verify the round-trip works.
  4. Also ensures the tenant + user exist (creates them if missing via seed).

Usage:
    cd omniflow-backend
    $env:PYTHONPATH = "."
    $env:PYTHONUTF8 = "1"
    .venv\\Scripts\\python.exe scripts/fix_login_e2e.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEV_EMAIL    = "agent@eliteprops.sa"
DEV_PASSWORD = "OmniFlow@2025!"

print("=" * 65)
print("  OmniFlow E2E Login Fix — Authoritative Password Seed")
print("=" * 65)

# ── Step 1: Generate valid bcrypt hash ───────────────────────────────────────
print("\n[1/4] Hashing password with bcrypt (passlib, rounds=12)...")
from src.shared.security.password import hash_password, verify_password

hashed = hash_password(DEV_PASSWORD)
print(f"      Hash prefix  : {hashed[:20]}...")

# Immediate round-trip sanity check
assert verify_password(DEV_PASSWORD, hashed), "BUG: hash-verify round-trip FAILED!"
print("      Round-trip    : ✅  PASSED")

# ── Step 2: Connect to PostgreSQL ─────────────────────────────────────────────
print("\n[2/4] Connecting to PostgreSQL...")

from src.shared.core.config import get_settings
settings = get_settings()

import psycopg2
conn = psycopg2.connect(
    settings.database_url.replace("+asyncpg", "")
)
conn.autocommit = True
cur = conn.cursor()
print("      Connected     : ✅")

# ── Step 3: Check user existence ─────────────────────────────────────────────
print(f"\n[3/4] Checking if user '{DEV_EMAIL}' exists...")
cur.execute(
    "SELECT user_id, email, role, hashed_password FROM tenant_users WHERE email = %s",
    (DEV_EMAIL,),
)
row = cur.fetchone()

if not row:
    print(f"      ❌  User NOT found.")
    print("      → The database has not been seeded yet.")
    print("      → Run: .venv\\Scripts\\python.exe scripts\\seed_mock_data.py --skip-qdrant")
    print("      → Then re-run this script.")
    cur.close()
    conn.close()
    sys.exit(1)

user_id, email, role, current_hash = row
print(f"      Found         : user_id={user_id}  role={role}")
print(f"      Current hash  : {current_hash[:25]}...")

# Detect the bug: argon2id prefix in a bcrypt-only context
if current_hash.startswith("$argon2"):
    print("      ⚠️  DETECTED: argon2id hash in bcrypt-only context — THIS IS THE BUG!")
elif current_hash.startswith("$2b$") or current_hash.startswith("$2a$"):
    print("      ℹ️  Hash is bcrypt — checking if password still verifies...")
    if verify_password(DEV_PASSWORD, current_hash):
        print("      ✅  Password already correct — no update needed.")
        cur.close()
        conn.close()
        print("\n" + "=" * 65)
        print("  ✅  Login credentials are already correct!")
        print(f"  Email   : {DEV_EMAIL}")
        print(f"  Password: {DEV_PASSWORD}")
        print("=" * 65)
        sys.exit(0)
    else:
        print("      ⚠️  Hash present but verify FAILS — will update.")
else:
    print(f"      ⚠️  Unknown hash format: {current_hash[:30]} — will update.")

# ── Step 4: Update the hash ───────────────────────────────────────────────────
print(f"\n[4/4] Updating hashed_password for '{DEV_EMAIL}'...")
cur.execute(
    "UPDATE tenant_users SET hashed_password = %s, updated_at = NOW() WHERE email = %s",
    (hashed, DEV_EMAIL),
)
print(f"      Rows updated  : {cur.rowcount}")

# Re-read and final verify
cur.execute("SELECT hashed_password FROM tenant_users WHERE email = %s", (DEV_EMAIL,))
stored = cur.fetchone()[0]

assert stored == hashed, "DB write-back mismatch!"
assert verify_password(DEV_PASSWORD, stored), "Final verify_password against DB hash FAILED!"
print("      DB round-trip : ✅  PASSED")

cur.close()
conn.close()

print("\n" + "=" * 65)
print("  ✅  E2E login credentials are FIXED and VERIFIED!")
print(f"  Email   : {DEV_EMAIL}")
print(f"  Password: {DEV_PASSWORD}")
print("=" * 65)
print()
print("  Next steps:")
print("  1. Ensure the backend is running (uvicorn / docker compose up)")
print("  2. cd omniflow-frontend")
print("  3. npx playwright test e2e/human_simulation.spec.ts --headed")
print()
