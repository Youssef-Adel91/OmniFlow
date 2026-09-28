"""
shared/observability/tracing.py — OpenTelemetry tracing + Sentry setup (item 17)

Wires a real TracerProvider + OTLP/gRPC exporter (Jaeger locally, any
OTLP-compatible collector in production) and auto-instruments FastAPI,
SQLAlchemy, and redis-py. Called once from `gateway/main.py`'s lifespan,
before the app starts serving.

NOT covered: aiokafka. opentelemetry-python-contrib has no aiokafka
instrumentor (only a kafka-python one, which patches a different client
library and would silently do nothing against this codebase's actual
Kafka usage) — Kafka producer/consumer spans are not emitted. A future
fix needs either a hand-written span wrapper around
`BaseKafkaConsumer`/`KafkaProducerManager`, or waiting for upstream
aiokafka support.

Sentry (`setup_sentry`, below): the SDK call itself was never wired in
before, independent of the "no real DSN yet" blocker -- `sentry_dsn` sat
in Settings unread. Wired now with `sentry_sdk.init()` no-oping (with a
log line) when the DSN is empty, exactly like `setup_opentelemetry` does
for `otel_enabled=False`, so this is real, testable code today and only
needs a DSN dropped into the environment to go live -- no further
engineering. `send_default_pii=False` is explicit, not just the SDK
default, given this codebase's Saudi PDPL sensitivity elsewhere (see
config.py's `is_processing_restricted` handling).
"""
from __future__ import annotations

import structlog
import sentry_sdk
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

logger = structlog.get_logger(__name__)

_initialized = False
_sentry_initialized = False


def setup_opentelemetry(settings, app: FastAPI) -> None:
    """
    Idempotent — safe to call once at startup. No-ops (with a log line, not
    a silent skip) if `settings.otel_enabled` is False, so a misconfigured
    production deployment doesn't silently ship without tracing (the
    production validator in config.py already hard-fails on
    otel_enabled=False in prod; this is the other half of that contract).
    """
    global _initialized
    if not settings.otel_enabled:
        logger.info("otel_disabled", reason="settings.otel_enabled is False")
        return
    if _initialized:
        return

    resource = Resource.create({
        "service.name": settings.otel_service_name,
        "deployment.environment": settings.app_env,
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)

    from src.shared.db.session import engine as _db_engine
    SQLAlchemyInstrumentor().instrument(engine=_db_engine.sync_engine)

    RedisInstrumentor().instrument()

    _initialized = True
    logger.info(
        "otel_initialized",
        service_name=settings.otel_service_name,
        endpoint=settings.otel_exporter_otlp_endpoint,
        instrumented=["fastapi", "sqlalchemy", "redis"],
        not_instrumented=["aiokafka — no upstream instrumentor exists"],
    )


def setup_sentry(settings) -> None:
    """
    Idempotent — safe to call once at startup. No-ops (with a log line, not
    a silent skip) if `settings.sentry_dsn` is empty, so local/dev/CI runs
    never try to talk to Sentry, and a misconfigured production deployment
    is caught loudly by config.py's own production validator instead of
    silently shipping without error tracking.
    """
    global _sentry_initialized
    if not settings.sentry_dsn:
        logger.info("sentry_disabled", reason="settings.sentry_dsn is empty")
        return
    if _sentry_initialized:
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment or settings.app_env,
        release=f"omniflow-backend@{settings.app_version}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        send_default_pii=False,
    )

    _sentry_initialized = True
    logger.info(
        "sentry_initialized",
        environment=settings.sentry_environment or settings.app_env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
    )
