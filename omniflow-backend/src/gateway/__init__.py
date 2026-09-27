"""
gateway/__init__.py — Omnichannel API Gateway

Exposes:
  - Webhook receivers (WhatsApp, Instagram). TikTok/X/Snapchat are explicitly
    out of scope for v1 -- channel_adapters/{tiktok,snapchat}/ are empty
    Sprint-0 stubs with no registered routes, no webhook, no adapter logic.
    See IMPLEMENTATION_STATUS.md for the scope decision.
  - REST API (B2B Dashboard, B2C Web)
  - SSE streaming endpoint for real-time Dashboard
  - Admin routes (tenant management, platform health)

Ref: SRS §2.8.3 — GET /api/v1/stream/dashboard?token=<JWT>
"""
