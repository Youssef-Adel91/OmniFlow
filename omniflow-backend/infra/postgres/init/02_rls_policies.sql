-- infra/postgres/init/02_rls_policies.sql
-- ─────────────────────────────────────────────────────────────────────────────
-- Row-Level Security (RLS) Policies for all tenant-scoped tables.
--
-- THIS FILE IS DOCUMENTATION / REFERENCE ONLY.
-- The actual enforcement is applied by the Alembic migration:
--   alembic/versions/<hash>_enable_rls_policies.py
--
-- HOW IT WORKS:
--   1. Every request handler calls get_tenant_session(tenant_id), which runs:
--        SET LOCAL app.current_tenant_id = '<uuid>';
--   2. PostgreSQL RLS evaluates the USING clause on every query automatically.
--   3. A query missing the SET LOCAL gets current_setting() returning '' or an
--      error, producing an empty result set — NOT a data leak.
--
-- TABLES COVERED:
--   tenant_users, customers, property_listings, conversations, customer_reports
--
-- NOTE: The 'tenants' and 'messages' tables are deliberately excluded:
--   - tenants: IS the tenant; no partition needed.
--   - messages: RLS enforced via parent Conversation join (no tenant_id column).
-- ─────────────────────────────────────────────────────────────────────────────

-- ── tenant_users ──────────────────────────────────────────────────────────────
ALTER TABLE tenant_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenant_users FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_policy ON tenant_users;
CREATE POLICY tenant_isolation_policy ON tenant_users
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- ── customers ─────────────────────────────────────────────────────────────────
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE customers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_policy ON customers;
CREATE POLICY tenant_isolation_policy ON customers
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- ── property_listings ─────────────────────────────────────────────────────────
ALTER TABLE property_listings ENABLE ROW LEVEL SECURITY;
ALTER TABLE property_listings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_policy ON property_listings;
CREATE POLICY tenant_isolation_policy ON property_listings
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- ── conversations ─────────────────────────────────────────────────────────────
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_policy ON conversations;
CREATE POLICY tenant_isolation_policy ON conversations
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- ── customer_reports ──────────────────────────────────────────────────────────
ALTER TABLE customer_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_reports FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation_policy ON customer_reports;
CREATE POLICY tenant_isolation_policy ON customer_reports
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);

-- ─────────────────────────────────────────────────────────────────────────────
-- Grant the application role bypass on the tenants table ONLY for
-- the system-level admin session (no RLS on tenants itself).
-- Replace 'omniflow_app' with your actual DB role name.
-- ─────────────────────────────────────────────────────────────────────────────
-- ALTER ROLE omniflow_app BYPASSRLS;  -- uncomment for superadmin role only
