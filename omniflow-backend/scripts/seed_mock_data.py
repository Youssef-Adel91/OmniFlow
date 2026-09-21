"""
scripts/seed_mock_data.py — Sprint 8: Mock Data Seeder

Populates PostgreSQL + Qdrant with realistic Saudi real-estate data.

Usage:
    cd omniflow-backend
    $env:PYTHONPATH = "."
    .venv\Scripts\python.exe scripts/seed_mock_data.py

    # Optional flags:
    #   --skip-db      skip PostgreSQL seeding
    #   --skip-qdrant  skip Qdrant ingestion
    #   --reset        delete existing tenant before re-seeding

Requirements:
    docker compose up -d   (PostgreSQL + Qdrant must be running)
    OPENAI_API_KEY and QDRANT_API_KEY set in .env
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ── Make project root importable ──────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from sqlalchemy import select, text

from src.shared.core.enums import (
    ListingStatus,
    OnboardingStatus,
    PropertyType,
    SubscriptionStatus,
    TenantTier,
    TenantUserRole,
)
from src.shared.db.models import PropertyListing, Tenant, TenantUser
from src.shared.db.session import get_system_session
from src.ai_workers.rag_engine.embedder import embedder
from src.shared.qdrant_client.client import qdrant_mgr
from src.shared.security.password import hash_password

structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(colors=True),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger("seeder")

# ══════════════════════════════════════════════════════════════════════════════
# Mock Data Definitions
# ══════════════════════════════════════════════════════════════════════════════

TENANT_FAL = "1200012345"
TENANT_NAME = "مكتب النخبة العقارية"
TENANT_PHONE_ID = "1234567890"   # dummy Meta phone number ID

AGENT_EMAIL = "agent@eliteprops.sa"
AGENT_NAME  = "محمد عبدالله الأحمدي"


@dataclass
class MockListing:
    rega_ad_number: str
    property_type: PropertyType
    city: str
    district: str
    price: float
    area_sqm: float
    bedrooms: int | None
    bathrooms: int | None
    description_ar: str
    latitude: float | None = None
    longitude: float | None = None
    status: ListingStatus = ListingStatus.VERIFIED_ACTIVE
    listing_id: uuid.UUID = field(default_factory=uuid.uuid4)


MOCK_LISTINGS: list[MockListing] = [
    MockListing(
        rega_ad_number="1100234501",
        property_type=PropertyType.APARTMENT,
        city="الرياض",
        district="النرجس",
        price=800_000,
        area_sqm=180,
        bedrooms=3,
        bathrooms=3,
        description_ar=(
            "شقة راقية للبيع في حي النرجس، الرياض. 3 غرف نوم، 3 حمامات، "
            "مساحة 180 م²، تشطيب سوبر لوكس، مدخل مستقل، موقف سيارتين، "
            "قريبة من المدارس والخدمات. السعر 800,000 ريال قابل للتفاوض."
        ),
        latitude=24.7808, longitude=46.6297,
    ),
    MockListing(
        rega_ad_number="1100234502",
        property_type=PropertyType.VILLA,
        city="الرياض",
        district="الملقا",
        price=4_500_000,
        area_sqm=600,
        bedrooms=6,
        bathrooms=7,
        description_ar=(
            "فيلا فاخرة في حي الملقا الراقي، الرياض. 6 غرف نوم، 7 حمامات، "
            "مساحة 600 م² على أرض 800 م². مسبح خاص، غرفة سائق، مجلس منفصل، "
            "نظام ذكاء اصطناعي للمنزل. السعر 4,500,000 ريال."
        ),
        latitude=24.8027, longitude=46.6382,
    ),
    MockListing(
        rega_ad_number="1100234503",
        property_type=PropertyType.OFFICE,
        city="الرياض",
        district="العليا",
        price=85_000,
        area_sqm=220,
        bedrooms=None,
        bathrooms=3,
        description_ar=(
            "مكتب تجاري للإيجار في شارع العليا الرئيسي، الرياض. مساحة 220 م²، "
            "الطابق الثامن، إطلالة بانورامية، غرفتا اجتماعات، كافيتريا، "
            "موقف مخصص. الإيجار السنوي 85,000 ريال."
        ),
        latitude=24.6917, longitude=46.6862,
    ),
    MockListing(
        rega_ad_number="1100234504",
        property_type=PropertyType.APARTMENT,
        city="جدة",
        district="الشاطئ",
        price=1_200_000,
        area_sqm=250,
        bedrooms=4,
        bathrooms=4,
        description_ar=(
            "شقة فاخرة بإطلالة بحرية في حي الشاطئ، جدة. 4 غرف نوم، 4 حمامات، "
            "مساحة 250 م²، شرفة واسعة، تشطيب إيطالي، أمن 24 ساعة. "
            "السعر 1,200,000 ريال."
        ),
        latitude=21.5755, longitude=39.1716,
    ),
    MockListing(
        rega_ad_number="1100234505",
        property_type=PropertyType.LAND,
        city="الرياض",
        district="المونسية",
        price=2_100_000,
        area_sqm=900,
        bedrooms=None,
        bathrooms=None,
        description_ar=(
            "أرض سكنية للبيع في حي المونسية، الرياض. المساحة 900 م²، "
            "واجهة على شارعين، موقع متميز قريب من طريق الملك عبدالله. "
            "مناسبة لبناء فيلا أو عمارة. السعر 2,100,000 ريال."
        ),
        latitude=24.7264, longitude=46.7698,
    ),
    MockListing(
        rega_ad_number="1100234506",
        property_type=PropertyType.APARTMENT,
        city="الرياض",
        district="حي الروضة",
        price=550_000,
        area_sqm=130,
        bedrooms=2,
        bathrooms=2,
        description_ar=(
            "شقة اقتصادية للبيع في حي الروضة، الرياض. 2 غرف نوم، 2 حمامات، "
            "مساحة 130 م²، دور ثالث، قريبة من المسجد والسوق. "
            "مناسبة للعزاب أو الأسر الصغيرة. السعر 550,000 ريال."
        ),
        latitude=24.7136, longitude=46.6753,
    ),
    MockListing(
        rega_ad_number="1100234507",
        property_type=PropertyType.VILLA,
        city="الدمام",
        district="الفيصلية",
        price=2_800_000,
        area_sqm=450,
        bedrooms=5,
        bathrooms=5,
        description_ar=(
            "فيلا مودرن للبيع في الفيصلية، الدمام. 5 غرف نوم، 5 حمامات، "
            "مساحة البناء 450 م² على أرض 500 م². تصميم عصري، مطبخ أمريكي، "
            "غرفة سينما. السعر 2,800,000 ريال."
        ),
        latitude=26.4207, longitude=50.0888,
    ),
    MockListing(
        rega_ad_number="1100234508",
        property_type=PropertyType.COMMERCIAL,
        city="الرياض",
        district="العليا",
        price=3_500_000,
        area_sqm=500,
        bedrooms=None,
        bathrooms=6,
        description_ar=(
            "صالة تجارية للبيع في العليا، الرياض. مساحة 500 م²، "
            "واجهة زجاجية على الشارع الرئيسي، 3 طوابق، مصعد، "
            "مناسبة لمحلات الأثاث أو الملابس أو الأجهزة الكهربائية. "
            "السعر 3,500,000 ريال."
        ),
        latitude=24.6950, longitude=46.6891,
    ),
    MockListing(
        rega_ad_number="1100234509",
        property_type=PropertyType.DAILY_RENTAL,
        city="مكة المكرمة",
        district="العزيزية",
        price=350,
        area_sqm=90,
        bedrooms=2,
        bathrooms=2,
        description_ar=(
            "شقة مفروشة للإيجار اليومي في العزيزية، مكة المكرمة. "
            "2 غرف نوم، 2 حمامات، مساحة 90 م²، على بعد 800 م من الحرم المكي، "
            "واي فاي مجاني، تكييف مركزي. السعر 350 ريال في الليلة."
        ),
        latitude=21.3891, longitude=39.8579,
    ),
    MockListing(
        rega_ad_number="1100234510",
        property_type=PropertyType.APARTMENT,
        city="الرياض",
        district="النرجس",
        price=1_050_000,
        area_sqm=220,
        bedrooms=4,
        bathrooms=4,
        description_ar=(
            "شقة دوبلكس للبيع في حي النرجس، الرياض. 4 غرف نوم، 4 حمامات، "
            "مساحة 220 م²، طابقان، تشطيب فاخر، مطبخ مجهز بالكامل، "
            "قريبة من طريق أبو بكر الصديق. السعر 1,050,000 ريال."
        ),
        latitude=24.7820, longitude=46.6310,
    ),
    MockListing(
        rega_ad_number="1100234511",
        property_type=PropertyType.WAREHOUSE,
        city="الرياض",
        district="المنطقة الصناعية",
        price=180_000,
        area_sqm=800,
        bedrooms=None,
        bathrooms=2,
        description_ar=(
            "مستودع للإيجار في المنطقة الصناعية، الرياض. مساحة 800 م²، "
            "ارتفاع 8 م، بوابة هيدروليكية، رافعة شوكية متاحة، "
            "قريب من طريق الدائري الشرقي. الإيجار السنوي 180,000 ريال."
        ),
        latitude=24.6382, longitude=46.7815,
    ),
    MockListing(
        rega_ad_number="1100234512",
        property_type=PropertyType.VILLA,
        city="جدة",
        district="الحمراء",
        price=5_200_000,
        area_sqm=700,
        bedrooms=7,
        bathrooms=8,
        description_ar=(
            "قصر سكني فاخر في حي الحمراء، جدة. 7 غرف نوم، 8 حمامات، "
            "مساحة 700 م² على أرض 1000 م². حديقة منسقة، مسبح، "
            "غرفة أمن، غرفة خادمة. السعر 5,200,000 ريال."
        ),
        latitude=21.5512, longitude=39.1798,
    ),
    MockListing(
        rega_ad_number="1100234513",
        property_type=PropertyType.APARTMENT,
        city="الرياض",
        district="القيروان",
        price=720_000,
        area_sqm=160,
        bedrooms=3,
        bathrooms=3,
        description_ar=(
            "شقة عائلية للبيع في حي القيروان، شمال الرياض. 3 غرف نوم، "
            "3 حمامات، مساحة 160 م²، تشطيب ممتاز، حارس أمن، "
            "قريبة من مدارس أهلية راقية. السعر 720,000 ريال."
        ),
        latitude=24.8217, longitude=46.6506,
    ),
    MockListing(
        rega_ad_number="1100234514",
        property_type=PropertyType.LAND,
        city="جدة",
        district="الواجهة البحرية",
        price=8_500_000,
        area_sqm=1500,
        bedrooms=None,
        bathrooms=None,
        description_ar=(
            "أرض تجارية مميزة على الواجهة البحرية في جدة. المساحة 1500 م²، "
            "تصريح بناء تجاري وسياحي، مناسبة لفندق أو مول ساحلي. "
            "السعر 8,500,000 ريال."
        ),
        latitude=21.5433, longitude=39.1327,
    ),
    MockListing(
        rega_ad_number="1100234515",
        property_type=PropertyType.OFFICE,
        city="الرياض",
        district="الملز",
        price=55_000,
        area_sqm=120,
        bedrooms=None,
        bathrooms=2,
        description_ar=(
            "مكتب للإيجار في الملز، وسط الرياض. مساحة 120 م²، دور أول، "
            "مجهز بالكامل بأثاث مكتبي، غرفة اجتماعات، انترنت فايبر. "
            "مناسب للشركات الناشئة. الإيجار السنوي 55,000 ريال."
        ),
        latitude=24.6810, longitude=46.7237,
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# Phase 1 — PostgreSQL Seeding
# ══════════════════════════════════════════════════════════════════════════════

async def seed_database(reset: bool = False) -> tuple[uuid.UUID, list[MockListing]]:
    """
    Insert Tenant, TenantUser, and PropertyListings into PostgreSQL.
    Uses get_system_session() to bypass RLS.
    Returns (tenant_id, listings_with_assigned_ids).
    """
    log.info("db_seed_start", reset=reset)

    async with get_system_session() as session:

        # ── Check / delete existing tenant ────────────────────────────────────
        existing = await session.execute(
            select(Tenant).where(Tenant.fal_license_number == TENANT_FAL)
        )
        existing_tenant = existing.scalar_one_or_none()

        if existing_tenant:
            if reset:
                log.warning("db_seed_deleting_existing_tenant",
                            tenant_id=str(existing_tenant.tenant_id))
                await session.delete(existing_tenant)
                await session.flush()
                existing_tenant = None
            else:
                log.info("db_seed_tenant_exists_skipping",
                         tenant_id=str(existing_tenant.tenant_id),
                         tip="Run with --reset to delete and re-seed")
                # Return existing tenant_id and listings
                listings_result = await session.execute(
                    select(PropertyListing).where(
                        PropertyListing.tenant_id == existing_tenant.tenant_id
                    )
                )
                count = len(listings_result.scalars().all())
                log.info("db_seed_existing_listings", count=count)
                return existing_tenant.tenant_id, MOCK_LISTINGS

        # ── Create Tenant ─────────────────────────────────────────────────────
        tenant = Tenant(
            tenant_id=uuid.uuid4(),
            business_name=TENANT_NAME,
            fal_license_number=TENANT_FAL,
            subscription_tier=TenantTier.PROFESSIONAL,
            status=SubscriptionStatus.ACTIVE,
            whatsapp_phone_number_id=TENANT_PHONE_ID,
            whatsapp_waba_id="9876543210",
            max_ai_conversations=2000,
            onboarding_status=OnboardingStatus.COMPLETED,
        )
        session.add(tenant)
        await session.flush()
        log.info("db_seed_tenant_created",
                 tenant_id=str(tenant.tenant_id),
                 name=tenant.business_name)

        # ── Create TenantUser (Admin Agent) ───────────────────────────────────
        # FIX: Use hash_password() to generate a REAL bcrypt hash.
        # The previous "$argon2id$..." mock string could NOT be verified by
        # the bcrypt CryptContext in auth.py → verify_password() returned False
        # → every E2E login failed with HTTP 401.
        _dev_password = "OmniFlow@2025!"
        agent = TenantUser(
            user_id=uuid.uuid4(),
            tenant_id=tenant.tenant_id,
            full_name=AGENT_NAME,
            email=AGENT_EMAIL,
            hashed_password=hash_password(_dev_password),
            role=TenantUserRole.ADMIN,
            is_active=True,
        )
        session.add(agent)
        log.info("db_seed_agent_created", email=AGENT_EMAIL)

        # ── Create PropertyListings ───────────────────────────────────────────
        for mock in MOCK_LISTINGS:
            listing = PropertyListing(
                listing_id=mock.listing_id,
                tenant_id=tenant.tenant_id,
                rega_ad_number=mock.rega_ad_number,
                property_type=mock.property_type,
                status=mock.status,
                is_verified=True,
                city=mock.city,
                district=mock.district,
                latitude=mock.latitude,
                longitude=mock.longitude,
                price=mock.price,
                area_sqm=mock.area_sqm,
                bedrooms=mock.bedrooms,
                bathrooms=mock.bathrooms,
                description_ar=mock.description_ar,
                qdrant_point_id=str(mock.listing_id),
            )
            session.add(listing)

        log.info("db_seed_listings_flushing", count=len(MOCK_LISTINGS))
        await session.flush()
        log.info("db_seed_complete",
                 tenant_id=str(tenant.tenant_id),
                 listings=len(MOCK_LISTINGS))

    return tenant.tenant_id, MOCK_LISTINGS


# ══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Qdrant Ingestion
# ══════════════════════════════════════════════════════════════════════════════

def _build_embed_text(mock: MockListing) -> str:
    """
    Build a rich Arabic text representation for embedding.
    More context → better semantic search.
    """
    price_str = f"{int(mock.price):,} ريال"
    type_map = {
        PropertyType.APARTMENT: "شقة",
        PropertyType.VILLA: "فيلا",
        PropertyType.LAND: "أرض",
        PropertyType.COMMERCIAL: "وحدة تجارية",
        PropertyType.DAILY_RENTAL: "شقة مفروشة إيجار يومي",
        PropertyType.OFFICE: "مكتب",
        PropertyType.WAREHOUSE: "مستودع",
    }
    prop_label = type_map.get(mock.property_type, mock.property_type)
    rooms = ""
    if mock.bedrooms:
        rooms = f"، {mock.bedrooms} غرف نوم، {mock.bathrooms} حمامات"

    header = (
        f"{prop_label} في {mock.district}، {mock.city}. "
        f"المساحة {int(mock.area_sqm)} م²{rooms}. "
        f"السعر: {price_str}. "
        f"رقم الإعلان: {mock.rega_ad_number}."
    )
    return f"{header} {mock.description_ar}"


def _build_payload(mock: MockListing, tenant_id: uuid.UUID) -> dict:
    """Build the Qdrant point payload."""
    return {
        "tenant_id": str(tenant_id),           # Vector RLS field (indexed)
        "listing_id": str(mock.listing_id),
        "rega_ad_number": mock.rega_ad_number,
        "title": mock.description_ar[:80],
        "property_type": mock.property_type.value,
        "price_sar": float(mock.price),
        "area_sqm": float(mock.area_sqm),
        "bedrooms": mock.bedrooms,
        "bathrooms": mock.bathrooms,
        "city": mock.city,
        "district": mock.district,
        "latitude": mock.latitude,
        "longitude": mock.longitude,
        "status": mock.status.value,            # "VERIFIED_ACTIVE" (indexed)
        "summary": mock.description_ar[:300],
        "listing_url": f"https://app.omniflow.ai/listings/{mock.listing_id}",
    }


async def seed_qdrant(
    tenant_id: uuid.UUID,
    listings: list[MockListing],
) -> None:
    """
    Embed each listing and upsert into the tenant's Qdrant collection.
    Uses batch embedding for efficiency (single OpenAI API call).
    """
    log.info("qdrant_seed_start",
             tenant_id=str(tenant_id),
             count=len(listings))

    # ── Build embed texts ─────────────────────────────────────────────────────
    texts = [_build_embed_text(m) for m in listings]
    log.info("qdrant_embedding_batch", count=len(texts), model="text-embedding-3-small")

    vectors = await embedder.embed_batch(texts)
    log.info("qdrant_embeddings_generated",
             count=len(vectors),
             dim=len(vectors[0]) if vectors else 0)

    # ── Upsert to Qdrant ──────────────────────────────────────────────────────
    for i, (mock, vector) in enumerate(zip(listings, vectors)):
        payload = _build_payload(mock, tenant_id)
        await qdrant_mgr.upsert_property_listing(
            tenant_id=tenant_id,
            listing_id=str(mock.listing_id),
            vector=vector,
            payload=payload,
        )
        log.info(
            "qdrant_point_upserted",
            index=i + 1,
            total=len(listings),
            rega=mock.rega_ad_number,
            city=mock.city,
            district=mock.district,
            score_dim=len(vector),
        )

    log.info("qdrant_seed_complete",
             tenant_id=str(tenant_id),
             collection=f"t_{str(tenant_id).replace('-','')} _listings",
             upserted=len(listings))


# ══════════════════════════════════════════════════════════════════════════════
# Verification
# ══════════════════════════════════════════════════════════════════════════════

async def verify_rag(tenant_id: uuid.UUID) -> None:
    """
    Quick end-to-end sanity check:
    embed a test query → search Qdrant → print top results.
    """
    test_query = "شقة 3 غرف في الرياض بسعر مناسب"
    log.info("verify_rag_start", query=test_query)

    vector = await embedder.embed_query(test_query)
    results = await qdrant_mgr.search_properties(
        tenant_id=tenant_id,
        query_vector=vector,
        limit=3,
        score_threshold=0.50,
    )

    if not results:
        log.warning("verify_rag_no_results", tip="Check Qdrant is running and indexed")
        return

    log.info("verify_rag_results", count=len(results))
    for i, hit in enumerate(results, 1):
        p = hit.payload or {}
        log.info(
            "verify_rag_hit",
            rank=i,
            score=round(hit.score, 4),
            rega=p.get("rega_ad_number"),
            type=p.get("property_type"),
            city=p.get("city"),
            district=p.get("district"),
            price=f"{int(p.get('price_sar', 0)):,} SAR",
        )


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

async def main(
    skip_db: bool = False,
    skip_qdrant: bool = False,
    reset: bool = False,
    verify: bool = True,
) -> None:
    log.info("omniflow_seeder_start",
             skip_db=skip_db,
             skip_qdrant=skip_qdrant,
             reset=reset)

    # ── Phase 1: PostgreSQL ───────────────────────────────────────────────────
    tenant_id: uuid.UUID
    listings: list[MockListing]

    if not skip_db:
        tenant_id, listings = await seed_database(reset=reset)
    else:
        log.warning("db_seed_skipped")
        # Still need a tenant_id for Qdrant — check DB for existing
        async with get_system_session() as session:
            result = await session.execute(
                select(Tenant).where(Tenant.fal_license_number == TENANT_FAL)
            )
            t = result.scalar_one_or_none()
            if not t:
                log.error("db_no_tenant_found",
                          tip="Run without --skip-db first to create the tenant.")
                return
            tenant_id = t.tenant_id
        listings = MOCK_LISTINGS

    # ── Phase 2: Qdrant ───────────────────────────────────────────────────────
    if not skip_qdrant:
        await qdrant_mgr.start()
        embedder.configure()

        try:
            await seed_qdrant(tenant_id, listings)
        finally:
            await qdrant_mgr.stop()
    else:
        log.warning("qdrant_seed_skipped")

    # ── Phase 3: Verify end-to-end RAG ───────────────────────────────────────
    if verify and not skip_qdrant:
        await qdrant_mgr.start()
        try:
            await verify_rag(tenant_id)
        finally:
            await qdrant_mgr.stop()

    log.info(
        "seeder_done",
        tenant_id=str(tenant_id),
        listings_seeded=len(listings),
        tip=(
            f"Set WA phone mapping in Redis: "
            f"redis-cli SET 'wa:phone:{TENANT_PHONE_ID}' '{tenant_id}'"
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OmniFlow mock data seeder")
    parser.add_argument("--skip-db",      action="store_true")
    parser.add_argument("--skip-qdrant",  action="store_true")
    parser.add_argument("--reset",        action="store_true",
                        help="Delete existing tenant before re-seeding")
    parser.add_argument("--no-verify",    action="store_true",
                        help="Skip end-to-end RAG verification")
    args = parser.parse_args()

    asyncio.run(main(
        skip_db=args.skip_db,
        skip_qdrant=args.skip_qdrant,
        reset=args.reset,
        verify=not args.no_verify,
    ))
