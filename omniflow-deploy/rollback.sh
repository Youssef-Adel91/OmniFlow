#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# rollback.sh — go back to a previous known-good release (code + optionally the DB schema).
#
#   ./rollback.sh <git-ref> [--db-revision <alembic-revision>] [--yes]
#
# DEFAULT IS A DRY RUN: it prints exactly what it would do and changes nothing. Add --yes to execute.
#
# What it does (in order, each step aborts the whole thing on failure):
#   1. refuses to run with uncommitted changes in the repo
#   2. ./backup.sh                      — fresh snapshot of the CURRENT state first
#   3. stops ONLY the application services (nginx stays up so TLS/ACME keep working; data volumes are NEVER touched)
#   4. checks out <git-ref> for the whole repo
#   5. optional: alembic downgrade <revision>  (only if you pass --db-revision — see the warning below)
#   6. ./deploy.sh                      — rebuilds and starts the old code
#
# ⚠️ Schema: new releases add migrations (0021…0027 at the time of writing). The OLD code usually still runs on a NEWER
# schema because the migrations are additive, so the safe default is NOT to downgrade. Downgrading DROPS the tables/columns
# those migrations created (billing history, notifications, invitations …). Only pass --db-revision when you know the old
# code cannot run on the new schema, and only after reading the downgrade() of every migration in between.
# ⚠️ This script never deletes volumes, never runs `git reset --hard`/`git clean`, and never touches .env.prod.
# ═════════════════════════════════════════════════════════════════════════════
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$DEPLOY_DIR"
REPO_ROOT="$(git rev-parse --show-toplevel)"
COMPOSE="docker compose --env-file .env.prod -f docker-compose.prod.yml"

REF="${1:-}"
[ -n "$REF" ] && [ "${REF#-}" = "$REF" ] || { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
shift
DB_REV=""; EXEC=0
while [ $# -gt 0 ]; do
    case "$1" in
        --db-revision) DB_REV="${2:-}"; [ -n "$DB_REV" ] || { echo "--db-revision needs a value"; exit 2; }; shift 2 ;;
        --yes) EXEC=1; shift ;;
        *) echo "Unknown argument: $1"; exit 2 ;;
    esac
done

run() { echo "  \$ $*"; if [ "$EXEC" -eq 1 ]; then "$@"; fi; }

echo "══ OmniFlow rollback ═══════════════════════════════════════"
echo "Target ref : $REF  ($(git -C "$REPO_ROOT" rev-parse --short "$REF" 2>/dev/null || echo 'NOT FOUND'))"
echo "Current    : $(git -C "$REPO_ROOT" rev-parse --short HEAD) ($(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD))"
echo "DB         : ${DB_REV:+downgrade to $DB_REV}${DB_REV:-schema left as is (recommended)}"
echo "Mode       : $([ "$EXEC" -eq 1 ] && echo EXECUTE || echo 'DRY RUN (add --yes to execute)')"
echo "────────────────────────────────────────────────────────────"

git -C "$REPO_ROOT" rev-parse --verify --quiet "$REF^{commit}" >/dev/null || { echo "❌ Unknown git ref: $REF"; exit 1; }
if [ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=no)" ]; then
    echo "❌ The repo has uncommitted changes. Commit or stash them first (this script will not discard anything)."; exit 1
fi
[ -f .env.prod ] || { echo "❌ .env.prod not found"; exit 1; }

echo "1) snapshot the current state"
run ./backup.sh
echo "2) stop the application services only (data plane and nginx keep running)"
run $COMPOSE stop fastapi-backend nextjs-frontend worker-router worker-invoker worker-dispatcher worker-vcard-gatekeeper worker-vault worker-broadcast celery-worker celery-beat
echo "3) check out $REF"
run git -C "$REPO_ROOT" checkout --detach "$REF"
if [ -n "$DB_REV" ]; then
    echo "4) DATABASE DOWNGRADE to $DB_REV (destroys data created by the newer migrations!)"
    run $COMPOSE --profile maintenance run --rm --no-deps migrate alembic downgrade "$DB_REV"
fi
echo "5) rebuild and start the old release"
run ./deploy.sh
echo "────────────────────────────────────────────────────────────"
if [ "$EXEC" -eq 1 ]; then
    echo "✅ Rollback finished. Verify: curl -fsS https://<domain>/health/ready  and  docker exec prod-fastapi python -c \"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health/workers').read())\""
    echo "   Return to the newer release later with: git -C $REPO_ROOT checkout <branch> && ./deploy.sh"
else
    echo "Dry run only — nothing was changed."
fi
