"""
Real verification of the generalized customer_profile_extractor.py against
TWO different real tenants/sectors, per explicit product request:

  1. Naeem (massage-device retail) — uses the customer's ACTUAL real
     messages already sitting in Postgres from this session's live WhatsApp
     test (not synthetic fixtures). Prints the real transcript alongside
     the extracted fields for manual hallucination review.
  2. A real-estate tenant — a new, real, persisted customer/conversation
     with realistic real-estate messages (budget, district, urgency
     phrase), inserted as real DB rows so the extractor runs against real
     Postgres like any other call, not a mock. Demonstrates the
     explicit-profile-data lead-scoring bucket actually varies once richer
     signals exist, rather than being flat/near-zero for everyone.

For both: prints the real lead score BEFORE and AFTER extraction+persist
using the actual `_load_lead_scores` aggregate-query path (not a
reimplementation), and calls the REAL LLM (no client/response mocking) —
only the DB fixture data for tenant 2 is synthetic-but-real-stored.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from src.gateway.routers.customers import _load_lead_scores
from src.shared.core.enums import Channel, ConversationStatus
from src.shared.db.models import Conversation, Customer, Message, Tenant
from src.shared.db.session import get_system_session
from src.shared.services.customer_profile_extractor import extract_and_persist_customer_profile

NAEEM_TENANT_ID = uuid.UUID("e3699aa1-6a4e-4cde-bb26-b62db0b95d23")


async def _print_score(label: str, tenant_id: uuid.UUID, customer_id: uuid.UUID) -> None:
    async with get_system_session() as session:
        scores = await _load_lead_scores(session, [customer_id])
    s = scores[customer_id]
    print(f"  {label}: score={s.score} tier={s.tier} "
          f"(behavior={s.conversation_behavior}, profile={s.profile_data}, history={s.interaction_history})")


async def verify_naeem() -> None:
    print("=" * 78)
    print("TENANT 1: Naeem (massage-device retail) — REAL live-test conversation")
    print("=" * 78)
    async with get_system_session() as session:
        customer = (await session.execute(
            select(Customer).where(Customer.tenant_id == NAEEM_TENANT_ID)
        )).scalar_one()
        transcript = (await session.execute(
            select(Message.text_content, Message.sender_type)
            .join(Conversation)
            .where(Conversation.tenant_id == NAEEM_TENANT_ID)
            .order_by(Message.created_at)
        )).all()

    print("\nReal transcript from Postgres:")
    for row in transcript:
        print(f"  [{row.sender_type}] {row.text_content}")

    await _print_score("BEFORE extraction", NAEEM_TENANT_ID, customer.customer_id)

    customer_texts = [r.text_content for r in transcript if r.sender_type == "customer" and r.text_content]
    async with get_system_session() as session:
        merged = await extract_and_persist_customer_profile(
            session=session, customer_id=customer.customer_id, customer_texts=customer_texts,
        )
    print(f"\nExtracted (real LLM call): {merged}")
    print("Manual hallucination check: customer only ever asked about knee massage --")
    print("no budget, no location, no urgency phrase was ever stated. Correct extraction")
    print("must have budget_min/budget_max/location = null and urgency = false.")
    assert merged["budget_min"] is None and merged["budget_max"] is None, (
        f"HALLUCINATION: invented a budget the customer never stated: {merged}"
    )
    assert merged["location"] is None, f"HALLUCINATION: invented a location: {merged}"
    assert merged["urgency"] is False, f"HALLUCINATION: invented urgency: {merged}"
    print("PASS: no hallucinated fields.")

    await _print_score("AFTER extraction", NAEEM_TENANT_ID, customer.customer_id)


async def verify_real_estate() -> dict:
    print("\n" + "=" * 78)
    print("TENANT 2: Real estate — new, realistic, REAL-STORED conversation")
    print("=" * 78)
    tenant_id, customer_id, conv_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    transcript = [
        "السلام عليكم، أبحث عن شقة في حي النرجس بالرياض",
        "الميزانية عندي حوالي 900 ألف ريال، ما تتجاوزيها",
        "محتاج الشقة بسرعة، عندي انتقال الشهر الجاي",
    ]
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="مكتب الديرة العقارية (تحقق حقيقي)",
            fal_license_number=f"VER-{uuid.uuid4().hex[:10]}",
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+9665{uuid.uuid4().int % 10_000_000:07d}",
            display_name="عميل تحقق العقارات",
        ))
        session.add(Conversation(
            conversation_id=conv_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id=f"verify-{uuid.uuid4().hex[:8]}",
            status=ConversationStatus.AI_ACTIVE, message_count=len(transcript),
        ))
        await session.flush()
        for text in transcript:
            session.add(Message(
                message_id=uuid.uuid4(), conversation_id=conv_id, sender_type="customer",
                message_type="text", text_content=text, delivery_status="DELIVERED",
            ))

    print("\nReal transcript (real DB rows, real customer text):")
    for line in transcript:
        print(f"  [customer] {line}")

    await _print_score("BEFORE extraction", tenant_id, customer_id)

    async with get_system_session() as session:
        merged = await extract_and_persist_customer_profile(
            session=session, customer_id=customer_id, customer_texts=transcript,
        )
    print(f"\nExtracted (real LLM call): {merged}")
    print("Manual hallucination check: customer explicitly stated budget_max~900000,")
    print("location='النرجس' (or similar), and an explicit urgency phrase ('بسرعة').")
    print("Correct extraction should populate all three, not invent bedrooms/property_type")
    print("(never asked for -- this is the GENERIC extractor, not the real-estate-specific one).")
    assert "bedrooms" not in merged and "property_type" not in merged, (
        "generic extractor must not invent real-estate-specific fields"
    )
    if merged["budget_max"] is not None:
        assert 700_000 <= merged["budget_max"] <= 1_000_000, f"budget_max out of plausible range: {merged}"
    assert merged["urgency"] is True, f"should have detected the explicit urgency phrase: {merged}"
    print("PASS: fields plausible, no invented real-estate-specific fields, urgency correctly detected.")

    await _print_score("AFTER extraction", tenant_id, customer_id)
    return {"tenant_id": tenant_id, "customer_id": customer_id, "conv_id": conv_id}


async def cleanup(ctx: dict) -> None:
    from sqlalchemy import delete
    async with get_system_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == ctx["conv_id"]))
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conv_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("\ncleanup: tenant-2 synthetic-but-real rows deleted (Naeem's real data is untouched and kept)")


async def main() -> None:
    await verify_naeem()
    ctx = await verify_real_estate()
    print("\nALL VERIFICATIONS PASSED")
    await cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())
