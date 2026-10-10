#!/usr/bin/env bash
# Exercise failure handling without a Docker socket or real service credentials.
set -euo pipefail
DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEST_DIR="$(mktemp -d)"
trap 'rm -rf "$TEST_DIR"' EXIT
mkdir -p "$TEST_DIR/bin"
cat > "$TEST_DIR/bin/docker" <<'SH'
#!/usr/bin/env bash
case "$1" in
  ps)
    echo prod-postgres
    if [ "$BACKUP_TEST_SCENARIO" != "missing_services" ]; then
      echo prod-qdrant
      echo prod-minio
    fi
    ;;
  exec) echo 'synthetic dump';;
  inspect) echo validation-net;;
  run)
    case "$*" in
      *curlimages/curl*)
        if [ "$BACKUP_TEST_SCENARIO" = "api_failure" ]; then exit 22; fi
        echo '{"result":{"collections":[]},"status":"ok"}'
        ;;
      *' mirror '*) printf '%s\n' "$*" >> "$BACKUP_TEST_MIRRORS";;
      *' rm --recursive'*) touch "$BACKUP_TEST_PRUNED";;
    esac
    ;;
  *) exit 1;;
esac
SH
chmod +x "$TEST_DIR/bin/docker"
export PATH="$TEST_DIR/bin:$PATH"
export ENV_FILE=/dev/null RETENTION_DAYS=0 TEXTFILE_DIR="$TEST_DIR/textfile"
export BACKUP_TEST_PRUNED="$TEST_DIR/pruned"
export BACKUP_TEST_MIRRORS="$TEST_DIR/mirrors"
for BACKUP_TEST_SCENARIO in missing_services api_failure; do
  export BACKUP_TEST_SCENARIO
  export BACKUP_ROOT="$TEST_DIR/$BACKUP_TEST_SCENARIO"
  mkdir -p "$BACKUP_ROOT/postgres"
  touch -d '3 days ago' "$BACKUP_ROOT/postgres/previous.dump"
  if bash "$DEPLOY_DIR/backup.sh" > "$TEST_DIR/output.log" 2>&1; then
    echo "FAIL: backup reported success for $BACKUP_TEST_SCENARIO"
    exit 1
  fi
  test -f "$BACKUP_ROOT/postgres/previous.dump"
  test ! -e "$BACKUP_TEST_PRUNED"
  grep -q 'skipping all pruning' "$TEST_DIR/output.log"
  echo "PASS: $BACKUP_TEST_SCENARIO fails and retains previous backups"
done

export BACKUP_TEST_SCENARIO=healthy
export BACKUP_ROOT="$TEST_DIR/healthy"
export MINIO_SOURCE_BUCKETS='test-media test-reports test-knowledge'
: > "$BACKUP_TEST_MIRRORS"
bash "$DEPLOY_DIR/backup.sh" > "$TEST_DIR/output.log" 2>&1
for bucket in test-media test-reports test-knowledge; do
  grep -q "myminio/$bucket myminio/omniflow-backups/[^ ]*/$bucket" "$BACKUP_TEST_MIRRORS"
done
test "$(wc -l < "$BACKUP_TEST_MIRRORS")" -eq 3
echo 'PASS: every configured source bucket gets a separate backup prefix'

# ── freshness markers + off-host copy ────────────────────────────────────────
PROM="$TEST_DIR/textfile/omniflow_backup.prom"
grep -q '^omniflow_backup_last_success_timestamp_seconds [0-9]' "$PROM"
grep -q '^omniflow_backup_offhost_configured 0' "$PROM"
! grep -q '^omniflow_backup_last_offhost_success_timestamp_seconds [0-9]' "$PROM"
grep -q 'NO off-host copy' "$TEST_DIR/output.log"
echo 'PASS: a healthy run records freshness and honestly reports that no off-host copy exists'

export BACKUP_ROOT="$TEST_DIR/offhost_ok" OFFHOST_COMMAND='echo "$BACKUP_ROOT $TIMESTAMP" > "$OFFHOST_MARK"' OFFHOST_MARK="$TEST_DIR/offhost_mark"
bash "$DEPLOY_DIR/backup.sh" > "$TEST_DIR/output.log" 2>&1
grep -q "^$BACKUP_ROOT [0-9]" "$OFFHOST_MARK"
grep -q '^omniflow_backup_last_offhost_success_timestamp_seconds [0-9]' "$PROM"
grep -q '^omniflow_backup_offhost_configured 1' "$PROM"
bash "$DEPLOY_DIR/backup-status.sh" | grep -q '^OK'
echo 'PASS: OFFHOST_COMMAND runs with BACKUP_ROOT/TIMESTAMP and is recorded'

export BACKUP_ROOT="$TEST_DIR/offhost_fail" OFFHOST_COMMAND='exit 7'
if bash "$DEPLOY_DIR/backup.sh" > "$TEST_DIR/output.log" 2>&1; then echo "FAIL: backup reported success although the off-host copy failed"; exit 1; fi
grep -q 'only on this server' "$TEST_DIR/output.log"
! grep -q '^omniflow_backup_last_offhost_success_timestamp_seconds [0-9]' "$PROM"
echo 'PASS: a failing OFFHOST_COMMAND fails the run and is not recorded as shipped'

export BACKUP_ROOT="$TEST_DIR/never_ran"
if bash "$DEPLOY_DIR/backup-status.sh" > "$TEST_DIR/status.log" 2>&1; then echo "FAIL: status ok without any backup"; exit 1; fi
grep -q '^STALE' "$TEST_DIR/status.log"
echo 'PASS: backup-status.sh flags a missing backup'
