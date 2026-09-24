#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# backup.sh — OmniFlow AI automated backup
#
# Backs up, in order:
#   1. PostgreSQL   -> gzip-compressed pg_dump (custom-format is also produced)
#   2. Qdrant       -> snapshot per collection via the Snapshot REST API
#   3. MinIO        -> `mc mirror` of configured data buckets into a backup bucket
#                      (and, optionally, to a local directory)
#
# Retention: anything older than $RETENTION_DAYS is removed. Deletion failures
# are logged as warnings and never abort the run (some mounted filesystems do
# not support unlink).
#
# ── INSTALL AS A CRON JOB ────────────────────────────────────────────────────
#   chmod +x /opt/omniflow/omniflow-deploy/backup.sh
#   crontab -e
#   # daily at 03:00 server time:
#   0 3 * * * /opt/omniflow/omniflow-deploy/backup.sh >> /var/log/omniflow-backup.log 2>&1
#
#   Make sure the cron user is in the `docker` group, and rotate the log:
#   /etc/logrotate.d/omniflow-backup:
#       /var/log/omniflow-backup.log { weekly rotate 8 compress missingok notifempty }
#
# ⚠️ CLIENT ACTION: a backup that only lives on the same server is not a
# backup. Ship $BACKUP_ROOT off-box (S3, another region, rsync to NAS) and test
# a RESTORE at least once per quarter.
# ═════════════════════════════════════════════════════════════════════════════

set -uo pipefail   # NOTE: no `-e` — a failing optional step must not kill the run.

# ── Configuration ────────────────────────────────────────────────────────────
DEPLOY_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$DEPLOY_DIR" || exit 1

COMPOSE_FILE="${COMPOSE_FILE:-$DEPLOY_DIR/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-$DEPLOY_DIR/.env.prod}"

BACKUP_ROOT="${BACKUP_ROOT:-$DEPLOY_DIR/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

PG_CONTAINER="${PG_CONTAINER:-prod-postgres}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-prod-qdrant}"
MINIO_CONTAINER="${MINIO_CONTAINER:-prod-minio}"

MINIO_SOURCE_BUCKET="${MINIO_SOURCE_BUCKET:-omniflow-media}"
MINIO_BACKUP_BUCKET="${MINIO_BACKUP_BUCKET:-omniflow-backups}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
EXIT_CODE=0

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
warn() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️  WARNING: $*" >&2; }
fail() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ❌ ERROR: $*" >&2; EXIT_CODE=1; }

log "=============================================="
log "OmniFlow backup started (retention: ${RETENTION_DAYS}d)"
log "=============================================="

# ── Load secrets ─────────────────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
else
    warn "$ENV_FILE not found — relying on the environment already exported."
fi

POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-omniflow_db}"

# Resolve after loading ENV_FILE. An explicit space-separated list overrides
# defaults; each source gets its own prefix to avoid colliding object names.
read -r -a SOURCE_BUCKETS <<< "${MINIO_SOURCE_BUCKETS:-${MINIO_SOURCE_BUCKET} ${S3_VAULT_BUCKET:-omniflow-reports-vault} ${S3_MEDIA_TEMP_BUCKET:-omniflow-media-temp} ${S3_ASSETS_BUCKET:-omniflow-public-assets} ${S3_KNOWLEDGE_BUCKET:-omniflow-knowledge-docs}}"

mkdir -p "$BACKUP_ROOT/postgres" "$BACKUP_ROOT/qdrant" "$BACKUP_ROOT/minio" || {
    fail "Cannot create backup directories under $BACKUP_ROOT"
    exit 1
}

container_running() {
    docker ps --format '{{.Names}}' | grep -qx "$1"
}

# ═════════════════════════════════════════════════════════════════════════════
# 1. PostgreSQL
# ═════════════════════════════════════════════════════════════════════════════
log "── [1/3] PostgreSQL dump ────────────────────────────────"
if container_running "$PG_CONTAINER"; then
    PG_PLAIN="$BACKUP_ROOT/postgres/omniflow_${TIMESTAMP}.sql.gz"

    # `set -o pipefail` is active, so a pg_dump failure propagates even though
    # gzip itself would exit 0.
    docker exec -e PGPASSWORD="${POSTGRES_PASSWORD:-}" "$PG_CONTAINER" \
        pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
                --no-owner --no-privileges --clean --if-exists \
        | gzip -9 > "$PG_PLAIN"
    DUMP_STATUS=$?

    if [ "$DUMP_STATUS" -eq 0 ] && [ -s "$PG_PLAIN" ]; then
        log "✅ PostgreSQL plain dump: $PG_PLAIN ($(du -h "$PG_PLAIN" | cut -f1))"
    else
        fail "pg_dump failed (exit $DUMP_STATUS) or produced an empty file: $PG_PLAIN"
    fi

    # Custom-format dump: required for selective / parallel pg_restore.
    PG_CUSTOM="$BACKUP_ROOT/postgres/omniflow_${TIMESTAMP}.dump"
    if docker exec -e PGPASSWORD="${POSTGRES_PASSWORD:-}" "$PG_CONTAINER" \
            pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
                    -Fc --no-owner --no-privileges > "$PG_CUSTOM" 2>/dev/null; then
        log "✅ PostgreSQL custom dump: $PG_CUSTOM"
    else
        warn "Custom-format dump failed (the .sql.gz above is still valid)."
        rm -f "$PG_CUSTOM" 2>/dev/null || true
    fi

    # RESTORE (for reference):
    #   gunzip -c omniflow_YYYYmmdd_HHMMSS.sql.gz | \
    #     docker exec -i prod-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
    #   # or, from the custom dump:
    #   docker exec -i prod-postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    #     --clean --if-exists -j 4 < omniflow_YYYYmmdd_HHMMSS.dump
else
    fail "Container $PG_CONTAINER is not running — PostgreSQL was NOT backed up."
fi

# ═════════════════════════════════════════════════════════════════════════════
# 2. Qdrant
# ═════════════════════════════════════════════════════════════════════════════
log "── [2/3] Qdrant snapshots ───────────────────────────────"
if container_running "$QDRANT_CONTAINER"; then
    QDRANT_DIR="$BACKUP_ROOT/qdrant/${TIMESTAMP}"
    mkdir -p "$QDRANT_DIR"

    QDRANT_KEY="${QDRANT_API_KEY:-}"
    AUTH_ARGS=()
    [ -n "$QDRANT_KEY" ] && AUTH_ARGS=(-H "api-key: $QDRANT_KEY")

    # The qdrant image ships no HTTP client, so we drive the REST API from a
    # throw-away curl container attached to the same docker network.
    QDRANT_NET="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$QDRANT_CONTAINER" 2>/dev/null | head -n1)"
    QDRANT_NET="${QDRANT_NET:-omniflow-prod-net}"

    qcurl() {
        docker run --rm --network "$QDRANT_NET" curlimages/curl:8.11.0 \
            -fsS --max-time 120 "${AUTH_ARGS[@]}" "$@"
    }

    COLLECTIONS_JSON="$(qcurl http://qdrant:6333/collections 2>/dev/null)"
    COLLECTIONS_STATUS=$?
    COLLECTIONS="$(echo "$COLLECTIONS_JSON" | grep -oE '"name"[[:space:]]*:[[:space:]]*"[^"]*"' | cut -d'"' -f4 || true)"

    if [ "$COLLECTIONS_STATUS" -ne 0 ] || ! echo "$COLLECTIONS_JSON" | grep -qE '"collections"[[:space:]]*:'; then
        fail "Qdrant collection listing failed — Qdrant was NOT backed up."
    elif [ -z "$COLLECTIONS" ]; then
        log "Qdrant has no collections to snapshot."
    else
        for COLLECTION in $COLLECTIONS; do
            log "   • snapshotting collection: $COLLECTION"
            SNAP_JSON="$(qcurl -X POST "http://qdrant:6333/collections/${COLLECTION}/snapshots" 2>/dev/null)"
            SNAP_NAME="$(echo "$SNAP_JSON" | grep -o '"name":"[^"]*"' | head -n1 | cut -d'"' -f4)"

            if [ -z "$SNAP_NAME" ]; then
                fail "Snapshot creation failed for collection $COLLECTION"
                continue
            fi

            # Snapshots land inside the container's storage volume; copy them out.
            if docker cp "${QDRANT_CONTAINER}:/qdrant/snapshots/${COLLECTION}/${SNAP_NAME}" \
                         "${QDRANT_DIR}/${SNAP_NAME}" 2>/dev/null; then
                gzip -9 -f "${QDRANT_DIR}/${SNAP_NAME}" 2>/dev/null || true
                log "   ✅ $COLLECTION -> ${QDRANT_DIR}/${SNAP_NAME}"
                # Keep the in-container copy from growing without bound.
                qcurl -X DELETE "http://qdrant:6333/collections/${COLLECTION}/snapshots/${SNAP_NAME}" >/dev/null 2>&1 \
                    || warn "Could not delete the in-container snapshot $SNAP_NAME"
            else
                fail "docker cp failed for snapshot $SNAP_NAME ($COLLECTION)"
            fi
        done
    fi

    # RESTORE: ./restore.sh qdrant <collection> <snapshot-file>
    # (item 18: this used to be a comment with no runnable code behind it —
    # see restore.sh, drilled against a real local Qdrant instance.)
else
    fail "Container $QDRANT_CONTAINER is not running — Qdrant was NOT backed up."
fi

# ═════════════════════════════════════════════════════════════════════════════
# 3. MinIO
# ═════════════════════════════════════════════════════════════════════════════
log "── [3/3] MinIO mirror ───────────────────────────────────"
# RESTORE: ./restore.sh minio <backup-bucket>/<timestamp>/<source-bucket> <destination-bucket>
# (item 18: no restore code or documentation existed for MinIO at all
# before this — see restore.sh, drilled against a real local MinIO instance.)
if container_running "$MINIO_CONTAINER"; then
    MINIO_NET="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$MINIO_CONTAINER" 2>/dev/null | head -n1)"
    MINIO_NET="${MINIO_NET:-omniflow-prod-net}"

    MC_RUN=(docker run --rm --network "$MINIO_NET"
            -e MC_HOST_myminio="http://${MINIO_ROOT_USER:-minio}:${MINIO_ROOT_PASSWORD:-}@minio:9000"
            minio/mc)

    # 3a. Timestamped in-cluster copies, separated by source bucket.
    for SOURCE_BUCKET in "${SOURCE_BUCKETS[@]}"; do
      if [ "$SOURCE_BUCKET" = "$MINIO_BACKUP_BUCKET" ]; then
        fail "The backup bucket must not be included in MINIO_SOURCE_BUCKETS."
        continue
      fi
    if "${MC_RUN[@]}" mirror --overwrite --quiet \
            "myminio/${SOURCE_BUCKET}" \
            "myminio/${MINIO_BACKUP_BUCKET}/${TIMESTAMP}/${SOURCE_BUCKET}"; then
        log "✅ MinIO mirrored to myminio/${MINIO_BACKUP_BUCKET}/${TIMESTAMP}/${SOURCE_BUCKET}"
    else
        fail "mc mirror to the backup bucket failed."
    fi

    # 3b. Optional off-container copy onto the host filesystem.
    #     Set MINIO_LOCAL_MIRROR=1 to enable (uses more disk).
    if [ "${MINIO_LOCAL_MIRROR:-0}" = "1" ]; then
        LOCAL_DIR="$BACKUP_ROOT/minio/${TIMESTAMP}/${SOURCE_BUCKET}"
        mkdir -p "$LOCAL_DIR"
        if docker run --rm --network "$MINIO_NET" \
                -e MC_HOST_myminio="http://${MINIO_ROOT_USER:-minio}:${MINIO_ROOT_PASSWORD:-}@minio:9000" \
                -v "$LOCAL_DIR:/backup" minio/mc \
                mirror --overwrite --quiet "myminio/${SOURCE_BUCKET}" /backup; then
            log "✅ MinIO mirrored to host: $LOCAL_DIR"
        else
            fail "Local mc mirror failed."
        fi
    else
        log "ℹ️  Local MinIO mirror skipped (set MINIO_LOCAL_MIRROR=1 to enable)."
    fi
    done
else
    fail "Container $MINIO_CONTAINER is not running — MinIO was NOT backed up."
fi

# ═════════════════════════════════════════════════════════════════════════════
# 4. Retention / pruning  (best-effort — never fatal)
# ═════════════════════════════════════════════════════════════════════════════
if [ "$EXIT_CODE" -ne 0 ]; then
    warn "Backup incomplete — preserving previous backups and skipping all pruning."
    exit "$EXIT_CODE"
fi

log "── Pruning backups older than ${RETENTION_DAYS} days ────"

prune_path() {
    local path="$1"
    [ -d "$path" ] || return 0

    # Some mounted filesystems refuse unlink(); log and continue instead of
    # letting `set -o pipefail` or a non-zero find status abort the script.
    if ! find "$path" -mindepth 1 -maxdepth 1 -mtime "+${RETENTION_DAYS}" \
            -exec rm -rf {} + 2>/dev/null; then
        warn "Could not prune old backups in $path (filesystem may not support deletion). Prune manually."
    else
        log "   • pruned: $path"
    fi
}

prune_path "$BACKUP_ROOT/postgres"
prune_path "$BACKUP_ROOT/qdrant"
prune_path "$BACKUP_ROOT/minio"

# Prune old MinIO backup prefixes in-cluster as well (best effort).
if container_running "$MINIO_CONTAINER"; then
    docker run --rm --network "${MINIO_NET:-omniflow-prod-net}" \
        -e MC_HOST_myminio="http://${MINIO_ROOT_USER:-minio}:${MINIO_ROOT_PASSWORD:-}@minio:9000" \
        minio/mc rm --recursive --force --older-than "${RETENTION_DAYS}d" \
        "myminio/${MINIO_BACKUP_BUCKET}" >/dev/null 2>&1 \
        || warn "Could not prune old objects in myminio/${MINIO_BACKUP_BUCKET}."
fi

# ═════════════════════════════════════════════════════════════════════════════
log "=============================================="
if [ "$EXIT_CODE" -eq 0 ]; then
    log "🎉 Backup finished successfully — $BACKUP_ROOT"
else
    log "⚠️  Backup finished WITH ERRORS — review the log above."
fi
log "Disk usage: $(du -sh "$BACKUP_ROOT" 2>/dev/null | cut -f1)"
log "=============================================="

exit "$EXIT_CODE"
