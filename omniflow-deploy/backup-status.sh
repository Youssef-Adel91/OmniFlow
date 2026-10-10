#!/usr/bin/env bash
# backup-status.sh — is the latest backup fresh? Exit 0 = yes, 1 = stale/missing, 2 = fresh locally but never copied off-host.
# Safe to run from cron/monitoring; prints one line. Reads the markers backup.sh writes (no Docker needed).
set -uo pipefail
DEPLOY_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BACKUP_ROOT="${BACKUP_ROOT:-$DEPLOY_DIR/backups}"
MAX_AGE_HOURS="${MAX_AGE_HOURS:-26}"
now="$(date +%s)"
last="$(cat "$BACKUP_ROOT/.last_success" 2>/dev/null || echo 0)"
off="$(cat "$BACKUP_ROOT/.last_offhost_success" 2>/dev/null || echo 0)"
if [ "$last" -le 0 ]; then echo "STALE: no successful backup has ever been recorded in $BACKUP_ROOT"; exit 1; fi
age_h=$(( (now - last) / 3600 ))
if [ "$age_h" -gt "$MAX_AGE_HOURS" ]; then echo "STALE: last successful backup was ${age_h}h ago (limit ${MAX_AGE_HOURS}h)"; exit 1; fi
if [ "$off" -le 0 ] || [ $(( (now - off) / 3600 )) -gt "$MAX_AGE_HOURS" ]; then
    echo "WARN: local backup is fresh (${age_h}h) but there is no recent OFF-HOST copy"; exit 2
fi
echo "OK: local backup ${age_h}h old, off-host copy $(( (now - off) / 3600 ))h old"
