"""
Real end-to-end validation of the "tell us about your business" onboarding
gap discovered while registering the Naeem test tenant.

Before this fix, `PATCH /api/v1/tenants/onboarding` only ever handled the
WhatsApp/Meta channel-linking step — no field anywhere let a new tenant
describe their own business, and `Tenant.business_name` stayed whatever the
Clerk-webhook auto-provisioner guessed ("Workspace for {name}"). A tenant's
AI had zero real company knowledge until someone separately discovered the
disconnected dashboard Settings/Knowledge screens. Worse: the default
persona in llm_orchestrator.py is hardcoded to a Saudi real-estate
consultant ("أحمد الصائغ") — actively wrong for any non-real-estate tenant.

This exercises the REAL `update_tenant_onboarding` endpoint function (not a
reimplementation) against real Postgres, and confirms the data actually
reaches the two real consumers: `company_context.get_company_context()`
(the RAG-facing knowledge block) and the resolved AI system prompt
(`_resolve_system_prompt`), not just that a DB row was written and ignored.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_engine.company_context import get_company_context
from src.gateway.routers.tenants import update_tenant_onboarding
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import CompanyProfile, Tenant, TenantUser
from src.shared.db.session import get_system_session, get_tenant_session
from src.shared.schemas.schemas import TenantOnboardingUpdate

settings = get_settings()


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


async def _setup() -> dict:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name=f"Workspace for Drill User {uuid.uuid4().hex[:6]}",
            fal_license_number=f"ONB-{uuid.uuid4().hex[:10]}",
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
        await session.execute(delete(CompanyProfile).where(CompanyProfile.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(TenantUser).where(TenantUser.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/admin/profile deleted")


async def main() -> None:
    _require_local()
    ctx = await _setup()
    print(f"synthetic tenant={ctx['tenant_id']}")
    try:
        async with get_system_session() as session:
            user = await session.get(TenantUser, ctx["user_id"])

            print("\n=== Driving the real onboarding endpoint with business data ===")
            response = await update_tenant_onboarding(
                payload=TenantOnboardingUpdate(
                    option="self_service",
                    business_name="نعيم",
                    business_category="أجهزة مساج احترافية",
                    about_text="ليه نعيم؟ في نعيم، إحنا مؤمنين إن الراحة مش رفاهية.",
                    products=["أجهزة مساج القدم", "أجهزة مساج الركبة"],
                    meta_access_token="EAADdrilltoken123",
                    whatsapp_phone_number_id="1234567890",
                    whatsapp_waba_id="0987654321",
                ),
                current_user=user,
            )
            assert response.onboarding_status == "completed"
            print(f"PASS: real endpoint accepted the onboarding submission, status={response.onboarding_status}")

        # ── Confirm Tenant.business_name actually changed, not just the WhatsApp fields ──
        async with get_tenant_session(ctx["tenant_id"]) as session:
            tenant = await session.get(Tenant, ctx["tenant_id"])
            assert tenant.business_name == "نعيم", f"expected 'نعيم', got {tenant.business_name!r}"
            assert tenant.whatsapp_phone_number_id == "1234567890"
            print("PASS: Tenant.business_name updated to the real submitted value (not left as 'Workspace for ...')")

            assert tenant.ai_system_prompt, "expected a generated persona, got None"
            assert "عقاري" not in tenant.ai_system_prompt, (
                "the generated persona must NOT be the hardcoded real-estate default"
            )
            assert "نعيم" in tenant.ai_system_prompt
            print(f"PASS: a non-real-estate persona was generated: {tenant.ai_system_prompt[:80]}...")

        # ── The actual gap being tested: does this reach the RAG/AI knowledge block? ──
        block = await get_company_context(ctx["tenant_id"])
        assert block is not None, "get_company_context returned nothing — onboarding data never reached the AI"
        assert "نعيم" in block, "business name missing from the real AI-facing knowledge block"
        assert "أجهزة مساج احترافية" in block, "category missing from the real AI-facing knowledge block"
        assert "أجهزة مساج القدم" in block, "product missing from the real AI-facing knowledge block"
        print("PASS: the real company_context.get_company_context() block contains the submitted business data")
        print(f"\n--- rendered block ---\n{block}\n----------------------")

        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())
