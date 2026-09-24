"""
shared/core/config.py — Pydantic Settings (Sprint 0 placeholder)

All application configuration is loaded from environment variables here.
This module is the single source of truth for configuration.
Business logic will be added in Sprint 1+.
"""
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    OmniFlow AI — Unified Application Settings.

    Loaded from environment variables with .env file fallback.
    All fields are validated by Pydantic v2 at startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore unknown env vars
    )

    # ── Application ───────────────────────────────────────────────────────────
    app_name: str = "OmniFlow AI"
    app_env: Literal["development", "staging", "production"] = "development"
    app_version: str = "2.0.0"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    app_log_format: Literal["json", "text"] = "json"
    app_workers: int = 4
    app_timezone: str = "Asia/Riyadh"
    api_base_url: AnyHttpUrl = Field(default="http://localhost:8000")
    frontend_url: AnyHttpUrl = Field(default="http://localhost:3000")
    allowed_origins: str = "http://localhost:3000,http://localhost:8000"

    # ── Security ──────────────────────────────────────────────────────────────
    secret_key: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7
    meta_webhook_hmac_secret: str
    field_encryption_key: str
    clerk_secret_key: str = ""
    clerk_publishable_key: str = ""
    clerk_issuer_url: str = ""
    # Svix signing secret for the Clerk webhook (env: CLERK_WEBHOOK_SECRET).
    # MANDATORY in production — see validate_production_settings below.
    clerk_webhook_secret: str = Field(
        default="",
        description="Clerk/Svix webhook signing secret — env: CLERK_WEBHOOK_SECRET",
    )

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "omniflow_db"
    postgres_user: str = "omniflow"
    postgres_password: str
    postgres_ssl_mode: str = "disable"
    postgres_min_pool_size: int = 5
    postgres_max_pool_size: int = 20
    pgbouncer_host: str = "localhost"
    pgbouncer_port: int = 6432

    @computed_field  # type: ignore[misc]
    @property
    def database_url(self) -> str:
        from sqlalchemy import URL
        return URL.create(
            "postgresql+asyncpg", username=self.postgres_user,
            password=self.postgres_password, host=self.pgbouncer_host,
            port=self.pgbouncer_port, database=self.postgres_db,
        ).render_as_string(hide_password=False)

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str
    redis_ssl: bool = False
    redis_max_connections: int = 100
    redis_db_conversations: int = 0
    redis_db_rate_limiting: int = 1
    redis_db_idempotency: int = 2
    redis_db_celery_broker: int = 3
    redis_db_celery_results: int = 4
    redis_db_cache: int = 5
    conversation_context_ttl: int = 86400
    session_state_ttl: int = 3600
    rate_limit_window: int = 60
    idempotency_key_ttl: int = 604800
    rega_verification_cache_ttl: int = 604800

    @computed_field  # type: ignore[misc]
    @property
    def redis_url(self) -> str:
        scheme = "rediss" if self.redis_ssl else "redis"
        return f"{scheme}://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db_conversations}"

    @computed_field  # type: ignore[misc]
    @property
    def celery_broker_url(self) -> str:
        scheme = "rediss" if self.redis_ssl else "redis"
        return f"{scheme}://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db_celery_broker}"

    # ── Kafka ─────────────────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_security_protocol: str = "PLAINTEXT"
    kafka_consumer_group_ingestion: str = "omniflow.ingestion.v1"
    kafka_consumer_group_ai_workers: str = "omniflow.ai-workers.v1"
    kafka_consumer_group_vault_workers: str = "omniflow.vault-workers.v1"
    kafka_consumer_group_broadcast_workers: str = "omniflow.broadcast-workers.v1"
    kafka_consumer_group_notification_svc: str = "omniflow.notification-svc.v1"
    kafka_consumer_group_vcard_gatekeeper: str = "omniflow.vcard-gatekeeper.v1"
    kafka_topic_messages_incoming: str = "messages.incoming.v1"
    kafka_topic_messages_outgoing: str = "messages.outgoing.v1"
    kafka_topic_messages_outgoing_human: str = "messages.outgoing.human.v1"
    kafka_topic_llm_routing: str = "llm.routing.v1"
    kafka_topic_multimodal_audio: str = "multimodal.audio.v1"
    kafka_topic_multimodal_vision: str = "multimodal.vision.v1"
    kafka_topic_broadcast_marketing: str = "broadcast.marketing.v1"
    kafka_topic_analytics_events: str = "analytics.events.v1"
    kafka_topic_conversations_updates: str = "conversations.updates.v1"
    kafka_topic_vault_retrieval: str = "vault.retrieval.v1"
    kafka_topic_payment_events: str = "payment.events.v1"
    kafka_topic_vcard_gatekeeper: str = "vcard.gatekeeper.v1"

    # ── Qdrant ────────────────────────────────────────────────────────────────
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_grpc_port: int = 6334
    qdrant_api_key: str
    qdrant_use_grpc: bool = True
    qdrant_timeout: int = 30
    qdrant_shared_knowledge_collection: str = "omniflow_shared_knowledge"
    qdrant_semantic_cache_collection: str = "omniflow_semantic_cache"
    semantic_cache_hit_threshold: float = 0.95
    semantic_cache_near_hit_threshold: float = 0.85
    rag_dense_top_k: int = 20
    rag_sparse_top_k: int = 20
    rag_reranked_top_k: int = 5
    rag_max_context_tokens: int = 4096

    # ── ClickHouse ────────────────────────────────────────────────────────────
    clickhouse_host: str = "localhost"
    clickhouse_http_port: int = 8123
    clickhouse_native_port: int = 9000
    clickhouse_db: str = "omniflow_analytics"
    clickhouse_user: str = "omniflow"
    clickhouse_password: str
    clickhouse_secure: bool = False

    # ── AWS / S3 ──────────────────────────────────────────────────────────────
    aws_region: str = "me-south-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_vault_bucket: str = "omniflow-reports-vault"
    s3_media_temp_bucket: str = "omniflow-media-temp"
    s3_assets_bucket: str = "omniflow-public-assets"
    # Knowledge Base uploads (SRS §4.2). MUST stay private — these are the
    # tenant's internal documents, unlike s3_assets_bucket which is public.
    s3_knowledge_bucket: str = "omniflow-knowledge-docs"
    s3_endpoint_url: str | None = None        # Set for MinIO in dev
    s3_use_path_style: bool = True
    vault_presigned_url_ttl_seconds: int = 900
    kms_dev_mock_key: bool = True

    # ── MinIO / S3 Media Storage (Sprint 16 — Voice Notes Storage) ───────────
    # For local dev: MinIO runs on port 9020 (API) / 9021 (Console) in Docker.
    # For production: leave MINIO_ENDPOINT empty and set AWS_* keys above.
    #
    # MINIO_ENDPOINT=http://localhost:9020
    # MINIO_ACCESS_KEY=minioadmin
    # MINIO_SECRET_KEY=minioadmin
    # MINIO_BUCKET_NAME=omniflow-media
    minio_endpoint: str = ""          # e.g. http://localhost:9020 (blank = use AWS S3)
    minio_access_key: str = ""        # MinIO root user (minioadmin in dev)
    minio_secret_key: str = ""        # MinIO root password
    minio_bucket_name: str = "omniflow-media"   # Bucket for TTS audio + media


    # ── Meta WhatsApp ─────────────────────────────────────────────────────────
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_graph_api_version: str = "v21.0"
    meta_whatsapp_phone_number_id: str = ""
    meta_whatsapp_business_account_id: str = ""
    meta_whatsapp_access_token: str = ""
    meta_whatsapp_webhook_verify_token: str = ""
    meta_whatsapp_rate_limit_per_second: int = 80
    meta_whatsapp_broadcast_rate_limit: int = 50

    # ── Meta Instagram / Facebook Messenger ───────────────────────────────────
    # PAGE_ACCESS_TOKEN for the connected Facebook Page (covers both Messenger
    # and Instagram DMs when the IG account is linked to the Page).
    # SECURITY: never hard-code a real token here — it is read from the
    # META_INSTAGRAM_PAGE_ACCESS_TOKEN environment variable only.
    meta_instagram_page_access_token: str = Field(
        default="",
        description="Meta Page Access Token — env: META_INSTAGRAM_PAGE_ACCESS_TOKEN",
    )
    # Token you enter in the Meta App Dashboard under Webhooks → Verify Token
    meta_instagram_verify_token: str = ""
    # Facebook Page ID (numeric string) for tenant resolution
    meta_instagram_page_id: str = ""

    # ── LLM Providers ────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_api_base: str = "https://api.openai.com/v1"
    openai_timeout: int = 60
    openai_max_retries: int = 3
    # ── Free/OpenAI-Compatible AI Provider Configuration ───────────────────
    # Switch between openrouter.ai, groq, or direct openai.
    llm_primary_provider: Literal["openai", "openrouter", "groq", "gemini"] = "openai"
    openrouter_api_key: str = ""
    groq_api_key: str = ""
    llm_free_only: bool = False
    llm_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        description="Base URL for OpenAI-compatible API (OpenRouter or Groq). "
        "Set to https://api.openai.com/v1 for direct OpenAI.",
    )
    llm_api_key: str = Field(
        default="",
        description=(
            "API key for the selected provider. Priority: LLM_API_KEY env var → "
            "OPENROUTER_API_KEY env var → GROQ_API_KEY env var. "
            "Leave blank if using mock mode or direct OpenAI with OPENAI_API_KEY."
        ),
    )
    # Model identifiers per routing tier — all should be valid for the selected provider.
    MODEL_ROUTER: str = Field(
        default="meta-llama/llama-3.1-8b-instruct:free",
        description="Default model for intent classification / router tier.",
    )
    MODEL_L1: str = Field(
        default="meta-llama/llama-3.1-8b-instruct:free",
        description="L1 triage model — simple FAQs / greetings.",
    )
    MODEL_L2: str = Field(
        default="qwen/qwen-2.5-72b-instruct:free",
        description="L2 RAG-augmented model — moderate complexity.",
    )
    MODEL_L3: str = Field(
        default="deepseek/deepseek-r1:free",
        description="L3 master agent model — deep consultation / legal.",
    )
    # Optional per-provider overrides (leave blank to use MODEL_* defaults above).
    model_l1_override: str = Field(default="", description="Override L1 model identifier.")
    model_l2_override: str = Field(default="", description="Override L2 model identifier.")
    model_l3_override: str = Field(default="", description="Override L3 model identifier.")
    # Legacy names remain available while the workers share this configuration.
    llm_l1_model: str = "gpt-4o-mini"
    llm_l3_model: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    embedding_model_dim: int = 1536
    # Local, no-API-key embedding fallback (ONNX via fastembed) — used
    # whenever no real OpenAI key is configured, instead of the previous
    # semantically-meaningless random-vector mock. See embedder.py.
    embedding_local_model: str = "BAAI/bge-small-en-v1.5"
    embedding_local_model_dim: int = 384
    whisper_api_model: str = "whisper-1"
    google_ai_api_key: str = Field(
        default="",
        description="Google Gemini API key — env: GOOGLE_AI_API_KEY",
    )
    gemini_api_key: str = ""
    gemini_l1_model: str = "gemini-2.0-flash-lite"
    gemini_l3_model: str = "gemini-2.0-pro"
    ai_confidence_escalation_threshold: float = 0.70

    # ── Multimodal ────────────────────────────────────────────────────────────
    whisper_self_hosted_enabled: bool = False
    whisper_self_hosted_model_size: str = "large-v3"
    whisper_max_audio_duration_seconds: int = 600
    whisper_short_audio_threshold: int = 30
    vision_llm_model: str = "gpt-4o"
    media_voice_retention_hours: int = 24
    media_image_retention_days: int = 7

    # ── ElevenLabs TTS (Sprint 14 — Voice Notes) ─────────────────────────────
    # Set ELEVENLABS_API_KEY=mock (or leave blank) to enable Mock Mode.
    # Mock Mode returns a static royalty-free MP3 URL with zero API calls,
    # making it safe for local dev and CI without any ElevenLabs account.
    elevenlabs_api_key: str = "mock"
    # ElevenLabs voice ID to use for synthesis.
    # Default: multilingual Adam voice (supports Arabic natively).
    # Override with a tenant-specific cloned voice in production.
    elevenlabs_voice_id: str = "pNInz6obpgDQGcFmaJgB"   # Adam (ElevenLabs)
    # Max concurrent TTS requests per worker process.
    # Starter plan: 2 concurrent; Creator plan: 5; Scale: 10+
    tts_max_concurrent_requests: int = 2
    # Keywords that trigger voice-note replies (comma-separated, Arabic/English).
    # Checked against the *customer's* message (case-insensitive contains).
    tts_voice_trigger_keywords: str = "صوت,voice note,voice,رسالة صوتية,اسمعني"

    # ── REGA ─────────────────────────────────────────────────────────────────
    rega_api_base_url: str = "https://api.rega.gov.sa"
    rega_api_key: str = ""
    rega_api_timeout: int = 15

    # ── Payment ───────────────────────────────────────────────────────────────
    moyasar_api_key: str = ""
    moyasar_webhook_secret: str = ""
    tap_secret_key: str = ""
    tap_webhook_secret: str = ""
    report_deed_check_price_sar: float = 29.00
    report_municipal_consulting_price_sar: float = 15.00

    # ── VCard & VIP ───────────────────────────────────────────────────────────
    vcard_validation_timeout_hours: int = 1
    vcard_reminder_2_delay_hours: int = 24
    vcard_dormant_delay_days: int = 7
    vip_max_messages_per_week: int = 3
    vip_resubscribe_cooldown_days: int = 90
    vip_engagement_score_threshold: int = 50

    # ── Escalation SLA ────────────────────────────────────────────────────────
    escalation_sla_minutes: int = 15
    escalation_manager_alert_minutes: int = 20

    # ── Observability ─────────────────────────────────────────────────────────
    otel_enabled: bool = True
    otel_service_name: str = "omniflow-backend"
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    prometheus_enabled: bool = True
    sentry_dsn: str = ""

    # ── Feature Flags ─────────────────────────────────────────────────────────
    feature_multimodal_voice: bool = True
    feature_multimodal_vision: bool = True
    feature_reports_vault: bool = True
    feature_vcard_gatekeeper: bool = True
    feature_vip_broadcast: bool = True
    feature_human_takeover: bool = True
    feature_rega_verification: bool = True
    feature_clickhouse_analytics: bool = True
    feature_semantic_cache: bool = True
    # Sprint 14: Enable AI voice-note replies via ElevenLabs TTS.
    # Requires ELEVENLABS_API_KEY to be set (or "mock" for dev).
    feature_voice_notes_enabled: bool = True

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_enabled: bool = True
    rate_limit_webhook_per_min: int = 300
    rate_limit_api_per_min: int = 120
    rate_limit_llm_per_min: int = 60

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        """Enforce strict settings in production."""
        if self.app_env == "production":
            # Explicit errors remain active when Python runs with -O.
            checks = {
                "APP_DEBUG must be false": not self.app_debug,
                "SENTRY_DSN is required": bool(self.sentry_dsn),
                "KMS mock must be disabled": not self.kms_dev_mock_key,
                "OpenTelemetry must be enabled": self.otel_enabled,
                "CLERK_SECRET_KEY is required": bool(self.clerk_secret_key),
                "CLERK_WEBHOOK_SECRET is required": bool(self.clerk_webhook_secret),
                "Clerk issuer or publishable key is required": bool(self.clerk_issuer_url or self.clerk_publishable_key),
                "ALLOWED_ORIGINS must contain explicit HTTPS origins": all(
                    origin.startswith("https://") and "*" not in origin
                    for origin in self.allowed_origins_list
                ),
            }
            for message, valid in checks.items():
                if not valid:
                    raise ValueError(message + " in production")
            for name in ("secret_key", "jwt_secret_key", "meta_webhook_hmac_secret", "field_encryption_key",
                         "clerk_secret_key", "clerk_webhook_secret", "postgres_password", "redis_password"):
                value = getattr(self, name).strip()
                if not value or value.lower() in {"mock", "changeme", "change_me"} or "<REQUIRED" in value.upper():
                    raise ValueError(name.upper() + " must not be a placeholder in production")
            if self.feature_voice_notes_enabled and self.elevenlabs_api_key.strip().lower() in {"", "mock"}:
                raise ValueError("Disable voice notes or configure ELEVENLABS_API_KEY in production")
            provider_keys = {"openai": self.openai_api_key, "openrouter": self.openrouter_api_key, "groq": self.groq_api_key}
            if self.llm_primary_provider not in provider_keys:
                raise ValueError("The gateway requires an OpenAI-compatible chat provider in production")
            chat_key = (self.llm_api_key or provider_keys[self.llm_primary_provider]).strip()
            if not chat_key or chat_key.lower() == "mock" or "<REQUIRED" in chat_key.upper():
                raise ValueError("A real key for the selected chat provider is required in production")
        return self

    @property
    def effective_gemini_api_key(self) -> str:
        """
        Resolve the Gemini credential.

        Preference order:
          1. GEMINI_API_KEY   (canonical)
          2. GOOGLE_AI_API_KEY (legacy name still used by the LLM-invoker worker)

        The literal value "mock" is treated as *not configured* so that mock
        mode never attempts a live API call.
        """
        for candidate in (self.gemini_api_key, self.google_ai_api_key):
            value = (candidate or "").strip()
            if value and value.lower() != "mock":
                return value
        return ""

    @property
    def embedding_provider(self) -> str:
        """"openai" when a real key is configured, else "fastembed" (local,
        no API key, no network dependency beyond the one-time model download)."""
        key = (self.openai_api_key or "").strip().lower()
        return "openai" if key and key != "mock" else "fastembed"

    @property
    def embedding_dim(self) -> int:
        """Vector size for the active embedding provider — Qdrant collections
        must be created with this size, and it changes if the provider does."""
        return self.embedding_model_dim if self.embedding_provider == "openai" else self.embedding_local_model_dim

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.
    Cached for the lifetime of the process.
    Use dependency injection in FastAPI: Depends(get_settings).
    """
    return Settings()
