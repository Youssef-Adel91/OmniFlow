#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# restore_drill.sh — real Qdrant + MinIO restore drill (item 18)
#
# Before this script, restore.sh's steps for both systems had NEVER been run
# against a real instance (RELEASE_CHECKLIST.md said so explicitly: "off-host
# copies and Qdrant/MinIO restoration remain unverified"). This drill creates
# real data, backs it up with the same commands backup.sh uses, destroys the
# original, restores with restore.sh, and verifies the data actually came
# back byte-for-byte / point-for-point — against a REAL local Qdrant + MinIO,
# not mocks.
#
# Requires: a local dev stack with Qdrant and MinIO running (docker ps must
# show them). Reads QDRANT_API_KEY / MINIO_ROOT_USER / MINIO_ROOT_PASSWORD
# from the environment or a .env file (see ENV_FILE below) — never prints
# their values.
#
# Usage:
#   QDRANT_CONTAINER=omniflow-qdrant MINIO_CONTAINER=omniflow-minio \
#   NETWORK=omniflow-net QDRANT_URL=http://localhost:6333 \
#     ./tests/restore_drill.sh
# ═════════════════════════════════════════════════════════════════════════════
set -uo pipefail

# Prevents Git Bash/MSYS on Windows from mangling container-side paths
# (e.g. `/data/file.txt`) into host Windows paths before they reach `docker
# run`. A no-op on native Linux, where this variable has no special meaning.
export MSYS_NO_PATHCONV=1

DEPLOY_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
ENV_FILE="${ENV_FILE:-}"
if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

QDRANT_CONTAINER="${QDRANT_CONTAINER:-omniflow-qdrant}"
MINIO_CONTAINER="${MINIO_CONTAINER:-omniflow-minio}"
NETWORK="${NETWORK:-omniflow-net}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
QDRANT_API_KEY="${QDRANT_API_KEY:-}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"

DRILL_ID="$(date +%s)"
FAIL=0

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
pass() { echo "[$(date '+%H:%M:%S')] PASS: $*"; }
err()  { echo "[$(date '+%H:%M:%S')] FAIL: $*" >&2; FAIL=1; }

# On native Linux this is a no-op passthrough. On Windows (Git Bash/MSYS),
# `docker run -v` needs a Windows-style path for the host side of a bind
# mount — a plain /tmp/... path is not visible to Docker Desktop's VM.
_docker_mount_path() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$1"
    else
        echo "$1"
    fi
}

qcurl() { curl -s -H "api-key: ${QDRANT_API_KEY}" "$@"; }
mc_run() {
    docker run --rm --network "$NETWORK" \
        -e MC_HOST_myminio="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
        minio/mc "$@"
}

# ═════════════════════════════════════════════════════════════════════════════
# Qdrant restore drill
# ═════════════════════════════════════════════════════════════════════════════
run_qdrant_drill() {
    local coll="restore_drill_${DRILL_ID}"
    log "── Qdrant: creating collection '${coll}' with 3 points ──"

    qcurl -X PUT "${QDRANT_URL}/collections/${coll}" -H 'Content-Type: application/json' \
        -d '{"vectors":{"size":4,"distance":"Cosine"}}' >/dev/null

    qcurl -X PUT "${QDRANT_URL}/collections/${coll}/points?wait=true" -H 'Content-Type: application/json' \
        -d '{"points":[{"id":1,"vector":[0.1,0.2,0.3,0.4],"payload":{"tag":"a"}},{"id":2,"vector":[0.5,0.6,0.7,0.8],"payload":{"tag":"b"}},{"id":3,"vector":[0.9,0.1,0.2,0.3],"payload":{"tag":"c"}}]}' >/dev/null

    local before_count
    before_count="$(qcurl "${QDRANT_URL}/collections/${coll}" | grep -o '"points_count":[0-9]*' | cut -d: -f2)"
    [ "$before_count" = "3" ] || { err "setup: expected 3 points before drill, got '${before_count}'"; return; }

    log "── Qdrant: snapshotting ──"
    local snap_json snap_name
    snap_json="$(qcurl -X POST "${QDRANT_URL}/collections/${coll}/snapshots")"
    snap_name="$(echo "$snap_json" | grep -o '"name":"[^"]*"' | head -n1 | cut -d'"' -f4)"
    [ -n "$snap_name" ] || { err "snapshot creation failed"; return; }

    # See run_minio_drill for why $TEMP (not Git Bash's own /tmp) is used —
    # `docker cp`'s destination argument gets the same MSYS path-mangling
    # treatment as `docker run` arguments on Windows.
    local tmp_dir="${TEMP:-${TMPDIR:-/tmp}}/restore_drill_${DRILL_ID}"
    mkdir -p "$tmp_dir"
    docker cp "${QDRANT_CONTAINER}:/qdrant/snapshots/${coll}/${snap_name}" "${tmp_dir}/${snap_name}" \
        || { err "docker cp of snapshot failed"; return; }

    log "── Qdrant: deleting collection (simulating data loss) ──"
    qcurl -X DELETE "${QDRANT_URL}/collections/${coll}" >/dev/null
    local after_delete
    after_delete="$(qcurl -o /dev/null -w '%{http_code}' "${QDRANT_URL}/collections/${coll}")"
    [ "$after_delete" = "404" ] || { err "collection still exists after delete (status ${after_delete})"; return; }

    log "── Qdrant: restoring via restore.sh ──"
    if ! QDRANT_CONTAINER="$QDRANT_CONTAINER" ENV_FILE=/dev/null \
            "$DEPLOY_DIR/restore.sh" qdrant "$coll" "${tmp_dir}/${snap_name}" >/dev/null 2>&1; then
        err "restore.sh qdrant returned non-zero"
        return
    fi

    local after_count
    after_count="$(qcurl "${QDRANT_URL}/collections/${coll}" | grep -o '"points_count":[0-9]*' | cut -d: -f2)"
    if [ "$after_count" = "3" ]; then
        pass "Qdrant restore: 3/3 points recovered via a real snapshot + real recover API call"
    else
        err "Qdrant restore: expected 3 points after restore, got '${after_count}'"
    fi

    qcurl -X DELETE "${QDRANT_URL}/collections/${coll}" >/dev/null
    rm -rf "$tmp_dir"
}

# ═════════════════════════════════════════════════════════════════════════════
# MinIO restore drill
# ═════════════════════════════════════════════════════════════════════════════
run_minio_drill() {
    local bucket="restore-drill-src-${DRILL_ID}"
    local backup_bucket="restore-drill-backup-${DRILL_ID}"
    # On Windows, prefer the real Windows temp dir (Docker Desktop shares the
    # user profile by default) over Git Bash's own /tmp, which usually lives
    # under the Git install directory and is not shareable into containers.
    local base_tmp="${TEMP:-${TMPDIR:-/tmp}}"
    local tmp_dir_host="${base_tmp}/minio_drill_${DRILL_ID}"
    mkdir -p "$tmp_dir_host"
    echo "hello world file 1" > "${tmp_dir_host}/drill1.txt"
    echo "hello world file 2, more content here" > "${tmp_dir_host}/drill2.txt"

    log "── MinIO: creating source bucket with 2 objects ──"
    mc_run mb "myminio/${bucket}" >/dev/null
    docker run --rm --network "$NETWORK" -v "$(_docker_mount_path "$tmp_dir_host"):/data" \
        -e MC_HOST_myminio="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
        minio/mc cp /data/drill1.txt /data/drill2.txt "myminio/${bucket}/" >/dev/null

    log "── MinIO: mirroring to backup bucket ──"
    mc_run mb "myminio/${backup_bucket}" >/dev/null
    mc_run mirror --overwrite --quiet "myminio/${bucket}" "myminio/${backup_bucket}/snapshot1" >/dev/null

    log "── MinIO: deleting source objects (simulating data loss) ──"
    mc_run rm --recursive --force "myminio/${bucket}" >/dev/null
    local remaining
    remaining="$(mc_run ls "myminio/${bucket}/" 2>/dev/null | wc -l)"
    [ "$remaining" = "0" ] || { err "source bucket still has objects after delete"; }

    log "── MinIO: restoring via restore.sh ──"
    if ! MINIO_CONTAINER="$MINIO_CONTAINER" ENV_FILE=/dev/null \
            MINIO_ROOT_USER="$MINIO_ROOT_USER" MINIO_ROOT_PASSWORD="$MINIO_ROOT_PASSWORD" \
            "$DEPLOY_DIR/restore.sh" minio "${backup_bucket}/snapshot1" "$bucket" >/dev/null 2>&1; then
        err "restore.sh minio returned non-zero"
        return
    fi

    local restored_dir="${tmp_dir_host}/restored"
    mkdir -p "$restored_dir"
    docker run --rm --network "$NETWORK" -v "$(_docker_mount_path "$tmp_dir_host"):/data" \
        -e MC_HOST_myminio="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000" \
        minio/mc cp "myminio/${bucket}/drill1.txt" "myminio/${bucket}/drill2.txt" /data/restored/ >/dev/null 2>&1

    if diff -q "${tmp_dir_host}/drill1.txt" "${restored_dir}/drill1.txt" >/dev/null 2>&1 \
            && diff -q "${tmp_dir_host}/drill2.txt" "${restored_dir}/drill2.txt" >/dev/null 2>&1; then
        pass "MinIO restore: 2/2 objects recovered byte-for-byte identical via real mc mirror"
    else
        err "MinIO restore: restored file content does not match the originals"
    fi

    mc_run rb --force "myminio/${bucket}" >/dev/null 2>&1
    mc_run rb --force "myminio/${backup_bucket}" >/dev/null 2>&1
    rm -rf "$tmp_dir_host"
}

log "=============================================="
log "Restore drill started (id=${DRILL_ID})"
log "=============================================="
run_qdrant_drill
run_minio_drill
log "=============================================="
if [ "$FAIL" -eq 0 ]; then
    log "ALL SCENARIOS PASSED"
else
    log "ONE OR MORE SCENARIOS FAILED — see FAIL lines above"
fi
exit "$FAIL"
