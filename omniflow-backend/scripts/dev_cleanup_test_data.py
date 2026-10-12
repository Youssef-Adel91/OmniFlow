"""Remove throw-away DEVELOPMENT workspaces (auto-provisioned "Dev Workspace" tenants and the "Local Dev User" accounts inside them).

Safe by construction — this is a developer convenience, never part of a deploy and never run automatically:
  * DRY-RUN by default: it only lists what it would delete. Nothing changes without --apply.
  * Refuses to run unless APP_ENV=development AND the database host is local (localhost / 127.0.0.1 / ::1 / a docker service name
    given in --allow-host). Production settings or a remote host => hard refusal, exit code 2.
  * Only touches tenants that look like dev provisioning: business_name 'Dev Workspace' (or starting with 'Workspace for') AND
    fal_license_number starting with 'DEV-'. Anything else is never a candidate.
  * Skips any candidate that has money history (paid transactions / invoices), an active paid subscription, or more than --max-users users.
  * Deletes tenant-scoped rows table by table (every table that has a tenant_id column), retrying around foreign-key order, inside ONE
    transaction per tenant: a failure rolls that tenant back completely.

    python -m scripts.dev_cleanup_test_data                 # list candidates
    python -m scripts.dev_cleanup_test_data --apply         # delete them
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from src.shared.core.config import get_settings
from src.shared.db.session import get_system_session

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
CANDIDATES_SQL = """
SELECT t.tenant_id, t.business_name, t.fal_license_number, t.created_at,
       (SELECT count(*) FROM tenant_users u WHERE u.tenant_id = t.tenant_id) AS users,
       (SELECT count(*) FROM transactions x WHERE x.tenant_id = t.tenant_id AND x.status = 'paid') AS paid_txns,
       (SELECT count(*) FROM invoices i WHERE i.tenant_id = t.tenant_id) AS invoices,
       (SELECT count(*) FROM subscriptions s WHERE s.tenant_id = t.tenant_id AND s.status = 'active' AND NOT s.is_exempt AND s.provider <> 'manual') AS paid_subs
FROM tenants t
WHERE (t.business_name = 'Dev Workspace' OR t.business_name LIKE 'Workspace for %') AND t.fal_license_number LIKE 'DEV-%'
ORDER BY t.created_at
"""
TENANT_TABLES_SQL = """
SELECT c.table_name FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = c.table_schema AND t.table_type = 'BASE TABLE'
WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' AND c.table_name <> 'tenants'
ORDER BY c.table_name
"""


def guard(host: str, app_env: str, allow_hosts: set[str]) -> str | None:
    """Returns a refusal reason, or None when running is allowed."""
    if app_env != "development":
        return f"APP_ENV={app_env!r}: this tool only runs when APP_ENV=development"
    if host not in LOCAL_HOSTS and host not in allow_hosts:
        return f"database host {host!r} is not local (use --allow-host only for your own docker dev database)"
    return None


async def find_candidates(session, max_users: int) -> tuple[list[dict], list[tuple[dict, str]]]:
    rows = [dict(r) for r in (await session.execute(text(CANDIDATES_SQL))).mappings()]
    ok, skipped = [], []
    for r in rows:
        if r["paid_txns"] or r["invoices"] or r["paid_subs"]:
            skipped.append((r, "has payment history / an active paid subscription"))
        elif r["users"] > max_users:
            skipped.append((r, f"has {r['users']} users (> {max_users})"))
        else:
            ok.append(r)
    return ok, skipped


async def delete_tenant(session, tenant_id) -> dict[str, int]:
    tables = [r[0] for r in (await session.execute(text(TENANT_TABLES_SQL))).all()]
    removed: dict[str, int] = {}
    errors: dict[str, str] = {}
    pending = list(tables)
    for _ in range(len(tables) + 3):
        still = []
        for tbl in pending:
            try:
                async with session.begin_nested():
                    res = await session.execute(text(f'DELETE FROM "{tbl}" WHERE tenant_id = :t'), {"t": tenant_id})
                removed[tbl] = removed.get(tbl, 0) + (res.rowcount or 0)
            except Exception as exc:  # noqa: BLE001 - foreign-key order: another table must go first; retry on the next pass
                errors[tbl] = f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"
                still.append(tbl)
        pending = still
        if not pending:
            break
    if pending:
        raise RuntimeError("could not clear tables: " + "; ".join(f"{t} ({errors.get(t, '?')})" for t in pending))
    await session.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": tenant_id})
    return {k: v for k, v in removed.items() if v}


async def run(apply: bool, max_users: int, allow_hosts: set[str]) -> int:
    cfg = get_settings()
    reason = guard(cfg.postgres_host, cfg.app_env, allow_hosts)
    if reason:
        print(f"REFUSED: {reason}", file=sys.stderr)
        return 2
    async with get_system_session() as session:
        ok, skipped = await find_candidates(session, max_users)
    for r, why in skipped:
        print(f"skip   {r['tenant_id']}  {r['business_name']!r:24} ({why})")
    for r in ok:
        print(f"{'DELETE' if apply else 'would '} {r['tenant_id']}  {r['business_name']!r:24} users={r['users']} created={r['created_at']:%Y-%m-%d}")
    if not ok:
        print("nothing to clean.")
        return 0
    if not apply:
        print(f"\n{len(ok)} dev workspace(s) would be deleted. Re-run with --apply to delete them.")
        return 0
    for r in ok:
        async with get_system_session() as session:        # one transaction per tenant
            removed = await delete_tenant(session, r["tenant_id"])
        print(f"deleted {r['tenant_id']}: " + ", ".join(f"{k}={v}" for k, v in removed.items()))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    ap.add_argument("--max-users", type=int, default=3, help="skip workspaces with more users than this (default 3)")
    ap.add_argument("--allow-host", action="append", default=[], help="extra non-localhost DB host name you own (e.g. a docker service)")
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.apply, a.max_users, set(a.allow_host))))


if __name__ == "__main__":
    main()
