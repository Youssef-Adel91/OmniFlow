"""Check what a connected Facebook Page / Instagram account is missing on the Meta side (READ-ONLY: it never changes anything at Meta).

    python -m scripts.meta_check_subscriptions --tenant-id <uuid>          # reads the tenant's stored Page + token (decrypted in memory only)
    python -m scripts.meta_check_subscriptions --all                       # every tenant that has a Page connected
    python -m scripts.meta_check_subscriptions --page-id 123 --token-env PAGE_TOKEN [--instagram]

What it prints per Page: whether the token is valid and when it expires, which permissions it carries vs. the ones OmniFlow needs,
and which webhook fields the app is subscribed to vs. the ones OmniFlow needs (messages, messaging_postbacks, message_deliveries,
message_reads, message_reactions, feed = comments, mention). Anything missing is listed in Arabic with what stops working.
Exit code 0 = everything present, 1 = something missing/invalid, 2 = usage / nothing to check.

Needs META_APP_ID + META_APP_SECRET in the environment (token inspection uses the app token). Tokens are never printed.
To FIX a missing subscription use "Connect with Facebook" again in Settings → Channels (it subscribes the full set).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import select

from src.shared.db.models import Tenant
from src.shared.db.session import get_system_session
from src.shared.security.crypto import reveal_secret
from src.shared.services import meta_diagnostics


def _line(ok: bool, text: str) -> str:
    return f"  [{'OK' if ok else '!!'}] {text}"


def format_report(label: str, rep: meta_diagnostics.PageReport) -> list[str]:
    out = [f"== {label}"]
    if not rep.reachable:
        return out + [_line(False, rep.error or "تعذّر الاتصال بـ Meta")]
    tv = "غير معروف (لا يوجد META_APP_ID/META_APP_SECRET)" if rep.token_valid is None else ("صالح" if rep.token_valid else "غير صالح — أعد الربط")
    out.append(_line(rep.token_valid is not False, f"الرمز: {tv}" + (f"، ينتهي {rep.expires_at:%Y-%m-%d}" if rep.expires_at else "، لا ينتهي أو غير معروف")))
    out.append(_line(not rep.missing_scopes, "الصلاحيات: " + (", ".join(rep.scopes) if rep.scopes else "غير معروفة")))
    for s in rep.missing_scopes:
        out.append(_line(False, f"صلاحية ناقصة {s} → {meta_diagnostics.FEATURE_BY_SCOPE.get(s, s)}"))
    out.append(_line(not rep.missing_fields, "الاشتراكات: " + (", ".join(rep.subscribed_fields) if rep.subscribed_fields else "لا شيء")))
    for f in rep.missing_fields:
        out.append(_line(False, f"اشتراك ناقص {f} → {meta_diagnostics.FEATURE_BY_FIELD.get(f, f)}"))
    if rep.error:
        out.append(_line(False, rep.error))
    return out


async def _tenants(tenant_id: str | None) -> list[tuple[str, str, str, bool]]:
    async with get_system_session() as session:
        stmt = select(Tenant.tenant_id, Tenant.instagram_page_id, Tenant.instagram_page_access_token, Tenant.instagram_account_id).where(
            Tenant.instagram_page_id.is_not(None), Tenant.instagram_page_access_token.is_not(None))
        if tenant_id:
            stmt = stmt.where(Tenant.tenant_id == tenant_id)
        rows = (await session.execute(stmt)).all()
    return [(str(t), p, reveal_secret(tok) or "", bool(ig)) for t, p, tok, ig in rows]


async def run(args: argparse.Namespace) -> int:
    targets: list[tuple[str, str, str, bool]] = []
    if args.page_id:
        token = os.environ.get(args.token_env or "", "")
        if not token:
            print(f"المتغير {args.token_env!r} فارغ أو غير موجود.", file=sys.stderr)
            return 2
        targets = [("manual", args.page_id, token, bool(args.instagram))]
    else:
        targets = await _tenants(args.tenant_id)
    if not targets:
        print("لا توجد صفحة مربوطة للفحص.", file=sys.stderr)
        return 2
    bad = 0
    for tenant, page_id, token, has_ig in targets:
        rep = await meta_diagnostics.inspect_page(page_id, token, has_instagram=has_ig)
        print("\n".join(format_report(f"tenant {tenant[:8]}… page {page_id}" + (" + instagram" if has_ig else ""), rep)))
        bad += 0 if rep.ok else 1
    print(f"\n{len(targets) - bad}/{len(targets)} سليمة.")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tenant-id")
    g.add_argument("--all", action="store_true")
    g.add_argument("--page-id")
    ap.add_argument("--token-env", help="name of the environment variable holding the Page token (with --page-id)")
    ap.add_argument("--instagram", action="store_true", help="the Page has a linked Instagram account (with --page-id)")
    args = ap.parse_args(argv)
    if args.page_id and not args.token_env:
        ap.error("--page-id needs --token-env")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
