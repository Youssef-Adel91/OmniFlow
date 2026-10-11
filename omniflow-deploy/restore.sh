#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# restore.sh — Qdrant and MinIO restore (item 18: backup.sh had backup code
# for both but only documented the restore steps as comments — never as
# runnable code, and never drilled. This is the real, tested counterpart.)
#
# PostgreSQL: `postgres` restores a backup.sh dump into a NEW database (it
# refuses to overwrite an existing one, so a live database can never be
# clobbered by a typo). Swap it in for the live one deliberately afterwards.
#
# Usage:
#   ./restore.sh postgres <dump-file> <new-database-name>
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

PG_CONTAINER="${PG_CONTAINER:-prod-postgres}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-prod-qdrant}"
MINIO_CONTAINER="${MINIO_CONTAINER:-prod-minio}"
# `minio/mc` no longer exists on Docker Hub. Bitnami's archived MinIO image bundles
# `mc` and pulls anonymously (see docker-compose.prod.yml). It runs as uid 1001
# instead of root, so mc invocations MUST use `--user 0`: as uid 1001, `mc mirror` into a
# root-owned host dir exits 0 while writing nothing (verified) -- a silent empty backup.
MC_IMAGE="${MC_IMAGE:-bitnamilegacy/minio:2025.4.22-debian-12-r1}"

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

restore_postgres() {
    local dump="$1" newdb="$2"
    [ -f "$dump" ] || fail "Dump file not found: $dump"
    [[ "$newdb" =~ ^[A-Za-z0-9_]+$ ]] || fail "Database name must be letters, digits and underscores only"
    local pg_user="${POSTGRES_USER:-omniflow}" live_db="${POSTGRES_DB:-omniflow_db}"
    [ "$newdb" != "$live_db" ] || fail "Refusing to restore over the live database '$live_db' — choose a new name (e.g. ${live_db}_restored)"

    if docker exec "$PG_CONTAINER" psql -U "$pg_user" -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='${newdb}'" | grep -q 1; then
        fail "Database '$newdb' already exists — refusing to overwrite it"
    fi
    log "Creating database ${newdb}"
    docker exec "$PG_CONTAINER" createdb -U "$pg_user" "$newdb" || fail "createdb failed"
    log "Copying dump into $PG_CONTAINER and restoring"
    docker cp "$dump" "${PG_CONTAINER}:/tmp/restore.dump" || fail "docker cp into container failed"
    docker exec "$PG_CONTAINER" pg_restore -U "$pg_user" -d "$newdb" --no-owner --no-privileges /tmp/restore.dump \
        || { docker exec "$PG_CONTAINER" rm -f /tmp/restore.dump; fail "pg_restore reported errors (database '$newdb' left in place for inspection)"; }
    docker exec "$PG_CONTAINER" rm -f /tmp/restore.dump
    log "Sanity check:"
    docker exec "$PG_CONTAINER" psql -U "$pg_user" -d "$newdb" -Atc "SELECT 'alembic', version_num FROM alembic_version; SELECT 'tenants', count(*) FROM tenants; SELECT 'rls_policies', count(*) FROM pg_policies WHERE schemaname='public';" \
        || fail "sanity query failed"
    log "Restore complete: database '${newdb}'. Point the app at it only after you have checked the numbers above."
}

restore_minio() {
    local source="$1" destination="$2"

    local net
    net="$(docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}' "$MINIO_CONTAINER" 2>/dev/null | head -n1)"
    net="${net:-omniflow-prod-net}"

    log "Mirroring myminio/${source} -> myminio/${destination}"
    docker run --rm --network "$net" \
        -e MC_HOST_myminio="http://${MINIO_ROOT_USER:-minio}:${MINIO_ROOT_PASSWORD:-}@minio:9000" \
        --user 0 --entrypoint mc "$MC_IMAGE" mirror --overwrite --quiet "myminio/${source}" "myminio/${destination}" \
        || fail "mc mirror restore failed"

    log "Restore complete: myminio/${destination}"
}

case "${1:-}" in
    postgres)
        [ $# -eq 3 ] || fail "Usage: $0 postgres <dump-file> <new-database-name>"
        restore_postgres "$2" "$3"
        ;;
    qdrant)
        [ $# -eq 3 ] || fail "Usage: $0 qdrant <collection> <snapshot-file>"
        restore_qdrant "$2" "$3"
        ;;
    minio)
        [ $# -eq 3 ] || fail "Usage: $0 minio <source-bucket-or-prefix> <destination-bucket>"
        restore_minio "$2" "$3"
        ;;
    *)
        fail "Usage: $0 {postgres|qdrant|minio} ..."
        ;;
esac
