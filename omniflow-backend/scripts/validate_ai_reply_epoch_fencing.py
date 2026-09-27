"""
Real validation of the ai_reply_epoch fencing token (migration
0017_conv_ai_reply_epoch), added to close one specific hole in the
AI-reply-after-takeover mitigation flagged as an open risk in
IMPLEMENTATION_STATUS.md / LAUNCH_READINESS_PROMPT item 13.

The exact scenario this closes, proven end-to-end here, not just asserted:
a human takes over a conversation, acts, and returns it to AI -- all while
an AI reply generated *before* the takeover is still working its way
through the pipeline. By the time that stale reply reaches
outbound_dispatcher's pre-send check, `Conversation.status` reads
AI_ACTIVE again (return_to_ai put it back), so a plain status check alone
is blind to the fact that a human already handled this conversation in
the meantime. `ai_reply_epoch` is different: repository.assign_agent
bumps it atomically on takeover, and return_to_ai never resets it, so a
stale captured epoch still correctly fails to match.

This script proves, against real Postgres, through the real production
entry points (not reimplementations):
  1. WITHOUT any takeover: a captured epoch of 0 matches the live value of
     0, and the real OutboundDispatcherWorker.process_message() correctly
     dispatches (mocked WhatsApp send IS invoked).
  2. Exact repro of the hole this closes: after the epoch is captured (0),
     a real takeover (repository.assign_agent) then a real return-to-ai
     (repository.update_status) run -- status ends back at AI_ACTIVE,
     epoch ends at 1. Feeding an OutboundMessage with the STALE captured
     epoch (0) into the real dispatcher must NOT invoke the mocked
     WhatsApp send, and the message must end up FAILED, not SENT.
  3. Regression proof, not just assertion: re-ran scenario 2's exact
     stale message with `ai_reply_epoch=None`, which forces the
     dispatcher down its fallback branch -- the plain `is_human_active`
     (status-only) check that is *all* this code path had before
     migration 0017. Confirmed that branch incorrectly WOULD have sent
     (mocked WhatsApp call invoked), proving the epoch check in scenario
     2 is what actually caught the stale reply, not some unrelated guard.

Only the WhatsApp HTTP call is mocked (no live Meta credentials in this
container, same disclosed limitation as every other channel validator
here). Everything else -- Postgres rows, the real ConversationRepository
atomic UPDATEs, the real OutboundDispatcherWorker.process_message() -- is
real.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_workers.outbound_dispatcher import worker as dispatcher_module
from src.ai_workers.outbound_dispatcher.worker import OutboundDispatcherWorker
from src.channel_adapters.whatsapp.client import SendResult
from src.shared.core.config import get_settings
from src.shared.core.enums import ConversationStatus, TenantUserRole
from src.shared.db.models import Conversation, Customer, Tenant, TenantUser
from src.shared.db.repository import ConversationRepository
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.events.outbound import OutboundMessage
from src.shared.redis_client.client import redis_mgr

settings = get_settings()


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


def _record(value: bytes):
    return SimpleNamespace(value=value, key=b"test-key", topic="messages.outgoing.v1", partition=0, offset=1)


async def _setup() -> dict:
    tenant_id, customer_id, conversation_id, agent_id = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
    )
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Fencing Drill Co",
            fal_license_number=f"FENCE-{uuid.uuid4().hex[:10]}",
            whatsapp_phone_number_id="000000000000000",
            meta_access_token="drill-dummy-token",
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Fencing Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel="whatsapp", platform_conversation_id="fencing-drill",
            status=ConversationStatus.AI_ACTIVE,
        ))
        session.add(TenantUser(
            user_id=agent_id, tenant_id=tenant_id,
            full_name="Fencing Drill Agent", email=f"fencing-agent-{agent_id.hex[:8]}@example.com",
            hashed_password="x", role=TenantUserRole.AGENT,
        ))
    return {"tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id, "agent_id": agent_id}


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete
    async with get_system_session() as session:
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(TenantUser).where(TenantUser.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/agent/conversation deleted")


def _outbound_message(ctx: dict, epoch: int | None) -> OutboundMessage:
    return OutboundMessage(
        tenant_id=ctx["tenant_id"], conversation_id=ctx["conversation_id"],
        customer_phone="+15551234567", platform_conversation_id="15551234567",
        text="stale AI reply generated before the takeover", sender_type="ai_bot",
        source_event_id=uuid.uuid4(), routing_tier_used="L1", model_used="drill",
        ai_reply_epoch=epoch,
    )


async def main() -> None:
    _require_local()
    ctx = await _setup()
    print(f"synthetic tenant={ctx['tenant_id']} conversation={ctx['conversation_id']}")

    sent_calls: list[str] = []

    async def _mock_send_text(*, phone_number_id: str, to: str, text: str, access_token: str):
        sent_calls.append(text)
        return SendResult(wamid=f"wamid.mock.{uuid.uuid4().hex[:8]}", phone_number=to, message_status="accepted")

    original_send = dispatcher_module.whatsapp_client.send_text_message
    dispatcher_module.whatsapp_client.send_text_message = _mock_send_text

    worker = OutboundDispatcherWorker()
    await redis_mgr.start()
    try:
        print("\n=== 1. No takeover: captured epoch (0) matches live epoch (0) -> dispatches ===")
        sent_calls.clear()
        msg = _outbound_message(ctx, epoch=0)
        await worker.process_message(_record(msg.model_dump_json().encode("utf-8")))
        assert sent_calls, "expected the mocked WhatsApp send to be invoked when no takeover happened"
        print("PASS: dispatched normally when the epoch is unchanged")

        print("\n=== 2. Exact repro: takeover THEN return-to-ai, all after epoch was captured ===")
        async with get_system_session() as session:
            repo = ConversationRepository(session)
            await repo.assign_agent(conversation_id=ctx["conversation_id"], agent_id=ctx["agent_id"])
        async with get_system_session() as session:
            repo = ConversationRepository(session)
            await repo.update_status(
                conversation_id=ctx["conversation_id"], status=ConversationStatus.AI_ACTIVE.value,
                is_ai_active=True,
            )

        async with get_tenant_session(ctx["tenant_id"]) as session:
            conv = await session.get(Conversation, ctx["conversation_id"])
            assert str(conv.status).lower() == ConversationStatus.AI_ACTIVE, (
                f"expected status back to ai_active after return_to_ai, got {conv.status!r}"
            )
            assert conv.ai_reply_epoch == 1, (
                f"expected ai_reply_epoch to have been bumped to 1 by the takeover and NOT reset "
                f"by return_to_ai, got {conv.ai_reply_epoch!r}"
            )
        print("PASS: status is back to AI_ACTIVE (confirming a plain status check would see nothing "
              "wrong), but ai_reply_epoch is now 1 -- permanently different from what was captured")

        sent_calls.clear()
        await redis_mgr.get_client()  # ensure connected before clearing any stale idem key
        stale_msg = _outbound_message(ctx, epoch=0)  # the reply's captured epoch, from before the takeover
        await worker.process_message(_record(stale_msg.model_dump_json().encode("utf-8")))
        assert not sent_calls, (
            "REGRESSION: the stale reply (captured epoch=0) was sent even though a real takeover "
            "(now at epoch=1) happened after it was captured -- the fencing token failed to catch "
            "the exact takeover-then-return-to-AI scenario it exists for"
        )

        from src.shared.db.models import Message
        async with get_tenant_session(ctx["tenant_id"]) as session:
            persisted = await session.get(Message, stale_msg.message_id)
            assert persisted is not None, "expected the message row to have been persisted before the fenced abort"
            assert persisted.delivery_status == "FAILED", (
                f"expected delivery_status=FAILED for a fenced-out stale reply, got {persisted.delivery_status!r}"
            )
        print("PASS: the stale reply was correctly cancelled (no WhatsApp send, DB row FAILED) even "
              "though Conversation.status alone reads AI_ACTIVE again")

        print("\n=== 3. Regression proof: without the epoch check, this WOULD have sent ===")
        # Simulate the pre-fix world: force the "no fencing token" branch
        # (ai_reply_epoch=None), which is exactly the plain status-only
        # check that shipped before migration 0017. Re-run scenario 2's
        # exact stale message through that code path.
        sent_calls.clear()
        stale_msg_2 = _outbound_message(ctx, epoch=0)
        # Force the "no fencing token" branch by pretending this message
        # predates the migration -- reproduces the pre-fix code path exactly.
        stale_msg_2.ai_reply_epoch = None
        await worker.process_message(_record(stale_msg_2.model_dump_json().encode("utf-8")))
        assert sent_calls, (
            "expected the pre-fix (status-only) code path to incorrectly send this stale reply -- "
            "if it did NOT send, the regression proof is invalid (something else is blocking it)"
        )
        print("PASS: confirmed the pre-fix status-only check alone WOULD have sent this exact stale "
              "reply (status reads AI_ACTIVE again) -- proving the epoch check in scenario 2 is what "
              "actually caught it, not some unrelated guard")

        print("\nALL SCENARIOS PASSED")
    finally:
        dispatcher_module.whatsapp_client.send_text_message = original_send
        await redis_mgr.stop()
        await _cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())
