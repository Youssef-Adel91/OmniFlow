"""
One command to run the customer simulation (tests/e2e/simulation): scripted roles — buyer, investor, attacker/angry customer, commenter, salesperson, lower-privilege staff, tenant B —
against the REAL sales hook / booking / follow-ups / hand-off / output guard / comment policy / deals API on PostgreSQL with RLS. The model, Meta and the network are NOT involved.

    OMNIFLOW_TEST_DATABASE_URL=postgresql+asyncpg://owner:...@localhost/db  OMNIFLOW_TEST_RLS_URL=postgresql+asyncpg://app_user:...@localhost/db \
        python -m scripts.run_simulation [-v]

Without the two URLs the DB-backed roles are SKIPPED (and the script says so, exit 3) — it never reports a pass it did not run.
Exit codes: 0 all ran and passed · 1 failures · 3 nothing DB-backed could run.
"""
from __future__ import annotations

import os
import sys
import unittest
from collections import Counter


def main(argv: list[str]) -> int:
    verbose = "-v" in argv
    suite = unittest.defaultTestLoader.discover("tests/e2e/simulation", pattern="test_role_*.py", top_level_dir=".")
    def walk(s):
        for x in s:
            if isinstance(x, unittest.TestSuite):
                yield from walk(x)
            else:
                yield x

    per_role: Counter = Counter(type(t).__module__.rsplit(".", 1)[-1] for t in walk(suite))      # counted BEFORE running: the runner empties the suite
    result = unittest.TextTestRunner(verbosity=2 if verbose else 1).run(suite)
    print("\nRoles:", ", ".join(f"{k}={v}" for k, v in sorted(per_role.items())))
    db = bool(os.environ.get("OMNIFLOW_TEST_DATABASE_URL") and os.environ.get("OMNIFLOW_TEST_RLS_URL"))
    print(f"Ran {result.testsRun}, failures {len(result.failures)}, errors {len(result.errors)}, skipped {len(result.skipped)}; database-backed roles: {'ran' if db else 'SKIPPED (no database URLs)'}")
    if result.failures or result.errors:
        return 1
    if not db:
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
