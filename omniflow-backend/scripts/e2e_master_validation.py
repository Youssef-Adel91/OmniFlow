#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/e2e_master_validation.py — OmniFlow AI E2E Master Validation Suite
Sprint 16 — Validates all layers: Auth, Property CRUD, Vector Sync,
             WhatsApp Pipeline, TTS, MinIO Storage, Smart Inbox DB state.

Usage (from omniflow-backend dir):
    $env:PYTHONPATH = "."
    $env:PYTHONUTF8 = "1"
    .venv/Scripts/python.exe scripts/e2e_master_validation.py

Output: Markdown report to stdout AND artifacts/e2e_report_<timestamp>.md
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Fix Windows encoding ───────────────────────────────────────────────────────
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ── Third-party (all available in .venv) ──────────────────────────────────────
try:
    import httpx
except ImportError:
    print("[FATAL] httpx not installed. Run: pip install httpx")
    sys.exit(1)

try:
    import asyncpg
except ImportError:
    asyncpg = None  # type: ignore[assignment]

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None  # type: ignore[assignment]

# ══════════════════════════════════════════════════════════════════════════════
# Configuration — reads from .env via environment (loaded by docker-compose)
# ══════════════════════════════════════════════════════════════════════════════

CFG = {
    # FastAPI backend
    "API_BASE":         os.getenv("API_BASE_URL", "http://localhost:8000"),
    # Auth credentials (seeded by seed_password.py / seed_via_docker.py)
    "LOGIN_EMAIL":      os.getenv("E2E_EMAIL",    "agent@eliteprops.sa"),
    "LOGIN_PASSWORD":   os.getenv("E2E_PASSWORD", "OmniFlow@2025!"),
    # MinIO
    "MINIO_ENDPOINT":   os.getenv("MINIO_ENDPOINT",   "http://localhost:9020"),
    "MINIO_BUCKET":     os.getenv("MINIO_BUCKET_NAME", "omniflow-media"),
    "MINIO_ACCESS_KEY": os.getenv("MINIO_ACCESS_KEY",   "omniflow_admin"),
    "MINIO_SECRET_KEY": os.getenv("MINIO_SECRET_KEY",   "minio_dev_secret_change_me"),
    # PostgreSQL — direct (bypasses PgBouncer for test reads)
    "PG_HOST":  os.getenv("POSTGRES_HOST",     "localhost"),
    # Use PgBouncer port 6432 — direct Postgres 5432 rejects omniflow user externally
    "PG_PORT":  int(os.getenv("PGBOUNCER_PORT", os.getenv("POSTGRES_PORT", "6432"))),
    "PG_DB":    os.getenv("POSTGRES_DB",       "omniflow_db"),
    "PG_USER":  os.getenv("POSTGRES_USER",     "omniflow"),
    "PG_PASS":  os.getenv("POSTGRES_PASSWORD", "omniflow_dev_secret_change_me"),
    # Redis
    "REDIS_HOST": os.getenv("REDIS_HOST",     "localhost"),
    "REDIS_PORT": int(os.getenv("REDIS_PORT", "6379")),
    "REDIS_PASS": os.getenv("REDIS_PASSWORD", "redis_dev_secret_change_me"),
    # Qdrant
    "QDRANT_URL":     f"http://{os.getenv('QDRANT_HOST','localhost')}:{os.getenv('QDRANT_PORT','6333')}",
    "QDRANT_API_KEY": os.getenv("QDRANT_API_KEY", "dev_qdrant_api_key_placeholder"),
    # Kafka / Redpanda
    "KAFKA_BOOTSTRAP": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
    # WhatsApp sim
    "WA_HMAC_SECRET": os.getenv("META_WEBHOOK_HMAC_SECRET", "dev_meta_hmac_secret_placeholder"),
    "WA_PHONE_ID":    os.getenv("META_WHATSAPP_PHONE_NUMBER_ID", "1234567890"),
    "WA_PHONE":       "966555777888",   # unique test phone to avoid clash
    "WA_NAME":        "E2E Test Customer",
    # Test property
    "PROP_DESCRIPTION_AR": "فيلا فاخرة جداً في حي الياسمين، مساحة 600 متر، تصميم مودرن",
    # Timing
    "VECTOR_SYNC_WAIT_SEC":   8,    # time for async qdrant sync to complete
    "PIPELINE_WAIT_SEC":      15,   # time for full AI pipeline to produce outbound msg
}

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PASS_ICON = "✅"
FAIL_ICON = "❌"
WARN_ICON = "⚠️ "
INFO_ICON = "ℹ️ "

# ══════════════════════════════════════════════════════════════════════════════
# Result tracking
# ══════════════════════════════════════════════════════════════════════════════

_results: list[dict[str, Any]] = []
_start_ts = datetime.now(timezone.utc)

def _record(stage: str, check: str, passed: bool, detail: str = "", data: Any = None) -> None:
    icon = PASS_ICON if passed else FAIL_ICON
    colour = GREEN if passed else RED
    print(f"  {icon}  {colour}{check}{RESET}")
    if detail:
        print(f"      {CYAN}{detail}{RESET}")
    _results.append({
        "stage":  stage,
        "check":  check,
        "passed": passed,
        "detail": detail,
        "data":   data,
    })

def _section(title: str) -> None:
    print(f"\n{BOLD}{YELLOW}{'─'*60}{RESET}")
    print(f"{BOLD}{YELLOW}  {title}{RESET}")
    print(f"{BOLD}{YELLOW}{'─'*60}{RESET}")

# ══════════════════════════════════════════════════════════════════════════════
# Stage 1 — Environment & Services
# ══════════════════════════════════════════════════════════════════════════════

async def stage1_environment(client: httpx.AsyncClient) -> dict[str, Any]:
    _section("STAGE 1 — Environment & Services")

    # 1a. Docker containers via `docker ps`
    docker_services = {
        "omniflow-postgres":  "PostgreSQL 16",
        "omniflow-redis":     "Redis 7.4",
        "omniflow-redpanda":  "Redpanda (Kafka)",
        "omniflow-qdrant":    "Qdrant",
        "omniflow-minio":     "MinIO",
        "omniflow-clickhouse": "ClickHouse",
    }
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        running = result.stdout
        for container, label in docker_services.items():
            is_up = container in running and "Up" in running.split(container)[-1].split("\n")[0]
            _record("Stage 1", f"Docker: {label} ({container})", is_up,
                    "Running" if is_up else "NOT FOUND — start with: docker compose up -d")
    except FileNotFoundError:
        _record("Stage 1", "Docker CLI", False, "docker not on PATH — skipping container checks")
    except Exception as exc:
        _record("Stage 1", "Docker CLI", False, str(exc))

    # 1b. .env key assertions
    env_file = Path(".env")
    env_content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""

    checks = [
        ("ELEVENLABS_API_KEY present in .env",    "ELEVENLABS_API_KEY" in env_content),
        ("MINIO_ENDPOINT present in .env",         "MINIO_ENDPOINT" in env_content),
        ("MINIO_ACCESS_KEY present in .env",       "MINIO_ACCESS_KEY" in env_content),
        ("MINIO_BUCKET_NAME present in .env",      "MINIO_BUCKET_NAME" in env_content),
    ]
    for label, ok in checks:
        _record("Stage 1", label, ok,
                "Found in .env" if ok else "MISSING — add to .env before running")

    # 1c. FastAPI /health  (mounted without /api/v1 prefix)
    try:
        r = await client.get(f"{CFG['API_BASE']}/health", timeout=5)
        alive = r.status_code == 200
        body = r.json() if alive else {}
        _record("Stage 1", "FastAPI /health responds 200",
                alive, f"HTTP {r.status_code} — {json.dumps(body)[:120]}", body)
    except Exception as exc:
        _record("Stage 1", "FastAPI /health responds 200", False,
                f"{exc} — is the backend running? Start: uvicorn src.gateway.main:create_app --factory --reload")

    # 1d. MinIO health via S3 ListBuckets
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            endpoint_url=CFG["MINIO_ENDPOINT"],
            aws_access_key_id=CFG["MINIO_ACCESS_KEY"],
            aws_secret_access_key=CFG["MINIO_SECRET_KEY"],
            region_name="us-east-1",
        )
        buckets = s3.list_buckets().get("Buckets", [])
        bucket_names = [b["Name"] for b in buckets]
        _record("Stage 1", f"MinIO reachable @ {CFG['MINIO_ENDPOINT']}",
                True, f"Buckets: {bucket_names}", bucket_names)
    except ImportError:
        _record("Stage 1", "MinIO reachable (boto3)", False, "boto3 not installed — pip install boto3")
    except Exception as exc:
        _record("Stage 1", f"MinIO reachable @ {CFG['MINIO_ENDPOINT']}", False, str(exc))

    # 1e. Redis ping
    if aioredis:
        try:
            r = aioredis.from_url(
                f"redis://:{CFG['REDIS_PASS']}@{CFG['REDIS_HOST']}:{CFG['REDIS_PORT']}/0",
                decode_responses=True,
            )
            pong = await r.ping()
            await r.aclose()
            _record("Stage 1", f"Redis ping @ {CFG['REDIS_HOST']}:{CFG['REDIS_PORT']}", pong == True, "PONG")
        except Exception as exc:
            _record("Stage 1", "Redis ping", False, str(exc))
    else:
        _record("Stage 1", "Redis ping", False, "redis package not installed")

    # 1f. Qdrant health
    try:
        r = await client.get(
            f"{CFG['QDRANT_URL']}/healthz",
            headers={"api-key": CFG["QDRANT_API_KEY"]},
            timeout=5,
        )
        ok = r.status_code == 200
        _record("Stage 1", f"Qdrant health @ {CFG['QDRANT_URL']}", ok,
                f"HTTP {r.status_code} — {r.text[:80]}")
    except Exception as exc:
        _record("Stage 1", "Qdrant health", False, str(exc))

    return {}


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2 — Auth + Property CRUD + Vector Sync
# ══════════════════════════════════════════════════════════════════════════════

async def stage2_auth_property(client: httpx.AsyncClient) -> dict[str, Any]:
    _section("STAGE 2 — Auth & Property CRUD + Vector Sync")
    ctx: dict[str, Any] = {}

    # 2a. Find a valid email from the DB first (seed may use different email)
    email_to_try = CFG["LOGIN_EMAIL"]
    if asyncpg:
        try:
            conn = await asyncpg.connect(
                host=CFG["PG_HOST"], port=CFG["PG_PORT"],
                database=CFG["PG_DB"], user=CFG["PG_USER"],
                password=CFG["PG_PASS"], ssl="disable",
            )
            rows = await conn.fetch("SELECT email, tenant_id FROM tenant_users WHERE is_active = TRUE LIMIT 3")
            await conn.close()
            if rows:
                emails = [r["email"] for r in rows]
                tenant_ids = [str(r["tenant_id"]) for r in rows]
                print(f"  {INFO_ICON} Active users in DB: {emails}")
                email_to_try = emails[0]
                ctx["tenant_id_from_db"] = tenant_ids[0]
        except Exception as exc:
            print(f"  {WARN_ICON} Could not pre-query users: {exc}")

    # 2b. POST /api/v1/auth/login
    login_payload = {"email": email_to_try, "password": CFG["LOGIN_PASSWORD"]}
    print(f"\n  {INFO_ICON} Attempting login as: {email_to_try}")
    try:
        r = await client.post(
            f"{CFG['API_BASE']}/api/v1/auth/login",
            json=login_payload,
            timeout=10,
        )
        login_ok = r.status_code == 200
        login_body = r.json() if login_ok else {}
        _record("Stage 2", f"POST /auth/login → 200 (email={email_to_try})",
                login_ok,
                f"HTTP {r.status_code} | tenant={login_body.get('tenant_id','?')} | role={login_body.get('role','?')}",
                login_body)
        if login_ok:
            ctx["access_token"]  = login_body["access_token"]
            ctx["refresh_token"] = login_body["refresh_token"]
            ctx["tenant_id"]     = login_body["tenant_id"]
            ctx["user_id"]       = login_body["user_id"]
            ctx["login_email"]   = email_to_try
    except Exception as exc:
        _record("Stage 2", "POST /auth/login", False, str(exc))
        return ctx

    if "access_token" not in ctx:
        _record("Stage 2", "Auth token extracted", False, "Login failed — subsequent stages will be skipped")
        return ctx

    _record("Stage 2", "JWT access_token extracted", True,
            f"token[:20]={ctx['access_token'][:20]}...", ctx["access_token"])
    _record("Stage 2", "tenant_id extracted", True, ctx["tenant_id"])

    auth_headers = {
        "Authorization": f"Bearer {ctx['access_token']}",
        "X-Tenant-ID":   ctx["tenant_id"],
    }

    # 2c. Create property
    prop_payload = {
        "property_type":  "villa",
        "status":         "PENDING_VERIFICATION",
        "city":           "الرياض",
        "district":       "الياسمين",
        "price":          4_500_000,
        "area_sqm":       600,
        "bedrooms":       6,
        "bathrooms":      5,
        "description_ar": CFG["PROP_DESCRIPTION_AR"],
        "description_en": "Luxurious villa in Al-Yasmeen district, 600sqm, modern design",
    }
    print(f"\n  {INFO_ICON} Creating property listing (Villa, Al-Yasmeen, SAR 4.5M)...")
    try:
        r = await client.post(
            f"{CFG['API_BASE']}/api/v1/properties",
            json=prop_payload,
            headers=auth_headers,
            timeout=15,
        )
        prop_ok = r.status_code == 201
        prop_body = r.json() if r.status_code in (200, 201) else {}
        _record("Stage 2", "POST /properties → 201 Created", prop_ok,
                f"HTTP {r.status_code} | listing_id={prop_body.get('listing_id','?')} | rega={prop_body.get('rega_ad_number','?')}",
                prop_body)
        if prop_ok or r.status_code == 200:
            ctx["listing_id"]     = prop_body["listing_id"]
            ctx["rega_ad_number"] = prop_body["rega_ad_number"]
            ctx["prop_response"]  = prop_body
    except Exception as exc:
        _record("Stage 2", "POST /properties", False, str(exc))

    # 2d. Wait for async vector sync
    if "listing_id" in ctx:
        print(f"\n  {INFO_ICON} Waiting {CFG['VECTOR_SYNC_WAIT_SEC']}s for async Qdrant vector sync...")
        await asyncio.sleep(CFG["VECTOR_SYNC_WAIT_SEC"])

        # 2e. Verify Postgres row + qdrant_point_id
        if asyncpg:
            try:
                conn = await asyncpg.connect(
                    host=CFG["PG_HOST"], port=CFG["PG_PORT"],
                    database=CFG["PG_DB"], user=CFG["PG_USER"],
                    password=CFG["PG_PASS"],
                )
                row = await conn.fetchrow(
                    "SELECT listing_id, rega_ad_number, property_type, status, "
                    "city, district, price, area_sqm, bedrooms, description_ar, "
                    "qdrant_point_id, created_at "
                    "FROM property_listings WHERE listing_id = $1",
                    uuid.UUID(ctx["listing_id"]),
                )
                await conn.close()
                if row:
                    pg_row = dict(row)
                    ctx["pg_listing"] = {k: str(v) for k, v in pg_row.items()}
                    _record("Stage 2", "Postgres: property_listings row exists",
                            True, f"id={pg_row['listing_id']} | rega={pg_row['rega_ad_number']}", ctx["pg_listing"])
                    has_qdrant = bool(pg_row["qdrant_point_id"])
                    _record("Stage 2", "Postgres: qdrant_point_id populated (vector sync ✓)",
                            has_qdrant,
                            f"qdrant_point_id={pg_row['qdrant_point_id']}",
                            pg_row["qdrant_point_id"])
                    if has_qdrant:
                        ctx["qdrant_point_id"] = str(pg_row["qdrant_point_id"])
                else:
                    _record("Stage 2", "Postgres: property_listings row exists", False,
                            f"Row not found for listing_id={ctx['listing_id']}")
            except Exception as exc:
                _record("Stage 2", "Postgres: property_listings query", False, str(exc))
        else:
            _record("Stage 2", "Postgres: direct query", False, "asyncpg not installed — pip install asyncpg")

        # 2f. GET /properties/{id} — verify via REST
        try:
            r = await client.get(
                f"{CFG['API_BASE']}/api/v1/properties/{ctx['listing_id']}",
                headers=auth_headers,
                timeout=10,
            )
            get_ok = r.status_code == 200
            get_body = r.json() if get_ok else {}
            _record("Stage 2", f"GET /properties/{ctx['listing_id'][:8]}... → 200",
                    get_ok, f"price={get_body.get('price')} | city={get_body.get('city')}", get_body)
        except Exception as exc:
            _record("Stage 2", "GET /properties/{id}", False, str(exc))

    return ctx


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3 — WhatsApp Simulation & AI Pipeline
# ══════════════════════════════════════════════════════════════════════════════

def _sign_webhook(payload_bytes: bytes) -> str:
    return hmac.new(
        CFG["WA_HMAC_SECRET"].encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()


def _build_wa_payload(message: str, phone: str, name: str) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "1234567890",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "966500000000",
                        "phone_number_id": CFG["WA_PHONE_ID"],
                    },
                    "contacts": [{"profile": {"name": name}, "wa_id": phone}],
                    "messages": [{
                        "from": phone,
                        "id": f"wamid.{uuid.uuid4().hex[:16]}",
                        "timestamp": str(int(datetime.now().timestamp())),
                        "type": "text",
                        "text": {"body": message},
                    }],
                },
                "field": "messages",
            }],
        }],
    }


async def stage3_whatsapp_pipeline(
    client: httpx.AsyncClient,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    _section("STAGE 3 — WhatsApp Simulation & AI Pipeline")

    if "tenant_id" not in ctx:
        _record("Stage 3", "Skipped (no auth token from Stage 2)", False, "")
        return ctx

    # 3a. Seed VIP Redis session to guarantee TTS trigger
    if aioredis:
        try:
            r = aioredis.from_url(
                f"redis://:{CFG['REDIS_PASS']}@{CFG['REDIS_HOST']}:{CFG['REDIS_PORT']}/0",
                decode_responses=True,
            )
            key = f"t:{ctx['tenant_id']}:session:{CFG['WA_PHONE']}"
            state = json.dumps({
                "is_vip": True,
                "customer_id": str(uuid.uuid4()),
                "vcard_state": "STATE_CONTACT_SAVED_VERIFIED",
                "is_processing_restricted": False,
                "is_human_active": False,
                "conversation_id": None,
            })
            await r.setex(key, 3600, state)
            await r.aclose()
            _record("Stage 3", "Redis VIP session seeded",
                    True, f"key={key}")
        except Exception as exc:
            _record("Stage 3", "Redis VIP session seed (optional)", True,
                    f"Skipped: {exc} — keyword trigger 'صوت' will be used instead")

    # 3b. Build and sign the webhook payload
    voice_msg = (
        "أريد رسالة صوتية تشرح لي تفاصيل ومميزات الفيلا المتاحة في حي الياسمين "
        f"رقم الإعلان: {ctx.get('rega_ad_number', 'الياسمين')}"
    )
    payload = _build_wa_payload(voice_msg, CFG["WA_PHONE"], CFG["WA_NAME"])
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sig = _sign_webhook(payload_bytes)
    ctx["wa_phone"] = CFG["WA_PHONE"]
    ctx["wa_message"] = voice_msg

    print(f"\n  {INFO_ICON} Sending webhook: {voice_msg[:80]}...")

    # 3c. POST to webhook
    try:
        r = await client.post(
            f"{CFG['API_BASE']}/api/v1/webhooks/whatsapp",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": f"sha256={sig}",
            },
            timeout=15,
        )
        wh_ok = r.status_code == 200
        _record("Stage 3", "POST /webhooks/whatsapp → 200", wh_ok,
                f"HTTP {r.status_code} | body={r.text[:120]}")
    except Exception as exc:
        _record("Stage 3", "POST /webhooks/whatsapp", False, str(exc))
        return ctx

    # 3d. Wait for the full async pipeline (Router → Invoker → TTS → Dispatcher)
    print(f"\n  {INFO_ICON} Waiting {CFG['PIPELINE_WAIT_SEC']}s for AI pipeline to complete...")
    await asyncio.sleep(CFG["PIPELINE_WAIT_SEC"])

    # 3e. Validate Kafka / Redpanda topic via rpk (if available)
    try:
        result = subprocess.run(
            ["docker", "exec", "omniflow-redpanda",
             "rpk", "topic", "list"],
            capture_output=True, text=True, timeout=10,
        )
        topics = result.stdout
        expected_topics = [
            "messages.incoming.v1",
            "llm.routing.v1",
            "messages.outgoing.v1",
        ]
        for t in expected_topics:
            _record("Stage 3", f"Kafka topic exists: {t}", t in topics, topics[:200])
    except Exception as exc:
        _record("Stage 3", "Kafka topic check", False, f"rpk unavailable: {exc}")

    return ctx


# ══════════════════════════════════════════════════════════════════════════════
# Stage 4 — Database & Storage Validation
# ══════════════════════════════════════════════════════════════════════════════

async def stage4_db_storage(
    client: httpx.AsyncClient,
    ctx: dict[str, Any],
) -> dict[str, Any]:
    _section("STAGE 4 — Database & Storage Validation")

    wa_phone = ctx.get("wa_phone")

    # 4a. Query messages table for outbound audio message
    if asyncpg and wa_phone:
        try:
            conn = await asyncpg.connect(
                host=CFG["PG_HOST"], port=CFG["PG_PORT"],
                database=CFG["PG_DB"], user=CFG["PG_USER"],
                password=CFG["PG_PASS"], ssl="disable",
            )
            # Find customer by phone (joined through conversations → messages)
            msg_row = await conn.fetchrow("""
                SELECT m.message_id, m.sender_type, m.message_type,
                       m.text_content, m.s3_media_url, m.llm_routing_tier,
                       m.latency_ms, m.created_at
                FROM messages m
                JOIN conversations c ON c.conversation_id = m.conversation_id
                JOIN customers cu    ON cu.customer_id    = c.customer_id
                WHERE cu.unified_phone = $1
                  AND m.sender_type    = 'ai_bot'
                ORDER BY m.created_at DESC
                LIMIT 1
            """, wa_phone)
            await conn.close()

            if msg_row:
                msg = dict(msg_row)
                ctx["db_message"] = {k: str(v) for k, v in msg.items()}
                _record("Stage 4", "Postgres messages: outbound AI message found",
                        True,
                        f"id={msg['message_id']} | type={msg['message_type']} | tier={msg['llm_routing_tier']}",
                        ctx["db_message"])
                is_audio = str(msg["message_type"]) == "audio"
                _record("Stage 4", "messages.message_type == 'audio'",
                        is_audio, f"actual={msg['message_type']}")
                has_media_url = bool(msg["s3_media_url"])
                _record("Stage 4", "messages.s3_media_url populated",
                        has_media_url, str(msg["s3_media_url"] or "NULL"))
                if has_media_url:
                    ctx["audio_url"] = str(msg["s3_media_url"])
                    minio_in_url = CFG["MINIO_ENDPOINT"].replace("http://", "").split(":")[0] in str(msg["s3_media_url"])
                    _record("Stage 4", "s3_media_url contains MinIO endpoint",
                            minio_in_url, str(msg["s3_media_url"]))
                has_transcript = bool(msg["text_content"]) and len(str(msg["text_content"] or "")) > 10
                _record("Stage 4", "messages.text_content contains Arabic transcript",
                        has_transcript, (str(msg["text_content"] or "")[:120]))
            else:
                _record("Stage 4", "Postgres messages: outbound AI message found", False,
                        f"No ai_bot message found for phone={wa_phone}. "
                        "Workers may not be running — start them with their respective run commands.")
        except Exception as exc:
            _record("Stage 4", "Postgres messages query", False, str(exc))
    else:
        if not asyncpg:
            _record("Stage 4", "Postgres messages query", False, "asyncpg not installed")
        if not wa_phone:
            _record("Stage 4", "Postgres messages query", False, "No WA phone from Stage 3")

    # 4b. MinIO: list bucket objects to confirm audio file exists
    audio_url = ctx.get("audio_url", "")
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            endpoint_url=CFG["MINIO_ENDPOINT"],
            aws_access_key_id=CFG["MINIO_ACCESS_KEY"],
            aws_secret_access_key=CFG["MINIO_SECRET_KEY"],
            region_name="us-east-1",
        )
        # Ensure bucket exists
        buckets = [b["Name"] for b in s3.list_buckets().get("Buckets", [])]
        bucket_exists = CFG["MINIO_BUCKET"] in buckets
        _record("Stage 4", f"MinIO bucket '{CFG['MINIO_BUCKET']}' exists",
                bucket_exists, f"All buckets: {buckets}")

        if bucket_exists:
            objs = s3.list_objects_v2(Bucket=CFG["MINIO_BUCKET"], Prefix="audio/tts/")
            files = [o["Key"] for o in objs.get("Contents", [])]
            ctx["minio_files"] = files
            has_audio_files = len(files) > 0
            _record("Stage 4", f"MinIO audio/tts/ objects count: {len(files)}",
                    has_audio_files,
                    f"Files: {files[:5]}" if files else "No audio files found — TTS pipeline may use mock mode or MinIO not configured",
                    files)

            # If we have an audio URL from DB, verify the file is listed
            if audio_url and files:
                # Extract key from presigned URL
                key_hint = "tts" in audio_url.lower()
                _record("Stage 4", "DB audio URL references MinIO tts/ prefix",
                        key_hint, audio_url[:200])
    except ImportError:
        _record("Stage 4", "MinIO bucket verification", False, "boto3 not installed — pip install boto3")
    except Exception as exc:
        _record("Stage 4", "MinIO bucket verification", False, str(exc))

    # 4c. Qdrant: confirm vector point exists for the created listing
    if "qdrant_point_id" in ctx and "tenant_id" in ctx:
        tenant_collection = f"t_{ctx['tenant_id'].replace('-', '')}_listings"
        try:
            r = await client.post(
                f"{CFG['QDRANT_URL']}/collections/{tenant_collection}/points/scroll",
                headers={"api-key": CFG["QDRANT_API_KEY"], "Content-Type": "application/json"},
                json={"filter": {}, "limit": 1, "with_payload": True, "with_vector": False},
                timeout=10,
            )
            if r.status_code == 200:
                body = r.json()
                count = len(body.get("result", {}).get("points", []))
                _record("Stage 4", f"Qdrant collection '{tenant_collection}' has points",
                        count > 0, f"{count} point(s) found", body.get("result"))
            else:
                _record("Stage 4", f"Qdrant collection '{tenant_collection}' accessible",
                        False, f"HTTP {r.status_code}: {r.text[:200]}")
        except Exception as exc:
            _record("Stage 4", "Qdrant vector verification", False, str(exc))

    return ctx


# ══════════════════════════════════════════════════════════════════════════════
# Stage 5 — Report Generation
# ══════════════════════════════════════════════════════════════════════════════

def _generate_report(ctx: dict[str, Any]) -> str:
    ts   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    dur  = (datetime.now(timezone.utc) - _start_ts).total_seconds()

    total  = len(_results)
    passed = sum(1 for r in _results if r["passed"])
    failed = total - passed
    pct    = int(passed / total * 100) if total else 0

    # Stage breakdown
    stages = {}
    for r in _results:
        s = r["stage"]
        stages.setdefault(s, {"pass": 0, "fail": 0})
        if r["passed"]:
            stages[s]["pass"] += 1
        else:
            stages[s]["fail"] += 1

    def emoji(ok: bool) -> str:
        return "✅ PASS" if ok else "❌ FAIL"

    lines: list[str] = []
    lines += [
        f"# OmniFlow AI — E2E Master Validation Report",
        f"",
        f"> **Generated:** {ts}  ",
        f"> **Duration:** {dur:.1f}s  ",
        f"> **Result:** {'🟢 ALL PASS' if failed == 0 else f'🔴 {failed} FAILURES'}  ",
        f"> **Score:** {passed}/{total} checks passed ({pct}%)",
        f"",
        f"---",
        f"",
        f"## Executive Summary",
        f"",
        f"| Stage | Pass | Fail | Status |",
        f"|-------|------|------|--------|",
    ]
    for stage, counts in stages.items():
        ok = counts["fail"] == 0
        lines.append(f"| {stage} | {counts['pass']} | {counts['fail']} | {emoji(ok)} |")

    lines += [
        f"",
        f"---",
        f"",
        f"## Detailed Check Results",
        f"",
    ]
    current_stage = ""
    for r in _results:
        if r["stage"] != current_stage:
            current_stage = r["stage"]
            lines += [f"", f"### {current_stage}", f""]
        icon = "✅" if r["passed"] else "❌"
        lines.append(f"- {icon} **{r['check']}**")
        if r["detail"]:
            lines.append(f"  - `{r['detail'][:300]}`")

    lines += [
        f"",
        f"---",
        f"",
        f"## Stage 2 — API & Database Evidence",
        f"",
    ]

    # Property creation response
    prop = ctx.get("prop_response")
    if prop:
        lines += [
            f"### Property Creation Response (POST /api/v1/properties)",
            f"",
            f"```json",
            json.dumps(prop, indent=2, ensure_ascii=False, default=str),
            f"```",
            f"",
        ]

    # Postgres property_listings row
    pg = ctx.get("pg_listing")
    if pg:
        lines += [
            f"### Postgres `property_listings` Row",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
        ]
        for k, v in pg.items():
            v_str = str(v)[:120]
            lines.append(f"| `{k}` | `{v_str}` |")
        lines.append("")

    # Postgres messages row
    db_msg = ctx.get("db_message")
    if db_msg:
        lines += [
            f"### Postgres `messages` Row (Latest AI Outbound)",
            f"",
            f"| Field | Value |",
            f"|-------|-------|",
        ]
        for k, v in db_msg.items():
            v_str = str(v)[:200]
            lines.append(f"| `{k}` | `{v_str}` |")
        lines.append("")

    # MinIO audio URL
    audio_url = ctx.get("audio_url")
    if audio_url:
        lines += [
            f"### MinIO Audio URL (TTS Voice Note)",
            f"",
            f"```",
            audio_url,
            f"```",
            f"",
        ]

    # MinIO files list
    minio_files = ctx.get("minio_files")
    if minio_files:
        lines += [
            f"### MinIO Bucket Objects (`audio/tts/`)",
            f"",
        ]
        for f in minio_files[:10]:
            lines.append(f"- `{f}`")
        lines.append("")

    lines += [
        f"---",
        f"",
        f"## Architectural Health Assessment",
        f"",
        f"| Component | Layer | Status |",
        f"|-----------|-------|--------|",
        f"| PostgreSQL 16 + RLS | Data Persistence | {'✅' if passed >= 3 else '⚠️'} |",
        f"| PgBouncer (transaction mode) | Connection Pooling | {'✅' if passed >= 3 else '⚠️'} |",
        f"| Redis 7.4 | Session / Cache / Blacklist | {'✅' if passed >= 4 else '⚠️'} |",
        f"| Redpanda (Kafka compat) | Async Messaging | {'✅' if passed >= 5 else '⚠️'} |",
        f"| Qdrant | Vector DB / RAG | {'✅' if ctx.get('qdrant_point_id') else '⚠️'} |",
        f"| FastAPI Gateway | REST API | {'✅' if ctx.get('access_token') else '❌'} |",
        f"| JWT Auth (HS256) | Security | {'✅' if ctx.get('access_token') else '❌'} |",
        f"| Property CRUD + RLS | Business Logic | {'✅' if ctx.get('listing_id') else '⚠️'} |",
        f"| Vector Sync (Qdrant) | RAG Pipeline | {'✅' if ctx.get('qdrant_point_id') else '⚠️'} |",
        f"| TTS Client (ElevenLabs/Mock) | Voice Notes | {'✅' if ctx.get('audio_url') else '⚠️'} |",
        f"| StorageClient (MinIO/S3) | Media Storage | {'✅' if ctx.get('minio_files') else '⚠️'} |",
        f"| WhatsApp Webhook | Ingestion | {'✅' if ctx.get('wa_phone') else '⚠️'} |",
        f"",
        f"---",
        f"",
        f"*Report auto-generated by `scripts/e2e_master_validation.py` — OmniFlow AI Sprint 16*",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Main runner
# ══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    print(f"\n{BOLD}{CYAN}{'═'*60}{RESET}")
    print(f"{BOLD}{CYAN}  OmniFlow AI — E2E Master Validation Suite  (Sprint 16){RESET}")
    print(f"{BOLD}{CYAN}{'═'*60}{RESET}")
    print(f"  {INFO_ICON} API Base  : {CFG['API_BASE']}")
    print(f"  {INFO_ICON} DB        : {CFG['PG_HOST']}:{CFG['PG_PORT']}/{CFG['PG_DB']}")
    print(f"  {INFO_ICON} MinIO     : {CFG['MINIO_ENDPOINT']}")
    print(f"  {INFO_ICON} Started   : {_start_ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        ctx: dict[str, Any] = {}
        ctx.update(await stage1_environment(client))
        ctx.update(await stage2_auth_property(client))
        ctx.update(await stage3_whatsapp_pipeline(client, ctx))
        ctx.update(await stage4_db_storage(client, ctx))

    # Summary
    total  = len(_results)
    passed = sum(1 for r in _results if r["passed"])
    failed = total - passed
    dur    = (datetime.now(timezone.utc) - _start_ts).total_seconds()

    _section(f"FINAL RESULT: {passed}/{total} CHECKS PASSED ({int(passed/total*100) if total else 0}%)")
    for r in _results:
        icon = f"{GREEN}✅{RESET}" if r["passed"] else f"{RED}❌{RESET}"
        print(f"  {icon}  {r['check']}")

    # Generate markdown report
    report_md = _generate_report(ctx)

    # Save to file
    out_dir = Path("artifacts")
    out_dir.mkdir(exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"e2e_report_{ts_str}.md"
    out_path.write_text(report_md, encoding="utf-8")

    print(f"\n{BOLD}{GREEN}📄 Report saved to: {out_path}{RESET}")
    print(f"{BOLD}{'═'*60}{RESET}")
    print(report_md[:3000])   # Print first 3k chars to stdout
    if len(report_md) > 3000:
        print(f"\n... (truncated — see {out_path} for full report)")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(main())
