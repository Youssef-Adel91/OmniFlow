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


WORKER_NAMES = ("SemanticRouterWorker", "LLMInvokerWorker", "OutboundDispatcherWorker", "BroadcastWorker", "VCardGatekeeperWorker", "VaultWorker")


@router.get("/health/workers", summary="Kafka worker heartbeats (informational; not part of readiness)")
async def workers_status(response: Response):
    """Each consumer refreshes `hb:worker:<Class>` every ~15 s (TTL 60 s). A missing key = that worker is down or stuck.

    Deliberately NOT folded into /health/ready: an unrelated worker outage must not take the API out of the load balancer.
    """
    out = {}
    for name in WORKER_NAMES:
        raw = await redis_mgr.get_raw(f"hb:worker:{name}")
        out[name] = "ok" if raw else "missing"
    response.headers["Cache-Control"] = "no-store"
    return {"all_ok": all(v == "ok" for v in out.values()), "workers": out}


OUTBOUND_STUCK_MINUTES = 5


async def _outbound_backlog() -> dict:
    """Messages we promised to send (PENDING) that are still waiting after a few minutes — the symptom customers notice first."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    from src.shared.db.models import Message
    from src.shared.db.session import get_system_session

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=OUTBOUND_STUCK_MINUTES)
    async with get_system_session() as s:
        n, oldest = (await s.execute(select(func.count(), func.min(Message.created_at)).where(Message.delivery_status == "PENDING", Message.created_at < cutoff))).one()
    age = int((datetime.now(timezone.utc) - oldest).total_seconds()) if oldest else 0
    level = "critical" if n >= 50 else "warn" if n else "ok"
    return {"stuck_pending": int(n), "oldest_age_seconds": age, "level": level, "threshold_minutes": OUTBOUND_STUCK_MINUTES}


@router.get("/health/lag", summary="Backlog warnings: Kafka consumer lag, dead letters, stuck outbound messages (informational)")
async def lag_status(response: Response):
    """Aggregate counts only (no tenant data). `status` is the worst level; a source we cannot read is reported "unavailable", never guessed.

    Deliberately NOT part of /health/ready: a backlog must page a human, not remove the API from the load balancer.
    """
    from src.shared.kafka import lag as kafka_lag

    levels: list[str] = []
    result: dict = {}
    try:
        result["outbound"] = await asyncio.wait_for(_outbound_backlog(), timeout=3.0)
        levels.append(result["outbound"]["level"])
    except Exception:
        result["outbound"] = {"level": "unavailable"}
    try:
        broker = await kafka_lag.collect(settings.kafka_bootstrap_servers, kafka_lag.watched(settings))
        result["kafka"] = broker
        levels += [t["level"] for g in broker["groups"].values() for t in g.values()]
        if any(broker["dead_letters"].values()):
            levels.append("warn")
    except Exception:
        result["kafka"] = {"level": "unavailable"}
    result["status"] = kafka_lag.worst(levels) if levels else "unavailable"
    if "unavailable" in (result["outbound"].get("level"), result["kafka"].get("level")) and result["status"] == "ok":
        result["status"] = "degraded"
    response.headers["Cache-Control"] = "no-store"
    return result
