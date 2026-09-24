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
})

from src.shared.core.config import Settings, get_settings

Settings.model_config["env_file"] = None
get_settings.cache_clear()
