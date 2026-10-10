#!/usr/bin/env bash
# Static validation of the deployment files. Needs NO Docker daemon (only the docker CLI + compose plugin for `config`).
# Does NOT prove the stack boots — that needs a real host (see MANUAL_STEPS.md).
set -euo pipefail
DEPLOY_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DEPLOY_DIR"
fail() { echo "FAIL: $*"; exit 1; }

for f in deploy.sh backup.sh restore.sh rollback.sh backup-status.sh tests/*.sh; do bash -n "$f" || fail "bash -n $f"; done
echo "PASS: shell syntax"

# compose files resolve with a throw-away env file made from the example (placeholders replaced; never a real secret)
TMP_ENV="$(mktemp)"; trap 'rm -f "$TMP_ENV" .env.prod.validate' EXIT
python3 - "$TMP_ENV" <<'PY'
import re, sys
out = []
for line in open(".env.prod.example"):
    m = re.match(r"^([A-Z_0-9]+)=(.*)$", line.rstrip("\n"))
    if m:
        v = re.sub(r"\s+#.*$", "", m.group(2))
        out.append(f"{m.group(1)}={'dummyvalue' if (v == '' or v.startswith('<REQUIRED')) else v}")
open(sys.argv[1], "w").write("\n".join(out) + "\n")
PY
DUP="$(awk -F= '/^[A-Z_0-9]+=/{c[$1]++} END{for(k in c) if(c[k]>1) print k}' .env.prod.example)"
[ -z "$DUP" ] || fail ".env.prod.example has duplicate keys: $DUP"
echo "PASS: no duplicate keys in .env.prod.example"
# the compose files reference `env_file: .env.prod`; give them a disposable one if the real one is absent
CREATED=0; if [ ! -f .env.prod ]; then cp "$TMP_ENV" .env.prod; CREATED=1; fi
trap 'rm -f "$TMP_ENV"; [ "$CREATED" -eq 1 ] && rm -f .env.prod' EXIT
docker compose --env-file "$TMP_ENV" -f docker-compose.prod.yml --profile maintenance config --quiet || fail "docker-compose.prod.yml"
docker compose --env-file "$TMP_ENV" -f docker-compose.prod.yml -f docker-compose.monitoring.yml config --quiet || fail "compose + monitoring"
echo "PASS: compose files resolve"
# every always-on service must restart automatically
python3 - "$TMP_ENV" <<'PY'
import subprocess, sys, json
cfg = json.loads(subprocess.check_output(["docker", "compose", "--env-file", sys.argv[1], "-f", "docker-compose.prod.yml", "--profile", "maintenance", "config", "--format", "json"]))
bad = [n for n, s in cfg["services"].items() if n not in ("migrate", "minio-init") and s.get("restart") != "always"]
assert not bad, f"services without restart: always -> {bad}"
print("PASS: restart: always on", len(cfg["services"]) - 2, "services")
PY
python3 - <<'PY'
import yaml
for f in ("monitoring/prometheus.yml", "monitoring/rules/omniflow-alerts.yml", "monitoring/blackbox.yml", "monitoring/alertmanager.yml"):
    yaml.safe_load(open(f))
rules = yaml.safe_load(open("monitoring/rules/omniflow-alerts.yml"))
names = [r["alert"] for g in rules["groups"] for r in g["rules"]]
assert len(names) == len(set(names)), "duplicate alert names"
print("PASS: monitoring YAML parses;", len(names), "alert rules")
PY
echo "ALL STATIC CHECKS PASSED (promtool/nginx -t were not run: no daemon)."
