"""Explicit live smoke test using synthetic text, without database or channel sends."""
import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from src.shared.services.llm_provider import create_chat_client, provider_options


async def main(application=False):
    if application:
        from src.ai_engine import llm_orchestrator as gateway
        from src.ai_workers.llm_invoker.client import GeminiLLMClient
        orchestrator = gateway.LLMOrchestrator()
        worker = GeminiLLMClient()
        worker.configure()
        try:
            tier = await orchestrator._classify_intent("السلام عليكم")
            answer = await orchestrator.invoke_l1("السلام عليكم", system_prompt="أنت مساعد تجريبي. رحب بالعميل بالعربية باختصار.")
            if not answer or answer == gateway._FALLBACK_ERROR_MESSAGE:
                raise RuntimeError("Gateway returned fallback instead of a model answer")
            print(f"PASS gateway classifier={tier}; answer={answer}")
            result = await worker.generate_response(
                messages=[{"role": "user", "parts": ["ما سعر شقة التجربة؟"]}], tier="L2",
                system_prompt="أجب بالعربية باستخدام بيانات التجربة فقط.",
                rag_context="بيانات وهمية: شقة التجربة في الرياض بسعر 500000 ريال.",
            )
            if "500" not in result.text and "٥٠٠" not in result.text and "500,000" not in result.text:
                raise RuntimeError("Worker did not use the supplied test context")
            print(f"PASS worker model={result.model_used}; answer={result.text}")
        finally:
            if gateway._openai_client:
                await gateway._openai_client.close()
            await worker._compatible_client.close()
        return
    client = create_chat_client()
    if client is None:
        raise RuntimeError("Selected provider key is missing")
    _, _, models = provider_options()
    try:
        for model in dict.fromkeys(models.values()):
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "أجب بالعربية باختصار. هذه تجربة تقنية ببيانات وهمية."},
                    {"role": "user", "content": "عميل يبحث عن شقة. اسأله عن المدينة والميزانية دون اختراع عروض."},
                ],
                max_tokens=512,
            )
            answer = (response.choices[0].message.content or "").strip()
            if not answer:
                raise RuntimeError("Provider returned no answer")
            print(f"PASS {model}; finish={response.choices[0].finish_reason}")
            print(answer)
    finally:
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--application", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.application))
    except Exception as exc:
        print(f"Live smoke test failed: {type(exc).__name__}; status={getattr(exc, 'status_code', None)}")
        sys.exit(1)
