"""
alembic/env.py — Async Alembic Environment for OmniFlow AI

Uses SQLAlchemy AsyncEngine so migrations run over the same asyncpg driver
used in production.  The DB URL is pulled from pydantic Settings so there
is no credential duplication between alembic.ini and .env.

Run migrations:
    alembic revision --autogenerate -m "description"
    alembic upgrade head
    alembic downgrade -1
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Load all ORM models so Base.metadata is populated ─────────────────────────
# This import MUST happen before target_metadata is assigned.
from src.shared.db.base import Base
import src.shared.db.models  # noqa: F401 — registers all mappers with Base

# ── Settings (reads .env) ──────────────────────────────────────────────────────
from src.shared.core.config import get_settings

settings = get_settings()

# ── Alembic Config ─────────────────────────────────────────────────────────────
config = context.config

# Override the placeholder URL in alembic.ini with the real async URL
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

# Set up Python logging from alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# This is the metadata object that autogenerate inspects
target_metadata = Base.metadata


# ─────────────────────────────────────────────────────────────────────────────
# Offline migrations (generate SQL without a live DB connection)
# ─────────────────────────────────────────────────────────────────────────────
def run_migrations_offline() -> None:
    """
    Generate SQL migration scripts without connecting to the database.
    Useful for review / audit workflows.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # PostgreSQL-specific: include schemas & compare server defaults
        include_schemas=False,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ─────────────────────────────────────────────────────────────────────────────
# Online migrations (runs against live async DB)
# ─────────────────────────────────────────────────────────────────────────────
def do_run_migrations(connection):  # type: ignore[no-untyped-def]
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_server_default=True,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Create an async engine specifically for Alembic (separate from the app engine).

    NOTE: We use NullPool here too — Alembic migrations are single-shot
    scripts, not long-running servers; no pool needed.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args={"prepared_statement_cache_size": 0, "statement_cache_size": 0},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
elif config.attributes.get("connection") is not None:
    do_run_migrations(config.attributes["connection"])
else:
    run_migrations_online()
