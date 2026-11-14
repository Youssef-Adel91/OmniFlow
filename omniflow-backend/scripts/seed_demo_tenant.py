"""Seed a realistic NON-EXEMPT demo tenant on a LOCAL development database: a real 14-day trial (300-reply cap, trial limits), three staff users, a verified inventory, and
the sales / comment-reply preferences switched on — everything the demo script and the customer simulation need.

Safe by construction (same rules as scripts/dev_cleanup_test_data.py): refuses unless APP_ENV=development AND the database host is local. Idempotent: the tenant is identified
by its fixed licence number, a second run only reports it. Passwords: the users get an UNUSABLE hash — log in through your dev auth, nothing here creates a credential.
Prints ids only (never a secret).

    python -m scripts.seed_demo_tenant                 # create (or report) the demo tenant
    python -m scripts.seed_demo_tenant --name "مكتب الأفق العقاري"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from sqlalchemy import text

from src.shared.core.config import get_settings
from src.shared.db.session import get_system_session
from scripts.dev_cleanup_test_data import guard

DEMO_FAL = "DEV-DEMO-0001"
LISTINGS = [  # rega ad number, type, district, price, bedrooms, bathrooms, area, description
    ("7100000001", "apartment", "النرجس", 850_000, 3, 2, 140, "شقة واسعة قرب الخدمات والمدارس"),
    ("7100000002", "apartment", "النرجس", 720_000, 2, 2, 110, "شقة حديثة بإطلالة"),
    ("7100000003", "apartment", "الياسمين", 960_000, 3, 3, 155, "شقة بمدخل خاص"),
    ("7100000004", "villa", "الياسمين", 1_950_000, 5, 4, 360, "فيلا بحديقة ومسبح"),
    ("7100000005", "villa", "الملقا", 2_600_000, 6, 5, 450, "فيلا فاخرة بتصميم عصري"),
    ("7100000006", "land", "حطين", 1_400_000, None, None, 600, "أرض سكنية على شارع 20"),
    ("7100000007", "apartment", "العارض", 540_000, 2, 1, 95, "شقة اقتصادية قرب الطريق الرئيسي"),
    ("7100000008", "commercial", "العليا", 3_200_000, None, 2, 220, "معرض تجاري على طريق رئيسي"),
]


async def seed(name: str) -> dict:
    async with get_system_session() as s:
        row = (await s.execute(text("SELECT tenant_id FROM tenants WHERE fal_license_number = :f"), {"f": DEMO_FAL})).first()
        if row:
            return {"tenant_id": str(row[0]), "created": False}
        t = uuid.uuid4()
        await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                             "VALUES (:t,:n,:f,'starter','trial',300,'completed',now(),now())"), {"t": t, "n": name, "f": DEMO_FAL})
        users = {}
        for role, full in (("admin", "مدير المكتب"), ("agent", "موظف مبيعات"), ("auditor", "مراجع")):
            u = uuid.uuid4()
            users[role] = str(u)
            await s.execute(text("INSERT INTO tenant_users (user_id,tenant_id,full_name,email,hashed_password,role,is_active,created_at,updated_at) "
                                 "VALUES (:u,:t,:n,:e,'!unusable',:r,true,now(),now())"),
                            {"u": u, "t": t, "n": full, "e": f"demo-{role}-{t.hex[:6]}@example.test", "r": role})
        for ad, ptype, district, price, beds, baths, area, desc in LISTINGS:
            await s.execute(text("INSERT INTO property_listings (listing_id,tenant_id,rega_ad_number,property_type,status,is_verified,district,city,price,bedrooms,bathrooms,area_sqm,description_ar,created_at,updated_at) "
                                 "VALUES (:i,:t,:ad,:pt,'VERIFIED_ACTIVE',true,:d,'الرياض',:p,:b,:ba,:a,:de,now(),now())"),
                            {"i": uuid.uuid4(), "t": t, "ad": ad, "pt": ptype, "d": district, "p": price, "b": beds, "ba": baths, "a": area, "de": desc})
        sales = {"enabled": True, "followup_enabled": True, "booking_enabled": True, "viewing_location_note": "مكتبنا في حي العليا — الدور الثاني"}
        comments = {"enabled": True, "mode": "public_and_private", "auto_hide_abusive": True}
        await s.execute(text("INSERT INTO tenant_preferences (tenant_id, sales, social_comments, updated_at) VALUES (:t, CAST(:s AS jsonb), CAST(:c AS jsonb), now())"),
                        {"t": t, "s": json.dumps(sales), "c": json.dumps(comments)})
        from src.shared.services.billing import service as billing

        sub = await billing.ensure_subscription(s, t)               # a real, NON-exempt trial: 14 days, trial limits (300 replies)
        return {"tenant_id": str(t), "created": True, "users": users, "listings": len(LISTINGS), "subscription": {"status": sub.status, "is_exempt": sub.is_exempt,
                                                                                                                     "trial_ends_at": sub.trial_ends_at.isoformat()}}


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed a non-exempt demo tenant (local development database only)")
    ap.add_argument("--name", default="مكتب الأفق العقاري (تجريبي)")
    ap.add_argument("--allow-host", action="append", default=[], help="extra database host accepted as local (e.g. a docker service name)")
    args = ap.parse_args()
    cfg = get_settings()
    reason = guard(cfg.postgres_host, cfg.app_env, set(args.allow_host))
    if reason:
        print(f"REFUSED: {reason}", file=sys.stderr)
        return 2
    print(json.dumps(asyncio.run(seed(args.name)), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
