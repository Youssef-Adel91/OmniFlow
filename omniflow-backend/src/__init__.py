"""
OmniFlow AI — Enterprise Real Estate Digital Twin
Backend Monorepo — Sprint 0 Scaffold

Directory Structure:
--------------------
omniflow-backend/
├── pyproject.toml               ← All dependencies + tool config
├── docker-compose.yml           ← Local dev data plane
├── .env.example                 ← All env var templates
├── .gitignore
├── README.md
│
├── src/                         ← All application source code
│   │
│   ├── gateway/                 ← Omnichannel API Gateway (FastAPI)
│   │   Exposes: Webhook receivers, REST API, SSE streaming, Admin routes
│   │
│   ├── channel_adapters/        ← Ingestion microservices (Stateless)
│   │   Receives webhooks, normalizes to canonical events, publishes to Kafka.
│   │   One adapter per channel: WhatsApp, TikTok, Instagram, X, Snapchat, Web.
│   │
│   ├── ai_workers/              ← Async Kafka consumers — AI Processing
│   │   Semantic Router, RAG Engine, LLM Invoker, AI Gateway guardrails,
│   │   Multimodal Pipeline (Voice + Vision), Outbound Dispatcher.
│   │
│   ├── vault_workers/           ← Reports Vault Worker
│   │   PDF generation, digital signing, S3 storage, Qdrant indexing.
│   │
│   ├── broadcast_workers/       ← VIP Broadcast Engine Worker
│   │   Campaign execution, Meta Template API, delivery tracking.
│   │
│   ├── notification_svc/        ← SSE/WebSocket notification hub
│   │   Bridges Kafka events to connected B2B Dashboard browsers.
│   │
│   ├── celery_app/              ← Celery tasks & schedules
│   │   SLA timers, VCard reminders, VIP follow-ups, REGA re-verification.
│   │
│   └── shared/                  ← Core shared modules (imported by ALL services)
│       ├── core/                ← Settings, enums, base models
│       ├── db/                  ← SQLAlchemy engine, sessions, RLS middleware
│       ├── models/              ← SQLAlchemy ORM models (all tables)
│       ├── schemas/             ← Pydantic v2 schemas (request/response/events)
│       ├── events/              ← Canonical event types (Kafka messages)
│       ├── kafka/               ← Kafka producer/consumer base classes
│       ├── redis_client/        ← Redis connection factory & helpers
│       ├── qdrant_client/       ← Qdrant connection & collection manager
│       ├── storage/             ← S3/MinIO client abstraction
│       ├── security/            ← JWT, HMAC, encryption utilities
│       ├── observability/       ← OpenTelemetry setup, structured logging
│       └── utils/               ← Arabic NLP, VCard, distance, etc.
│
├── tests/                       ← Test suite (mirrors src/ structure)
├── infra/                       ← Local dev infrastructure configs
├── migrations/                  ← Alembic database migrations
└── scripts/                     ← Dev helper scripts
"""
