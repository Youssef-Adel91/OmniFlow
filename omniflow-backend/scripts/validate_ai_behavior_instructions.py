"""
Real end-to-end validation of the new "custom AI behavior instructions" lever
added to onboarding (TenantOnboardingUpdate.ai_instructions) per the client's
explicit overnight ask: a business owner must be able to tell their AI how to
behave with customers -- tone, objection handling, working toward closing a
sale, sector-specific guidance -- and that must be a REAL lever reaching the
model's prompt, not cosmetic.

Before this change, onboarding had exactly one free-text instructions field
(`detailed_instructions`), and it fed `CompanyProfile.policies_text` -- the
company-FACTS block, hard-capped at 2500 chars total across every
CompanyProfile field (see ai_engine/company_context.py's _MAX_BLOCK_CHARS).
A business owner typing "if the customer pushes back on price, offer a 10%
discount and mention the 30-day return guarantee" into that field would have
had it silently competing for space with FAQs/services/policies, in a block
whose entire purpose (per its own docstring) is "what's true", not "how to
behave". The actual behavior lever, Tenant.ai_system_prompt, was uncapped and
already wired into the real pipeline (see validate_tenant_persona_used.py's
fix) but nothing in onboarding ever wrote to it beyond a one-time generic
identity string.

This script proves, against real Postgres and through the real production
entry points (not a reimplementation):
  1. The real `update_tenant_onboarding()` endpoint, given `ai_instructions`,
     writes it into `Tenant.ai_system_prompt` -- NOT into
     `CompanyProfile.policies_text` (that stays exclusively fed by
     `detailed_instructions`, proving the two levers don't bleed into each
     other, which was exactly the failure class the persona-regression audit
     flagged as a risk for every field like this).
  2. Resubmitting onboarding with DIFFERENT ai_instructions updates the
     stored value in place -- it does not duplicate/accumulate across
     repeated saves (a real risk given ai_system_prompt is plain text with
     no separate column to diff against).
  3. The real `LLMInvokerWorker.process_message()` consumer entry point,
     given a real customer message that raises a price objection, builds a
     `system_prompt` (via the real `compose_system_prompt`/
     `get_tenant_persona` call chain) that contains the objection-handling
     instruction VERBATIM -- i.e. if a real model received this exact
     prompt, the instruction would be available to condition its reply.

What this does NOT prove, disclosed explicitly (same limitation as every
other channel validator in this repo lacking live provider credentials --
see IMPLEMENTATION_STATUS.md's Instagram/WhatsApp entries): there is no real
LLM provider API key in this container (checked: GROQ_API_KEY unset,
OPENAI_API_KEY is a placeholder, ANTHROPIC_API_KEY empty), so this cannot
capture actual MODEL-GENERATED text and confirm it obeys the instruction --
only that the instruction reaches the exact outbound prompt, at the same
network boundary (GeminiLLMClient.generate_response) every other validator
in this codebase mocks when no live key exists. A live-model content check
is flagged in IMPLEMENTATION_STATUS.md as needing a real provider key.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_engine.company_context import invalidate_company_context
from src.ai_workers.llm_invoker import worker as invoker_module
from src.ai_workers.llm_invoker.worker import LLMInvokerWorker
from src.ai_workers.semantic_router.worker import RoutingDecision
from src.gateway.routers.tenants import update_tenant_onboarding
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, ConversationStatus, MessageType, TenantUserRole
from src.shared.db.models import CompanyProfile, Conversation, Customer, Tenant, TenantUser
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.events.canonical import CanonicalInboundEvent
from src.shared.redis_client.client import redis_mgr
from src.shared.schemas.schemas import TenantOnboardingUpdate

settings = get_settings()

FACTS_TEXT = "سياسة الاستبدال والاسترجاع خلال 14 يوم من تاريخ الشراء."
OBJECTION_INSTRUCTIONS_V1 = (
    "إذا اعترض العميل على السعر، اعرض خصم 10% على أول طلب واذكر ضمان "
    "استرجاع الأموال لمدة 30 يوم. اسأل دائماً في نهاية الرد إذا كان "
    "يريد إتمام الطلب الآن."
)
OBJECTION_INSTRUCTIONS_V2 = (
    "لا تعرض أي خصومات إطلاقاً. إذا اعترض العميل على السعر، اشرح له جودة "
    "الخامات الألمانية المستخدمة وأن الضمان يمتد 3 سنوات."
)


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


def _record(value: bytes):
    return SimpleNamespace(value=value, key=b"test-key", topic="llm.routing.v1", partition=0, offset=1)


async def _setup() -> dict:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id,
            business_name=f"Workspace for AI-Instructions Drill {uuid.uuid4().hex[:6]}",
            fal_license_number=f"AIB-{uuid.uuid4().hex[:10]}",
        ))
        session.add(TenantUser(
            user_id=user_id, tenant_id=tenant_id,
            full_name="Drill Admin", email=f"drill-admin-{user_id.hex[:8]}@example.com",
            hashed_password="x", role=TenantUserRole.ADMIN,
        ))
    return {"tenant_id": tenant_id, "user_id": user_id}


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete
    async with get_system_session() as session:
        await session.execute(delete(Conversation).where(Conversation.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(Customer).where(Customer.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(CompanyProfile).where(CompanyProfile.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(TenantUser).where(TenantUser.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    invalidate_company_context(ctx["tenant_id"])
    print("cleanup: synthetic tenant/admin/profile/customer/conversation deleted")


async def main() -> None:
    _require_local()
    ctx = await _setup()
    tenant_id = ctx["tenant_id"]
    print(f"synthetic tenant={tenant_id}")

    try:
        async with get_system_session() as session:
            user = await session.get(TenantUser, ctx["user_id"])

            print("\n=== 1. Real onboarding submit with BOTH facts and behavior fields ===")
            response = await update_tenant_onboarding(
                payload=TenantOnboardingUpdate(
                    option="self_service",
                    business_name="متجر الأجهزة المنزلية",
                    business_category="أجهزة منزلية",
                    detailed_instructions=FACTS_TEXT,
                    ai_instructions=OBJECTION_INSTRUCTIONS_V1,
                ),
                current_user=user,
            )
            assert response.onboarding_status == "completed"
            print("PASS: real endpoint accepted the submission")

        async with get_tenant_session(tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id)
            profile = await session.get(CompanyProfile, tenant_id)

            assert OBJECTION_INSTRUCTIONS_V1 in tenant.ai_system_prompt, (
                "ai_instructions did not reach Tenant.ai_system_prompt"
            )
            print("PASS: ai_instructions reached Tenant.ai_system_prompt verbatim")

            assert OBJECTION_INSTRUCTIONS_V1 not in (profile.policies_text or ""), (
                "REGRESSION-CLASS BUG: behavior instructions leaked into the "
                "facts block (CompanyProfile.policies_text) -- would compete "
                "for the 2500-char knowledge-block cap with real FAQs/services"
            )
            print("PASS: behavior instructions did NOT leak into the facts block")

            assert FACTS_TEXT in (profile.policies_text or ""), (
                "detailed_instructions did not reach CompanyProfile.policies_text"
            )
            assert FACTS_TEXT not in tenant.ai_system_prompt, (
                "REGRESSION-CLASS BUG: facts leaked into the uncapped behavior "
                "prompt -- the two fields must stay on separate tracks"
            )
            print("PASS: detailed_instructions reached the facts block only, not the behavior prompt")

            first_write_prompt = tenant.ai_system_prompt

        print("\n=== 2. Resubmitting with DIFFERENT ai_instructions must UPDATE, not duplicate ===")
        async with get_system_session() as session:
            user = await session.get(TenantUser, ctx["user_id"])
            await update_tenant_onboarding(
                payload=TenantOnboardingUpdate(
                    option="self_service",
                    ai_instructions=OBJECTION_INSTRUCTIONS_V2,
                ),
                current_user=user,
            )

        async with get_tenant_session(tenant_id) as session:
            tenant = await session.get(Tenant, tenant_id)
            assert OBJECTION_INSTRUCTIONS_V2 in tenant.ai_system_prompt, (
                "second submission's ai_instructions never took effect"
            )
            assert OBJECTION_INSTRUCTIONS_V1 not in tenant.ai_system_prompt, (
                "REGRESSION: the old instructions are still present -- resubmitting "
                "onboarding accumulates duplicate/contradictory behavior instructions "
                "instead of replacing them (e.g. both 'never discount' and "
                "'offer 10% discount' would be live at once)"
            )
            assert len(tenant.ai_system_prompt) < len(first_write_prompt) + len(OBJECTION_INSTRUCTIONS_V2), (
                "prompt grew roughly by a full extra copy -- looks like accumulation, not replacement"
            )
            print("PASS: resubmission replaced the instructions in place, no duplication")

        print("\n=== 3. Real LLMInvokerWorker pipeline: does the objection instruction "
              "reach the exact prompt sent to the model for a real objection message? ===")
        customer_id, conversation_id = uuid.uuid4(), uuid.uuid4()
        async with get_system_session() as session:
            session.add(Customer(
                customer_id=customer_id, tenant_id=tenant_id,
                unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
                display_name="Price-Objection Drill Customer",
            ))
            session.add(Conversation(
                conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
                channel=Channel.WHATSAPP, platform_conversation_id="ai-instructions-drill",
                status=ConversationStatus.AI_ACTIVE,
            ))
        invalidate_company_context(tenant_id)

        event = CanonicalInboundEvent(
            tenant_id=tenant_id, channel=Channel.WHATSAPP,
            platform_message_id=f"wamid.{uuid.uuid4().hex[:10]}",
            platform_conversation_id="ai-instructions-drill",
            platform_user_id="966500000000", customer_phone="966500000000",
            message_type=MessageType.TEXT,
            text_content="السعر غالي بالنسبة لي، ممكن سعر أفضل؟",
        )
        decision = RoutingDecision(
            event=event, tenant_id=tenant_id, conversation_id=conversation_id,
            customer_id=customer_id, target_tier="L1", route_reason="triage", skip_llm=False,
            session_state={},
        )

        captured: dict = {}

        async def _capture_and_fail(*args, **kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt", "")
            raise RuntimeError("intentional tripwire -- capturing the exact prompt, not sending a real request")

        original = invoker_module.gemini_client.generate_response
        invoker_module.gemini_client.generate_response = _capture_and_fail

        worker = LLMInvokerWorker()
        await worker._outbound_producer.start()
        await redis_mgr.start()
        try:
            try:
                await worker.process_message(_record(decision.to_kafka_bytes()))
            except Exception:
                pass

            prompt = captured.get("system_prompt", "")
            assert prompt, "system_prompt was never captured -- the LLM call was never reached"
            assert OBJECTION_INSTRUCTIONS_V2 in prompt, (
                "the real customer-facing pipeline's outbound prompt does NOT contain "
                "the tenant's own custom AI behavior instructions -- the field would be "
                "cosmetic, never actually reaching the model"
            )
            print(f"PASS: the exact prompt the real LLMInvokerWorker pipeline would send to "
                  f"the model contains the tenant's objection-handling instructions verbatim "
                  f"({len(prompt)} chars total)")
        finally:
            invoker_module.gemini_client.generate_response = original
            await worker._outbound_producer.stop()
            await redis_mgr.stop()

        print("\nALL SCENARIOS PASSED")
        print(
            "\nNOTE (disclosed, not silently skipped): no live LLM provider key exists "
            "in this container, so this cannot confirm a real MODEL-GENERATED reply "
            "obeys the instruction -- only that it reaches the exact outbound prompt, "
            "at the same network boundary every other channel validator in this repo "
            "mocks when lacking live credentials. Flagged in IMPLEMENTATION_STATUS.md "
            "as needing a real provider key for a fully live content-level check."
        )
    finally:
        await _cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())
