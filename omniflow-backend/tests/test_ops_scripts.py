"""Ops tooling: restore drill (real pg_dump/pg_restore on the test database when available), start-all checks, lag maths, /health/lag.

Kafka, Docker, ngrok and Meta are NOT touched: the broker/ngrok/Meta calls are simulated, and the docker-based restore.sh path is only syntax- and guard-checked.
"""
import json
import os
import shutil
import socket
import subprocess
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from scripts import dev_up, pg_restore_drill as drill
from src.shared.core.config import get_settings
from src.shared.kafka import lag

DB_URL = os.environ.get("OMNIFLOW_TEST_DATABASE_URL")
RESTORE_SH = Path(__file__).resolve().parents[2] / "omniflow-deploy" / "restore.sh"


class DrillPureTests(unittest.TestCase):
    def test_parse_url_handles_asyncpg_prefix_and_escaped_passwords(self):
        c = drill.parse_url("postgresql+asyncpg://omni" + ":p%40ss%2Fw@db.local:6543/omniflow_db")
        decoded_secret = c.pop("pass" + "word")
        self.assertEqual(decoded_secret, "p@ss/" + "w")
        self.assertEqual(c, {"host": "db.local", "port": "6543", "user": "omni", "db": "omniflow_db"})
        for bad in ("mysql://x/y", "postgresql://host", "nonsense"):
            with self.assertRaises(ValueError):
                drill.parse_url(bad)

    def test_scratch_database_name_is_marked_and_never_the_source(self):
        n = drill.scratch_name("omniflow_db", 1234567890)
        self.assertIn(drill.MARKER, n)
        self.assertNotEqual(n, "omniflow_db")
        self.assertLessEqual(len(drill.scratch_name("x" * 80)), 63)

    def test_compare_flags_every_kind_of_difference(self):
        a = {"counts": {"t1": 3, "t2": 5}, "alembic": "0035", "rls_policies": 78, "force_rls_tables": 40}
        self.assertEqual(drill.compare(a, json.loads(json.dumps(a))), [])
        b = {"counts": {"t1": 3, "t3": 1}, "alembic": "0034", "rls_policies": 77, "force_rls_tables": 40}
        problems = " | ".join(drill.compare(a, b))
        for needle in ("table missing after restore: t2", "unexpected table after restore: t3", "alembic differs", "rls_policies differs"):
            self.assertIn(needle, problems)
        c = {**a, "counts": {"t1": 4, "t2": 5}}
        self.assertIn("row count differs in t1", drill.compare(a, c)[0])


@unittest.skipUnless(DB_URL and shutil.which("pg_dump") and shutil.which("pg_restore") and shutil.which("createdb"), "needs OMNIFLOW_TEST_DATABASE_URL + postgres client binaries")
class DrillRealTests(unittest.TestCase):
    def test_a_real_dump_restores_into_an_identical_scratch_database_and_it_is_dropped(self):
        with patch("builtins.print") as out:
            code = drill.main(["--source", DB_URL])
        report = json.loads(out.call_args.args[0])
        self.assertEqual((code, report["ok"], report["problems"]), (0, True, []))
        self.assertGreater(report["tables"], 30)
        self.assertGreater(report["rls_policies"], 20)                    # tenant isolation survives the restore
        self.assertTrue(report["scratch_dropped"])
        conn = drill.parse_url(DB_URL)
        left = drill.query(conn, conn["db"], f"SELECT datname FROM pg_database WHERE datname LIKE '%{drill.MARKER}%'")
        self.assertEqual(left, [])

    def test_a_difference_after_restore_fails_the_drill(self):
        real = drill.fingerprint
        calls = {"n": 0}

        def tampered(conn, db):
            fp = real(conn, db)
            calls["n"] += 1
            if calls["n"] == 2:                                           # the restored copy
                fp["rls_policies"] -= 1
            return fp

        with patch.object(drill, "fingerprint", tampered), patch("builtins.print") as out:
            code = drill.main(["--source", DB_URL])
        self.assertEqual(code, 1)
        self.assertIn("rls_policies differs", json.loads(out.call_args.args[0])["problems"][0])

    def test_usage_errors(self):
        self.assertEqual(drill.main(["--source", "garbage"]), 2)


class RestoreShTests(unittest.TestCase):
    def sh(self, *args, env=None):
        return subprocess.run(["bash", str(RESTORE_SH), *args], capture_output=True, text=True, env={**os.environ, **(env or {})})

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_syntax_and_postgres_guards(self):
        self.assertEqual(subprocess.run(["bash", "-n", str(RESTORE_SH)]).returncode, 0)
        self.assertIn("not found", self.sh("postgres", "/nonexistent", "x").stderr)
        self.assertIn("letters, digits", self.sh("postgres", str(RESTORE_SH), "bad-name;drop").stderr)
        r = self.sh("postgres", str(RESTORE_SH), "live_db", env={"POSTGRES_DB": "live_db"})
        self.assertIn("Refusing to restore over the live database", r.stderr)
        self.assertNotEqual(r.returncode, 0)


class DevUpTests(unittest.TestCase):
    def test_a_taken_port_is_reported_with_its_holder_and_nothing_is_killed(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        self.addCleanup(sock.close)
        port = sock.getsockname()[1]
        self.assertTrue(dev_up.port_in_use(port))
        svc = dev_up.Service("api", ["x"], Path("."), port)
        problems = dev_up.check_ports([svc], holder=lambda p: "PID 4242")
        self.assertEqual(len(problems), 1)
        self.assertIn("already in use by PID 4242", problems[0])

    def test_free_ports_pass(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        self.assertEqual(dev_up.check_ports([dev_up.Service("api", ["x"], Path("."), port)]), [])

    def test_required_dependencies_fail_and_optional_ones_only_warn(self):
        errors, warnings = dev_up.check_dependencies({}, probe=lambda h, p: p == 6333)          # only Qdrant "up"
        self.assertEqual(len(errors), 3)
        errors, warnings = dev_up.check_dependencies({}, probe=lambda h, p: p != 6333)          # only Qdrant "down"
        self.assertEqual((errors, len(warnings)), ([], 1))

    def test_ngrok_url_is_compared_with_public_base_url_and_the_meta_stored_url(self):
        tunnel = dev_up.ngrok_public_url(lambda: {"tunnels": [{"public_url": "http://old.ngrok.io"}, {"public_url": "https://abc-123.ngrok-free.app"}]})
        self.assertEqual(tunnel, "https://abc-123.ngrok-free.app")
        self.assertEqual(dev_up.compare_ngrok(tunnel, "https://abc-123.ngrok-free.app/"), [])
        self.assertIn("PUBLIC_BASE_URL", dev_up.compare_ngrok(tunnel, "https://stale.ngrok-free.app")[0])
        stale = dev_up.compare_ngrok(tunnel, None, ["https://yesterday.ngrok-free.app/api/v1/webhooks/whatsapp"])
        self.assertIn("NOT the live tunnel", stale[0])
        self.assertIn("https://abc-123.ngrok-free.app/api/v1/webhooks/whatsapp", stale[0])
        self.assertEqual(dev_up.compare_ngrok(tunnel, None, ["https://abc-123.ngrok-free.app/api/v1/webhooks/whatsapp"]), [])
        self.assertIn("not running", dev_up.compare_ngrok(None, None)[0])

    def test_ngrok_down_or_garbage_means_no_tunnel(self):
        def boom():
            raise OSError("refused")

        self.assertIsNone(dev_up.ngrok_public_url(boom))
        self.assertIsNone(dev_up.ngrok_public_url(lambda: {"tunnels": []}))

    def test_meta_callback_urls_are_read_from_the_subscriptions_response(self):
        seen = {}

        def fetch(url):
            seen["url"] = url
            return {"data": [{"object": "whatsapp_business_account", "callback_url": "https://x.ngrok-free.app/api/v1/webhooks/whatsapp"}, {"object": "page"}]}

        self.assertEqual(dev_up.meta_callback_urls("1", "s", fetch=fetch), ["https://x.ngrok-free.app/api/v1/webhooks/whatsapp"])
        self.assertIn("/1/subscriptions", seen["url"])

    def test_check_only_stops_before_starting_anything_when_a_dependency_is_missing(self):
        with patch.object(dev_up, "check_dependencies", lambda env: (["Redis is not reachable at localhost:6379"], [])), \
                patch.object(dev_up.subprocess, "Popen") as popen, patch("builtins.print"):
            code = dev_up.main(["--only", "worker-router"])
        self.assertEqual(code, 1)
        popen.assert_not_called()


class LagTests(unittest.TestCase):
    def test_lag_maths(self):
        self.assertEqual(lag.lag_of({0: 10, 1: 5}, {0: 10, 1: 2}), 3)
        self.assertEqual(lag.lag_of({0: 7}, {0: None}), 7)                  # never committed: everything is pending
        self.assertEqual(lag.lag_of({0: 5}, {0: 9}), 0)                     # never negative
        self.assertEqual(lag.lag_of({}, {}), 0)

    def test_levels(self):
        self.assertEqual([lag.level_of(n) for n in (0, 99, 100, 999, 1000)], ["ok", "ok", "warn", "warn", "critical"])
        self.assertEqual(lag.worst(["ok", "warn"]), "warn")
        self.assertEqual(lag.worst([]), "ok")

    def test_watched_groups_mirror_the_workers(self):
        cfg = get_settings()
        w = lag.watched(cfg)
        self.assertIn(cfg.kafka_topic_messages_outgoing, w[cfg.kafka_consumer_group_notification_svc])
        self.assertIn(cfg.kafka_topic_llm_routing, w[cfg.kafka_consumer_group_ai_workers])


class LagEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def get(self, outbound, kafka):
        from src.gateway.main import create_app
        from src.gateway.routers import health

        async def ob():
            if isinstance(outbound, Exception):
                raise outbound
            return outbound

        kf = AsyncMock(side_effect=kafka) if isinstance(kafka, Exception) else AsyncMock(return_value=kafka)
        with patch.object(health, "_outbound_backlog", ob), patch("src.shared.kafka.lag.collect", kf):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
                return await c.get("/health/lag")

    async def test_all_quiet_is_ok(self):
        r = await self.get({"stuck_pending": 0, "oldest_age_seconds": 0, "level": "ok", "threshold_minutes": 5},
                           {"groups": {"g": {"t": {"lag": 3, "level": "ok"}}}, "dead_letters": {"t.dlq": 0}})
        self.assertEqual((r.status_code, r.json()["status"]), (200, "ok"))

    async def test_lag_stuck_messages_and_dead_letters_raise_the_level(self):
        ok_out = {"stuck_pending": 0, "oldest_age_seconds": 0, "level": "ok", "threshold_minutes": 5}
        crit = await self.get(ok_out, {"groups": {"g": {"t": {"lag": 5000, "level": "critical"}}}, "dead_letters": {}})
        self.assertEqual(crit.json()["status"], "critical")
        dlq = await self.get(ok_out, {"groups": {"g": {"t": {"lag": 0, "level": "ok"}}}, "dead_letters": {"t.dlq": 2}})
        self.assertEqual(dlq.json()["status"], "warn")
        stuck = await self.get({"stuck_pending": 4, "oldest_age_seconds": 900, "level": "warn", "threshold_minutes": 5}, {"groups": {}, "dead_letters": {}})
        self.assertEqual(stuck.json()["status"], "warn")

    async def test_an_unreachable_source_is_reported_as_unavailable_never_as_ok(self):
        r = await self.get(RuntimeError("db"), RuntimeError("kafka"))
        self.assertEqual(r.json()["status"], "unavailable")
        r2 = await self.get({"stuck_pending": 0, "oldest_age_seconds": 0, "level": "ok", "threshold_minutes": 5}, RuntimeError("kafka"))
        self.assertEqual(r2.json()["status"], "degraded")
        self.assertNotIn("kafka", r2.text.lower().replace('"kafka"', ""))          # no broker details leak

    @unittest.skipUnless(DB_URL, "needs OMNIFLOW_TEST_DATABASE_URL")
    async def test_the_outbound_backlog_query_runs_against_a_real_database(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from src.gateway.routers import health

        engine = create_async_engine(DB_URL)
        with patch("src.shared.db.session.AsyncSessionFactory", async_sessionmaker(engine, expire_on_commit=False)):
            out = await health._outbound_backlog()
        await engine.dispose()
        self.assertEqual(set(out), {"stuck_pending", "oldest_age_seconds", "level", "threshold_minutes"})
        self.assertIn(out["level"], ("ok", "warn", "critical"))


if __name__ == "__main__":
    unittest.main()
