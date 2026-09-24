"""
Real end-to-end validation of item 7's sign-off decision: the tenant's
VCard contact number is normalized to full international E.164 format
(not a bare local number), defaulting to the Saudi country code when the
admin types a number with no country code, but never overriding a number
that already has its own country code.

Real Postgres, through the actual `update_settings_endpoint` function with
a real `Tenant`/`TenantUser` row and a real tenant-scoped session — not a
reimplementation of `_normalize_e164`.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError

from src.gateway.routers.settings import TenantSettingsPatch, update_settings_endpoint
from src.shared.core.config import get_settings
from src.shared.core.enums import TenantUserRole
from src.shared.db.models import Tenant, TenantUser
from src.shared.db.session import get_system_session, get_tenant_session

settings = get_settings()


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


async def _setup() -> dict:
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id, business_name="E164 Drill Realty",
            fal_license_number=f"E164-{uuid.uuid4().hex[:10]}",
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
        await session.execute(delete(TenantUser).where(TenantUser.tenant_id == ctx["tenant_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/admin deleted")


async def _patch(ctx: dict, raw_phone: str):
    async with get_tenant_session(ctx["tenant_id"]) as session:
        user = await session.get(TenantUser, ctx["user_id"])
        result = await update_settings_endpoint(
            user=user, session=session,
            body=TenantSettingsPatch(whatsapp_display_phone_number=raw_phone),
        )
        return result


async def main() -> None:
    _require_local()
    ctx = await _setup()
    print(f"synthetic tenant={ctx['tenant_id']}")
    try:
        # Scenario A: local Saudi format (no country code) defaults to +966.
        result = await _patch(ctx, "0501234567")
        assert result.whatsapp_display_phone_number == "+966501234567", (
            f"expected +966501234567, got {result.whatsapp_display_phone_number!r}"
        )
        print("PASS: local Saudi number '0501234567' normalized to '+966501234567'")

        # Scenario B: a number WITH its own country code is respected as-is,
        # not forced into +966 — a brokerage's customers aren't all Saudi.
        result = await _patch(ctx, "+201001234567")
        assert result.whatsapp_display_phone_number == "+201001234567", (
            f"expected the Egyptian number preserved, got {result.whatsapp_display_phone_number!r}"
        )
        print("PASS: a number with its own country code (+20...) is preserved, not forced to +966")

        # Scenario C: garbage input is rejected, not silently accepted. This
        # raises pydantic's ValidationError here (constructing the request
        # body directly in Python); under real FastAPI request handling the
        # same validator failure is what produces the framework's real 422.
        try:
            await _patch(ctx, "not-a-phone-number")
            raise AssertionError("expected a ValidationError for invalid input")
        except ValidationError as exc:
            assert "Not a valid phone number" in str(exc)
            print("PASS: invalid input is rejected by the real validator (FastAPI turns this into a 422)")

        # Scenario D: the persisted value survives a fresh read (not just the
        # response object) — confirms it actually landed in the DB row.
        async with get_tenant_session(ctx["tenant_id"]) as session:
            from sqlalchemy import select
            tenant = await session.scalar(select(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
            assert tenant.whatsapp_display_phone_number == "+201001234567"
        print("PASS: the normalized E.164 value is durably persisted, confirmed on a fresh read")

        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())
