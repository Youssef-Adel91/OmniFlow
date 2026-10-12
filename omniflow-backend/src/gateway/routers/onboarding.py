"""
gateway/routers/onboarding.py — "first steps" checklist shown on the dashboard until the workspace is ready.

Every step is derived from real data (company profile, persona, properties, channel link, a successful bot test), so it can never
claim progress that did not happen. The only stored state is `tenants.onboarding_state` (bot_tested_at, dismissed_at).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from src.gateway.dependencies import AuthTenantSession, CurrentUser
from src.shared.db.models import CompanyProfile, PropertyListing, Tenant

router = APIRouter(prefix="/api/v1/onboarding", tags=["Onboarding"])

STEP_ORDER = ("company", "persona", "properties", "channel", "bot_test")


def compute_steps(*, has_company: bool, has_persona: bool, properties: int, wa: bool, fb: bool, bot_tested: bool) -> list[dict[str, Any]]:
    """Pure function (unit-tested): one dict per step with `done` and where to go to finish it."""
    done = {"company": has_company, "persona": has_persona, "properties": properties > 0, "channel": wa or fb, "bot_test": bot_tested}
    href = {"company": "/knowledge", "persona": "/settings#ai", "properties": "/properties", "channel": "/settings#channels", "bot_test": "/settings#ai"}
    return [{"key": k, "done": done[k], "href": href[k]} for k in STEP_ORDER]


@router.get("/checklist", summary="Setup checklist derived from the workspace's real data")
async def checklist(user: CurrentUser, session: AuthTenantSession) -> dict[str, Any]:
    t = await session.scalar(select(Tenant).where(Tenant.tenant_id == user.tenant_id))
    profile = await session.scalar(select(CompanyProfile).where(CompanyProfile.tenant_id == user.tenant_id))
    props = await session.scalar(select(func.count()).select_from(PropertyListing).where(PropertyListing.tenant_id == user.tenant_id)) or 0
    state = (t.onboarding_state if t else None) or {}
    has_company = bool(profile and ((profile.business_description or "").strip() or profile.services_offered or profile.target_areas))
    steps = compute_steps(
        has_company=has_company,
        has_persona=bool(t and ((t.ai_system_prompt or "").strip() or (t.custom_ai_instructions or "").strip())),
        properties=int(props),
        wa=bool(t and t.whatsapp_phone_number_id and t.meta_access_token),
        fb=bool(t and t.instagram_page_id and t.instagram_page_access_token),
        bot_tested=bool(state.get("bot_tested_at")),
    )
    done = sum(1 for s in steps if s["done"])
    return {"steps": steps, "done": done, "total": len(steps), "complete": done == len(steps), "dismissed": bool(state.get("dismissed_at"))}


@router.post("/checklist/dismiss", summary="Hide the checklist from the dashboard")
async def dismiss(user: CurrentUser, session: AuthTenantSession) -> dict[str, bool]:
    t = await session.scalar(select(Tenant).where(Tenant.tenant_id == user.tenant_id))
    if t:
        t.onboarding_state = {**(t.onboarding_state or {}), "dismissed_at": datetime.now(timezone.utc).isoformat()}
        await session.flush()
    return {"dismissed": True}
