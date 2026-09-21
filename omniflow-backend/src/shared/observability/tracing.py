"""shared/observability/tracing.py — OpenTelemetry tracing setup (Sprint 0 stub)"""
from __future__ import annotations
# Sprint 1: Configure OpenTelemetry SDK with:
#   - OTLP exporter to Jaeger
#   - Auto-instrumentation for FastAPI, SQLAlchemy, Redis, aiokafka
#   - Custom spans for: LLM calls, Qdrant queries, S3 ops
#   - Tenant_id as span attribute for cross-tenant analysis
