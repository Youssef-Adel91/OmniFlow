"""Provider routing and worker contract regressions without live API calls."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.shared.core.config import Settings
from src.shared.services.llm_provider import provider_options, create_chat_client, completion_options
from src.ai_workers.llm_invoker import client as worker_client


class ProviderTests(unittest.TestCase):
    def test_groq_uses_its_own_key_endpoint_and_model_overrides(self):
        settings = Settings(llm_primary_provider="groq", groq_api_key="groq-test",
                            openai_api_key="other-provider", MODEL_L1="small",
                            model_l3_override="large")
        key, base, models = provider_options(settings)
        self.assertEqual(key, "groq-test")
        self.assertEqual(base, "https://api.groq.com/openai/v1")
        self.assertEqual(models["L1"], "small")
        self.assertEqual(models["L3"], "large")

    def test_missing_groq_key_does_not_fall_back_to_openai(self):
        settings = Settings(llm_primary_provider="groq", openai_api_key="other-provider")
        self.assertIsNone(create_chat_client(settings))

    def test_openrouter_free_guard_checks_overrides(self):
        settings = Settings(llm_primary_provider="openrouter", llm_free_only=True,
                            MODEL_ROUTER="openrouter/free", MODEL_L1="openrouter/free",
                            MODEL_L2="openrouter/free", MODEL_L3="openrouter/free")
        provider_options(settings)
        settings.model_l3_override = "paid-model"
        with self.assertRaises(ValueError):
            provider_options(settings)

    def test_groq_reasoning_stays_out_of_answer(self):
        options = completion_options("openai/gpt-oss-20b", Settings(llm_primary_provider="groq"))
        self.assertFalse(options["extra_body"]["include_reasoning"])


class WorkerProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_preserves_history_persona_context_and_usage(self):
        completion = SimpleNamespace(
            model="openai/gpt-oss-120b",
            choices=[SimpleNamespace(message=SimpleNamespace(content="Test answer"), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=15, total_tokens=25),
        )
        create = AsyncMock(return_value=completion)
        compatible = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        settings = Settings(llm_primary_provider="groq", groq_api_key="test",
                            MODEL_L2="openai/gpt-oss-120b")
        with patch.object(worker_client, "settings", settings), \
                patch.object(worker_client, "create_chat_client", return_value=compatible):
            client = worker_client.GeminiLLMClient()
            client.configure()
            result = await client.generate_response(
                messages=[{"role": "model", "parts": ["Previous answer"]},
                          {"role": "user", "parts": ["Question"]}],
                tier="L2", system_prompt="Tenant persona", rag_context="Verified listing",
            )
            self.assertEqual(result.text, "Test answer")
            self.assertEqual(result.total_tokens, 25)
            sent = create.call_args.kwargs
            self.assertEqual(sent["model"], "openai/gpt-oss-120b")
            self.assertEqual(sent["messages"][1]["role"], "assistant")
            self.assertIn("Verified listing", sent["messages"][0]["content"])
            completion.choices[0].message.content = ""
            with self.assertRaises(RuntimeError):
                await client.generate_response(messages=[], tier="L2", system_prompt="Test")
