-- infra/clickhouse/init/01_create_database.sql
-- ClickHouse initial database and schema setup.

CREATE DATABASE IF NOT EXISTS omniflow_analytics
    ENGINE = Atomic
    COMMENT 'OmniFlow AI OLAP Analytics';

-- Switch to analytics DB for subsequent DDL
USE omniflow_analytics;

-- ─── events_raw (Master OLAP Table) ─────────────────────────────────────────
-- Receives all events from Kafka via ClickHouse Kafka Engine Table.
-- Ref: SRS §4.5.3

CREATE TABLE IF NOT EXISTS events_raw (
    event_id            UUID,
    event_type          LowCardinality(String),
    tenant_id           UUID,
    customer_id         Nullable(UUID),
    conversation_id     Nullable(UUID),
    channel             LowCardinality(String),
    district            LowCardinality(String),
    city                LowCardinality(String),
    intent              LowCardinality(String),
    routing_tier        LowCardinality(String),
    tokens_in           UInt32,
    tokens_out          UInt32,
    cost_usd            Float64,
    latency_ms          UInt32,
    sentiment_score     Float32,
    escalation_flag     UInt8,
    payment_amount_sar  Nullable(Float64),
    occurred_at         DateTime64(3, 'Asia/Riyadh'),
    inserted_at         DateTime DEFAULT now()
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(occurred_at)
ORDER BY (tenant_id, occurred_at, event_type)
TTL occurred_at + INTERVAL 2 YEAR
SETTINGS index_granularity = 8192;

-- ─── Materialized View: Conversion by District & Hour ───────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_conversion_by_district_hour
ENGINE = SummingMergeTree
PARTITION BY toYYYYMM(hour_bucket)
ORDER BY (tenant_id, district, hour_bucket)
AS SELECT
    tenant_id,
    district,
    toStartOfHour(occurred_at) AS hour_bucket,
    countIf(event_type = 'conversation_started') AS conversations_started,
    countIf(event_type = 'report_purchased') AS reports_purchased,
    sumIf(payment_amount_sar, event_type = 'report_purchased') AS revenue_sar
FROM events_raw
GROUP BY tenant_id, district, hour_bucket;

-- ─── Materialized View: Token Consumption by Tier & Hour ────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_token_consumption_hourly
ENGINE = SummingMergeTree
PARTITION BY toYYYYMM(hour_bucket)
ORDER BY (tenant_id, hour_bucket, routing_tier)
AS SELECT
    tenant_id,
    routing_tier,
    toStartOfHour(occurred_at) AS hour_bucket,
    sum(tokens_in) AS total_tokens_in,
    sum(tokens_out) AS total_tokens_out,
    sum(cost_usd) AS total_cost_usd,
    count() AS request_count,
    avg(latency_ms) AS avg_latency_ms
FROM events_raw
GROUP BY tenant_id, routing_tier, hour_bucket;

-- ─── Materialized View: Sentiment & Escalation by Day ───────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_sentiment_escalation_daily
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMM(day_bucket)
ORDER BY (tenant_id, day_bucket)
AS SELECT
    tenant_id,
    toDate(occurred_at) AS day_bucket,
    avgState(sentiment_score) AS avg_sentiment_state,
    sumState(escalation_flag) AS total_escalations_state,
    countState() AS total_events_state
FROM events_raw
GROUP BY tenant_id, day_bucket;

-- ─── Materialized View: Property Inquiry Trends ──────────────────────────────
CREATE MATERIALIZED VIEW IF NOT EXISTS mv_property_inquiry_trends
ENGINE = SummingMergeTree
PARTITION BY toYYYYMM(day_bucket)
ORDER BY (tenant_id, district, day_bucket)
AS SELECT
    tenant_id,
    district,
    city,
    intent,
    toDate(occurred_at) AS day_bucket,
    count() AS inquiry_count
FROM events_raw
WHERE event_type IN ('inquiry_received', 'listing_search')
GROUP BY tenant_id, district, city, intent, day_bucket;
