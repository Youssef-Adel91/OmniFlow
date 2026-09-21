"""Verify stored bcrypt hash matches OmniFlow@2025! password."""
from src.shared.security.password import verify_password, hash_password

# The hash currently in the DB (from docker exec query)
stored = "$2b$12$ivEfJtCwy5uwsTctXos94enVqZgbwLMn689ljK5A5HhNbhE9t8mY2"
password = "OmniFlow@2025!"

result = verify_password(password, stored)
print(f"[*] Verifying stored hash against '{password}'")
print(f"[{'OK' if result else 'FAIL'}] verify_password = {result}")

if not result:
    print("[!] Hash does NOT match — generating new correct hash...")
    new_hash = hash_password(password)
    print(f"    New hash: {new_hash}")
    print()
    print("    Run this SQL to update:")
    print(f"    UPDATE tenant_users SET hashed_password = '{new_hash}';")
else:
    print("[✓] Existing hash is correct — no DB update needed!")
    print("    Login with: OmniFlow@2025!")
