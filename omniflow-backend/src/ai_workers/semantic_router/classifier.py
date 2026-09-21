"""
ai_workers/semantic_router/classifier.py — Semantic Intent Classifier

Determines the routing tier (L0 → L3 / HUMAN) for an inbound message
before it reaches any LLM. Uses a cascading rule engine + Gemini Flash-Lite
as the L1 fast classifier.

Classification cascade (ordered, first match wins):

  ┌─────────────────────────────────────────────────────────────────────┐
  │  L0 — Deterministic rules (0ms, no LLM)                            │
  │    • Greeting / farewell / thanks / vcard_confirmation keywords     │
  │    • Empty / sticker / reaction messages                            │
  │    • Pattern match on known FAQ triggers                            │
  ├─────────────────────────────────────────────────────────────────────┤
  │  L1 — Fast triage (Gemini Flash-Lite, ~300ms, cheap)               │
  │    • General real-estate chat                                       │
  │    • Short questions ≤ 2 sentences                                  │
  │    • Non-property-specific queries                                  │
  ├─────────────────────────────────────────────────────────────────────┤
  │  L2 — RAG retrieval (Gemini Flash, ~800ms, Qdrant context)         │
  │    • Specific listing inquiry (price, area, rooms, REGA number)     │
  │    • Location-based property search                                 │
  │    • Document analysis requests                                     │
  ├─────────────────────────────────────────────────────────────────────┤
  │  L3 — Master agent (Gemini Pro, ~2-5s, deep reasoning)             │
  │    • Negotiation / price counter-offer                              │
  │    • Complex legal / regulatory questions                           │
  │    • Multi-step deal structuring                                    │
  │    • Confidence escalation from L1/L2 below threshold              │
  ├─────────────────────────────────────────────────────────────────────┤
  │  VAULT — Document retrieval (no LLM needed)                        │
  │    • "أرسل لي تقريري" / "show my report" patterns                  │
  ├─────────────────────────────────────────────────────────────────────┤
  │  HUMAN — Bypass AI entirely                                         │
  │    • Already set by SemanticRouterWorker for PDPL/human-active      │
  └─────────────────────────────────────────────────────────────────────┘

References: SRS §2.3 — AI Routing Pipeline, Sprint 6 spec
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

import structlog

from src.shared.core.enums import IntentCategory, MessageType, RoutingTier
from src.shared.events.canonical import CanonicalInboundEvent

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Classification Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ClassificationResult:
    """
    Output of the SemanticClassifier.

    Consumed by SemanticRouterWorker to populate the RoutingDecision.
    """
    tier: RoutingTier
    intent: IntentCategory
    confidence: float                  # 0.0 – 1.0
    reasoning: str                     # Human-readable explanation (for logs/debug)
    matched_patterns: list[str] = field(default_factory=list)
    requires_rag: bool = False         # L2+ need Qdrant context injection
    escalate_to_human: bool = False    # Confidence below threshold

    @property
    def skip_llm(self) -> bool:
        return self.tier in (RoutingTier.L0_SEMANTIC_CACHE, RoutingTier.HUMAN_ESCALATION)


# ─────────────────────────────────────────────────────────────────────────────
# L0 Pattern Library (deterministic, Arabic + English)
# ─────────────────────────────────────────────────────────────────────────────

# Greeting / farewell patterns — map to L0 with GREETING intent
_GREETING_PATTERNS: Final[list[str]] = [
    r"^\s*(مرحب[اً]?|أهلا?ً?|سلام|هلا|hi|hello|hey|مساء\s*الخير|صباح\s*الخير)\s*[!.،,]*\s*$",
    r"^\s*(كيف\s*(حالك|الحال)|عامل\s*إيه|how\s*are\s*you)\s*[?؟]*\s*$",
    r"^\s*(شكر[اً]?|thanks?|thank\s*you|شكراً\s*جزيلاً)\s*[!.]*\s*$",
    r"^\s*(مع\s*السلامة|باي|bye|goodbye|وداع)\s*[!.]*\s*$",
]

# VCard confirmation — customer replies "تم" / "1" after VCard prompt
_VCARD_PATTERNS: Final[list[str]] = [
    r"^\s*(تم|حفظت?|عملت?\s*ذلك|ok|okay|done|saved?|حفظ)\s*[!.]*\s*$",
    r"^\s*[1٢٣١]\s*$",  # Button clicks
]

# Vault retrieval — customer asking for their own report/document
_VAULT_PATTERNS: Final[list[str]] = [
    r"(تقرير[ي]?|ملفات[ي]?|وثيقت[ي]?|report|document|my\s+files?)",
    r"(ارسل\s*لي|أرسل|send\s*me|أحتاج|أريد)\s*(تقرير|ملف|وثيق)",
]

# Listing-specific patterns — strong signal for L2 RAG
_LISTING_PATTERNS: Final[list[str]] = [
    r"(شقة|فيلا|أرض|عقار|وحدة|عمارة|دور|apartment|villa|land|property|unit)",
    r"(غرف\s*نوم|حمام|مساحة|طابق|دور|rooms?|bedrooms?|bathrooms?|area|floor)",
    r"(سعر|ثمن|تكلفة|price|cost|ريال|sar|sr)\s*\d",
    r"(رقم\s*الإعلان|rega|رقم\s*ريغا?|ad\s*number)\s*[\d\-]+",
    r"(حي|منطقة|مدينة|شارع|neighborhood|district|city|area)\s+\w+",
    r"(بحث|أريد|أبحث|search|looking\s*for|find)\s*(عن\s*)?(عقار|شقة|فيلا)",
]

# Deep consultation / negotiation — L3 signals
_NEGOTIATION_PATTERNS: Final[list[str]] = [
    r"(سعر\s*نهائي|خصم|تفاوض|negotiate|final\s*price|discount|counter\s*offer|عرض\s*مضاد)",
    r"(صك|سند\s*ملكية|deed|legal|نظام|لوائح|regulation|قانوني)",
    r"(استثمار|عائد|roi|investment|return|ربح|profit|yield)",
    r"(مقارنة|قارن|compare|أفضل\s*من|vs\.\s*)",
]

# Compiled pattern groups for performance (compile once at module load)
_COMPILED = {
    "greeting": [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _GREETING_PATTERNS],
    "vcard": [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _VCARD_PATTERNS],
    "vault": [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _VAULT_PATTERNS],
    "listing": [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _LISTING_PATTERNS],
    "negotiation": [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _NEGOTIATION_PATTERNS],
}


def _match_patterns(text: str, group: str) -> list[str]:
    """Return list of matched pattern strings for a given group."""
    matched = []
    for pattern in _COMPILED[group]:
        if pattern.search(text):
            matched.append(pattern.pattern[:60])
    return matched


# ══════════════════════════════════════════════════════════════════════════════
# SemanticClassifier
# ══════════════════════════════════════════════════════════════════════════════

class SemanticClassifier:
    """
    Stateless semantic classifier for routing inbound messages to LLM tiers.

    Architecture:
        1. L0 deterministic rules (free, instant)
        2. L1/L2/L3 heuristic scoring (free, instant)
        3. [Sprint 7] L1 Gemini Flash-Lite LLM confirm (paid, ~300ms)
           Currently stubbed — returns L1 classification from heuristics.

    Instantiate once and reuse:
        classifier = SemanticClassifier()
        result = await classifier.classify(event)
    """

    def __init__(
        self,
        confidence_escalation_threshold: float = 0.70,
    ) -> None:
        self.confidence_threshold = confidence_escalation_threshold

    async def classify(
        self,
        event: CanonicalInboundEvent,
        conversation_history: list[dict] | None = None,
    ) -> ClassificationResult:
        """
        Main classification entry point.

        Args:
            event               — The inbound canonical event.
            conversation_history — Recent messages for context (from Redis).

        Returns:
            ClassificationResult with tier, intent, confidence, reasoning.
        """
        # ── Non-text types: route immediately by message type ─────────────────
        type_result = self._classify_by_message_type(event)
        if type_result:
            return type_result

        text = (event.text_content or "").strip()
        if not text:
            return ClassificationResult(
                tier=RoutingTier.L0_SEMANTIC_CACHE,
                intent=IntentCategory.GENERAL_QUERY,
                confidence=1.0,
                reasoning="Empty text content — L0 no-op",
            )

        # ── L0: Deterministic pattern matching ────────────────────────────────
        l0_result = self._classify_l0(text)
        if l0_result:
            return l0_result

        # ── Heuristic scoring for L1 / L2 / L3 ───────────────────────────────
        return self._classify_by_heuristics(text, conversation_history or [])

    # ── L0 Classification ─────────────────────────────────────────────────────

    def _classify_l0(self, text: str) -> ClassificationResult | None:
        """
        Deterministic L0 rules. Returns None if no pattern matches.
        All L0 decisions have confidence=1.0 and skip_llm=True.
        """
        # Greeting / farewell / thanks
        greeting_matches = _match_patterns(text, "greeting")
        if greeting_matches:
            return ClassificationResult(
                tier=RoutingTier.L0_SEMANTIC_CACHE,
                intent=IntentCategory.GREETING,
                confidence=1.0,
                reasoning="Deterministic greeting/farewell pattern match",
                matched_patterns=greeting_matches,
            )

        # VCard confirmation ("تم"/"done"/"1")
        vcard_matches = _match_patterns(text, "vcard")
        if vcard_matches:
            return ClassificationResult(
                tier=RoutingTier.L0_SEMANTIC_CACHE,
                intent=IntentCategory.VCARD_CONFIRMATION,
                confidence=1.0,
                reasoning="Deterministic VCard confirmation pattern",
                matched_patterns=vcard_matches,
            )

        # Vault retrieval ("أرسل لي تقريري")
        vault_matches = _match_patterns(text, "vault")
        if vault_matches:
            return ClassificationResult(
                tier=RoutingTier.VAULT_RETRIEVAL,
                intent=IntentCategory.VAULT_RETRIEVAL,
                confidence=0.95,
                reasoning="Vault retrieval keyword detected",
                matched_patterns=vault_matches,
            )

        return None

    # ── Message type routing ──────────────────────────────────────────────────

    def _classify_by_message_type(
        self, event: CanonicalInboundEvent
    ) -> ClassificationResult | None:
        """Route non-text messages by their type before text analysis."""
        mt = event.message_type

        if mt == MessageType.STICKER:
            return ClassificationResult(
                tier=RoutingTier.L0_SEMANTIC_CACHE,
                intent=IntentCategory.GREETING,
                confidence=1.0,
                reasoning="Sticker → L0 emoji-equivalent response",
            )

        if mt == MessageType.REACTION:
            return ClassificationResult(
                tier=RoutingTier.L0_SEMANTIC_CACHE,
                intent=IntentCategory.GENERAL_QUERY,
                confidence=1.0,
                reasoning="Reaction → L0 acknowledgment only",
            )

        if mt == MessageType.AUDIO:
            # Audio must go through Whisper first — route to L1 as placeholder
            return ClassificationResult(
                tier=RoutingTier.L1_TRIAGE,
                intent=IntentCategory.GENERAL_QUERY,
                confidence=0.5,
                reasoning="Audio message — transcription pending via Whisper worker",
            )

        if mt in (MessageType.IMAGE, MessageType.DOCUMENT):
            # Vision pipeline — L2 because it likely contains property documents
            return ClassificationResult(
                tier=RoutingTier.L2_RAG,
                intent=IntentCategory.DEED_CHECK,
                confidence=0.7,
                reasoning=f"{mt} message → Vision pipeline + L2 RAG",
                requires_rag=True,
            )

        if mt == MessageType.LOCATION:
            return ClassificationResult(
                tier=RoutingTier.L2_RAG,
                intent=IntentCategory.LISTING_SEARCH,
                confidence=0.85,
                reasoning="Location → geographic property search via L2 RAG",
                requires_rag=True,
            )

        if mt == MessageType.INTERACTIVE:
            # Interactive button/list clicks — often map to a known intent
            return ClassificationResult(
                tier=RoutingTier.L1_TRIAGE,
                intent=IntentCategory.GENERAL_QUERY,
                confidence=0.8,
                reasoning="Interactive payload — L1 interprets button selection",
            )

        return None  # Text — handled by text classifiers

    # ── Heuristic scoring ─────────────────────────────────────────────────────

    def _classify_by_heuristics(
        self,
        text: str,
        history: list[dict],
    ) -> ClassificationResult:
        """
        Score the text against listing/negotiation patterns and assign tier.

        Scoring logic:
          negotiation matches ≥ 1   → L3 (high confidence for explicit signals)
          listing matches ≥ 2       → L2 RAG (property context needed)
          listing matches == 1      → L1 (ambiguous, triage first)
          no strong matches         → L1 (general query)

        History depth bonus: long conversations (> 10 msgs) increase L3 likelihood
        because complex deals take multiple turns.
        """
        negotiation_matches = _match_patterns(text, "negotiation")
        listing_matches = _match_patterns(text, "listing")

        history_depth = len(history)
        text_length = len(text.split())

        # ── L3: Negotiation / legal / deep analysis ───────────────────────────
        if negotiation_matches:
            confidence = min(0.85 + 0.05 * len(negotiation_matches), 0.98)
            return ClassificationResult(
                tier=RoutingTier.L3_MASTER,
                intent=self._infer_negotiation_intent(text),
                confidence=confidence,
                reasoning=(
                    f"Negotiation/legal patterns detected ({len(negotiation_matches)} matches). "
                    f"History depth: {history_depth} messages."
                ),
                matched_patterns=negotiation_matches,
            )

        # ── L3 escalation: long conversation with complex multi-turn context ───
        if history_depth > 15 and text_length > 30:
            return ClassificationResult(
                tier=RoutingTier.L3_MASTER,
                intent=IntentCategory.DEEP_CONSULTATION,
                confidence=0.75,
                reasoning=(
                    f"Deep consultation inferred from long history "
                    f"({history_depth} msgs, {text_length} words)."
                ),
            )

        # ── L2: Specific property inquiry ─────────────────────────────────────
        if len(listing_matches) >= 1 and text_length > 3:
            return ClassificationResult(
                tier=RoutingTier.L2_RAG,
                intent=IntentCategory.LISTING_SEARCH,
                confidence=min(0.75 + 0.05 * len(listing_matches), 0.95),
                reasoning=(
                    f"Strong property inquiry: {len(listing_matches)} listing patterns. "
                    "Requires Qdrant RAG context."
                ),
                matched_patterns=listing_matches,
                requires_rag=True,
            )

        # ── L1: General real-estate chat / short query ────────────────────────
        intent = self._infer_general_intent(text)
        confidence = 0.80 if text_length <= 15 else 0.70

        # Escalation: confidence below threshold → flag for human review
        escalate = confidence < self.confidence_threshold and history_depth > 5

        return ClassificationResult(
            tier=RoutingTier.L1_TRIAGE,
            intent=intent,
            confidence=confidence,
            reasoning=(
                f"General query: no strong patterns. "
                f"L1 fast triage. "
                f"Words: {text_length}, history: {history_depth}."
            ),
            escalate_to_human=escalate,
        )

    # ── Intent inference helpers ──────────────────────────────────────────────

    @staticmethod
    def _infer_negotiation_intent(text: str) -> IntentCategory:
        text_lower = text.lower()
        if any(k in text_lower for k in ["صك", "deed", "سند", "legal", "قانوني"]):
            return IntentCategory.DEED_CHECK
        if any(k in text_lower for k in ["استثمار", "investment", "عائد", "roi"]):
            return IntentCategory.DEEP_CONSULTATION
        return IntentCategory.DEEP_CONSULTATION

    @staticmethod
    def _infer_general_intent(text: str) -> IntentCategory:
        text_lower = text.lower()
        if any(k in text_lower for k in ["شكوى", "complaint", "مشكلة", "problem"]):
            return IntentCategory.COMPLAINT
        if any(k in text_lower for k in ["موعد", "زيارة", "booking", "appointment"]):
            return IntentCategory.BOOKING
        if any(k in text_lower for k in ["vip", "عروض", "offers", "حصري"]):
            return IntentCategory.VIP_OPT_IN
        if any(k in text_lower for k in ["موظف", "مسؤول", "human", "agent", "إنسان"]):
            return IntentCategory.ESCALATION_REQUEST
        return IntentCategory.GENERAL_QUERY
