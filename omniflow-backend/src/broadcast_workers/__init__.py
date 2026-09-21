"""broadcast_workers/__init__.py — VIP Broadcast Engine Worker (Sprint 0 stub)

Ref: SRS §5.6 — VIP Broadcast Engine & Opt-in Infrastructure
Responsibilities:
  - Audience segmentation from vip_subscribers table
  - Compliance check (3 msg/week limit, template validation)
  - Rate-limited Meta Marketing Template API calls (50 msg/sec/tenant)
  - Delivery tracking via Meta webhooks
  - Opt-out handling (immediate, audit logged)
  - Campaign analytics → ClickHouse events_raw
"""
