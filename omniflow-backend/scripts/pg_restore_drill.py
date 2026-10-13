"""PostgreSQL backup → restore drill (no Docker needed; uses the pg_dump / pg_restore / createdb / dropdb binaries on PATH).

    python -m scripts.pg_restore_drill --source <postgres-connection-url> [--keep]

What it proves: that a dump of SOURCE can be restored into a brand-new database and that the restored copy matches the source —
same tables, same exact row counts, same Alembic revision, same number of RLS policies (so tenant isolation survives a restore).

Safety:
  * SOURCE is only READ (pg_dump + count queries). Nothing is ever written to it.
  * The scratch database is created by this run, its name must contain "_restore_drill_", and only that database is dropped afterwards
    (unless --keep). The script refuses to touch any other database.
  * Passwords go to the child processes through PGPASSWORD and are never printed.
Run it against a quiet database (staging, or production at a low-traffic moment): counts are taken after the dump, so live writes show up as
differences. Exit code 0 = restored copy identical, 1 = differences / failure, 2 = bad usage.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

MARKER = "_restore_drill_"


def parse_url(url: str) -> dict[str, str]:
    u = urlsplit(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    if u.scheme not in ("postgresql", "postgres") or not u.hostname or not u.path.strip("/"):
        raise ValueError("expected a postgresql URL: scheme://credentials@host:port/dbname")
    return {"host": u.hostname, "port": str(u.port or 5432), "user": unquote(u.username or ""), "password": unquote(u.password or ""), "db": u.path.strip("/")}


def scratch_name(source_db: str, now: float | None = None) -> str:
    return f"{source_db}{MARKER}{int(now if now is not None else time.time())}"[:63]


def _env(conn: dict[str, str]) -> dict[str, str]:
    env = dict(os.environ)
    env.update(PGPASSWORD=conn["password"], PGHOST=conn["host"], PGPORT=conn["port"], PGUSER=conn["user"], PGCONNECT_TIMEOUT="10")
    return env


def run(cmd: list[str], conn: dict[str, str], *, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(cmd, env=_env(conn), capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed (exit {res.returncode}): {res.stderr.strip()[:400]}")
    return res


def query(conn: dict[str, str], db: str, sql: str) -> list[list[str]]:
    res = run(["psql", "-X", "-A", "-t", "-F", "\t", "-d", db, "-c", sql], conn)
    return [line.split("\t") for line in res.stdout.splitlines() if line.strip()]


def table_counts(conn: dict[str, str], db: str) -> dict[str, int]:
    tables = [r[0] for r in query(conn, db, "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1")]
    if not tables:
        return {}
    union = " UNION ALL ".join(f"SELECT '{t}', count(*) FROM \"{t}\"" for t in tables)
    return {name: int(n) for name, n in query(conn, db, union)}


def fingerprint(conn: dict[str, str], db: str) -> dict:
    version = query(conn, db, "SELECT version_num FROM alembic_version") if "alembic_version" in table_counts(conn, db) else []
    policies = query(conn, db, "SELECT count(*) FROM pg_policies WHERE schemaname='public'")
    force = query(conn, db, "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity")
    return {"counts": table_counts(conn, db), "alembic": version[0][0] if version else None, "rls_policies": int(policies[0][0]), "force_rls_tables": int(force[0][0])}


def compare(src: dict, dst: dict) -> list[str]:
    problems = []
    for t in sorted(set(src["counts"]) | set(dst["counts"])):
        if t not in dst["counts"]:
            problems.append(f"table missing after restore: {t}")
        elif t not in src["counts"]:
            problems.append(f"unexpected table after restore: {t}")
        elif src["counts"][t] != dst["counts"][t]:
            problems.append(f"row count differs in {t}: source {src['counts'][t]} vs restored {dst['counts'][t]}")
    for k in ("alembic", "rls_policies", "force_rls_tables"):
        if src[k] != dst[k]:
            problems.append(f"{k} differs: source {src[k]} vs restored {dst[k]}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=os.environ.get("DRILL_SOURCE_URL"), help="postgresql connection URL (or env DRILL_SOURCE_URL)")
    ap.add_argument("--keep", action="store_true", help="do not drop the restored scratch database afterwards")
    args = ap.parse_args(argv)
    if not args.source:
        print("--source (or DRILL_SOURCE_URL) is required", file=sys.stderr)
        return 2
    try:
        conn = parse_url(args.source)
    except ValueError as exc:
        print(f"bad --source: {exc}", file=sys.stderr)
        return 2

    scratch = scratch_name(conn["db"])
    assert MARKER in scratch and scratch != conn["db"]
    report: dict = {"source_db": conn["db"], "scratch_db": scratch, "steps": []}
    created = False
    started = time.time()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "drill.dump"
            run(["pg_dump", "-Fc", "--no-owner", "-d", conn["db"], "-f", str(dump)], conn)
            report["steps"].append({"dump_bytes": dump.stat().st_size})
            src = fingerprint(conn, conn["db"])
            run(["createdb", scratch], conn)
            created = True
            res = run(["pg_restore", "--no-owner", "--no-privileges", "-d", scratch, str(dump)], conn, check=False)
            report["steps"].append({"restore_exit": res.returncode, "restore_stderr_tail": res.stderr.strip()[-300:] if res.stderr else ""})
            if res.returncode != 0:
                raise RuntimeError("pg_restore reported errors")
            dst = fingerprint(conn, scratch)
        problems = compare(src, dst)
        report.update(tables=len(src["counts"]), rows=sum(src["counts"].values()), alembic=src["alembic"], rls_policies=src["rls_policies"], problems=problems, ok=not problems)
    except Exception as exc:  # noqa: BLE001
        report.update(ok=False, problems=[str(exc)])
    finally:
        if created and not args.keep:
            if MARKER in scratch:                                   # belt and braces: never drop anything else
                run(["dropdb", "--if-exists", scratch], conn, check=False)
                report["scratch_dropped"] = True
    report["seconds"] = round(time.time() - started, 1)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
