"""
gateway/__init__.py — Omnichannel API Gateway

Exposes:
  - Webhook receivers (WhatsApp, TikTok, Instagram, X, Snapchat)
  - REST API (B2B Dashboard, B2C Web)
  - SSE streaming endpoint for real-time Dashboard
  - Admin routes (tenant management, platform health)

Ref: SRS §2.8.3 — GET /api/v1/stream/dashboard?token=<JWT>
"""
