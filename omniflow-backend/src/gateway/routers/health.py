"""
gateway/routers/health.py — Health Endpoints

Routers:
  GET /health — Kubernetes liveness / readiness probe.

SECURITY NOTE (removed endpoint):
  The developer-only `GET /api/v1/test-rls` endpoint used to live here. It
  dumped raw customer rows (phone numbers, display names, VIP flags) for any
  tenant supplied via the `X-Tenant-ID` header, with no authentication at all.
  Its own docstring stated it MUST be removed before production, so it has
  been deleted. Do not re-introduce it — RLS enforcement is covered by the
  integration test suite instead.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from src.shared.core.config import get_settings

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
        "Kubernetes liveness and readiness probe endpoint. "
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
