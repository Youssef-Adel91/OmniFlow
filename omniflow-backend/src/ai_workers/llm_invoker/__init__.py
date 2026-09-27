"""ai_workers/llm_invoker/__init__.py — LLM Invoker Worker

Ref: SRS §4.1.1 — Cost-Optimal Hierarchy (L0→L1→L2→L3)

Real provider support (see GeminiLLMClient in client.py and
Settings.llm_primary_provider in shared/core/config.py): OpenAI-compatible
providers (OpenRouter, Groq, or direct OpenAI) and Gemini. No Anthropic
fallback chain exists -- the `anthropic` package was declared in
pyproject.toml but never imported anywhere in this codebase; removed from
dependencies rather than left as a misleading, unbuilt claim.
"""
