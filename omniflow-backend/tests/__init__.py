"""OmniFlow regression tests — isolated from live credentials and services."""
import os

os.environ.update({
    "APP_ENV": "development",
    "SECRET_KEY": "test-secret-key-not-for-production",
    "JWT_SECRET_KEY": "test-jwt-key-not-for-production",
    "META_WEBHOOK_HMAC_SECRET": "test-webhook-secret",
    "FIELD_ENCRYPTION_KEY": "test-field-key",
    "POSTGRES_PASSWORD": "test-password",
    "REDIS_PASSWORD": "test-password",
    "QDRANT_API_KEY": "test-key",
    "CLICKHOUSE_PASSWORD": "test-password",
    "OPENAI_API_KEY": "",
    "GOOGLE_AI_API_KEY": "mock",
    "GEMINI_API_KEY": "",
    "CLERK_ISSUER_URL": "https://test.clerk.accounts.dev",
    "BILLING_GATE_ENABLED": "false",      # billing tests switch the gate on explicitly
    # The API rate limiter counts per IP in Redis. With a real Redis reachable, ~600 tests share one IP and one minute-window, so the later ones got 429 and 24 tests failed
    # in the full run (each passed alone). Tests that exercise the limiter switch it on explicitly (tests/test_security_middleware.py).
    "RATE_LIMIT_ENABLED": "false",
})

from src.shared.core.config import Settings, get_settings

Settings.model_config["env_file"] = None
get_settings.cache_clear()
