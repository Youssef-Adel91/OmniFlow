"""
ai_engine/company_context.py — Company Profile → system-prompt block

The *static* half of the Knowledge Base (SRS §4.2).

Two halves, do not confuse them:

    company_profiles  (this module)  → ALWAYS in the system prompt.
        Small, curated facts the AI must never get wrong: who the company is,
        what it sells, where it operates, its commission policy, its hours,
        how to reach a human.

    knowledge_documents → Qdrant → RAG retrieval (rag_engine/retriever.py).
        Long-form uploaded material, pulled in only when the customer's
        question actually matches it.

Everything emitted here is billed on every single LLM call for the tenant, so
the block is hard-capped at `_MAX_BLOCK_CHARS` and the FAQ is capped at
`_MAX_FAQ_ITEMS`. If a tenant writes an essay in `business_description`, it is
truncated rather than allowed to crowd out the conversation history.

CACHING
-------
Profiles change roughly never, and the AI path is latency-sensitive, so the
rendered block is cached in-process for `_CACHE_TTL_SECONDS`. The settings
endpoint calls `invalidate_company_context(tenant_id)` after a write, which
makes an edit visible immediately in the process that served the PATCH; other
worker processes pick it up within the TTL. That trade-off is deliberate —
a shared Redis cache would be strictly better and is noted as a TODO.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Final

import structlog
from sqlalchemy import select

logger = structlog.get_logger(__name__)

_CACHE_TTL_SECONDS: Final[int] = 300
_MAX_BLOCK_CHARS: Final[int] = 2500
_MAX_FAQ_ITEMS: Final[int] = 8
_MAX_FAQ_ANSWER_CHARS: Final[int] = 300

# tenant_id (str) → (expires_at_epoch, rendered_block_or_None)
_CACHE: dict[str, tuple[float, str | None]] = {}

# tenant_id (str) → (expires_at_epoch, ai_system_prompt_or_None)
_PERSONA_CACHE: dict[str, tuple[float, str | None]] = {}


def invalidate_company_context(tenant_id: str | uuid.UUID) -> None:
    """Drop the cached block/persona for a tenant (call after any profile or persona write)."""
    key = str(tenant_id)
    _CACHE.pop(key, None)
    _PERSONA_CACHE.pop(key, None)


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _join_list(value: Any, limit: int = 20) -> str:
    """Render a JSONB array as a comma-separated Arabic list."""
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        items = [_clean(v) for v in value if _clean(v)]
        return "، ".join(items[:limit])
    return ""


def _render_social(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = [f"{k}: {_clean(v)}" for k, v in value.items() if _clean(v)]
    return " | ".join(parts)


def _render_faq(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    lines: list[str] = []
    for entry in value[:_MAX_FAQ_ITEMS]:
        if not isinstance(entry, dict):
            continue
        question = _clean(entry.get("question"))
        answer = _clean(entry.get("answer"))
        if not question or not answer:
            continue
        if len(answer) > _MAX_FAQ_ANSWER_CHARS:
            answer = answer[:_MAX_FAQ_ANSWER_CHARS].rstrip() + "…"
        lines.append(f"  - س: {question}\n    ج: {answer}")
    return lines


def format_company_profile(
    profile: Any,
    *,
    business_name: str | None = None,
) -> str | None:
    """
    Render a CompanyProfile ORM row (or any object/dict with the same fields)
    into the Arabic knowledge block injected into the system prompt.

    Returns None when there is nothing worth injecting, so callers can simply
    do `if block:` without checking for empty strings.
    """
    if profile is None and not business_name:
        return None

    def field(name: str) -> Any:
        if profile is None:
            return None
        if isinstance(profile, dict):
            return profile.get(name)
        return getattr(profile, name, None)

    lines: list[str] = ["## معلومات الشركة (مصدر موثوق — اعتمد عليها في ردودك):"]

    if business_name:
        lines.append(f"- الاسم: {business_name}")

    simple_fields: list[tuple[str, str]] = [
        ("نبذة", _clean(field("business_description"))),
        ("الخدمات", _join_list(field("services_offered"))),
        ("مناطق العمل", _join_list(field("target_areas"))),
        ("سياسة الأسعار/العمولة", _clean(field("pricing_policy"))),
        ("أوقات العمل", _clean(field("working_hours"))),
        ("هاتف التواصل", _clean(field("contact_phone"))),
        ("البريد الإلكتروني", _clean(field("contact_email"))),
        ("العنوان", _clean(field("contact_address"))),
        ("روابط التواصل", _render_social(field("social_links"))),
        ("ما يميزنا", _clean(field("unique_selling_points"))),
        ("السياسات", _clean(field("policies_text"))),
    ]
    for label, value in simple_fields:
        if value:
            lines.append(f"- {label}: {value}")

    faq_lines = _render_faq(field("faq"))
    if faq_lines:
        lines.append("- أسئلة شائعة:")
        lines.extend(faq_lines)

    # Only the header + name is not worth 40 tokens of prompt.
    if len(lines) <= 2:
        return None

    lines.append(
        "التزم بهذه المعلومات ولا تخترع تفاصيل غير مذكورة فيها. "
        "إذا سُئلت عن شيء غير موجود هنا، قل إنك ستتحقق وتحوّل العميل لأحد المستشارين."
    )

    block = "\n".join(lines)
    if len(block) > _MAX_BLOCK_CHARS:
        block = block[:_MAX_BLOCK_CHARS].rstrip() + "\n[…]"
    return block


async def get_company_context(tenant_id: str | uuid.UUID | None) -> str | None:
    """
    Load (and cache) the rendered company block for a tenant.

    Never raises: on any DB/import error it logs and returns None so the AI
    still answers, just without the company facts. Losing the block degrades
    answer quality; raising here would drop the customer's message entirely.
    """
    if not tenant_id:
        return None

    key = str(tenant_id)
    now = time.monotonic()

    cached = _CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1]

    block: str | None = None
    try:
        # Imported lazily: this module is pulled in by the AI hot path, and the
        # DB layer must not be a hard import dependency of prompt building.
        from src.shared.db.models import CompanyProfile, Tenant  # noqa: PLC0415
        from src.shared.db.session import get_tenant_session  # noqa: PLC0415

        tenant_uuid = uuid.UUID(key)
        async with get_tenant_session(tenant_uuid) as session:
            profile = await session.scalar(
                select(CompanyProfile).where(CompanyProfile.tenant_id == tenant_uuid)
            )
            business_name = await session.scalar(
                select(Tenant.business_name).where(Tenant.tenant_id == tenant_uuid)
            )
        block = format_company_profile(profile, business_name=business_name)
    except Exception as exc:  # noqa: BLE001 — degradation, never a hard failure
        logger.warning(
            "company_context_load_failed",
            tenant_id=key,
            error=str(exc)[:300],
        )
        # Cache the miss briefly so a broken DB does not get hammered per message.
        _CACHE[key] = (now + 30, None)
        return None

    _CACHE[key] = (now + _CACHE_TTL_SECONDS, block)
    logger.debug(
        "company_context_loaded",
        tenant_id=key,
        chars=len(block) if block else 0,
    )
    return block


async def get_tenant_persona(tenant_id: str | uuid.UUID | None) -> str | None:
    """
    Load (and cache) `Tenant.ai_system_prompt` — the per-tenant persona set at
    onboarding or via Settings. Returns None if unset or on any error, so
    callers can fall back to a generic default; never raises.

    Same cache/TTL/invalidation contract as `get_company_context` (both are
    cleared together by `invalidate_company_context`), kept as a separate
    cache because most callers of `get_company_context` today do not also
    need the persona, and vice versa.
    """
    if not tenant_id:
        return None

    key = str(tenant_id)
    now = time.monotonic()

    cached = _PERSONA_CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1]

    persona: str | None = None
    try:
        from src.shared.db.models import Tenant  # noqa: PLC0415
        from src.shared.db.session import get_tenant_session  # noqa: PLC0415

        tenant_uuid = uuid.UUID(key)
        async with get_tenant_session(tenant_uuid) as session:
            persona = await session.scalar(
                select(Tenant.ai_system_prompt).where(Tenant.tenant_id == tenant_uuid)
            )
    except Exception as exc:  # noqa: BLE001 — degradation, never a hard failure
        logger.warning("tenant_persona_load_failed", tenant_id=key, error=str(exc)[:300])
        _PERSONA_CACHE[key] = (now + 30, None)
        return None

    _PERSONA_CACHE[key] = (now + _CACHE_TTL_SECONDS, persona)
    return persona


def compose_system_prompt(base_prompt: str, company_block: str | None) -> str:
    """
    Combine the tenant persona with the company knowledge block.

    Persona first (it defines *how* to speak), facts second (they define *what*
    is true). The separator keeps the two visually distinct for the model.
    """
    if not company_block:
        return base_prompt
    return f"{base_prompt}\n\n---\n\n{company_block}"


__all__ = [
    "compose_system_prompt",
    "format_company_profile",
    "get_company_context",
    "get_tenant_persona",
    "invalidate_company_context",
]
