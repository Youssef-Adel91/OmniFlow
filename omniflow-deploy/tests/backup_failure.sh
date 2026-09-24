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
export ENV_FILE=/dev/null RETENTION_DAYS=0
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
