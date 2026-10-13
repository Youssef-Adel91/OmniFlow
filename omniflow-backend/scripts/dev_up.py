"""Start the whole local stack in one command, with the checks that bite on a developer machine.

    python -m scripts.dev_up                 # check, then start API + workers + celery + frontend (+ ngrok if --ngrok)
    python -m scripts.dev_up --check-only    # only run the checks (ports, dependencies, ngrok URL), start nothing
    python -m scripts.dev_up --only api,worker-router
    python -m scripts.dev_up --ngrok --check-meta   # also compare the ngrok URL with the webhook URL stored in the Meta app (read-only call)

Checks, in this order — it stops BEFORE starting anything if one fails:
  1. PORT IN USE (Windows error 10048 / "address already in use"): every port this stack will bind is probed first. If one is taken it tells you
     which process holds it (netstat/ss) and how to free it. It NEVER kills anything for you.
  2. DEPENDENCIES: Postgres/PgBouncer, Redis, Kafka, Qdrant must accept connections (warning only for the optional ones).
  3. NGROK vs WEBHOOK URL: reads the live tunnel from ngrok's local API (127.0.0.1:4040) and compares it with PUBLIC_BASE_URL and, with
     --check-meta, with the callback URL stored in the Meta app (needs META_APP_ID/META_APP_SECRET; a read-only Graph call). A free ngrok
     domain changes on every restart, which silently breaks inbound webhooks: this is how you notice before debugging for an hour.
Ctrl+C stops everything it started.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "omniflow-backend"
FRONTEND = ROOT / "omniflow-frontend"
WEBHOOK_PATH = "/api/v1/webhooks/whatsapp"


@dataclass(frozen=True)
class Service:
    name: str
    cmd: list[str]
    cwd: Path
    port: int | None = None          # the port it will bind, if any


def services(py: str = sys.executable) -> list[Service]:
    solo = ["--pool=solo"] if platform.system() == "Windows" else []        # prefork does not work on Windows
    return [
        Service("api", [py, "-m", "uvicorn", "src.gateway.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"], BACKEND, 8000),
        Service("worker-router", [py, "-m", "src.ai_workers.semantic_router.worker"], BACKEND),
        Service("worker-llm", [py, "-m", "src.ai_workers.llm_invoker.worker"], BACKEND),
        Service("worker-outbound", [py, "-m", "src.ai_workers.outbound_dispatcher.worker"], BACKEND),
        Service("celery-worker", [py, "-m", "celery", "-A", "src.celery_app", "worker", "--loglevel=INFO", *solo], BACKEND),
        Service("celery-beat", [py, "-m", "celery", "-A", "src.celery_app", "beat", "--loglevel=INFO"], BACKEND),
        Service("frontend", ["npm", "run", "dev"], FRONTEND, 3000),
    ]


DEPENDENCIES = [("PostgreSQL/PgBouncer", "POSTGRES", True), ("Redis", "REDIS", True), ("Kafka", "KAFKA", True), ("Qdrant", "QDRANT", False)]


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def who_holds(port: int) -> str:
    """Best-effort description of the process listening on `port` (read-only)."""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10).stdout
            for line in out.splitlines():
                if re.search(rf":{port}\s+\S+\s+LISTENING", line):
                    pid = line.split()[-1]
                    return f"PID {pid} (find it with: tasklist /FI \"PID eq {pid}\"; stop it with: taskkill /PID {pid} /F)"
        elif shutil.which("ss"):
            out = subprocess.run(["ss", "-ltnp", f"sport = :{port}"], capture_output=True, text=True, timeout=10).stdout
            tail = [ln for ln in out.splitlines()[1:] if ln.strip()]
            if tail:
                return tail[0].strip()
    except Exception:  # noqa: BLE001
        pass
    return "unknown process"


def check_ports(selected: list[Service], probe=port_in_use, holder=who_holds) -> list[str]:
    problems = []
    for svc in selected:
        if svc.port and probe(svc.port):
            problems.append(f"port {svc.port} (needed by {svc.name}) is already in use by {holder(svc.port)}")
    return problems


def dependency_endpoints(env: dict[str, str]) -> list[tuple[str, str, int, bool]]:
    def hp(value: str, default_port: int) -> tuple[str, int]:
        host, _, port = value.partition(":")
        return host or "127.0.0.1", int(port or default_port)

    pg_host, pg_port = env.get("PGBOUNCER_HOST") or env.get("POSTGRES_HOST") or "localhost", int(env.get("PGBOUNCER_PORT") or env.get("POSTGRES_PORT") or 6432)
    redis_host, redis_port = env.get("REDIS_HOST", "localhost"), int(env.get("REDIS_PORT", 6379))
    kafka_host, kafka_port = hp(env.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092").split(",")[0], 9092)
    qdrant_host, qdrant_port = env.get("QDRANT_HOST", "localhost"), int(env.get("QDRANT_PORT", 6333))
    eps = {"POSTGRES": (pg_host, pg_port), "REDIS": (redis_host, redis_port), "KAFKA": (kafka_host, kafka_port), "QDRANT": (qdrant_host, qdrant_port)}
    return [(label, *eps[key], required) for label, key, required in DEPENDENCIES]


def check_dependencies(env: dict[str, str], probe=lambda h, p: port_in_use(p, h)) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    for label, host, port, required in dependency_endpoints(env):
        if not probe(host, port):
            (errors if required else warnings).append(f"{label} is not reachable at {host}:{port}")
    return errors, warnings


def ngrok_public_url(fetch=None) -> str | None:
    """https URL of the first live ngrok tunnel, from ngrok's local API. None if ngrok is not running."""
    fetch = fetch or (lambda: json.load(urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3)))
    try:
        tunnels = fetch().get("tunnels", [])
    except Exception:  # noqa: BLE001
        return None
    https = [t["public_url"] for t in tunnels if str(t.get("public_url", "")).startswith("https://")]
    return https[0] if https else None


def origin(url: str) -> str:
    u = urlsplit(url.strip())
    return f"{u.scheme}://{u.netloc}".lower() if u.scheme and u.netloc else ""


def compare_ngrok(tunnel: str | None, public_base_url: str | None, meta_callback_urls: list[str] | None = None) -> list[str]:
    problems = []
    if tunnel is None:
        return ["ngrok is not running (nothing on http://127.0.0.1:4040) — inbound webhooks from Meta cannot reach this machine"]
    if public_base_url and origin(public_base_url) != origin(tunnel):
        problems.append(f"PUBLIC_BASE_URL is {origin(public_base_url)} but the live tunnel is {origin(tunnel)} — update .env")
    if meta_callback_urls is not None:
        wanted = origin(tunnel)
        if not any(origin(u) == wanted for u in meta_callback_urls):
            stored = ", ".join(sorted({origin(u) for u in meta_callback_urls})) or "none"
            problems.append(f"the webhook URL stored in the Meta app ({stored}) is NOT the live tunnel ({wanted}). In the Meta dashboard set the callback to {wanted}{WEBHOOK_PATH}")
    return problems


def meta_callback_urls(app_id: str, app_secret: str, version: str = "v21.0", fetch=None) -> list[str]:
    """READ-ONLY: GET /{app-id}/subscriptions with an app access token → the callback URLs configured in the Meta app."""
    fetch = fetch or (lambda url: json.load(urllib.request.urlopen(url, timeout=10)))
    data = fetch(f"https://graph.facebook.com/{version}/{app_id}/subscriptions?access_token={app_id}|{app_secret}")
    return [s["callback_url"] for s in data.get("data", []) if s.get("callback_url")]


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    f = BACKEND / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                env.setdefault(m.group(1), m.group(2).split("  #")[0].strip().strip('"').strip("'"))
    return env


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma-separated service names")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--ngrok", action="store_true", help="also start ngrok on port 8000 and verify the URL")
    ap.add_argument("--check-meta", action="store_true", help="compare the tunnel with the callback URL stored in the Meta app (read-only Graph call)")
    args = ap.parse_args(argv)

    env = load_env()
    chosen = [s for s in services() if not args.only or s.name in args.only.split(",")]
    if not chosen:
        print("no matching service; choose from: " + ", ".join(s.name for s in services()), file=sys.stderr)
        return 2

    problems = check_ports(chosen)
    errors, warnings = check_dependencies(env)
    for w in warnings:
        print(f"[warn] {w}")
    for p in problems + errors:
        print(f"[FAIL] {p}")
    if problems or errors:
        print("Nothing was started. Free the port / start the dependency (e.g. docker compose up -d) and run again.")
        return 1

    procs: list[subprocess.Popen] = []
    try:
        if args.ngrok and shutil.which("ngrok"):
            procs.append(subprocess.Popen(["ngrok", "http", "8000"]))
            time.sleep(3)
        if args.ngrok or args.check_meta:
            urls = None
            if args.check_meta:
                if env.get("META_APP_ID") and env.get("META_APP_SECRET"):
                    try:
                        urls = meta_callback_urls(env["META_APP_ID"], env["META_APP_SECRET"], env.get("META_GRAPH_API_VERSION", "v21.0"))
                    except Exception as exc:  # noqa: BLE001
                        print(f"[warn] could not read the Meta app's stored callback URL ({type(exc).__name__})")
                else:
                    print("[warn] --check-meta needs META_APP_ID and META_APP_SECRET")
            for p in compare_ngrok(ngrok_public_url(), env.get("PUBLIC_BASE_URL"), urls):
                print(f"[FAIL] {p}")
        else:
            tunnel = ngrok_public_url()
            if tunnel:
                for p in compare_ngrok(tunnel, env.get("PUBLIC_BASE_URL")):
                    print(f"[warn] {p}")
                print(f"[info] ngrok tunnel: {tunnel}  → webhook URL to configure in Meta: {tunnel}{WEBHOOK_PATH}")
        if args.check_only:
            print("[ok] checks passed")
            return 0
        for svc in chosen:
            exe = shutil.which(svc.cmd[0]) or svc.cmd[0]
            print(f"[start] {svc.name}: {' '.join(svc.cmd)}")
            procs.append(subprocess.Popen([exe, *svc.cmd[1:]], cwd=svc.cwd, env={**os.environ, "PYTHONUTF8": "1"}))
            time.sleep(1)
        print("[ok] running — Ctrl+C to stop everything")
        while all(p.poll() is None for p in procs):
            time.sleep(2)
        print("[warn] a process exited; stopping the rest")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    sys.exit(main())
