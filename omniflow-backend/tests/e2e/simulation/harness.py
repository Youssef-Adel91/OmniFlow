"""
Customer-simulation harness. Drives REAL code (sales hook, booking, follow-ups, hand-off, output guard) on PostgreSQL/RLS + a Redis stand-in, with a SCRIPTED model:
no Meta, no LLM, no network. It answers "what would a customer experience" for scripted roles; it does NOT prove anything about real Meta delivery or real model behaviour.

    chat = await sim.customer("سالم")          # a new customer + conversation (a fresh WhatsApp/Instagram/... thread)
    turn = await chat.say("ابغى شقة ٣ غرف في النرجس")   # -> Turn(reply, source="rule"|"model"|"human", findings, handoff)

The scripted model is deliberately naive: it quotes the first listing of the inventory block it was given, or asks a question. `hostile_model=True` makes it misbehave
(invented price, discount, phone number) so the output guard is exercised.
"""
from __future__ import annotations

import json
import os
import re
import unittest
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from src.sales import hook, output_guard
from src.shared.core.config import get_settings
from src.shared.services import tenant_prefs
from src.shared.services.handoff import perform_handoff

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RLS_URL = os.environ.get("OMNIFLOW_TEST_RLS_URL")
UTC = timezone.utc
NOW = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)          # Sunday 09:00 Riyadh: inside the default viewing days

LISTINGS = [  # (ad number, type, district, price, bedrooms)
    ("7100000001", "apartment", "النرجس", 850000, 3),
    ("7100000002", "villa", "الياسمين", 1950000, 5),
    ("7100000003", "apartment", "الملقا", 620000, 2),
]


class FakeRedis:
    def __init__(self):
        self.d: dict[str, str] = {}

    async def get_raw(self, k):
        return self.d.get(k)

    async def set_raw(self, k, v, ttl=None):
        self.d[k] = v

    async def delete_raw(self, k):
        self.d.pop(k, None)


@dataclass
class Turn:
    text: str
    reply: str | None
    source: str                                   # rule | model | human (nobody answers: a person owns the conversation)
    event: str | None = None
    handoff: str | None = None
    findings: list[str] = field(default_factory=list)


def scripted_model(addendum: str, hostile: bool = False) -> str:
    if hostile:
        return "عندي لك فيلا بسعر 1,200,000 ريال ونعطيك خصم 15% وكلم المدير على 0599999999"
    for line in (addendum or "").splitlines():                 # the inventory block only (not the customer summary or the rules)
        m = re.search(r"(\d{1,3}(?:,\d{3})+) ريال", line)
        if m and not line.startswith(("ما قاله", "-", "=")):
            return f"عندنا خيار مناسب: {line.strip(' :-•')[:80]}. تحب نرتب لك معاينة؟"
    return "أهلًا فيك، وش نوع العقار والحي اللي تفضله؟"


class Chat:
    def __init__(self, sim: "Sim", customer_id, conv_id, channel: str):
        self.sim, self.customer_id, self.conv_id, self.channel = sim, customer_id, conv_id, channel
        self.turns: list[Turn] = []
        self.hostile_model = False

    async def status(self) -> str:
        return (await self.sim.q("SELECT status FROM conversations WHERE conversation_id=:c", c=self.conv_id))[0][0]

    async def say(self, text: str, *, now: datetime | None = None) -> Turn:
        sim = self.sim
        now = now or sim.now
        await sim.record("customer", self.conv_id, text, now)
        if await self.status() != "ai_active":                      # the router would send it to the human queue: the assistant stays silent
            t = Turn(text, None, "human")
            self.turns.append(t)
            return t
        d = SimpleNamespace(tenant_id=sim.tenant, conversation_id=self.conv_id, customer_id=self.customer_id,
                            event=SimpleNamespace(text_content=text, channel=self.channel))
        res = await hook.before_llm(d, now=now)
        if res.reply:
            if res.handoff_reason:
                await perform_handoff(sim.tenant, self.conv_id, reason=res.handoff_reason, now=now)
            t = Turn(text, res.reply, "rule", res.event, res.handoff_reason)
            await sim.record("ai_bot", self.conv_id, res.reply, now)
        else:
            raw = scripted_model(res.system_addendum or "", self.hostile_model)
            context = f"{sim.company_context}\n{res.system_addendum or ''}\n" + "\n".join(x.text for x in self.turns) + f"\n{text}"
            findings = output_guard.vet_answer(raw, context=context) if res.system_addendum is not None else []
            reply = output_guard.SAFE_FALLBACK_AR if findings else raw
            t = Turn(text, reply, "model", res.event, findings=findings)
            await sim.record("ai_bot", self.conv_id, reply, now)
            await hook.after_reply(d, now=now)
        self.turns.append(t)
        return t


class SimCase(unittest.IsolatedAsyncioTestCase):
    """Base for the role files. One tenant per test (non-exempt trial-like: Starter, active), RLS enforced."""

    sales_prefs: dict = {}
    company_context = "شركة الأفق العقارية"
    now = NOW

    async def asyncSetUp(self):
        if not (DB_URL and RLS_URL):
            self.skipTest("needs OMNIFLOW_TEST_DATABASE_URL + OMNIFLOW_TEST_RLS_URL")
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        self.text = text
        self.admin_engine, self.app_engine = create_async_engine(DB_URL), create_async_engine(RLS_URL)
        self.Admin = async_sessionmaker(self.admin_engine, expire_on_commit=False)
        self.App = async_sessionmaker(self.app_engine, expire_on_commit=False)
        self.redis = FakeRedis()
        for p in (patch("src.shared.db.session.AsyncSessionFactory", self.App), patch.object(hook, "redis_mgr", self.redis),
                  patch.object(get_settings(), "feature_sales_closing", True)):
            p.start()
            self.addCleanup(p.stop)
        self.tenant, self.staff, self.admin_user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        tenant_prefs.invalidate(self.tenant)
        async with self.Admin() as s:
            await s.execute(text("INSERT INTO tenants (tenant_id,business_name,fal_license_number,subscription_tier,status,max_ai_conversations,onboarding_status,created_at,updated_at) "
                                 "VALUES (:t,'الأفق العقارية',:f,'starter','active',300,'completed',now(),now())"), {"t": self.tenant, "f": f"FAL-{self.tenant.hex[:8]}"})
            for uid, role in ((self.staff, "agent"), (self.admin_user, "admin")):
                await s.execute(text("INSERT INTO tenant_users (user_id,tenant_id,full_name,email,hashed_password,role,is_active,created_at,updated_at) "
                                     "VALUES (:u,:t,:n,:e,'x',:r,true,now(),now())"), {"u": uid, "t": self.tenant, "n": role, "e": f"{role}{uuid.uuid4().hex[:8]}@example.com", "r": role})
            for ad, typ, district, price, beds in LISTINGS:
                await s.execute(text("INSERT INTO property_listings (listing_id,tenant_id,rega_ad_number,property_type,status,is_verified,district,city,price,bedrooms,created_at,updated_at) "
                                     "VALUES (:i,:t,:ad,:ty,'VERIFIED_ACTIVE',true,:d,'الرياض',:p,:b,now(),now())"),
                                {"i": uuid.uuid4(), "t": self.tenant, "ad": ad, "ty": typ, "d": district, "p": price, "b": beds})
            await s.commit()
        await self.set_sales(**self.sales_prefs)
        self.addAsyncCleanup(self._cleanup)

    async def _cleanup(self):
        async with self.Admin() as s:
            for table in ("followup_tasks", "deals", "appointments", "notifications", "messages", "property_listings", "tenant_preferences", "conversations", "customers",
                          "tenant_users", "audit_logs", "tenants"):
                col = "conversation_id IN (SELECT conversation_id FROM conversations WHERE tenant_id=:t)" if table == "messages" else "tenant_id=:t"
                await s.execute(self.text(f"DELETE FROM {table} WHERE {col}"), {"t": self.tenant})
            await s.commit()
        tenant_prefs.invalidate(self.tenant)
        await self.admin_engine.dispose()
        await self.app_engine.dispose()

    async def set_sales(self, **over):
        sales = {"enabled": True, "followup_enabled": True, "reminder_hours_before": 2, **over}
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO tenant_preferences (tenant_id, sales, updated_at) VALUES (:t, CAST(:j AS jsonb), now()) "
                                      "ON CONFLICT (tenant_id) DO UPDATE SET sales = EXCLUDED.sales"), {"t": self.tenant, "j": json.dumps(sales)})
            await s.commit()
        tenant_prefs.invalidate(self.tenant)

    async def q(self, sql, **params):
        async with self.Admin() as s:
            return (await s.execute(self.text(sql), {"t": self.tenant, **params})).all()

    async def record(self, sender, conv_id, text, when):
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO messages (message_id,conversation_id,sender_type,message_type,text_content,created_at) VALUES (:m,:c,:s,'text',:x,:w)"),
                            {"m": uuid.uuid4(), "c": conv_id, "s": sender, "x": text, "w": when})
            await s.commit()

    async def customer(self, name="عميل", channel="whatsapp", tenant=None) -> Chat:
        cust, conv = uuid.uuid4(), uuid.uuid4()
        async with self.Admin() as s:
            await s.execute(self.text("INSERT INTO customers (customer_id,tenant_id,unified_phone,display_name,is_processing_restricted,is_vip,vcard_state,created_at,updated_at) "
                                      "VALUES (:c,:t,:p,:n,false,false,'new',now(),now())"), {"c": cust, "t": self.tenant, "p": f"+9665{uuid.uuid4().int % 10**8:08d}", "n": name})
            await s.execute(self.text("INSERT INTO conversations (conversation_id,tenant_id,customer_id,channel,status,message_count,created_at,updated_at) "
                                      "VALUES (:v,:t,:c,:ch,'ai_active',0,now(),now())"), {"v": conv, "t": self.tenant, "c": cust, "ch": channel})
            await s.commit()
        chat = Chat(self, cust, conv, channel)
        chat.sim = self
        return chat

    async def staff_replies(self, chat: Chat, text: str):
        """A salesperson takes the conversation and answers (the assistant must stay out of it)."""
        async with self.Admin() as s:
            await s.execute(self.text("UPDATE conversations SET status='human_active', assigned_agent_id=:a WHERE conversation_id=:c"), {"a": self.staff, "c": chat.conv_id})
            await s.commit()
        await self.record("agent", chat.conv_id, text, self.now)


# the Chat class reads `sim.tenant`, `sim.q`, `sim.record`, `sim.now`, `sim.company_context`: SimCase provides them all
Sim = SimCase
