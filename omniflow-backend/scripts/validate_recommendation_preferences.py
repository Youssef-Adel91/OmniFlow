"""
Real end-to-end validation of item 10's sign-off decision: an explicit LLM
preference-extraction step (budget/district/property_type/bedrooms) runs
before the Qdrant search, and price/location are applied as a REAL filter
— not just folded into text similarity — so a listing priced far outside
the customer's stated budget cannot outrank one that's actually in range.

Real Postgres + real Qdrant + real fastembed + a REAL LLM call (Groq, a
real configured provider in this environment) through the actual
`extract_preferences()` and `get_conversation_recommendations()` functions
— no mocks anywhere in this pipeline.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_workers.rag_engine.embedder import embedder
from src.ai_workers.rag_engine.preference_extractor import extract_preferences
from src.gateway.routers.conversations import get_conversation_recommendations
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, PropertyType, ListingStatus
from src.shared.db.models import Conversation, Customer, Message, PropertyListing, Tenant
from src.shared.db.repository import ConversationRepository
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.qdrant_client.client import qdrant_mgr
from src.shared.services.vector_sync import sync_listing_to_qdrant

settings = get_settings()


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")
    if not settings.groq_api_key or settings.groq_api_key.lower() == "mock":
        raise RuntimeError("requires a real GROQ_API_KEY (or another real LLM provider) configured")


async def _setup() -> dict:
    tenant_id, customer_id, conversation_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    in_budget_id, over_budget_id = uuid.uuid4(), uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="Preference Drill Realty",
            fal_license_number=f"PREF-{uuid.uuid4().hex[:10]}",
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Preference Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id="preference-drill",
        ))
        session.add(Message(
            message_id=uuid.uuid4(), conversation_id=conversation_id, sender_type="customer",
            text_content="ابحث عن شقة 3 غرف في حي النرجس بالرياض بميزانية حوالي مليون ومئتين ألف ريال",
        ))
        # In-budget listing: similar text, price within the extracted budget.
        session.add(PropertyListing(
            listing_id=in_budget_id, tenant_id=tenant_id, rega_ad_number=f"PD-{uuid.uuid4().hex[:8]}",
            property_type=PropertyType.APARTMENT, status=ListingStatus.VERIFIED_ACTIVE, is_verified=False,
            city="الرياض", district="النرجس", price=1150000, area_sqm=210, bedrooms=3, bathrooms=2,
            description_ar="شقة عصرية 3 غرف بتشطيب راقٍ في حي النرجس، قريبة من الخدمات.",
        ))
        # Over-budget listing: near-identical text (higher raw text-similarity
        # potential) but priced far outside the customer's stated budget.
        session.add(PropertyListing(
            listing_id=over_budget_id, tenant_id=tenant_id, rega_ad_number=f"PD-{uuid.uuid4().hex[:8]}",
            property_type=PropertyType.APARTMENT, status=ListingStatus.VERIFIED_ACTIVE, is_verified=False,
            city="الرياض", district="النرجس", price=4500000, area_sqm=210, bedrooms=3, bathrooms=2,
            description_ar="شقة فاخرة جدًا 3 غرف بتشطيب راقٍ في حي النرجس، إطلالة بانورامية مميزة.",
        ))
    return {
        "tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id,
        "in_budget_id": in_budget_id, "over_budget_id": over_budget_id,
    }


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete

    async with get_system_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(PropertyListing).where(
            PropertyListing.listing_id.in_([ctx["in_budget_id"], ctx["over_budget_id"]])
        ))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/conversation/listings deleted")


async def scenario_a_extraction(ctx: dict) -> None:
    print("\n=== Scenario A: real LLM extracts budget/district/type/bedrooms ===")
    prefs = await extract_preferences([
        "ابحث عن شقة 3 غرف في حي النرجس بالرياض بميزانية حوالي مليون ومئتين ألف ريال",
    ])
    print(f"extracted: {prefs}")
    assert prefs["property_type"] == "apartment", f"expected apartment, got {prefs['property_type']!r}"
    assert prefs["bedrooms"] == 3, f"expected 3 bedrooms, got {prefs['bedrooms']!r}"
    assert prefs["district"] and "نرجس" in prefs["district"], f"expected النرجس in district, got {prefs['district']!r}"
    assert prefs["budget_max"] and 1_000_000 <= prefs["budget_max"] <= 1_400_000, (
        f"expected budget_max around 1.2M, got {prefs['budget_max']!r}"
    )
    print("PASS: real LLM correctly extracted property_type=apartment, bedrooms=3, district contains النرجس, budget_max ~1.2M")


async def scenario_b_price_filter_excludes_overbudget(ctx: dict) -> None:
    print("\n=== Scenario B: real endpoint filters out the over-budget listing ===")
    async with get_tenant_session(ctx["tenant_id"]) as session:
        repo = ConversationRepository(session)
        user = type("U", (), {"tenant_id": ctx["tenant_id"]})()
        results = await get_conversation_recommendations(
            conversation_id=ctx["conversation_id"], user=user, repo=repo, limit=5,
        )
    listing_ids_returned = set()
    for r in results:
        print(f"  -> {r.title} | {r.price} SAR | score={r.score}")
    # Re-fetch by price to identify which real listing each result corresponds to.
    in_budget_prices = {1150000.0}
    over_budget_prices = {4500000.0}
    returned_prices = {r.price for r in results}
    assert returned_prices & in_budget_prices, "expected the in-budget listing to be recommended"
    assert not (returned_prices & over_budget_prices), (
        f"the over-budget listing (4.5M SAR) leaked through the price filter: {returned_prices}"
    )
    print("PASS: real endpoint returned the in-budget listing and correctly excluded the 4.5M SAR over-budget one")


async def main() -> None:
    _require_local()
    ctx = await _setup()
    print(f"synthetic tenant={ctx['tenant_id']} conversation={ctx['conversation_id']}")
    if not embedder.is_configured:
        embedder.configure()
    if not qdrant_mgr._started:
        await qdrant_mgr.start()
    try:
        for listing_id in (ctx["in_budget_id"], ctx["over_budget_id"]):
            async with get_tenant_session(ctx["tenant_id"]) as session:
                from sqlalchemy import select
                listing = await session.scalar(select(PropertyListing).where(PropertyListing.listing_id == listing_id))
            await sync_listing_to_qdrant(listing, ctx["tenant_id"])

        await scenario_a_extraction(ctx)
        await scenario_b_price_filter_excludes_overbudget(ctx)
        print("\nALL SCENARIOS PASSED")
    finally:
        from src.shared.qdrant_client.client import _tenant_collection
        try:
            await qdrant_mgr._c.delete_collection(_tenant_collection(ctx["tenant_id"]))
        except Exception:
            pass
        await _cleanup(ctx)
        await qdrant_mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())
