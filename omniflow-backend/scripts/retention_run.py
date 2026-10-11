"""Run the data-retention pass by hand (what the daily Celery task does). REPORT ONLY unless --apply is given.

    python -m scripts.retention_run                      # all tenants, dry-run report
    python -m scripts.retention_run --tenant <uuid>      # one tenant
    python -m scripts.retention_run --apply              # really anonymise / delete (irreversible)

--apply also requires RETENTION_ENABLED=true so a stray environment cannot erase data by accident.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from src.shared.core.config import get_settings
from src.shared.services import retention


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", type=uuid.UUID)
    ap.add_argument("--apply", action="store_true", help="really delete (default: report only)")
    args = ap.parse_args(argv)
    if args.apply and not get_settings().retention_enabled:
        print("Refusing: --apply needs RETENTION_ENABLED=true in the environment.", file=sys.stderr)
        return 2
    result = await retention.run_all(dry_run=not args.apply, only_tenant=args.tenant)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
