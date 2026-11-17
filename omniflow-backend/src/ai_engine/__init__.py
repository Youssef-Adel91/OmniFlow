"""
src/ai_engine/__init__.py — AI Engine Package

Public API surface for the AI Engine orchestration layer.

This package provides a clean, high-level facade over the lower-level
`ai_workers` implementations:

    schemas.py          — Pydantic request/response models for the AI pipeline
    llm_orchestrator.py — Multi-tier LLM invocation controller (L1/L2/L3)
    company_context.py  — company profile / persona -> system-prompt block

(The never-wired `semantic_router.py` and `ai_gateway.py` Phase-2 stubs were removed; routing lives in `ai_workers/semantic_router`,
and the guards that actually run are in `src/sales` (rule layer + output guard).)

References: SRS §2.3 — AI Routing Pipeline & Agentic Layer
"""
from src.ai_engine.llm_orchestrator import LLMOrchestrator
from src.ai_engine.schemas import RoutingDecision, RoutingRequest

__all__ = [
    "LLMOrchestrator",
    "RoutingDecision",
    "RoutingRequest",
]
