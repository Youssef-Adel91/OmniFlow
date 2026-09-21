-- infra/postgres/init/01_extensions.sql
-- Extensions and initial setup — runs on first container start.

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable performance statistics
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- Enable full-text search (Arabic support)
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Enable unaccent for Arabic text normalization
CREATE EXTENSION IF NOT EXISTS "unaccent";

-- Set timezone for the database
ALTER DATABASE omniflow_db SET timezone TO 'Asia/Riyadh';

-- Set the default search path
ALTER DATABASE omniflow_db SET search_path TO public;

-- Log slow queries (>500ms) for performance analysis
ALTER DATABASE omniflow_db SET log_min_duration_statement TO 500;

COMMENT ON DATABASE omniflow_db IS 'OmniFlow AI — Enterprise Real Estate Digital Twin';
