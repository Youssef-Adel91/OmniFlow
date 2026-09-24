"""
Real end-to-end validation of two of the three inbox quick actions (item 11):
internal notes and viewing appointments. Both were previously permanently
`disabled` buttons with no backend at all. (The third, "reports", is wired
frontend-only to the existing GET /api/v1/reports?customer_id= endpoint —
nothing new to validate here since that endpoint already has coverage.)

Real Postgres, through the actual endpoint functions
(create_conversation_note / list_conversation_notes / create_appointment /
list_appointments) with a real ConversationRepository bound to a real
tenant-scoped session — not the full FastAPI/HTTP/Clerk-auth layer, an
acknowledged scope reduction, same as validate_recommendations.py.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.gateway.routers.conversations import (
    AppointmentCreate,
    ConversationNoteCreate,
    create_appointment,
    create_conversation_note,
    list_appointments,
    list_conversation_notes,
)
from src.shared.core.config import get_settings
from src.shared.core.enums import Channel
from src.shared.db.models import Appointment, Conversation, ConversationNote, Customer, Tenant, TenantUser
from src.shared.db.repository import ConversationRepository
from src.shared.db.session import get_system_session, get_tenant_session

settings = get_settings()


def _require_local() -> None:
    if settings.postgres_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Postgres database")


async def _setup() -> dict:
    tenant_id, customer_id, conversation_id, user_id = (
        uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4(),
    )
    async with get_system_session() as session:
        session.add(Tenant(
            tenant_id=tenant_id,
            business_name="Quick Actions Drill Realty",
            fal_license_number=f"QA-{uuid.uuid4().hex[:10]}",
        ))
        session.add(Customer(
            customer_id=customer_id, tenant_id=tenant_id,
            unified_phone=f"+1555{uuid.uuid4().int % 10_000_000:07d}",
            display_name="Quick Actions Drill Customer",
        ))
        session.add(Conversation(
            conversation_id=conversation_id, tenant_id=tenant_id, customer_id=customer_id,
            channel=Channel.WHATSAPP, platform_conversation_id="qa-drill",
        ))
        session.add(TenantUser(
            user_id=user_id, tenant_id=tenant_id, full_name="Drill Agent",
            email=f"drill-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x",
        ))
    return {"tenant_id": tenant_id, "customer_id": customer_id, "conversation_id": conversation_id, "user_id": user_id}


async def _cleanup(ctx: dict) -> None:
    from sqlalchemy import delete

    async with get_system_session() as session:
        await session.execute(delete(Appointment).where(Appointment.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(ConversationNote).where(ConversationNote.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Conversation).where(Conversation.conversation_id == ctx["conversation_id"]))
        await session.execute(delete(Customer).where(Customer.customer_id == ctx["customer_id"]))
        await session.execute(delete(TenantUser).where(TenantUser.user_id == ctx["user_id"]))
        await session.execute(delete(Tenant).where(Tenant.tenant_id == ctx["tenant_id"]))
    print("cleanup: synthetic tenant/customer/conversation/user/notes/appointments deleted")


async def scenario_notes(ctx: dict) -> None:
    print("\n=== Notes: create + list via the real endpoint functions ===")
    fake_user = SimpleNamespace(tenant_id=ctx["tenant_id"], user_id=ctx["user_id"])

    async with get_tenant_session(ctx["tenant_id"]) as session:
        repo = ConversationRepository(session)
        created = await create_conversation_note(
            conversation_id=ctx["conversation_id"],
            body=ConversationNoteCreate(body="Customer is a repeat complainer — handle with care.", severity="warning"),
            user=fake_user,
            repo=repo,
        )
    assert created.severity == "warning"
    assert created.author_user_id == ctx["user_id"]
    print(f"PASS: note created (id={created.note_id}, severity={created.severity})")

    async with get_tenant_session(ctx["tenant_id"]) as session:
        repo = ConversationRepository(session)
        notes = await list_conversation_notes(conversation_id=ctx["conversation_id"], user=fake_user, repo=repo)
    assert len(notes) == 1, f"expected exactly 1 note, got {len(notes)}"
    assert notes[0].note_id == created.note_id
    print("PASS: note correctly listed back from a fresh real DB read")


async def scenario_appointments(ctx: dict) -> None:
    print("\n=== Appointments: reject past date, create real, list real ===")
    fake_user = SimpleNamespace(tenant_id=ctx["tenant_id"], user_id=ctx["user_id"])

    from fastapi import HTTPException

    async with get_tenant_session(ctx["tenant_id"]) as session:
        repo = ConversationRepository(session)
        try:
            await create_appointment(
                conversation_id=ctx["conversation_id"],
                body=AppointmentCreate(scheduled_at=datetime.now(timezone.utc) - timedelta(days=1)),
                user=fake_user,
                repo=repo,
            )
            raise AssertionError("expected a past scheduled_at to be rejected with 422")
        except HTTPException as exc:
            assert exc.status_code == 422
    print("PASS: past scheduled_at correctly rejected (422)")

    future = datetime.now(timezone.utc) + timedelta(days=2)
    async with get_tenant_session(ctx["tenant_id"]) as session:
        repo = ConversationRepository(session)
        created = await create_appointment(
            conversation_id=ctx["conversation_id"],
            body=AppointmentCreate(scheduled_at=future, location_note="Villa entrance, Narjis district"),
            user=fake_user,
            repo=repo,
        )
    assert created.customer_id == ctx["customer_id"], "appointment should inherit the conversation's real customer_id"
    assert created.status == "scheduled"
    print(f"PASS: real appointment created (id={created.appointment_id}, customer_id correctly inherited)")

    async with get_tenant_session(ctx["tenant_id"]) as session:
        repo = ConversationRepository(session)
        appts = await list_appointments(conversation_id=ctx["conversation_id"], user=fake_user, repo=repo)
    assert len(appts) == 1
    assert appts[0].appointment_id == created.appointment_id
    print("PASS: appointment correctly listed back from a fresh real DB read")


async def main() -> None:
    _require_local()
    ctx = await _setup()
    print(f"synthetic tenant={ctx['tenant_id']} conversation={ctx['conversation_id']}")
    try:
        await scenario_notes(ctx)
        await scenario_appointments(ctx)
        print("\nALL SCENARIOS PASSED")
    finally:
        await _cleanup(ctx)


if __name__ == "__main__":
    asyncio.run(main())
