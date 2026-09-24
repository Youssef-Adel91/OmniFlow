"""
shared/observability/tracing.py — OpenTelemetry tracing setup (item 17)

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
"""
from __future__ import annotations

import structlog
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
