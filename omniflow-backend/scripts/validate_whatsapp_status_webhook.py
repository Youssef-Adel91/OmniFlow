"""
Real end-to-end validation of the WhatsApp delivery-status webhook gap
found via a real live test: sending a real outbound message through the
real WhatsAppClient succeeded at the Graph API (200, a real wamid), but
never arrived — Meta's real status webhook for that message (error 131047,
outside the 24h customer-service window) was completely ignored, because
`_process_whatsapp_value()` only ever read `value["messages"]`, never
`value["statuses"]`. A failed send was invisible: the message just sat at
whatever status it started at, forever.

This exercises the REAL `_process_whatsapp_value()` function (the exact
one Meta's real webhook POSTs are routed to) against a real Postgres
message row, with a status payload shaped exactly like Meta's real
documented "failed" status webhook — not a reimplementation of the fix.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.channel_adapters.whatsapp.router import _process_whatsapp_value
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel
from src.shared.db.models import Conversation, Customer, Message, Tenant
from src.shared.db.persistence import persist_outbound_message
from src.shared.db.session import get_system_session, get_tenant_session

settings = get_settings()


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


async def _setup() -> dict:
    tenant_id, customer_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    phone_number_id = f"drill-{uuid.uuid4().hex[:10]}"
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Status Webhook Drill",
            fal_license_number=f"SW-{uuid.uuid4().hex[:10]}",
            whatsapp_phone_number_id=phone_number_id,
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Status Webhook Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id="status-webhook-drill",
        ))
    return {
        "tenant_id": tenant_id, "customer_id": customer_id,
        "conversation_id": conversation_id, "phone_number_id": phone_number_id,
    }


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete

    async with get_system_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/conversation/message deleted")


async def main() -> None:
    _require_local()
    ctx = await _setup()
    wamid = f"wamid.DRILL{uuid.uuid4().hex}"
    print(f"synthetic tenant={ctx['tenant_id']} wamid={wamid}")
    try:
        # ── Persist a real outbound message the way outbound_dispatcher does ──
        message_id = await persist_outbound_message(
            tenant_id=ctx["tenant_id"], conversation_id=ctx["conversation_id"],
            text="مرحباً من نعيم! رسالة اختبار.", platform_message_id=wamid,
            delivery_status="SENT",
        )
        assert message_id is not None
        print(f"PASS: real outbound message persisted, delivery_status=SENT, message_id={message_id}")

        # ── Real Meta "failed" status webhook shape (documented format) ──────
        status_value = {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": ctx["phone_number_id"], "display_phone_number": "+1 555 000 0000"},
            "statuses": [
                {
                    "id": wamid,
                    "status": "failed",
                    "timestamp": "1790000000",
                    "recipient_id": "201002804304",
                    "errors": [
                        {"code": 131047, "title": "Re-engagement message",
                         "message": "More than 24 hours have passed since the customer last replied to this number."},
                    ],
                }
            ],
        }

        # Call the REAL webhook processing function — the exact one Meta's
        # real POST /api/v1/webhooks/whatsapp is routed to.
        await _process_whatsapp_value(status_value)

        # ── Verify the real DB row was actually updated ──────────────────────
        async with get_tenant_session(ctx["tenant_id"]) as session:
            msg = await session.get(Message, message_id)
            assert msg.delivery_status == "FAILED", f"expected FAILED, got {msg.delivery_status!r}"
            assert msg.failure_reason and "131047" in msg.failure_reason, (
                f"expected the real error code in failure_reason, got {msg.failure_reason!r}"
            )
            print(f"PASS: real DB row updated — delivery_status=FAILED, failure_reason={msg.failure_reason!r}")

        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())
