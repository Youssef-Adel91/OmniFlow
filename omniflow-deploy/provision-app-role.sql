-- Run as the migration owner after migrations; values come from container env.
\set ON_ERROR_STOP on
\getenv app_user APP_POSTGRES_USER
\getenv app_password APP_POSTGRES_PASSWORD

SELECT :'app_user' = current_user OR :'app_user' = '' AS invalid_role \gset
\if :invalid_role
  \echo 'Application role must be distinct from the migration owner.'
  DO $$ BEGIN RAISE EXCEPTION 'Invalid application role'; END $$;
\endif

SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user'
               AND (rolsuper OR rolbypassrls OR rolcreaterole)) AS privileged_role \gset
\if :privileged_role
  \echo 'Refusing to reuse a privileged application role.'
  DO $$ BEGIN RAISE EXCEPTION 'Privileged application role'; END $$;
\endif

SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'app_user', :'app_password') \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'app_user') \gexec
SELECT format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I', :'app_user') \gexec
SELECT format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I', :'app_user') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I', :'app_user') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I', :'app_user') \gexec
