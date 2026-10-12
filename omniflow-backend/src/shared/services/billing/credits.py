"""Hard AI-reply cap: monthly plan quota first, then purchased top-up credits, then stop — decided atomically in Redis BEFORE the LLM call.

Flow (LLM invoker): `reserve()` → LLM call → on failure `release()`. One Lua script does "count one more reply for this month; if that
is over the plan quota, undo it and try to take one top-up credit instead", so concurrent workers can never overshoot the cap
(INCR-then-check, and credits are a single check-and-DECR). Replies that never reach the LLM (deterministic L0 answers, handoffs,
canned fallbacks) are not metered because they cost nothing.

Durability: Redis is the hot path, Postgres the ledger.
  * `usage:{tenant}:{YYYY-MM}`  monthly counter, flushed to `usage_counters` by the existing Celery job.
  * `credits:{tenant}`          mirror of the tenant's spendable top-up balance (TTL 1 h, re-hydrated from `reply_credits`).
  * `credits_used:{tenant}`     top-up replies consumed since the last flush; the flush job drains it and deducts `reply_credits`
                                FIFO by expiry. A Redis flush can lose at most one flush interval of consumption (free replies, never lost money).
Failure policy: if Redis is unreachable the reply is allowed (and logged) — an outage of the cache must not silence every tenant's bot;
the cost exposure of that is bounded by the outage itself.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select

from src.shared.core.config import get_settings
from src.shared.db.models import ReplyCredit, TopupPack
from src.shared.db.session import get_system_session
from src.shared.redis_client import redis_mgr
from src.shared.services.billing import usage

logger = structlog.get_logger(__name__)

_USAGE_TTL = 45 * 86400
_BAL_TTL = 3600
_BAL_KEY = "credits:{tid}"
_USED_KEY = "credits_used:{tid}"
_L3_KEY = "l3:{tid}:{period}"

# KEYS: 1 usage counter, 2 credit balance mirror, 3 consumed-since-flush counter.  ARGV: 1 limit (<0 = unmetered), 2 usage ttl.
# Returns {code, source}: code 1 = allowed, 0 = exhausted, 2 = balance mirror not loaded yet (caller hydrates and retries).
_RESERVE = """
local limit = tonumber(ARGV[1])
local c = redis.call('INCR', KEYS[1])
if c == 1 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2])) end
if limit < 0 or c <= limit then return {1, 'plan'} end
redis.call('DECR', KEYS[1])
local bal = redis.call('GET', KEYS[2])
if not bal then return {2, 'load'} end
if tonumber(bal) > 0 then
  redis.call('DECR', KEYS[2])
  redis.call('INCR', KEYS[3])
  return {1, 'topup'}
end
return {0, 'exhausted'}
"""


@dataclass(frozen=True)
class Reservation:
    allowed: bool
    source: str            # plan | topup | unmetered | exhausted
    used: int | None = None


def _usage_key(tenant_id: uuid.UUID, now: datetime | None = None) -> str:
    return usage._key(tenant_id, usage.period_of(now))


async def _db_balance(tenant_id: uuid.UUID, now: datetime) -> int:
    async with get_system_session() as session:
        total = await session.scalar(select(func.coalesce(func.sum(ReplyCredit.replies_remaining), 0)).where(
            ReplyCredit.tenant_id == tenant_id, ReplyCredit.expires_at > now, ReplyCredit.replies_remaining > 0))
    return int(total or 0)


async def hydrate(tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    """(Re)load the spendable balance mirror from the ledger, minus consumption not yet flushed. SET NX: never clobbers a live value."""
    now = now or datetime.now(timezone.utc)
    pending = await redis_mgr.get_raw(_USED_KEY.format(tid=tenant_id))
    bal = max(0, await _db_balance(tenant_id, now) - (int(pending) if pending and pending.isdigit() else 0))
    await redis_mgr.set_if_absent(_BAL_KEY.format(tid=tenant_id), str(bal), ttl=_BAL_TTL)
    return bal


async def reserve(tenant_id: uuid.UUID, snap: dict, now: datetime | None = None) -> Reservation:
    """Count one AI reply against the plan quota, falling back to top-up credits. `snap` = entitlements.snapshot()."""
    cfg = get_settings()
    limit = None if (snap.get("exempt") or not cfg.billing_enforce_message_limit) else (snap.get("limits") or {}).get("messages_per_month")
    keys = [_usage_key(tenant_id, now), _BAL_KEY.format(tid=tenant_id), _USED_KEY.format(tid=tenant_id)]
    args = [int(limit) if limit else -1, _USAGE_TTL]
    try:
        for _ in range(2):
            code, source = await redis_mgr.eval_script(_RESERVE, keys, args)
            code = int(code)
            if code == 2:
                await hydrate(tenant_id, now)
                continue
            return Reservation(code == 1, ("unmetered" if (code == 1 and limit is None and source == "plan") else str(source)))
        return Reservation(False, "exhausted")
    except Exception as exc:  # noqa: BLE001 - cache outage must not silence every tenant
        logger.error("credits_reserve_failed_open", tenant_id=str(tenant_id), error=str(exc)[:200])
        return Reservation(True, "unmetered")


async def release(tenant_id: uuid.UUID, res: Reservation, now: datetime | None = None) -> None:
    """Give back a reservation when the LLM call failed (the customer got no AI answer, so no quota is spent)."""
    try:
        if res.source == "plan":
            await redis_mgr.eval_script("local v = tonumber(redis.call('GET', KEYS[1]) or '0'); if v > 0 then redis.call('DECR', KEYS[1]) end; return v",
                                        [_usage_key(tenant_id, now)], [])
        elif res.source == "topup":
            await redis_mgr.eval_script("if redis.call('EXISTS', KEYS[1]) == 1 then redis.call('INCR', KEYS[1]) end; "
                                        "local u = tonumber(redis.call('GET', KEYS[2]) or '0'); if u > 0 then redis.call('DECR', KEYS[2]) end; return u",
                                        [_BAL_KEY.format(tid=tenant_id), _USED_KEY.format(tid=tenant_id)], [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("credits_release_failed", tenant_id=str(tenant_id), error=str(exc)[:200])


async def total_used(tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    raw = await redis_mgr.get_raw(_usage_key(tenant_id, now))
    return int(raw) if raw and raw.isdigit() else 0


# ── most expensive tier (L3) share ────────────────────────────────────────────────

async def l3_allowed(tenant_id: uuid.UUID, now: datetime | None = None) -> bool:
    """L3 may answer at most `llm_l3_max_share_pct` % of a tenant's monthly AI replies (the first L3 reply of a month is always allowed).
    Called AFTER `reserve`, so the monthly counter already includes the reply being answered."""
    cfg = get_settings()
    share = cfg.llm_l3_max_share_pct
    if share >= 100:
        return True
    key = _L3_KEY.format(tid=tenant_id, period=usage.period_of(now))
    total = await total_used(tenant_id, now)
    try:
        used = int(await redis_mgr.get_raw(key) or 0)
        if used + 1 > max(1, (share * max(total, 1)) // 100):
            return False
        await redis_mgr.incr_counter(key, ttl=_USAGE_TTL)
        return True
    except Exception:  # noqa: BLE001
        return True


# ── purchases / ledger ────────────────────────────────────────────────────────────

async def grant(session, *, tenant_id: uuid.UUID, pack: TopupPack, transaction_id: uuid.UUID | None, source: str = "purchase",
                now: datetime | None = None) -> ReplyCredit:
    """Add a purchased block to the ledger (idempotent on the transaction) and mirror it into Redis once the DB commit succeeds."""
    now = now or datetime.now(timezone.utc)
    if transaction_id is not None:
        existing = await session.scalar(select(ReplyCredit).where(ReplyCredit.transaction_id == transaction_id))
        if existing is not None:
            return existing
    from dateutil.relativedelta import relativedelta

    credit = ReplyCredit(credit_id=uuid.uuid4(), tenant_id=tenant_id, pack_code=pack.code, replies_total=pack.replies,
                         replies_remaining=pack.replies, source=source, transaction_id=transaction_id, purchased_at=now,
                         expires_at=now + relativedelta(months=pack.validity_months))
    session.add(credit)
    await session.flush()
    _after_commit(session, tenant_id, pack.replies)
    return credit


def _after_commit(session, tenant_id: uuid.UUID, replies: int) -> None:
    """Mirror the new credits into Redis only after the ledger row is durable (a rolled-back purchase must never grant replies)."""
    import asyncio

    from sqlalchemy import event

    async def _apply() -> None:
        key = _BAL_KEY.format(tid=tenant_id)
        if await redis_mgr.get_raw(key) is not None:
            await redis_mgr.incr_by(key, replies, ttl=_BAL_TTL)
        else:
            await hydrate(tenant_id)

    def _fire(_sync_session) -> None:
        try:
            asyncio.get_running_loop().create_task(_apply())
        except RuntimeError:       # no running loop (sync context): the 1 h mirror TTL re-hydrates from the ledger anyway
            pass

    event.listen(session.sync_session, "after_commit", _fire, once=True)


async def balance(session, tenant_id: uuid.UUID, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = (await session.execute(select(ReplyCredit).where(
        ReplyCredit.tenant_id == tenant_id, ReplyCredit.expires_at > now, ReplyCredit.replies_remaining > 0)
        .order_by(ReplyCredit.expires_at))).scalars().all()
    pending = await redis_mgr.get_raw(_USED_KEY.format(tid=tenant_id))
    remaining = max(0, sum(r.replies_remaining for r in rows) - (int(pending) if pending and pending.isdigit() else 0))
    return {"remaining": remaining, "packs": [{"pack_code": r.pack_code, "remaining": r.replies_remaining, "total": r.replies_total,
                                               "expires_at": r.expires_at.isoformat()} for r in rows]}


async def flush_consumption(now: datetime | None = None) -> int:
    """Drain the Redis consumption counters into the ledger, FIFO by expiry. Returns the number of replies deducted."""
    now = now or datetime.now(timezone.utc)
    deducted = 0
    counters = await redis_mgr.scan_counters("credits_used:*")
    for key, _n in counters.items():
        tid = uuid.UUID(key.split(":", 1)[1])
        taken = await redis_mgr.getdel_raw(key)
        n = int(taken) if taken and taken.isdigit() else 0
        if n <= 0:
            continue
        async with get_system_session() as session:
            rows = (await session.execute(select(ReplyCredit).where(
                ReplyCredit.tenant_id == tid, ReplyCredit.replies_remaining > 0, ReplyCredit.expires_at > now)
                .order_by(ReplyCredit.expires_at).with_for_update())).scalars().all()
            left = n
            for r in rows:
                take = min(left, r.replies_remaining)
                r.replies_remaining -= take
                left -= take
                if not left:
                    break
            deducted += n - left
    return deducted


async def exhausted_alert_once(tenant_id: uuid.UUID, now: datetime | None = None) -> bool:
    """True the first time in a month that a tenant hits the wall (so the alert goes out once, not per message)."""
    return await redis_mgr.set_if_absent(f"quota_exhausted_alert:{tenant_id}:{usage.period_of(now)}", "1", ttl=_USAGE_TTL)
