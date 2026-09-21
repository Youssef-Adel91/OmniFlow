#!/usr/bin/env bash
# infra/redpanda/create-topics.sh
# Run after Redpanda is healthy to create all required Kafka topics.
# Usage: docker exec omniflow-redpanda bash /topics.sh

set -euo pipefail

BROKER="localhost:9093"

create_topic() {
  local TOPIC=$1
  local PARTITIONS=${2:-12}
  local REPLICATION=${3:-1}  # 1 for local dev, 3 for production

  rpk topic create "$TOPIC" \
    --brokers "$BROKER" \
    --partitions "$PARTITIONS" \
    --replicas "$REPLICATION" \
    --topic-config retention.ms=604800000 \  # 7-day retention
    --topic-config compression.type=lz4 \
    2>/dev/null || echo "[SKIP] Topic '$TOPIC' already exists."
}

echo "⏳ Creating OmniFlow AI Kafka Topics..."

# ─── Core Message Pipeline ─────────────────────────────────────────────────
create_topic "messages.incoming.v1"      24  # High partitions for peak throughput
create_topic "messages.outgoing.v1"      12
create_topic "messages.outgoing.human.v1" 6

# ─── AI Processing ─────────────────────────────────────────────────────────
create_topic "llm.routing.v1"            12
create_topic "multimodal.audio.v1"       6
create_topic "multimodal.vision.v1"      6

# ─── Growth Engines ────────────────────────────────────────────────────────
create_topic "broadcast.marketing.v1"    6
create_topic "vault.retrieval.v1"        4

# ─── Analytics & Notifications ─────────────────────────────────────────────
create_topic "analytics.events.v1"       12  # ClickHouse consumer
create_topic "conversations.updates.v1"  12  # Dashboard SSE bridge
create_topic "payment.events.v1"         4

# ─── Dead Letter Queues ─────────────────────────────────────────────────────
create_topic "dlq.messages.incoming"     4
create_topic "dlq.llm.routing"           4

echo "✅ All Kafka topics created successfully."
rpk topic list --brokers "$BROKER"
