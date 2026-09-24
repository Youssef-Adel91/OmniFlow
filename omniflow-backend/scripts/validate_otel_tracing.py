"""
Real end-to-end validation of item 17's OpenTelemetry wiring.

Before this fix: `sentry_sdk.init()` didn't exist anywhere, and
`shared/observability/tracing.py` was a 3-line docstring stub with the real
setup call commented out in `gateway/main.py`. Settings existed
(`otel_enabled`, `otel_exporter_otlp_endpoint`) but nothing ever read them
to actually export a trace anywhere. Sentry itself remains genuinely
blocked (no real DSN exists) — this only covers OpenTelemetry, which has a
real local OTLP collector (Jaeger, already defined in docker-compose.yml
under the "monitoring" profile) requiring no external account.

This boots the REAL FastAPI app via its own `create_app()`/lifespan (not a
reimplementation), with `setup_opentelemetry()` wired in for real, makes a
real HTTP request through a real ASGI transport, and then queries Jaeger's
own real HTTP API to confirm a real span for that exact request actually
arrived — not just that the exporter was configured without error.
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from src.shared.core.config import get_settings

settings = get_settings()

JAEGER_API = "http://localhost:16686/api"


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


async def _jaeger_reachable() -> bool:
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(f"{JAEGER_API}/services", timeout=3.0)
            return r.status_code == 200
        except Exception:
            return False


async def main() -> None:
    _require_local()
    if not await _jaeger_reachable():
        raise RuntimeError(
            "Jaeger is not reachable at localhost:16686 — start it with: "
            "docker compose --profile monitoring up -d jaeger"
        )
    print("PASS: real Jaeger API is reachable")

    from src.gateway.main import create_app

    app = create_app()

    # A unique marker so this run's request is unambiguous among any other
    # traffic Jaeger may have recorded.
    marker = uuid.uuid4().hex[:12]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # The lifespan (which calls the real setup_opentelemetry) only runs
        # under an actual ASGI lifespan cycle — httpx's ASGITransport does
        # not trigger it automatically, so drive it explicitly.
        async with app.router.lifespan_context(app):
            response = await client.get("/health", headers={"X-Otel-Drill-Marker": marker})
            assert response.status_code == 200, f"unexpected /health status: {response.status_code}"
            print(f"PASS: real request to the real app succeeded (marker={marker})")

            # BatchSpanProcessor exports asynchronously — force a flush so
            # the span reaches Jaeger before this script queries for it,
            # rather than relying on the default export interval.
            from opentelemetry import trace
            provider = trace.get_tracer_provider()
            provider.force_flush(timeout_millis=5000)

    deadline = time.monotonic() + 15
    found = None
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline and found is None:
            r = await client.get(
                f"{JAEGER_API}/traces",
                params={"service": settings.otel_service_name, "lookback": "5m", "limit": 20},
            )
            data = r.json().get("data") or []
            for trace_obj in data:
                for span in trace_obj.get("spans", []):
                    tags = {t["key"]: t.get("value") for t in span.get("tags", [])}
                    if tags.get("http.target") == "/health" or tags.get("http.route") == "/health":
                        found = trace_obj
                        break
                if found:
                    break
            if found is None:
                await asyncio.sleep(1.0)

    assert found is not None, (
        f"no real Jaeger trace for service={settings.otel_service_name!r} /health "
        "showed up within 15s — the FastAPI auto-instrumentation is not actually exporting"
    )
    print(f"PASS: a real span for GET /health was found in real Jaeger (trace_id={found['traceID']})")
    print("\nALL SCENARIOS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
