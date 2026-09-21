"""system_bypass_policy — make get_system_session() actually work under FORCE RLS

Revision ID: 0006_system_bypass_policy
Revises: 9e62a0b74221
Create Date: 2026-08-12

PROBLEM
-------
Migration 0002 enabled FORCE ROW LEVEL SECURITY on all tenant-scoped tables
with the policy:

    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)

`src/shared/db/session.py::get_system_session()` used to set that GUC to the
literal string 'system'. The `::uuid` cast then failed at query time with:

    invalid input syntax for type uuid: "system"

breaking every code path that runs through a system session — most importantly
`get_current_user()` (reads `tenant_users`) and the Clerk `user.created`
webhook that auto-provisions Tenant + TenantUser.

SOLUTION
--------
`get_system_session()` now sets the GUC to the reserved nil UUID
`00000000-0000-0000-0000-000000000000` (cast-safe). This migration adds a
second, PERMISSIVE policy on each RLS table. PostgreSQL OR-combines permissive
policies, so a session running with the sentinel sees every row, while normal
tenant sessions are unaffected (the sentinel comparison is simply false).

Because the policy omits WITH CHECK, PostgreSQL reuses the USING expression for
INSERT/UPDATE checks — so system sessions can write as well as read.

MAINTENANCE CONTRACT
--------------------
Any NEW tenant-scoped table that gets `tenant_isolation_policy` MUST also get
`system_bypass_policy`, otherwise system-session queries against it silently
return zero rows. Add the table name to `_RLS_TABLES` below AND to the same
list in 0002_rls_policies.py.

LONG-TERM ALTERNATIVE (DevOps decision, intentionally not done here)
-------------------------------------------------------------------
Connect system operations with a dedicated database role that holds BYPASSRLS:

    CREATE ROLE app_system_role LOGIN PASSWORD '...' BYPASSRLS;
    GRANT ALL ON ALL TABLES IN SCHEMA public TO app_system_role;

That requires a second connection string / engine and credential provisioning
in the deployment environment, so it is left as a follow-up.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0006_system_bypass_policy"
down_revision: Union[str, None] = "9e62a0b74221"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Must stay in sync with _RLS_TABLES in 0002_rls_policies.py
_RLS_TABLES = [
    "tenant_users",
    "customers",
    "property_listings",
    "conversations",
    "customer_reports",
]

_POLICY_NAME = "system_bypass_policy"
_GUC = "app.current_tenant_id"
_SYSTEM_TENANT_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table};")
        op.execute(
            f"""
            CREATE POLICY {_POLICY_NAME} ON {table}
                AS PERMISSIVE
                FOR ALL
                USING (
                    current_setting('{_GUC}', true)::uuid
                        = '{_SYSTEM_TENANT_ID}'::uuid
                );
            """
        )


def downgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {_POLICY_NAME} ON {table};")
