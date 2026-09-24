"""
Real end-to-end validation of the recommendation panel (item 10). Before
this, `CustomerContext.tsx`'s recommendations array was a hardcoded empty
list with no backend endpoint behind it at all, and the property-listing
ingestion path (`vector_sync.py`) had its own independent, buggy embedding
implementation that silently fell back to a *constant* dummy vector
([0.1] * 1536, identical for every listing) whenever no OpenAI key was
configured — meaning every real listing would have looked equally
"relevant" to any query, making recommendations meaningless even if the
panel had been wired up.

Real Postgres (conversation + messages), real Qdrant (a real listing,
ingested through the real `vector_sync.sync_listing_to_qdrant` — the same
function the properties API actually calls on listing create/update — not
a hand-rolled upsert), and the real local fastembed model. Calls the actual
`get_conversation_recommendations` endpoint function directly with manually
constructed dependencies (real DB session, real repository) — the FastAPI/
HTTP/Clerk-auth layer itself is not exercised here, which is a real,
acknowledged scope reduction, not a claim that the full HTTP route was
tested.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gateway.routers.conversations import get_conversation_recommendations
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel, ListingStatus, PropertyType
from src.shared.db.models import Conversation, Customer, Message, PropertyListing, Tenant
from src.shared.db.repository import ConversationRepository
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.qdrant_client.client import _tenant_collection, qdrant_mgr
from src.shared.services.vector_sync import sync_listing_to_qdrant

settings = get_settings()


def _require_local() -> None:
    if settings.qdrant_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Qdrant instance")
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


async def _setup() -> dict:
    tenant_id, customer_id, conversation_id, listing_id = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
    )
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id,
            business_name="Recommendation Drill Realty",
            fal_license_number=f"REC-{uuid.uuid4().hex[:10]}",
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Recommendation Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id="rec-drill",
        ))

    async with get_tenant_session(tenant_id) as session:
        session.add(Message(
            conversation_id=conversation_id, sender_type="customer",
            text_content="ابحث عن شقة 3 غرف في حي النرجس بالرياض",
        ))
        session.add(Message(
            conversation_id=conversation_id, sender_type="ai_bot",
            text_content="بالتأكيد، سأبحث لك عن خيارات مناسبة.",
        ))
        listing = PropertyListing(
            listing_id=listing_id,
            tenant_id=tenant_id,
            rega_ad_number=f"RD-{uuid.uuid4().hex[:8]}",
            property_type=PropertyType.APARTMENT,
            status=ListingStatus.VERIFIED_ACTIVE,
            city="الرياض",
            district="النرجس",
            price=1_150_000,
            area_sqm=210,
            bedrooms=3,
            bathrooms=2,
            description_ar="شقة عصرية بتشطيب راقٍ في حي النرجس، قريبة من الخدمات.",
        )
        session.add(listing)
    return {
        "tenant_id": tenant_id, "customer_id": customer_id,
        "conversation_id": conversation_id, "listing_id": listing_id, "listing": listing,
    }


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete

    try:
        await qdrant_mgr._c.delete_collection(_tenant_collection(ctx["tenant_id"]))
    except Exception as exc:
        print(f"cleanup warning: could not delete Qdrant collection: {exc}")

    async with get_system_session() as session:
        await session.execute(delete(Message).where(Message.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(PropertyListing).where(PropertyListing.listing_id == ctx["listing_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/conversation/messages/listing deleted")


async def main() -> None:
    _require_local()
    await qdrant_mgr.start()
    ctx = await _setup()
    print(f"synthetic tenant={ctx['tenant_id']} conversation={ctx['conversation_id']}")
    try:
        print("\n=== Ingesting the listing via the REAL vector_sync.sync_listing_to_qdrant ===")
        await sync_listing_to_qdrant(ctx["listing"], ctx["tenant_id"])
        # sync_listing_to_qdrant is fire-and-forget-shaped but we're awaiting
        # it directly (not via asyncio.create_task) so it's synchronous here.

        print("\n=== Calling the real get_conversation_recommendations endpoint function ===")
        async with get_tenant_session(ctx["tenant_id"]) as session:
            repo = ConversationRepository(session)
            recommendations = await get_conversation_recommendations(
                conversation_id=ctx["conversation_id"],
                user=SimpleNamespace(),  # unused inside the function body
                repo=repo,
                limit=3,
            )

        assert recommendations, "expected at least one real recommendation, got an empty list"
        top = recommendations[0]
        assert "النرجس" in top.title, f"expected the Narjis-district listing to surface, got title={top.title!r}"
        assert top.price == 1_150_000, f"expected the real listing's price, got {top.price}"
        assert top.area == 210, f"expected the real listing's area, got {top.area}"
        assert 0.0 <= top.score <= 1.0
        print(f"PASS: real recommendation returned (title has {len(top.title)} chars), {top.price} SAR, score={top.score}")
        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(ctx)
        await qdrant_mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())
