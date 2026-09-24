"""shared/events/broadcast.py — broadcast dispatch event contract.

Published to `broadcast.marketing.v1` by the Celery Beat sweep
(`omniflow.broadcast_dispatch_check`) once a campaign's `scheduled_at` has
arrived and it has passed the Meta template-approval gate. Consumed by
`BroadcastWorker`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


class BroadcastEvent(BaseModel):
    """Signal to send a scheduled campaign now. Carries no recipient data —
    the worker re-reads the campaign and resolves its audience fresh at send
    time, so the send always reflects the current customer list/opt-outs."""

    campaign_id: uuid.UUID
    tenant_id: uuid.UUID
    dispatched_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
