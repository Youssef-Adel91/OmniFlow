"""channel_adapters/instagram/__init__.py — Meta Instagram & Facebook Adapter

Handles three Meta Graph API channels through the unified /page webhook:
  - Facebook Messenger (private DMs via messaging events)
  - Instagram Direct Messages (private DMs via messaging events)
  - Page/Feed Comments (public comments via feed changes)

Modules:
  client.py   — Async Meta Graph API sender (httpx)
  router.py   — FastAPI webhook receiver (GET verify + POST ingest)
"""
# Intentionally no top-level imports: router.py requires FastAPI and is
# registered via the gateway app factory, not imported by AI workers.
