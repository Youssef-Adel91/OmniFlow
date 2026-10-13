"""Light load test: signed WhatsApp webhooks → the gateway (Locust).

    pip install locust            # not a project dependency
    export META_HMAC_SECRET=...   # the SAME value the API under test uses (META_WEBHOOK_HMAC_SECRET); never commit it
    export WA_PHONE_NUMBER_ID=... # the phone_number_id of a TEST tenant on the target stack
    locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 --headless -u 30 -r 5 -t 5m --csv loadtest/out

Target ONLY a local / staging stack with a test tenant — never production (every request creates a customer, a conversation and an AI reply).
What this measures is the INGESTION leg: signature check → parse → persist → Kafka. The reply leg (router → LLM → outbound) is asynchronous;
measure it from the database afterwards with loadtest/reply_latency.sql. Goals (initial, to be revised after the first real run) are in
loadtest/README.md.
"""
import hashlib
import hmac
import json
import os
import random
import time
import uuid

from locust import HttpUser, between, events, task

SECRET = os.environ.get("META_HMAC_SECRET", "")
PHONE_NUMBER_ID = os.environ.get("WA_PHONE_NUMBER_ID", "")
QUESTIONS = ["السلام عليكم، عندكم شقق للإيجار في الرياض؟", "كم سعر الفيلا في حي الملقا؟", "أبغى أكلم موظف", "هل في عروض هذا الشهر؟", "ما مساحة الشقة المعروضة؟"]


@events.test_start.add_listener
def _check(environment, **_):
    if not SECRET or not PHONE_NUMBER_ID:
        raise SystemExit("set META_HMAC_SECRET and WA_PHONE_NUMBER_ID (test tenant) before running")
    if "127.0.0.1" not in (environment.host or "") and "localhost" not in (environment.host or "") and not os.environ.get("LOADTEST_ALLOW_REMOTE"):
        raise SystemExit("refusing to run against a non-local host without LOADTEST_ALLOW_REMOTE=1 (never production)")


def payload(phone: str, text: str) -> bytes:
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{"id": "loadtest", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "966500000000", "phone_number_id": PHONE_NUMBER_ID},
            "contacts": [{"profile": {"name": "Load Test"}, "wa_id": phone}],
            "messages": [{"from": phone, "id": f"wamid.LT{uuid.uuid4().hex[:20]}", "timestamp": str(int(time.time())), "type": "text", "text": {"body": text}}],
        }}]}],
    }, ensure_ascii=False).encode("utf-8")


class WebhookSender(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        self.phone = f"96655{random.randint(1000000, 9999999)}"          # a distinct fake customer per virtual user

    @task
    def send_message(self):
        body = payload(self.phone, random.choice(QUESTIONS))
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        with self.client.post("/api/v1/webhooks/whatsapp", data=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
                              name="POST /webhooks/whatsapp", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"HTTP {r.status_code}")
