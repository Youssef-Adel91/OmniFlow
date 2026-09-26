"""
gateway/routers/health.py — Health Endpoints

Routers:
  GET /health — Process liveness probe.
  GET /health/ready — Database, Redis and Kafka readiness probe.

SECURITY NOTE (removed endpoint):
  The developer-only `GET /api/v1/test-rls` endpoint used to live here. It
  dumped raw customer rows (phone numbers, display names, VIP flags) for any
  tenant supplied via the `X-Tenant-ID` header, with no authentication at all.
  Its own docstring stated it MUST be removed before production, so it has
  been deleted. Do not re-introduce it — RLS enforcement is covered by the
  integration test suite instead.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from src.shared.core.config import get_settings
from src.shared.db.session import engine
from src.shared.kafka.producer import kafka_producer
from src.shared.redis_client.client import redis_mgr

settings = get_settings()

router = APIRouter(tags=["Health & Diagnostics"])


# ══════════════════════════════════════════════════════════════════════════════
# Response models
# ══════════════════════════════════════════════════════════════════════════════

class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    db_pool: str


# ══════════════════════════════════════════════════════════════════════════════
# GET /health
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Platform Health Probe",
    description=(
        "Process liveness probe endpoint. "
        "Returns HTTP 200 when the API process is running. "
        "Does NOT check downstream dependencies (DB, Redis, Kafka)."
    ),
    include_in_schema=True,
)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.app_env,
        db_pool="NullPool (PgBouncer managed)",
    )


async def _database_ready() -> bool:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        if settings.is_production:
            # A privileged application role silently bypasses tenant RLS.
            privileged = await connection.scalar(text(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            ))
            if privileged is not False:
                return False
    return True


async def dependency_status() -> dict[str, str]:
    async def probe(check):
        try:
            return "ok" if await asyncio.wait_for(check(), timeout=3.0) else "unavailable"
        except Exception:
            # Never disclose connection strings, credentials or SQL errors.
            return "unavailable"

    names = ("database", "redis", "kafka")
    results = await asyncio.gather(*[
        probe(check) for check in (_database_ready, redis_mgr.ping, kafka_producer.ping)
    ])
    return dict(zip(names, results))


@router.get("/health/ready", summary="Dependency readiness probe")
async def readiness_check(response: Response):
    checks = await dependency_status()
    ready = all(value == "ok" for value in checks.values())
    response.status_code = 200 if ready else 503
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ready" if ready else "unavailable", "checks": checks}
