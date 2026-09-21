"""channel_adapters/whatsapp/__init__.py — WhatsApp Cloud API Adapter

Modules:
  client.py  — Async WhatsApp Cloud API sender (httpx)
  router.py  — FastAPI webhook receiver (requires fastapi)
  security.py — HMAC signature verification
"""
# Intentionally no top-level imports: router.py requires FastAPI and is
# registered via the gateway app factory, not imported by AI workers.

