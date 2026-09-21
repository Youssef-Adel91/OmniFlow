#!/usr/bin/env bash
# ═════════════════════════════════════════════════════════════════════════════
# deploy.sh — OmniFlow AI Production Deployment Script
# Focus: Security, Idempotency, Clean Upgrades
#
# ── ROLLBACK (manual, documented on purpose) ─────────────────────────────────
# There is no automatic rollback: a failed deploy leaves the previous images on
# the host, so reverting is a two-command operation. Tag every release first:
#
#     git tag -a v1.4.0 -m "release 1.4.0" && git push --tags
#
# To roll back:
#     cd /opt/omniflow/omniflow-deploy
#     ./backup.sh                              # 1. snapshot current state FIRST
#     docker compose --env-file .env.prod -f docker-compose.prod.yml down
#     git checkout v1.3.0                      # 2. previous known-good tag
#     (cd ../omniflow-backend  && git checkout v1.3.0)
#     (cd ../omniflow-frontend && git checkout v1.3.0)
#     ./deploy.sh                              # 3. rebuild & start the old code
#
# ⚠️ Database migrations are NOT reverted by the steps above. If the failed
# release ran `alembic upgrade head`, downgrade explicitly before starting the
# old code:
#     docker compose --env-file .env.prod -f docker-compose.prod.yml \
#         run --rm fastapi-backend alembic downgrade <previous_revision>
# ...or restore the pre-deploy dump produced by backup.sh.
# ═════════════════════════════════════════════════════════════════════════════

set -euo pipefail

COMPOSE_FILE="docker-compose.prod.yml"
# --env-file is REQUIRED: `env_file:` only injects variables INTO containers,
# it does NOT feed Compose's own ${VAR} interpolation. Without this flag every
# ${POSTGRES_PASSWORD}/${REDIS_PASSWORD}/... in the compose file resolves to an
# empty string and the stack silently comes up with blank credentials.
COMPOSE="docker compose --env-file .env.prod -f $COMPOSE_FILE"

echo "=============================================="
echo "🚀 OmniFlow AI Production Deployment Starting"
echo "=============================================="

# ── 1. Ensure we run from the deployment directory ───────────────────────────
DEPLOY_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$DEPLOY_DIR"

# ── 2. Check for the environment file ────────────────────────────────────────
if [ ! -f .env.prod ]; then
    echo "❌ Error: .env.prod file not found in $DEPLOY_DIR"
    echo "   Create it from the template:  cp .env.prod.example .env.prod"
    echo "   ...then fill in every <REQUIRED> value."
    exit 1
fi
chmod 600 .env.prod 2>/dev/null || true
echo "✅ Environment file found."

# Fail fast if a placeholder secret was left in place.
if grep -qE '^[A-Z_]+=(<REQUIRED|CHANGE_ME|changeme)' .env.prod; then
    echo "❌ Error: .env.prod still contains placeholder values:"
    grep -nE '^[A-Z_]+=(<REQUIRED|CHANGE_ME|changeme)' .env.prod | sed 's/^/     /'
    exit 1
fi

# ── 3. Persistent directories for Nginx & Certbot ────────────────────────────
echo "✅ Validating persistent volume directories..."
mkdir -p nginx/snippets
mkdir -p certbot/www
mkdir -p certbot/conf
mkdir -p monitoring
mkdir -p backups/postgres backups/qdrant backups/minio

# The ACME challenge folder must be world-readable for nginx to serve it.
chmod -R 755 certbot/www || true

# Basic-auth file for /docs — must exist as a FILE before compose bind-mounts
# it, otherwise Docker silently creates a directory in its place.
if [ ! -e nginx/htpasswd ]; then
    echo "# empty: nobody can authenticate until a user is added (fail-closed)" > nginx/htpasswd
    echo "⚠️  Created an empty nginx/htpasswd — /docs is locked to everyone."
    echo "    Add a user: docker run --rm httpd:2.4-alpine htpasswd -nbB omniflow 'PASSWORD' >> nginx/htpasswd"
fi

# ── 4. Domain placeholder check ──────────────────────────────────────────────
if grep -q 'SERVER_DOMAIN' nginx/prod.conf; then
    echo ""
    echo "⚠️ ============================================================="
    echo "⚠️  nginx/prod.conf still contains the SERVER_DOMAIN placeholder."
    echo "⚠️  Replace it before serving real traffic:"
    echo "⚠️      sed -i 's/SERVER_DOMAIN/yourdomain.com/g' nginx/prod.conf"
    echo "⚠️  Deployment continues so you can bootstrap TLS, but HTTPS will"
    echo "⚠️  present a self-signed certificate until this is fixed."
    echo "⚠️ ============================================================="
    echo ""
fi

# Derive the certificate directory from whatever server_name is configured.
CERT_DOMAIN="$(grep -m1 -oE 'ssl_certificate +/etc/letsencrypt/live/[^/]+' nginx/prod.conf \
               | awk -F/ '{print $NF}')"
CERT_DOMAIN="${CERT_DOMAIN:-SERVER_DOMAIN}"

# ── 5. Bootstrap a self-signed certificate so nginx can always start ─────────
# Without a certificate file present, the 443 server block makes nginx refuse
# to boot — which would also take down the HTTP-01 challenge needed to obtain
# the real certificate. A throw-away self-signed cert breaks that deadlock.
CERT_DIR="certbot/conf/live/${CERT_DOMAIN}"
if [ ! -f "${CERT_DIR}/fullchain.pem" ]; then
    echo "🔐 No certificate at ${CERT_DIR} — generating a self-signed placeholder..."
    mkdir -p "$CERT_DIR"
    docker run --rm -v "$DEPLOY_DIR/${CERT_DIR}:/certs" alpine/openssl \
        req -x509 -nodes -newkey rsa:2048 -days 365 \
            -keyout /certs/privkey.pem -out /certs/fullchain.pem \
            -subj "/CN=${CERT_DOMAIN}" >/dev/null 2>&1 \
        || {
            echo "❌ Could not generate the placeholder certificate."
            echo "   Generate one manually, or install the real Let's Encrypt cert, then re-run."
            exit 1
        }
    cp "${CERT_DIR}/fullchain.pem" "${CERT_DIR}/chain.pem" 2>/dev/null || true
    echo "⚠️  SELF-SIGNED certificate in use — browsers WILL show a warning."
    echo "    Replace it with a real one (see step 'Certbot' at the end of this script)."
fi

# ── 6. Pull latest base images ───────────────────────────────────────────────
echo "✅ Pulling latest base images..."
$COMPOSE pull --ignore-buildable || $COMPOSE pull || true

# ── 7. Validate the compose file before touching anything ────────────────────
echo "✅ Validating compose configuration..."
$COMPOSE config --quiet

# ── 8. Build and deploy ──────────────────────────────────────────────────────
echo "✅ Building and starting services (network isolated)..."
# --remove-orphans cleans up containers no longer defined in the compose file.
$COMPOSE up -d --build --remove-orphans

# ── 9. Wait for the database to be genuinely ready ───────────────────────────
echo "⏳ Waiting for PostgreSQL to report healthy..."
DB_READY=0
for i in $(seq 1 60); do
    STATUS="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
              prod-postgres 2>/dev/null || echo "missing")"
    if [ "$STATUS" = "healthy" ]; then
        DB_READY=1
        echo "✅ PostgreSQL healthy after ${i} attempt(s)."
        break
    fi
    printf '.'
    sleep 3
done
echo ""

if [ "$DB_READY" -ne 1 ]; then
    echo "❌ PostgreSQL did not become healthy within ~180s. Aborting before migrations."
    echo "   Inspect with: $COMPOSE logs postgres"
    exit 1
fi

# ── 10. Database migrations ──────────────────────────────────────────────────
echo "🗄️  Applying database migrations (alembic upgrade head)..."
if $COMPOSE exec -T fastapi-backend alembic upgrade head; then
    echo "✅ Migrations applied."
else
    echo "❌ Migration failed. The new code is running against an OUTDATED schema."
    echo "   Investigate:  $COMPOSE logs fastapi-backend"
    echo "   Roll back:    see the ROLLBACK section at the top of this script."
    exit 1
fi

# ── 11. Post-deploy health check ─────────────────────────────────────────────
echo "🩺 Running post-deployment health checks..."
HEALTH_OK=0
for i in $(seq 1 20); do
    # nginx redirects :80 -> :443, so probe the dedicated nginx liveness route
    # first, then the backend through the proxy (-k: the cert may be self-signed).
    if curl -fsS -o /dev/null http://localhost/nginx-health 2>/dev/null \
       && curl -fksS -o /dev/null https://localhost/health 2>/dev/null; then
        HEALTH_OK=1
        break
    fi
    printf '.'
    sleep 5
done
echo ""

echo "── Service status ─────────────────────────────────────"
$COMPOSE ps
echo "───────────────────────────────────────────────────────"

if [ "$HEALTH_OK" -eq 1 ]; then
    echo "✅ HEALTH CHECK PASSED — nginx is serving and /health returns 200."
else
    echo "❌ HEALTH CHECK FAILED — the stack is up but not answering correctly."
    echo "   Debug with:"
    echo "     $COMPOSE logs --tail=100 nginx"
    echo "     $COMPOSE logs --tail=100 fastapi-backend"
    echo "     curl -v http://localhost/nginx-health"
    exit 1
fi

# ── 12. Cleanup dangling images ──────────────────────────────────────────────
echo "🧹 Cleaning up unused Docker images..."
docker image prune -f >/dev/null

echo "=============================================="
echo "🎉 Deployment successful."
echo "=============================================="
echo "Logs:"
echo " - Nginx Ingress : $COMPOSE logs -f nginx"
echo " - FastAPI APIs  : $COMPOSE logs -f fastapi-backend"
echo " - Next.js App   : $COMPOSE logs -f nextjs-frontend"
echo " - Dispatcher    : $COMPOSE logs -f worker-dispatcher"
echo " - Celery        : $COMPOSE logs -f celery-worker celery-beat"
echo ""
echo "Optional monitoring stack (Prometheus + Grafana, localhost only):"
echo "  docker compose --env-file .env.prod -f $COMPOSE_FILE -f docker-compose.monitoring.yml up -d"
echo ""
echo "Backups — install the cron job once:"
echo "  0 3 * * * $DEPLOY_DIR/backup.sh >> /var/log/omniflow-backup.log 2>&1"
echo ""
echo "── TLS: issue the REAL certificate ────────────────────"
echo "1) Point the DNS A/AAAA records of your domain at this server."
echo "2) Replace the placeholder:  sed -i 's/SERVER_DOMAIN/yourdomain.com/g' nginx/prod.conf"
echo "3) Run certbot (HTTP-01 via the webroot nginx already serves):"
echo "   docker run --rm \\"
echo "     -v \"\$(pwd)/certbot/conf:/etc/letsencrypt\" \\"
echo "     -v \"\$(pwd)/certbot/www:/var/www/certbot\" \\"
echo "     certbot/certbot certonly --webroot -w /var/www/certbot --force-renewal \\"
echo "     -d yourdomain.com -d www.yourdomain.com \\"
echo "     --email ops@yourdomain.com --agree-tos --no-eff-email"
echo "4) Reload: $COMPOSE restart nginx"
echo "5) Auto-renew (cron, twice daily as Let's Encrypt recommends):"
echo "   0 */12 * * * cd $DEPLOY_DIR && docker run --rm -v \"\$(pwd)/certbot/conf:/etc/letsencrypt\" -v \"\$(pwd)/certbot/www:/var/www/certbot\" certbot/certbot renew --quiet && $COMPOSE exec -T nginx nginx -s reload"
echo "───────────────────────────────────────────────────────"
