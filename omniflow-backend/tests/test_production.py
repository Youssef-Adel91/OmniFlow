"""Production must fail closed without dependencies or real service outputs."""
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import make_url

from src.gateway.routers import health
from src.gateway.main import lifespan
from src.shared.core.config import Settings
from src.shared.services import storage_client, tts_client
from src.ai_workers.vault_worker import generator


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_dependency_failure_returns_503_without_exception_details(self):
        app = FastAPI()
        app.include_router(health.router)
        with patch.object(health, "_database_ready", AsyncMock(side_effect=RuntimeError("secret-connection-string"))), \
                patch.object(health.redis_mgr, "ping", AsyncMock(return_value=True)), \
                patch.object(health.kafka_producer, "ping", AsyncMock(return_value=True)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/health/ready")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["checks"]["database"], "unavailable")
                self.assertNotIn("secret", response.text)
                self.assertEqual((await client.get("/health")).status_code, 200)

    async def test_all_dependencies_required(self):
        for failed in (None, "redis", "kafka"):
            with patch.object(health, "_database_ready", AsyncMock(return_value=True)), \
                    patch.object(health.redis_mgr, "ping", AsyncMock(return_value=failed != "redis")), \
                    patch.object(health.kafka_producer, "ping", AsyncMock(return_value=failed != "kafka")):
                checks = await health.dependency_status()
                self.assertEqual(all(value == "ok" for value in checks.values()), failed is None)

    async def test_production_rejects_rls_bypass_role(self):
        connection = AsyncMock()
        engine = MagicMock()
        engine.connect.return_value.__aenter__.return_value = connection
        with patch.object(health, "engine", engine), patch.object(health.settings, "app_env", "production"):
            for privileged in (True, False):
                connection.scalar.return_value = privileged
                self.assertEqual(await health._database_ready(), not privileged)

    async def test_production_cannot_start_without_kafka(self):
        with patch.object(health.settings, "app_env", "production"), \
                patch.object(health.kafka_producer, "start", AsyncMock(side_effect=RuntimeError("offline"))), \
                patch.object(health.kafka_producer, "stop", AsyncMock()) as stop:
            with self.assertRaisesRegex(RuntimeError, "Kafka is required"):
                async with lifespan(FastAPI()):
                    self.fail("Application started without Kafka")
            stop.assert_awaited_once()


class ProductionSettingsTests(unittest.TestCase):
    def settings(self, **overrides):
        values = dict(app_env="production", app_debug=False, kms_dev_mock_key=False,
                      sentry_dsn="https://test@example.invalid/1", otel_enabled=True,
                      clerk_secret_key="configured", clerk_webhook_secret="configured",
                      clerk_issuer_url="https://test.clerk.accounts.dev",
                      allowed_origins="https://app.example.invalid", feature_voice_notes_enabled=False,
                      openai_api_key="configured")
        values.update(overrides)
        return Settings(**values)

    def test_production_rejects_debug_missing_auth_wildcard_and_mock_voice(self):
        self.settings()
        for override in ({"app_debug": True}, {"clerk_webhook_secret": ""},
                         {"allowed_origins": "https://*.example.invalid"},
                         {"allowed_origins": "http://localhost:3000"},
                         {"postgres_password": "<REQUIRED_password>"},
                         {"openai_api_key": "mock"},
                         {"feature_voice_notes_enabled": True, "elevenlabs_api_key": "mock"}):
            with self.subTest(fields=list(override)), self.assertRaises(ValueError):
                self.settings(**override)

    def test_database_password_reserved_characters_are_encoded(self):
        settings = Settings(postgres_password="test@:/?#%password")
        self.assertEqual(make_url(settings.database_url).password, "test@:/?#%password")


class PlaceholderOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_uploaded_audio_returns_the_storage_result(self):
        with patch.object(tts_client.storage_client, "upload_audio_bytes", AsyncMock(return_value="https://example.invalid/audio")) as upload:
            url = await tts_client.TTSClient()._upload_audio(b"audio", "synthetic-id", str(uuid.uuid4()))
            self.assertEqual(url, "https://example.invalid/audio")
            upload.assert_awaited_once()

    async def test_mock_voice_is_never_returned_in_production(self):
        with patch.object(tts_client.settings, "app_env", "production"), \
                patch.object(tts_client.settings, "elevenlabs_api_key", "mock"):
            with self.assertRaises(tts_client.TTSGenerationError):
                await tts_client.TTSClient().synthesize("Synthetic text")

    async def test_unconfigured_audio_storage_never_returns_music(self):
        with patch.object(storage_client._settings, "app_env", "production"), \
                patch.object(storage_client.StorageClient, "is_configured", new_callable=PropertyMock, return_value=False):
            with self.assertRaises(RuntimeError):
                await storage_client.StorageClient().upload_audio_bytes(b"audio", "test.mp3")

    async def test_paid_report_stub_cannot_upload_or_record_success(self):
        event = generator.PaymentEvent(transaction_id=uuid.uuid4(), tenant_id=uuid.uuid4(),
                                       customer_id=uuid.uuid4(), transaction_type="report_fee", amount=29, status="completed")
        with patch.object(generator.settings, "app_env", "production"), \
                patch.object(generator.s3_mgr, "upload_file", AsyncMock()) as upload:
            worker = generator.VaultGeneratorWorker()
            with self.assertRaises(RuntimeError):
                await worker.process_message(SimpleNamespace(value=event.model_dump_json()))
            upload.assert_not_awaited()
