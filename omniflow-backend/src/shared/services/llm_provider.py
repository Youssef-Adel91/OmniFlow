"""Shared OpenAI-compatible provider configuration for gateway and workers."""
from openai import AsyncOpenAI

from src.shared.core.config import get_settings


def provider_options(settings=None):
    settings = settings or get_settings()
    provider = settings.llm_primary_provider
    if provider == "openai":
        key = settings.llm_api_key or settings.openai_api_key
        base_url = settings.openai_api_base
        models = {
            "ROUTER": settings.llm_l1_model, "L1": settings.llm_l1_model,
            "L2": settings.llm_l1_model, "L3": settings.llm_l3_model,
        }
    elif provider in {"openrouter", "groq"}:
        key = settings.llm_api_key or (
            settings.openrouter_api_key if provider == "openrouter" else settings.groq_api_key
        )
        base_url = settings.llm_base_url
        if provider == "groq" and "llm_base_url" not in settings.model_fields_set:
            base_url = "https://api.groq.com/openai/v1"
        models = {tier: getattr(settings, f"MODEL_{tier}") for tier in ("ROUTER", "L1", "L2", "L3")}
    else:
        raise ValueError("This client requires an OpenAI-compatible provider")
    for tier in ("L1", "L2", "L3"):
        models[tier] = getattr(settings, f"model_{tier.lower()}_override") or models[tier]
    if settings.llm_free_only:
        if provider != "openrouter" or base_url.rstrip("/") != "https://openrouter.ai/api/v1":
            raise ValueError("LLM_FREE_ONLY requires the official OpenRouter endpoint")
        if any(model != "openrouter/free" and not model.endswith(":free") for model in models.values()):
            raise ValueError("LLM_FREE_ONLY rejects paid model identifiers")
    return key, base_url, models


def create_chat_client(settings=None):
    settings = settings or get_settings()
    key, base_url, _ = provider_options(settings)
    if not key or key.lower() == "mock":
        return None
    return AsyncOpenAI(
        api_key=key, base_url=base_url,
        max_retries=settings.openai_max_retries,
        timeout=float(settings.openai_timeout),
    )


def completion_options(model, settings=None):
    """Keep Groq reasoning separate from customer-facing text."""
    settings = settings or get_settings()
    if settings.llm_primary_provider == "groq" and model.startswith("openai/gpt-oss-"):
        return {"extra_body": {"reasoning_effort": "low", "include_reasoning": False}}
    return {}
