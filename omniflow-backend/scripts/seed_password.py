"""
scripts/seed_password.py -- Dev Password Seeder (Sprint 13)

Hashes the dev password with bcrypt and updates all rows in tenant_users
so the JWT login endpoint can verify credentials correctly.

Connects DIRECTLY to Postgres on port 5432 (bypasses PgBouncer)
to avoid SSL / NullPool issues in one-off scripts.

Usage (PowerShell):
    $env:PYTHONPATH = "."
    $env:PYTHONUTF8 = "1"
    .venv/Scripts/python.exe scripts/seed_password.py
"""
from __future__ import annotations

import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.shared.security.password import hash_password

# ── Dev credentials (NEVER use in production) ─────────────────────────────────
DEV_PASSWORD = "OmniFlow@2025!"

# ── Connect directly to Postgres (bypasses PgBouncer + SSL issues) ────────────
PG_URL = (
    f"postgresql+asyncpg://"
    f"{os.getenv('POSTGRES_USER', 'omniflow')}:"
    f"{os.getenv('POSTGRES_PASSWORD', 'omniflow_dev_secret_change_me')}"
    f"@{os.getenv('POSTGRES_HOST', 'localhost')}:"
    f"{os.getenv('POSTGRES_PORT', '5432')}/"
    f"{os.getenv('POSTGRES_DB', 'omniflow_db')}"
)


async def seed_dev_password() -> None:
    """Hash the dev password and write it to every row in tenant_users."""
    print("[*] Hashing password with bcrypt (rounds=12)...")
    hashed_pw = hash_password(DEV_PASSWORD)

    engine = create_async_engine(PG_URL, poolclass=NullPool, echo=False, connect_args={"ssl": False})
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        async with session.begin():
            result = await session.execute(
                text("UPDATE tenant_users SET hashed_password = :hash"),
                {"hash": hashed_pw},
            )
            rows_updated = result.rowcount

    await engine.dispose()

    print(f"[OK] Password '{DEV_PASSWORD}' hashed and seeded successfully!")
    print(f"     Rows updated : {rows_updated}")
    print()
    print("[i]  You can now log in at  http://localhost:3001/ar/login")
    print(f"     Password : {DEV_PASSWORD}")


if __name__ == "__main__":
    asyncio.run(seed_dev_password())
