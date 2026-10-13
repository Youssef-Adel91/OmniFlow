# Environment variable reference

Generated from `omniflow-backend/src/shared/core/config.py` (the `Settings` class) and the frontend's `process.env` usage. **Names and types only — no values.** `secret` = never commit, never log, rotate on leak. Flags default to the safe value (off) unless the Notes column says otherwise; defaults live in `config.py` and `.env.example`.

## Backend / workers (`omniflow-backend`)

| Variable | Kind | Required | In `.env.example` | Notes |
|---|---|---|---|---|
| `AI_CONFIDENCE_ESCALATION_THRESHOLD` | float | optional | yes |  |
| `AI_MARGIN_ALERT_PCT` | int | optional | yes |  |
| `ALLOWED_ORIGINS` | str | optional | yes |  |
| `API_BASE_URL` | AnyHttpUrl | optional | yes |  |
| `APP_DEBUG` | flag | optional | yes |  |
| `APP_ENV` | Literal | optional | yes |  |
| `APP_HOST` | str | optional | yes |  |
| `APP_LOG_FORMAT` | Literal | optional | yes |  |
| `APP_LOG_LEVEL` | Literal | optional | yes |  |
| `APP_NAME` | str | optional | yes |  |
| `APP_PORT` | int | optional | yes |  |
| `APP_TIMEZONE` | str | optional | yes |  |
| `APP_VERSION` | str | optional | yes |  |
| `APP_WORKERS` | int | optional | yes |  |
| `AWS_ACCESS_KEY_ID` | str | optional | yes |  |
| `AWS_REGION` | str | optional | yes |  |
| `AWS_SECRET_ACCESS_KEY` | secret | optional | yes |  |
| `BILLING_ADMIN_API_KEY` | secret | optional | yes |  |
| `BILLING_AUTO_TOPUP_MAX_PER_MONTH` | int | optional | yes |  |
| `BILLING_ENFORCE_LIMITS` | flag | optional | yes |  |
| `BILLING_ENFORCE_MESSAGE_LIMIT` | flag | optional | yes |  |
| `BILLING_GATEWAY_FEE_BP` | int | optional | no |  |
| `BILLING_GATEWAY_FEE_FIXED_HALALAS` | int | optional | no |  |
| `BILLING_GATE_ENABLED` | flag | optional | no |  |
| `BILLING_GRACE_DAYS` | int | optional | yes |  |
| `BILLING_REFUND_WINDOW_DAYS` | int | optional | no |  |
| `BILLING_RETRY_INTERVAL_HOURS` | int | optional | no |  |
| `BILLING_RETRY_MAX` | int | optional | no |  |
| `BILLING_SELLER_ADDRESS` | str | optional | yes |  |
| `BILLING_SELLER_BUILDING` | str | optional | no |  |
| `BILLING_SELLER_CITY` | str | optional | no |  |
| `BILLING_SELLER_CR_NUMBER` | str | optional | no |  |
| `BILLING_SELLER_DISTRICT` | str | optional | no |  |
| `BILLING_SELLER_NAME` | str | optional | yes |  |
| `BILLING_SELLER_POSTAL_ZONE` | str | optional | no |  |
| `BILLING_SELLER_STREET` | str | optional | no |  |
| `BILLING_SELLER_TAX_NUMBER` | str | optional | yes |  |
| `BILLING_TRIAL_DAYS` | int | optional | yes |  |
| `BILLING_VAT_RATE_BP` | int | optional | yes |  |
| `CLERK_API_BASE` | str | optional | no |  |
| `CLERK_ISSUER_URL` | str | optional | no |  |
| `CLERK_PUBLISHABLE_KEY` | secret | optional | yes |  |
| `CLERK_SECRET_KEY` | secret | optional | yes |  |
| `CLERK_WEBHOOK_SECRET` | secret | optional | yes | Clerk/Svix webhook signing secret — env: CLERK_WEBHOOK_SECRET |
| `CLICKHOUSE_DB` | str | optional | yes |  |
| `CLICKHOUSE_HOST` | str | optional | yes |  |
| `CLICKHOUSE_HTTP_PORT` | int | optional | yes |  |
| `CLICKHOUSE_NATIVE_PORT` | int | optional | yes |  |
| `CLICKHOUSE_PASSWORD` | secret | required | yes |  |
| `CLICKHOUSE_SECURE` | flag | optional | yes |  |
| `CLICKHOUSE_USER` | str | optional | yes |  |
| `CONVERSATION_CONTEXT_TTL` | int | optional | yes |  |
| `EINVOICE_ALLOW_PRODUCTION_SUBMIT` | flag | optional | no |  |
| `EINVOICE_BASE_URL` | str | optional | no |  |
| `EINVOICE_ENABLED` | flag | optional | no |  |
| `EINVOICE_ENV` | Literal | optional | no |  |
| `EINVOICE_MAX_ATTEMPTS` | int | optional | no |  |
| `ELEVENLABS_API_KEY` | secret | optional | no |  |
| `ELEVENLABS_VOICE_ID` | str | optional | no |  |
| `EMAIL_FROM` | str | optional | yes |  |
| `EMAIL_PROVIDER` | str | optional | yes |  |
| `EMBEDDING_LOCAL_MODEL` | str | optional | no |  |
| `EMBEDDING_LOCAL_MODEL_DIM` | int | optional | no |  |
| `EMBEDDING_MODEL` | str | optional | yes |  |
| `EMBEDDING_MODEL_DIM` | int | optional | no |  |
| `ESCALATION_MANAGER_ALERT_MINUTES` | int | optional | yes |  |
| `ESCALATION_SLA_MINUTES` | int | optional | yes |  |
| `FEATURE_CLICKHOUSE_ANALYTICS` | flag | optional | yes |  |
| `FEATURE_HUMAN_TAKEOVER` | flag | optional | yes |  |
| `FEATURE_MULTIMODAL_VISION` | flag | optional | yes |  |
| `FEATURE_MULTIMODAL_VOICE` | flag | optional | yes |  |
| `FEATURE_REGA_VERIFICATION` | flag | optional | yes |  |
| `FEATURE_REPORTS_VAULT` | flag | optional | yes |  |
| `FEATURE_SEMANTIC_CACHE` | flag | optional | yes |  |
| `FEATURE_SOCIAL_COMMENT_REPLIES` | flag | optional | yes |  |
| `FEATURE_VCARD_GATEKEEPER` | flag | optional | yes |  |
| `FEATURE_VIP_BROADCAST` | flag | optional | yes |  |
| `FEATURE_VOICE_NOTES_ENABLED` | flag | optional | no |  |
| `FEATURE_WHATSAPP_EMBEDDED_SIGNUP` | flag | optional | yes |  |
| `FIELD_ENCRYPTION_KEY` | secret | required | yes |  |
| `FRONTEND_URL` | AnyHttpUrl | optional | yes |  |
| `GEMINI_API_KEY` | secret | optional | yes |  |
| `GEMINI_L1_MODEL` | str | optional | yes |  |
| `GEMINI_L3_MODEL` | str | optional | yes |  |
| `GOOGLE_AI_API_KEY` | secret | optional | yes | Google Gemini API key — env: GOOGLE_AI_API_KEY |
| `GROQ_API_KEY` | secret | optional | no |  |
| `IDEMPOTENCY_KEY_TTL` | int | optional | yes |  |
| `IMPORT_BATCH_SIZE` | int | optional | no |  |
| `IMPORT_MAX_FILE_MB` | int | optional | yes |  |
| `IMPORT_MAX_ROWS` | int | optional | no |  |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | int | optional | yes |  |
| `JWT_ALGORITHM` | str | optional | yes |  |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | int | optional | yes |  |
| `JWT_SECRET_KEY` | secret | required | yes |  |
| `KAFKA_BOOTSTRAP_SERVERS` | str | optional | yes |  |
| `KAFKA_CONSUMER_GROUP_AI_WORKERS` | str | optional | yes |  |
| `KAFKA_CONSUMER_GROUP_BROADCAST_WORKERS` | str | optional | yes |  |
| `KAFKA_CONSUMER_GROUP_INGESTION` | str | optional | yes |  |
| `KAFKA_CONSUMER_GROUP_NOTIFICATION_SVC` | str | optional | yes |  |
| `KAFKA_CONSUMER_GROUP_VAULT_WORKERS` | str | optional | yes |  |
| `KAFKA_CONSUMER_GROUP_VCARD_GATEKEEPER` | str | optional | no |  |
| `KAFKA_SECURITY_PROTOCOL` | str | optional | yes |  |
| `KAFKA_TOPIC_ANALYTICS_EVENTS` | str | optional | yes |  |
| `KAFKA_TOPIC_BROADCAST_MARKETING` | str | optional | yes |  |
| `KAFKA_TOPIC_CONVERSATIONS_UPDATES` | str | optional | yes |  |
| `KAFKA_TOPIC_LLM_ROUTING` | str | optional | yes |  |
| `KAFKA_TOPIC_MESSAGES_INCOMING` | str | optional | yes |  |
| `KAFKA_TOPIC_MESSAGES_OUTGOING` | str | optional | yes |  |
| `KAFKA_TOPIC_MESSAGES_OUTGOING_HUMAN` | str | optional | yes |  |
| `KAFKA_TOPIC_MULTIMODAL_AUDIO` | str | optional | yes |  |
| `KAFKA_TOPIC_MULTIMODAL_VISION` | str | optional | yes |  |
| `KAFKA_TOPIC_PAYMENT_EVENTS` | str | optional | yes |  |
| `KAFKA_TOPIC_VAULT_RETRIEVAL` | str | optional | yes |  |
| `KAFKA_TOPIC_VCARD_GATEKEEPER` | str | optional | no |  |
| `KMS_DEV_MOCK_KEY` | secret | optional | yes |  |
| `LLM_API_KEY` | secret | optional | no | API key for the selected provider. Priority: LLM_API_KEY env var → OPENROUTER_API_KEY env  |
| `LLM_BASE_URL` | str | optional | no | Base URL for OpenAI-compatible API (OpenRouter or Groq). Set to https://api.openai.com/v1  |
| `LLM_DEFAULT_COST_SAR_PER_REPLY` | float | optional | yes |  |
| `LLM_FREE_ONLY` | flag | optional | no |  |
| `LLM_HISTORY_MAX_MESSAGES` | int | optional | yes |  |
| `LLM_L1_MODEL` | str | optional | yes |  |
| `LLM_L3_MAX_SHARE_PCT` | int | optional | yes |  |
| `LLM_L3_MODEL` | str | optional | yes |  |
| `LLM_MAX_OUTPUT_TOKENS` | int | optional | yes |  |
| `LLM_PRICES_JSON` | str | optional | yes |  |
| `LLM_PRIMARY_PROVIDER` | Literal | optional | yes |  |
| `LLM_RAG_MAX_CHARS` | int | optional | yes |  |
| `MEDIA_IMAGE_RETENTION_DAYS` | int | optional | yes |  |
| `MEDIA_VOICE_RETENTION_HOURS` | int | optional | yes |  |
| `META_APP_ID` | str | optional | yes |  |
| `META_APP_SECRET` | secret | optional | yes |  |
| `META_ES_CONFIG_ID` | str | optional | yes |  |
| `META_GRAPH_API_VERSION` | str | optional | yes |  |
| `META_INSTAGRAM_PAGE_ACCESS_TOKEN` | secret | optional | yes | Meta Page Access Token — env: META_INSTAGRAM_PAGE_ACCESS_TOKEN |
| `META_INSTAGRAM_PAGE_ID` | str | optional | yes |  |
| `META_INSTAGRAM_VERIFY_TOKEN` | secret | optional | yes |  |
| `META_OAUTH_REDIRECT_URI` | str | optional | yes | OAuth callback URL — env: META_OAUTH_REDIRECT_URI. Must be registered in the Meta App's Fa |
| `META_OAUTH_SCOPES` | str | optional | yes |  |
| `META_WEBHOOK_HMAC_SECRET` | secret | required | yes |  |
| `META_WHATSAPP_ACCESS_TOKEN` | secret | optional | yes |  |
| `META_WHATSAPP_BROADCAST_RATE_LIMIT` | int | optional | yes |  |
| `META_WHATSAPP_BUSINESS_ACCOUNT_ID` | str | optional | yes |  |
| `META_WHATSAPP_PHONE_NUMBER_ID` | str | optional | yes |  |
| `META_WHATSAPP_RATE_LIMIT_PER_SECOND` | int | optional | yes |  |
| `META_WHATSAPP_WEBHOOK_VERIFY_TOKEN` | secret | optional | yes |  |
| `MINIO_ACCESS_KEY` | secret | optional | no |  |
| `MINIO_BUCKET_NAME` | str | optional | no |  |
| `MINIO_ENDPOINT` | str | optional | no |  |
| `MINIO_SECRET_KEY` | secret | optional | no |  |
| `MODEL_L1` | str | optional | no | L1 triage model — simple FAQs / greetings. |
| `MODEL_L1_OVERRIDE` | str | optional | no | Override L1 model identifier. |
| `MODEL_L2` | str | optional | no | L2 RAG-augmented model — moderate complexity. |
| `MODEL_L2_OVERRIDE` | str | optional | no | Override L2 model identifier. |
| `MODEL_L3` | str | optional | no | L3 master agent model — deep consultation / legal. |
| `MODEL_L3_OVERRIDE` | str | optional | no | Override L3 model identifier. |
| `MODEL_ROUTER` | str | optional | no | Default model for intent classification / router tier. |
| `MOYASAR_API_KEY` | secret | optional | yes |  |
| `MOYASAR_BASE_URL` | str | optional | no |  |
| `MOYASAR_PUBLISHABLE_KEY` | secret | optional | yes |  |
| `MOYASAR_SECRET_KEY` | secret | optional | yes |  |
| `MOYASAR_WEBHOOK_SECRET` | secret | optional | yes |  |
| `OPENAI_API_BASE` | str | optional | yes |  |
| `OPENAI_API_KEY` | secret | optional | yes |  |
| `OPENAI_MAX_RETRIES` | int | optional | yes |  |
| `OPENAI_TIMEOUT` | int | optional | yes |  |
| `OPENROUTER_API_KEY` | secret | optional | no |  |
| `OTEL_ENABLED` | flag | optional | yes |  |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | str | optional | yes |  |
| `OTEL_SERVICE_NAME` | str | optional | yes |  |
| `PAYMENT_PROVIDER` | str | optional | yes |  |
| `PGBOUNCER_HOST` | str | optional | yes |  |
| `PGBOUNCER_PORT` | int | optional | yes |  |
| `POSTGRES_DB` | str | optional | yes |  |
| `POSTGRES_HOST` | str | optional | yes |  |
| `POSTGRES_MAX_POOL_SIZE` | int | optional | yes |  |
| `POSTGRES_MIN_POOL_SIZE` | int | optional | yes |  |
| `POSTGRES_PASSWORD` | secret | required | yes |  |
| `POSTGRES_PORT` | int | optional | yes |  |
| `POSTGRES_SSL_MODE` | str | optional | yes |  |
| `POSTGRES_USER` | str | optional | yes |  |
| `PROMETHEUS_ENABLED` | flag | optional | yes |  |
| `PUBLIC_BASE_URL` | str | optional | yes |  |
| `QDRANT_API_KEY` | secret | required | yes |  |
| `QDRANT_GRPC_PORT` | int | optional | yes |  |
| `QDRANT_HOST` | str | optional | yes |  |
| `QDRANT_PORT` | int | optional | yes |  |
| `QDRANT_SEMANTIC_CACHE_COLLECTION` | str | optional | yes |  |
| `QDRANT_SHARED_KNOWLEDGE_COLLECTION` | str | optional | yes |  |
| `QDRANT_TIMEOUT` | int | optional | yes |  |
| `QDRANT_USE_GRPC` | flag | optional | yes |  |
| `RAG_DENSE_TOP_K` | int | optional | yes |  |
| `RAG_MAX_CONTEXT_TOKENS` | int | optional | yes |  |
| `RAG_RERANKED_TOP_K` | int | optional | yes |  |
| `RAG_SPARSE_TOP_K` | int | optional | yes |  |
| `RATE_LIMIT_API_PER_MIN` | int | optional | yes |  |
| `RATE_LIMIT_ENABLED` | flag | optional | yes |  |
| `RATE_LIMIT_LLM_PER_MIN` | int | optional | yes |  |
| `RATE_LIMIT_WEBHOOK_PER_MIN` | int | optional | yes |  |
| `RATE_LIMIT_WINDOW` | int | optional | yes |  |
| `REDIS_DB_CACHE` | int | optional | yes |  |
| `REDIS_DB_CELERY_BROKER` | int | optional | yes |  |
| `REDIS_DB_CELERY_RESULTS` | int | optional | yes |  |
| `REDIS_DB_CONVERSATIONS` | int | optional | yes |  |
| `REDIS_DB_IDEMPOTENCY` | int | optional | yes |  |
| `REDIS_DB_RATE_LIMITING` | int | optional | yes |  |
| `REDIS_HOST` | str | optional | yes |  |
| `REDIS_MAX_CONNECTIONS` | int | optional | yes |  |
| `REDIS_PASSWORD` | secret | required | yes |  |
| `REDIS_PORT` | int | optional | yes |  |
| `REDIS_SSL` | flag | optional | yes |  |
| `REGA_API_BASE_URL` | str | optional | yes |  |
| `REGA_API_KEY` | secret | optional | yes |  |
| `REGA_API_TIMEOUT` | int | optional | yes |  |
| `REGA_VERIFICATION_CACHE_TTL` | int | optional | yes |  |
| `REPORT_DEED_CHECK_PRICE_SAR` | float | optional | yes |  |
| `REPORT_MUNICIPAL_CONSULTING_PRICE_SAR` | float | optional | yes |  |
| `RESEND_API_KEY` | secret | optional | yes |  |
| `RETENTION_API_USAGE_DAYS` | int | optional | yes |  |
| `RETENTION_AUDIT_LOG_DAYS` | int | optional | yes |  |
| `RETENTION_BATCH_LIMIT` | int | optional | yes |  |
| `RETENTION_DELETION_REQUEST_DAYS` | int | optional | yes |  |
| `RETENTION_DRY_RUN` | flag | optional | yes |  |
| `RETENTION_ENABLED` | flag | optional | yes |  |
| `RETENTION_INACTIVE_CUSTOMER_DAYS` | int | optional | yes |  |
| `RETENTION_WARNING_DAYS` | int | optional | yes |  |
| `RETENTION_WEBHOOK_DELIVERY_DAYS` | int | optional | yes |  |
| `S3_ASSETS_BUCKET` | str | optional | yes |  |
| `S3_ENDPOINT_URL` | str | None | optional | yes |  |
| `S3_IMPORTS_BUCKET` | str | optional | yes |  |
| `S3_KNOWLEDGE_BUCKET` | str | optional | yes |  |
| `S3_MEDIA_TEMP_BUCKET` | str | optional | yes |  |
| `S3_SUPPORT_BUCKET` | str | optional | yes |  |
| `S3_USE_PATH_STYLE` | flag | optional | yes |  |
| `S3_VAULT_BUCKET` | str | optional | yes |  |
| `SECRET_KEY` | secret | required | yes |  |
| `SEMANTIC_CACHE_HIT_THRESHOLD` | float | optional | yes |  |
| `SEMANTIC_CACHE_NEAR_HIT_THRESHOLD` | float | optional | yes |  |
| `SENTRY_DSN` | secret | optional | yes |  |
| `SENTRY_ENVIRONMENT` | str | optional | yes |  |
| `SENTRY_PROFILES_SAMPLE_RATE` | float | optional | yes |  |
| `SENTRY_TRACES_SAMPLE_RATE` | float | optional | yes |  |
| `SESSION_STATE_TTL` | int | optional | yes |  |
| `SMTP_HOST` | str | optional | yes |  |
| `SMTP_PASSWORD` | secret | optional | yes |  |
| `SMTP_PORT` | int | optional | yes |  |
| `SMTP_STARTTLS` | flag | optional | no |  |
| `SMTP_USER` | str | optional | yes |  |
| `SOCIAL_COMMENT_RATE_PER_MINUTE` | int | optional | yes |  |
| `SUPPORT_ADMIN_API_KEY` | secret | optional | yes |  |
| `SUPPORT_DOCS_URL` | str | optional | yes |  |
| `SUPPORT_STAFF_EMAILS` | str | optional | yes |  |
| `SUPPORT_TICKETS_PER_HOUR` | int | optional | no |  |
| `SUPPORT_WHATSAPP_NUMBER` | str | optional | yes |  |
| `SUPPORT_ZOOM_URL` | str | optional | yes |  |
| `TAP_SECRET_KEY` | secret | optional | yes |  |
| `TAP_WEBHOOK_SECRET` | secret | optional | yes |  |
| `TEAM_INVITE_TTL_DAYS` | int | optional | no |  |
| `TTS_MAX_CONCURRENT_REQUESTS` | int | optional | no |  |
| `TTS_VOICE_TRIGGER_KEYWORDS` | str | optional | no |  |
| `VAULT_PRESIGNED_URL_TTL_SECONDS` | int | optional | yes |  |
| `VCARD_DORMANT_DELAY_DAYS` | int | optional | yes |  |
| `VCARD_REMINDER_2_DELAY_HOURS` | int | optional | yes |  |
| `VCARD_VALIDATION_TIMEOUT_HOURS` | int | optional | yes |  |
| `VIP_ENGAGEMENT_SCORE_THRESHOLD` | int | optional | yes |  |
| `VIP_MAX_MESSAGES_PER_WEEK` | int | optional | yes |  |
| `VIP_RESUBSCRIBE_COOLDOWN_DAYS` | int | optional | yes |  |
| `VISION_LLM_MODEL` | str | optional | yes |  |
| `WHISPER_API_MODEL` | str | optional | yes |  |
| `WHISPER_MAX_AUDIO_DURATION_SECONDS` | int | optional | yes |  |
| `WHISPER_SELF_HOSTED_ENABLED` | flag | optional | yes |  |
| `WHISPER_SELF_HOSTED_MODEL_SIZE` | str | optional | yes |  |
| `WHISPER_SHORT_AUDIO_THRESHOLD` | int | optional | yes |  |

## Frontend (`omniflow-frontend`)
Build-time variables (`NEXT_PUBLIC_*`) are baked into the bundle: change them → rebuild the image.

| Variable | Kind | Used for |
|---|---|---|
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | public key | Clerk browser SDK (use the **live** instance key in production) |
| `CLERK_SECRET_KEY` | secret | Clerk middleware / server (never `NEXT_PUBLIC_`) |
| `NEXT_PUBLIC_API_URL` | URL | backend API base, e.g. `https://DOMAIN/api/v1` (also drives the CSP `connect-src`) |
| `PUBLIC_BASE_URL` / `NEXT_PUBLIC_BASE_URL` | URL | site origin for `sitemap.xml`, `robots.txt`, Open Graph/canonical links; empty = relative URLs |

Test-only (Playwright): `E2E_BASE_URL`, `E2E_CLERK_EMAIL`, `E2E_CLERK_PASSWORD`, `PW_CHROMIUM_PATH`, `PW_NO_PROXY`, `HEADED`.

## Ops scripts
`DRILL_SOURCE_URL` (restore drill), `META_HMAC_SECRET` + `WA_PHONE_NUMBER_ID` + `LOADTEST_ALLOW_REMOTE` (load test), `OMNIFLOW_TEST_DATABASE_URL` + `OMNIFLOW_TEST_RLS_URL` (DB-backed tests, scratch DB only).

## Production file
`omniflow-deploy/.env.prod.example` is the template for the server; every variable introduced in night 2 was added to it with the safe default.
