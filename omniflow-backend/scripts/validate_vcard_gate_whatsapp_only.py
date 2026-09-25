"""
Real validation that the VCard gate only fires for WhatsApp.

Found during a P0 audit (real inbound WhatsApp messages getting no reply /
a raw .vcf instead of AI text): `semantic_router`'s VCard-gate condition had
no channel check at all. A non-WhatsApp customer's genuinely first message
(vcard_state == NEW) was routed to `vcard_gatekeeper` anyway, which builds a
WhatsApp-format VCard that `outbound_dispatcher` then rejects outright
(`msg.channel != Channel.WHATSAPP` -> raise) — a guaranteed dead end, and
with `skip_llm=True` the customer never even got an LLM reply attempt.

This calls the real `SemanticRouterWorker._make_routing_decision()` (not a
reimplementation) with a real `CustomerVCardState.NEW` session for two
channels. No Kafka/DB needed — this method takes its inputs directly.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_workers.semantic_router.worker import SemanticRouterWorker
from src.shared.core.enums import Channel, CustomerVCardState, MessageType, RoutingTier
from src.shared.events.canonical import CanonicalInboundEvent


def _event(channel: Channel) -> CanonicalInboundEvent:
    return CanonicalInboundEvent(
        tenant_id=uuid.uuid4(), channel=channel,
        platform_message_id=f"msg-{uuid.uuid4().hex[:8]}",
        platform_conversation_id="gate-drill",
        platform_user_id="drill-user", customer_phone="+15550000000",
        message_type=MessageType.TEXT, text_content="هل عندكم توصيل؟",
        vcard_state=CustomerVCardState.NEW,
    )


async def main() -> None:
    worker = SemanticRouterWorker.__new__(SemanticRouterWorker)  # skip Kafka/Redis startup, not needed
    from src.ai_workers.semantic_router.classifier import SemanticClassifier
    worker._classifier = SemanticClassifier()

    print("=== WhatsApp, vcard_state=NEW: must be gated ===")
    decision = await worker._make_routing_decision(
        _event(Channel.WHATSAPP), uuid.uuid4(), {"vcard_state": CustomerVCardState.NEW},
    )
    assert decision.target_tier == RoutingTier.VCARD_GATEKEEPER, (
        f"expected WhatsApp's first message gated to VCARD_GATEKEEPER, got {decision.target_tier}"
    )
    assert decision.skip_llm is True
    print(f"PASS: real routing decision = {decision.target_tier}, route_reason={decision.route_reason!r}")

    print("\n=== Instagram, vcard_state=NEW: must NOT be gated (would dead-end at outbound_dispatcher) ===")
    decision = await worker._make_routing_decision(
        _event(Channel.INSTAGRAM), uuid.uuid4(), {"vcard_state": CustomerVCardState.NEW},
    )
    assert decision.target_tier != RoutingTier.VCARD_GATEKEEPER, (
        f"Instagram's first message must not be routed to the WhatsApp-only VCard gate, got {decision.target_tier}"
    )
    print(f"PASS: real routing decision = {decision.target_tier} (reaches the normal AI path instead), route_reason={decision.route_reason!r}")

    print("\nALL SCENARIOS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
