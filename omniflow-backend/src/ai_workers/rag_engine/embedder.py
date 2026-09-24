"""
ai_workers/rag_engine/embedder.py — Embedding Service (OpenAI or local fastembed)

Converts text into a vector for semantic search in Qdrant, using one of two
real providers — never a mock:

  - OpenAI text-embedding-3-small (1536-dim) when a real API key is configured.
  - fastembed / BAAI/bge-small-en-v1.5 (384-dim), an ONNX model that runs
    entirely locally on CPU, when no key is configured. This replaces what
    used to be a `random.seed(text)`-based mock vector — semantically
    meaningless, so RAG retrieval was disabled outright rather than run on
    it. fastembed and qdrant-client[fastembed] were already project
    dependencies for exactly this purpose but were never wired in anywhere.

`settings.embedding_provider`/`settings.embedding_dim` (config.py) are the
single source of truth for which provider is active and its vector size —
`shared/qdrant_client/client.py` reads the same `embedding_dim` when creating
collections, so the two can never drift out of sync with each other.

Design decisions kept from the original OpenAI-only version:
  - Singleton pattern: one client/model per worker process.
  - Short-lived in-memory LRU-ish cache for identical queries.
  - Explicit L2 normalization (both providers already return unit vectors,
    but Qdrant cosine distance wants that guaranteed, not assumed).

References: SRS §3.3 — Embedding Pipeline, Sprint 7 spec
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Final

import numpy as np
import structlog
from openai import AsyncOpenAI, RateLimitError, APITimeoutError, APIConnectionError
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.shared.core.config import get_settings

logger = structlog.get_logger(__name__)
settings = get_settings()

EMBEDDING_MODEL: Final[str] = settings.embedding_model

# In-memory query → vector cache (512 slots, FIFO eviction)
_EMBEDDING_CACHE: dict[str, list[float]] = {}
_CACHE_MAX_SIZE: Final[int] = 512


def _cache_key(text: str) -> str:
    """SHA-256 of the normalized query text."""
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def _normalize_vector(vector: list[float]) -> list[float]:
    """L2-normalize a vector to unit length for cosine similarity."""
    arr = np.array(vector, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return vector
    return (arr / norm).tolist()


def _cache_get_many(texts: list[str]) -> tuple[list[list[float] | None], list[int], list[str]]:
    """Split a batch into (results-with-None-gaps, uncached-indices, uncached-texts)."""
    results: list[list[float] | None] = [None] * len(texts)
    uncached_indices: list[int] = []
    uncached_texts: list[str] = []
    for i, text in enumerate(texts):
        key = _cache_key(text)
        if key in _EMBEDDING_CACHE:
            results[i] = _EMBEDDING_CACHE[key]
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)
    return results, uncached_indices, uncached_texts


def _cache_put(text: str, vector: list[float]) -> None:
    if len(_EMBEDDING_CACHE) >= _CACHE_MAX_SIZE:
        oldest_key = next(iter(_EMBEDDING_CACHE))
        del _EMBEDDING_CACHE[oldest_key]
    _EMBEDDING_CACHE[_cache_key(text)] = vector


# ══════════════════════════════════════════════════════════════════════════════
# EmbeddingService
# ══════════════════════════════════════════════════════════════════════════════

class EmbeddingService:
    """
    Async embedding client — OpenAI or local fastembed, real either way.

    Usage:
        embedder = EmbeddingService()
        embedder.configure()
        vector = await embedder.embed_query("شقة 3 غرف في حي النرجس")
    """

    def __init__(self) -> None:
        self._openai_client: AsyncOpenAI | None = None
        self._local_model: "object | None" = None  # fastembed.TextEmbedding, imported lazily
        self.provider: str = settings.embedding_provider
        self.dim: int = settings.embedding_dim

    @property
    def is_configured(self) -> bool:
        return self._openai_client is not None or self._local_model is not None

    def configure(self) -> None:
        """Initialize whichever provider is active. Call once during worker on_startup()."""
        self.provider = settings.embedding_provider
        self.dim = settings.embedding_dim

        if self.provider == "openai":
            self._openai_client = AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_api_base,
                timeout=settings.openai_timeout,
                max_retries=0,  # tenacity handles retries below for better control
            )
            logger.info("embedding_service_configured", provider="openai", model=EMBEDDING_MODEL, dim=self.dim)
            return

        # fastembed loads/downloads ONNX model weights synchronously (first
        # call may fetch from Hugging Face Hub; cached locally afterward) —
        # run it off the event loop so worker startup doesn't block on it.
        from fastembed import TextEmbedding

        self._local_model = TextEmbedding(model_name=settings.embedding_local_model)
        logger.info(
            "embedding_service_configured",
            provider="fastembed",
            model=settings.embedding_local_model,
            dim=self.dim,
        )

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single text query. Returns a unit vector of length `self.dim`."""
        clean_text = " ".join(text.strip().split())
        if not clean_text:
            logger.warning("embed_query_empty_text")
            return [0.0] * self.dim

        key = _cache_key(clean_text)
        if key in _EMBEDDING_CACHE:
            logger.debug("embedding_cache_hit", key=key[:8])
            return _EMBEDDING_CACHE[key]

        vector = await self._embed_one(clean_text)
        normalized = _normalize_vector(vector)
        _cache_put(clean_text, normalized)
        return normalized

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts (cheaper/faster than N calls to embed_query)."""
        if not texts:
            return []

        clean_texts = [" ".join(t.strip().split()) for t in texts]
        results, uncached_indices, uncached_texts = _cache_get_many(clean_texts)

        if uncached_texts:
            vectors = await self._embed_many(uncached_texts)
            for idx, text, vector in zip(uncached_indices, uncached_texts, vectors):
                normalized = _normalize_vector(vector)
                results[idx] = normalized
                _cache_put(text, normalized)

        return [r for r in results if r is not None]

    # ── Provider dispatch ─────────────────────────────────────────────────────

    async def _embed_one(self, text: str) -> list[float]:
        if self.provider == "openai":
            return (await self._openai_embed_with_retry([text]))[0]
        return (await self._fastembed_embed([text]))[0]

    async def _embed_many(self, texts: list[str]) -> list[list[float]]:
        if self.provider == "openai":
            return await self._openai_embed_with_retry(texts)
        return await self._fastembed_embed(texts)

    async def _fastembed_embed(self, texts: list[str]) -> list[list[float]]:
        """fastembed's API is synchronous CPU (ONNX) work — offload to a thread
        so it doesn't block the worker's event loop."""
        if self._local_model is None:
            raise RuntimeError("EmbeddingService not configured. Call configure() first.")

        def _run() -> list[list[float]]:
            return [vec.tolist() for vec in self._local_model.embed(texts)]  # type: ignore[union-attr]

        vectors = await asyncio.to_thread(_run)
        logger.debug("embedding_generated", provider="fastembed", count=len(vectors))
        return vectors

    async def _openai_embed_with_retry(self, texts: list[str]) -> list[list[float]]:
        if self._openai_client is None:
            raise RuntimeError("EmbeddingService not configured. Call configure() first.")

        async for attempt in AsyncRetrying(
            retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError)),
            stop=stop_after_attempt(settings.openai_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            reraise=True,
        ):
            with attempt:
                response = await self._openai_client.embeddings.create(
                    input=texts,
                    model=EMBEDDING_MODEL,
                    encoding_format="float",
                )
                vectors = [item.embedding for item in sorted(response.data, key=lambda x: x.index)]
                logger.debug(
                    "embedding_generated",
                    provider="openai",
                    model=EMBEDDING_MODEL,
                    count=len(vectors),
                    tokens=response.usage.total_tokens,
                )
                return vectors

        raise RuntimeError("Embedding failed after all retries")  # unreachable — reraise=True


# ── Module-level singleton ─────────────────────────────────────────────────────
embedder = EmbeddingService()
