"""
gateway/main.py — FastAPI Application Factory

Sprint 3: Full gateway with exception handlers, middleware, lifespan,
and router registration.

Architecture:
    create_app()
      ├── lifespan (startup/shutdown hooks)
      ├── Middleware stack
      │     ├── CORSMiddleware
      │     └── (Request-ID middleware — Sprint 4)
      ├── Exception handlers
      │     ├── SQLAlchemy integrity errors → HTTP 409
      │     ├── SQLAlchemy operational errors → HTTP 503
      │     ├── ValueError (repo "not found") → HTTP 404
      │     └── Unhandled → HTTP 500
      └── Routers
            └── health.router (/health)

References: SRS §2.3 — Security, SRS §4 — API Design
"""
from __future__ import annotations

import asyncio
import os
import sys

# Force UTF-8 encoding on Windows to prevent UnicodeEncodeError with Arabic text
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from src.shared.core.config import get_settings
from src.shared.db.repository import ConversationConflictError
from src.shared.kafka.producer import kafka_producer
from src.shared.redis_client.client import redis_mgr
from src.gateway.routers import health as health_router
from src.gateway.routers import conversations as conversations_router
from src.gateway.routers import auth as auth_router
from src.gateway.routers import properties as properties_router
from src.gateway.routers import tenants as tenants_router
from src.gateway.routers import broadcasts as broadcasts_router
from src.gateway.routers import customers as customers_router
from src.gateway.routers import dashboard as dashboard_router
from src.gateway.routers import knowledge as knowledge_router
from src.gateway.routers import reports as reports_router
from src.gateway.routers import settings as settings_router
from src.gateway.routers import support as support_router
from src.gateway.routers import users as users_router
from src.channel_adapters.whatsapp import router as whatsapp_router
from src.channel_adapters.instagram.router import router as meta_webhook_router

settings = get_settings()
logger = structlog.get_logger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Lifespan — startup / shutdown hooks
# ══════════════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan context manager.

    Startup: log readiness, init Kafka producer, Redis pool, OTEL.
    Shutdown: flush buffers, close connections gracefully.

    Note: Kafka and Redis failures are non-fatal in development — the server
    will start with a warning so webhook endpoints remain reachable for local
    testing without the full Docker Compose stack running.
    """
    logger.info(
        "omniflow_startup",
        app=settings.app_name,
        version=settings.app_version,
        env=settings.app_env,
    )
    # ── Sprint 4: Kafka Producer ───────────────────────────────────────────────
    try:
        await asyncio.wait_for(kafka_producer.start(), timeout=5.0)
    except Exception as exc:
        if settings.is_production:
            await kafka_producer.stop()
            raise RuntimeError("Kafka is required for production startup") from exc
        logger.warning(
            "kafka_producer_unavailable_continuing",
            error=str(exc)[:120],
            tip="Start Redpanda via 'docker compose up -d redpanda' for full functionality.",
        )

    # ── Sprint 5: Redis Client ─────────────────────────────────────────────────
    try:
        await redis_mgr.start()
    except Exception as exc:
        if settings.is_production:
            await kafka_producer.stop()
            await redis_mgr.stop()
            raise RuntimeError("Redis is required for production startup") from exc
        logger.warning(
            "redis_unavailable_continuing",
            error=str(exc),
            tip="Start Redis via 'docker compose up -d redis' for full functionality.",
        )

    # ── Future (Sprint 6+) ────────────────────────────────────────────────────
    # setup_opentelemetry(settings)

    if settings.is_production:
        checks = await health_router.dependency_status()
        if any(value != "ok" for value in checks.values()):
            await redis_mgr.stop()
            await kafka_producer.stop()
            raise RuntimeError("Production dependency readiness failed: " + ", ".join(
                name for name, value in checks.items() if value != "ok"
            ))

    logger.info("omniflow_ready", host=settings.app_host, port=settings.app_port)
    yield  # ← application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("omniflow_shutdown", app=settings.app_name)
    try:
        await redis_mgr.stop()
    except Exception:
        pass
    try:
        await kafka_producer.stop()
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════════════════
# App Factory
# ══════════════════════════════════════════════════════════════════════════════

def create_app() -> FastAPI:
    """
    Construct and configure the FastAPI application.

    Called once at module load time. The resulting `app` object is what
    Uvicorn / Gunicorn references as the ASGI callable.
    """
    app = FastAPI(
        title="OmniFlow AI — API Gateway",
        description=(
            "Enterprise Real Estate Digital Twin — Backend API.\n\n"
            "Multi-tenant SaaS with PostgreSQL RLS, Kafka event bus, "
            "and AI-powered omni-channel customer engagement."
        ),
        version=settings.app_version,
        # Hide docs in production — expose only via internal VPN / bastion
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
        lifespan=lifespan,
    )

    _register_middleware(app)
    _register_exception_handlers(app)
    _register_routers(app)

    return app


# ══════════════════════════════════════════════════════════════════════════════
# Middleware
# ══════════════════════════════════════════════════════════════════════════════

def _register_middleware(app: FastAPI) -> None:
    """
    Attach middleware in reverse order (last added = outermost layer).

    Current middleware:
      1. CORSMiddleware — handles preflight and cross-origin headers.

    Sprint 4 additions:
      - RequestIDMiddleware — inject X-Request-ID for distributed tracing.
      - TenantContextMiddleware — validate JWT and attach tenant to request state.
      - RateLimitMiddleware — Redis token-bucket per tenant.
    """
    # In development: allow all common localhost ports so Next.js port changes
    # (3000 → 3001 etc.) never block CORS. In production: use the strict list.
    _dev_origins = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:8000",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:8000",
    ]
    _origins = _dev_origins if settings.is_development else settings.allowed_origins_list
    if "http://localhost:3000" not in _origins:
        _origins.append("http://localhost:3000")
    if "http://127.0.0.1:3000" not in _origins:
        _origins.append("http://127.0.0.1:3000")
        
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Tenant-ID",
            "X-Request-ID",
            "Accept",
        ],
        expose_headers=["X-Request-ID"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# Exception Handlers
# ══════════════════════════════════════════════════════════════════════════════

def _register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers for all routes."""

    @app.exception_handler(IntegrityError)
    async def integrity_error_handler(
        request: Request, exc: IntegrityError
    ) -> JSONResponse:
        """
        PostgreSQL constraint violation (unique key, FK, not-null).
        Map to HTTP 409 Conflict — the resource already exists or
        a referenced resource is missing.
        """
        logger.warning(
            "db_integrity_error",
            path=request.url.path,
            detail=str(exc.orig),
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "CONFLICT",
                "message": "A database constraint was violated.",
                "detail": _sanitize_db_error(str(exc.orig)),
            },
        )

    @app.exception_handler(OperationalError)
    async def operational_error_handler(
        request: Request, exc: OperationalError
    ) -> JSONResponse:
        """
        Database unreachable, connection timeout, PgBouncer exhausted.
        Map to HTTP 503 Service Unavailable.
        """
        logger.error(
            "db_operational_error",
            path=request.url.path,
            detail=str(exc.orig),
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "code": "DATABASE_UNAVAILABLE",
                "message": "The database is temporarily unavailable. Please retry.",
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(
        request: Request, exc: SQLAlchemyError
    ) -> JSONResponse:
        """Catch-all for any other SQLAlchemy errors."""
        logger.error(
            "db_unexpected_error",
            path=request.url.path,
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": "DATABASE_ERROR",
                "message": "An unexpected database error occurred.",
            },
        )

    @app.exception_handler(ConversationConflictError)
    async def conversation_conflict_handler(
        request: Request, exc: ConversationConflictError
    ) -> JSONResponse:
        """
        ConversationRepository.assign_agent lost the takeover race to a
        different agent (item 13 concurrency audit). Map to HTTP 409 so the
        losing agent gets an explicit error instead of a false "success".
        """
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={
                "code": "ALREADY_ASSIGNED",
                "message": str(exc),
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(
        request: Request, exc: ValueError
    ) -> JSONResponse:
        """
        Repository raises ValueError for "not found" (get_or_404).
        Map to HTTP 404.
        """
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "code": "NOT_FOUND",
                "message": str(exc),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """
        Final safety net. Logs the error and returns a generic 500.
        Never expose internal details to the client.
        """
        error_id = str(uuid.uuid4())
        logger.exception(
            "unhandled_exception",
            error_id=error_id,
            path=request.url.path,
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred.",
                "error_id": error_id,  # correlate with server logs
            },
        )


def _sanitize_db_error(raw: str) -> str:
    """
    Strip PostgreSQL internal details before sending to clients.
    Keeps only the high-level constraint name if present.
    """
    if "DETAIL:" in raw:
        raw = raw.split("DETAIL:")[0].strip()
    return raw[:200]  # hard cap


# ══════════════════════════════════════════════════════════════════════════════
# Router Registration
# ══════════════════════════════════════════════════════════════════════════════

def _register_routers(app: FastAPI) -> None:
    """
    Mount all API routers.

    Sprint 3: health + RLS test only.
    Sprint 4+: webhooks, conversations, customers, listings, reports, admin.
    """
    app.include_router(health_router.router)

    # ── Sprint 4: WhatsApp Ingestion ──────────────────────────────────────────
    app.include_router(whatsapp_router.router, prefix="/api/v1")

    # ── Sprint N: Meta Instagram / Facebook Messenger + Comments ────────────
    app.include_router(meta_webhook_router, prefix="/api/v1")

    # ── Sprint 12: Conversations, Messages & SSE Stream ───────────────────────
    app.include_router(conversations_router.router)

    # ── Auth (DEPRECATED) ─────────────────────────────────────────────────────
    # Clerk is the only supported identity provider. This router now serves
    # HTTP 410 Gone for /login, /register and /refresh; only /logout (Clerk
    # token revocation) remains functional. See routers/auth.py for the
    # rationale. Kept mounted deliberately so old clients get an explicit,
    # actionable 410 instead of a confusing 404.
    app.include_router(auth_router.router, prefix="/api/v1/auth", tags=["Auth"])
    app.include_router(tenants_router.router)
    app.include_router(properties_router.router)

    from src.gateway.routers import webhooks
    app.include_router(webhooks.router, prefix="/api/v1", tags=["Webhooks"])

    # ── Dashboard API surface ─────────────────────────────────────────────────
    # Each router declares its own /api/v1/... prefix internally.
    app.include_router(customers_router.router)    # /api/v1/customers
    app.include_router(reports_router.router)      # /api/v1/reports
    app.include_router(settings_router.router)     # /api/v1/settings
    app.include_router(support_router.router)      # /api/v1/support
    app.include_router(broadcasts_router.router)   # /api/v1/broadcasts
    app.include_router(users_router.router)        # /api/v1/users
    app.include_router(dashboard_router.router)    # /api/v1/dashboard
    app.include_router(knowledge_router.router)    # /api/v1/knowledge


# ══════════════════════════════════════════════════════════════════════════════
# ASGI app instance — referenced by uvicorn / gunicorn
# ══════════════════════════════════════════════════════════════════════════════
app = create_app()
