#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# restore.sh — Qdrant and MinIO restore (item 18: backup.sh had backup code
# for both but only documented the restore steps as comments — never as
# runnable code, and never drilled. This is the real, tested counterpart.)
#
# PostgreSQL restore is intentionally NOT here: it is a single well-known
# `pg_restore`/`psql` invocation, already documented as a comment in
# backup.sh, and RELEASE_CHECKLIST.md already records a real local restore
# drill for it. Qdrant/MinIO restore had no code and no drill at all, which
# is the actual gap this script closes.
#
# Usage:
#   ./restore.sh qdrant <collection> <snapshot-file>
#   ./restore.sh minio  <source-bucket-or-prefix> <destination-bucket>
#
# Container/network names follow the exact same env-var conventions as
# backup.sh (QDRANT_CONTAINER, MINIO_CONTAINER, MINIO_ROOT_USER, ...) so a
# restore always targets the same stack a backup was taken from.
# ═════════════════════════════════════════════════════════════════════════════
set -uo pipefail

DEPLOY_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ENV_FILE="${ENV_FILE:-$DEPLOY_DIR/.env.prod}"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

QDRANT_CONTAINER="${QDRANT_CONTAINER:-prod-qdrant}"
MINIO_CONTAINER="${MINIO_CONTAINER:-prod-minio}"

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
fail() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; exit 1; }

restore_qdrant() {
    local collection="$1" snapshot_path="$2"
    [ -f "$snapshot_path" ] || fail "Snapshot file not found: $snapshot_path"

    local net
    net="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$QDRANT_CONTAINER" 2>/dev/null | head -n1)"
    net="${net:-omniflow-prod-net}"

    local key="${QDRANT_API_KEY:-}"
    local auth_args=()
    [ -n "$key" ] && auth_args=(-H "api-key: $key")

    qcurl() {
        docker run --rm --network "$net" curlimages/curl:8.11.0 \
            -fsS --max-time 120 "${auth_args[@]}" "$@"
    }

    # A gzipped snapshot is decompressed to a scratch file first — Qdrant's
    # recover API expects the raw `.snapshot` file, not the `.gz` backup.sh
    # actually writes to disk.
    local in_container_name
    in_container_name="$(basename "$snapshot_path")"
    local tmp_snapshot="$snapshot_path"
    if [[ "$snapshot_path" == *.gz ]]; then
        tmp_snapshot="${snapshot_path%.gz}"
        gunzip -c "$snapshot_path" > "$tmp_snapshot" || fail "Failed to decompress $snapshot_path"
        in_container_name="$(basename "$tmp_snapshot")"
    fi

    log "Copying snapshot into $QDRANT_CONTAINER:/qdrant/snapshots/${collection}/"
    docker exec "$QDRANT_CONTAINER" mkdir -p "/qdrant/snapshots/${collection}" \
        || fail "Could not create snapshot dir in container"
    docker cp "$tmp_snapshot" "${QDRANT_CONTAINER}:/qdrant/snapshots/${collection}/${in_container_name}" \
        || fail "docker cp into container failed"

    log "Recovering collection '${collection}' from ${in_container_name}"
    qcurl -X PUT "http://qdrant:6333/collections/${collection}/snapshots/recover" \
        -H 'Content-Type: application/json' \
        -d "{\"location\":\"file:///qdrant/snapshots/${collection}/${in_container_name}\"}" \
        || fail "Qdrant recover API call failed"

    log "Restore complete: collection '${collection}'"
}

restore_minio() {
    local source="$1" destination="$2"

    local net
    net="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$MINIO_CONTAINER" 2>/dev/null | head -n1)"
    net="${net:-omniflow-prod-net}"

    log "Mirroring myminio/${source} -> myminio/${destination}"
    docker run --rm --network "$net" \
        -e MC_HOST_myminio="http://${MINIO_ROOT_USER:-minio}:${MINIO_ROOT_PASSWORD:-}@minio:9000" \
        minio/mc mirror --overwrite --quiet "myminio/${source}" "myminio/${destination}" \
        || fail "mc mirror restore failed"

    log "Restore complete: myminio/${destination}"
}

case "${1:-}" in
    qdrant)
        [ $# -eq 3 ] || fail "Usage: $0 qdrant <collection> <snapshot-file>"
        restore_qdrant "$2" "$3"
        ;;
    minio)
        [ $# -eq 3 ] || fail "Usage: $0 minio <source-bucket-or-prefix> <destination-bucket>"
        restore_minio "$2" "$3"
        ;;
    *)
        fail "Usage: $0 {qdrant|minio} ..."
        ;;
esac
