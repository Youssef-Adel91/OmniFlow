"""channel_adapters/__init__.py — Ingestion Microservices (Stateless)"""
# One adapter per channel: WhatsApp, TikTok, Instagram, X, Snapchat, Web.
# Each receives webhooks, verifies HMAC, normalizes to CanonicalInboundEvent,
# publishes to Kafka messages.incoming.v1, returns HTTP 200 OK in < 200ms.
# Ref: SRS §2.3 Steps 1–3
