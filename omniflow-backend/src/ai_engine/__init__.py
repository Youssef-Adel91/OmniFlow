"""
src/ai_engine/__init__.py — AI Engine Package

Public API surface for the AI Engine orchestration layer.

This package provides a clean, high-level facade over the lower-level
`ai_workers` implementations. It separates concerns into three distinct modules:

    schemas.py         — Pydantic request/response models for the AI pipeline
    semantic_router.py — Routing decision engine (L0 → HUMAN tier selection)
    ai_gateway.py      — Pre/Post LLM security & compliance middleware
    llm_orchestrator.py — Multi-tier LLM invocation controller (L1/L2/L3)

References: SRS §2.3 — AI Routing Pipeline & Agentic Layer
"""
from src.ai_engine.ai_gateway import AIGateway
from src.ai_engine.llm_orchestrator import LLMOrchestrator
from src.ai_engine.schemas import (
    PostLLMCheckResult,
    PreLLMCheckResult,
    RoutingDecision,
    RoutingRequest,
)
from src.ai_engine.semantic_router import SemanticRouter

__all__ = [
    "AIGateway",
    "LLMOrchestrator",
    "PostLLMCheckResult",
    "PreLLMCheckResult",
    "RoutingDecision",
    "RoutingRequest",
    "SemanticRouter",
]
