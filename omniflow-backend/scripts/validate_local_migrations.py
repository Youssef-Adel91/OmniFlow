"""Validate migrations in a temporary local schema; always roll back all changes.

Run from omniflow-backend: .venv/Scripts/python.exe scripts/validate_local_migrations.py
Defaults to direct PostgreSQL. Pass --via-pgbouncer to use the application's
local connection when another PostgreSQL service occupies the direct port.
"""
import asyncio
import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine
from src.shared.core.config import get_settings


async def main(via_pgbouncer=False, inbox=False):
    settings = get_settings()
    host = settings.pgbouncer_host if via_pgbouncer else settings.postgres_host
    port = settings.pgbouncer_port if via_pgbouncer else settings.postgres_port
    if host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Validation requires a local PostgreSQL host")
    url = URL.create(
        "postgresql+asyncpg", username=settings.postgres_user,
        password=settings.postgres_password, host=host,
        port=port, database=settings.postgres_db,
    )
    engine = create_async_engine(
        url, echo=False,
        connect_args={"prepared_statement_cache_size": 0, "statement_cache_size": 0},
    )
    schema = "validation_" + uuid.uuid4().hex
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

                def upgrade(sync_connection):
                    config = Config("alembic.ini")
                    config.attributes["connection"] = sync_connection
                    command.upgrade(config, "head")

                await connection.run_sync(upgrade)
                revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
                print(f"Migration chain passed: {revision}")
                if inbox:
                    from local_inbox_checks import check_inbox
                    await check_inbox(connection, schema)
            finally:
                await transaction.rollback()
                print("Temporary validation transaction rolled back")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--via-pgbouncer", action="store_true")
    parser.add_argument("--inbox", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.via_pgbouncer, args.inbox))
    except Exception as exc:
        # Database driver messages can include bound data or connection details.
        print(f"Validation failed: {type(exc).__name__}", file=sys.stderr)
        cause = getattr(exc, "orig", None)
        if cause:
            print(str(cause).split("DETAIL:")[0], file=sys.stderr)
        sys.exit(1)
