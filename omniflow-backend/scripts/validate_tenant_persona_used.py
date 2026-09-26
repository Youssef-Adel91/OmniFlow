"""
Regression test for a real P0 bug: EVERY tenant's real WhatsApp message was
answered using the hardcoded "Ahmad Al-Sayegh, Elite Properties" real-estate
persona (persona.py's DEFAULT_SYSTEM_PROMPT), never the tenant's own real,
correctly-onboarded Tenant.ai_system_prompt.

Root cause (confirmed by code inspection, not guessed): `LLMInvokerWorker.
_get_tenant_context()` only ever read `session_state["business_name"]` (a
Redis session field) to decide whether to build a tenant-specific persona --
and a full-repo grep confirmed NOTHING anywhere in the pipeline (webhook,
semantic_router, conversation_state.py) ever writes that key. So the
"tenant-aware" branch was permanently dead code, and `_build_persona()`
always took the "no business metadata" fast path back to
DEFAULT_SYSTEM_PROMPT -- for every tenant, every message, unconditionally.
This was not a cache/race/cross-tenant-leak bug: it never read the real
per-tenant persona AT ALL in this code path (a separate, unused class,
ai_engine/llm_orchestrator.py, had the correct pattern all along).

This script reproduces the exact failure condition against real Postgres:
a real tenant with a real, distinctive `ai_system_prompt` (like any real
onboarded tenant, e.g. Naeem) and a real `RoutingDecision` whose
`session_state` has no `business_name` key -- exactly what every real
WhatsApp message actually looks like in production. Runs the REAL
`LLMInvokerWorker.process_message()` (not a reimplementation); only the
model call itself is intercepted (not mocked-away) to capture the exact
`system_prompt` string that would have been sent to the LLM.

Before the fix: the captured prompt is the hardcoded real-estate persona
(contains "أحمد الصائغ" / "النخبة العقارية") and does NOT contain the
tenant's real business name -- the customer-facing symptom.
After the fix: the captured prompt contains the tenant's own persona and
does NOT contain the hardcoded real-estate markers.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_workers.llm_invoker import worker as invoker_module
from src.ai_workers.llm_invoker.worker import LLMInvokerWorker
from src.ai_workers.semantic_router.worker import RoutingDecision
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, ConversationStatus, MessageType
from src.shared.db.models import Conversation, Customer, Tenant
from src.shared.db.session import get_system_session
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.redis_client.client import redis_mgr
from src.ai_engine.company_context import invalidate_company_context

settings = get_settings()

_REAL_ESTATE_MARKERS = ("أحمد الصائغ", "النخبة العقارية")
_DISTINCTIVE_PERSONA_MARKER = "PERSONA-MARKER-MASSAGE-STORE-XYZ"


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


def _record(value: bytes):
    return SimpleNamespace(value=value, key=b"test-key", topic="llm.routing.v1", partition=0, offset=1)


async def setup() -> dict:
    tenant_id, customer_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    real_persona = (
        f"أنت المساعد الذكي لمتجر أجهزة مساج. {_DISTINCTIVE_PERSONA_MARKER}. "
        "لا علاقة لك بالعقارات إطلاقاً."
    )
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Persona Regression Drill Co",
            fal_license_number=f"PRD-{uuid.uuid4().hex[:10]}",
            ai_system_prompt=real_persona,
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Persona Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id="persona-drill",
            status=ConversationStatus.AI_ACTIVE,
        ))
    invalidate_company_context(tenant_id)  # ensure no stale cache from a prior run
    return {"tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id}


async def cleanup(ctx: dict) -> None:
    from sqlalchemy import delete
    async with get_system_session() as session:
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    invalidate_company_context(ctx["tenant_id"])
    print("cleanup: synthetic tenant/customer/conversation deleted")


def _decision(ctx: dict) -> RoutingDecision:
    event = CanonicalInboundEvent(
        tenant_id=ctx["tenant_id"], channel=Channel.WHATSAPP,
        platform_message_id=f"wamid.{uuid.uuid4().hex[:10]}",
        platform_conversation_id="persona-drill",
        platform_user_id="966500000000", customer_phone="966500000000",
        message_type=MessageType.TEXT, text_content="هل عندكم جهاز مساج للركبة؟",
    )
    return RoutingDecision(
        event=event, tenant_id=ctx["tenant_id"], conversation_id=ctx["conversation_id"],
        customer_id=ctx["customer_id"], target_tier="L1", route_reason="triage", skip_llm=False,
        # Exactly what every real production message looks like: no
        # business_name key was ever populated here by anything upstream.
        session_state={},
    )


async def main() -> None:
    _require_local()
    ctx = await setup()
    print(f"real tenant={ctx['tenant_id']} with a distinctive real ai_system_prompt set")

    captured: dict = {}

    async def _capture_and_fail(*args, **kwargs):
        captured["system_prompt"] = kwargs.get("system_prompt", "")
        raise RuntimeError("intentional tripwire -- only capturing the prompt, not sending a real request")

    original = invoker_module.gemini_client.generate_response
    invoker_module.gemini_client.generate_response = _capture_and_fail

    worker = LLMInvokerWorker()
    await worker._outbound_producer.start()
    await redis_mgr.start()
    try:
        try:
            await worker.process_message(_record(_decision(ctx).to_kafka_bytes()))
        except Exception:
            pass  # the tripwire always raises after capturing; the retry/fallback path handles it

        prompt = captured.get("system_prompt", "")
        assert prompt, "system_prompt was never captured -- the LLM call was never reached"

        print(f"\nCaptured system_prompt ({len(prompt)} chars), checking for the real bug's symptom...")
        found_real_estate_markers = [m for m in _REAL_ESTATE_MARKERS if m in prompt]
        has_real_persona = _DISTINCTIVE_PERSONA_MARKER in prompt

        assert not found_real_estate_markers, (
            f"REGRESSION: the hardcoded real-estate persona leaked into a non-real-estate "
            f"tenant's prompt (found: {found_real_estate_markers}) -- this is the exact bug"
        )
        assert has_real_persona, (
            "REGRESSION: the tenant's real, configured ai_system_prompt was not used at all"
        )
        print("PASS: the real per-tenant persona was used; the hardcoded real-estate "
              "persona did not leak in.")
    finally:
        invoker_module.gemini_client.generate_response = original
        await worker._outbound_producer.stop()
        await redis_mgr.stop()
        await cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())
