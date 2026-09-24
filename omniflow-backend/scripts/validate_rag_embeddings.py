"""
Real end-to-end validation of RAG retrieval (item 9). Before this, the whole
pipeline was disabled outright whenever no OpenAI key was configured
(`rag_unavailable_no_embedding_provider`), even though `fastembed` — a real,
local, no-API-key embedding library — was already a project dependency and
simply never wired into any code path. The alternative that was hit whenever
someone forced it on without a key was a `random.seed(text)`-based mock
vector, semantically meaningless, which is exactly why the team chose to
disable retrieval rather than run on it.

This validates the real fix against a real, running Qdrant (docker-compose
service, already up) and the real local fastembed model — no mocks, no API
key required. Only the LLM call that would consume the retrieved context is
out of scope here (a separate, unrelated piece of the pipeline).

Scenario A: ingest two tenants' property listings with real fastembed vectors
into real per-tenant Qdrant collections, then prove `search_properties`
returns only the querying tenant's listing — never the other tenant's — for
a semantically similar query.
Scenario B: the real `RAGRetriever.get_rag_context()` call (the actual
production code path the L2 routing tier invokes) returns a real, relevant
context string built from real retrieved content, not an empty/mocked result.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_workers.rag_engine.embedder import embedder
from src.ai_workers.rag_engine.retriever import rag_retriever
from src.shared.core.config import get_settings
from src.shared.qdrant_client.client import _tenant_collection, qdrant_mgr

settings = get_settings()


def _require_local() -> None:
    if settings.qdrant_host not in {"localhost", "127.0.0.1"}:
        raise RuntimeError("requires a local Qdrant instance")


async def scenario_a_tenant_isolation() -> tuple[uuid.UUID, uuid.UUID]:
    print("\n=== Scenario A: real fastembed + real Qdrant, verified tenant isolation ===")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    listing_a = "شقة فاخرة 3 غرف نوم للبيع في حي النرجس بالرياض، مساحة 220 متر، تشطيب سوبر لوكس"
    listing_b = "فيلا حديثة 5 غرف للإيجار في حي الملقا، مسبح خاص، حديقة واسعة"

    vec_a = await embedder.embed_query(listing_a)
    vec_b = await embedder.embed_query(listing_b)
    assert len(vec_a) == settings.embedding_dim, f"expected {settings.embedding_dim}-dim vector, got {len(vec_a)}"
    print(f"real fastembed vectors generated: dim={len(vec_a)}, provider={embedder.provider}")

    # Payload fields match what _format_property_listing() in retriever.py
    # actually reads (property_type/city/district/summary/...) — not an
    # arbitrary "text" field, so scenario B's context-formatting check below
    # exercises the real production formatter, not just the search call.
    await qdrant_mgr.upsert_property_listing(
        tenant_a, str(uuid.uuid4()), vec_a,
        {
            "status": "VERIFIED_ACTIVE", "text": listing_a, "summary": listing_a,
            "property_type": "apartment", "city": "الرياض", "district": "النرجس",
            "bedrooms": 3, "price_sar": 1_200_000, "area_sqm": 220,
        },
    )
    await qdrant_mgr.upsert_property_listing(
        tenant_b, str(uuid.uuid4()), vec_b,
        {
            "status": "VERIFIED_ACTIVE", "text": listing_b, "summary": listing_b,
            "property_type": "villa", "city": "الرياض", "district": "الملقا",
            "bedrooms": 5, "area_sqm": 400,
        },
    )
    print("real listings upserted into real, separate per-tenant Qdrant collections")

    # Query with something semantically close to BOTH listings (real estate,
    # Riyadh) so a leak would show up as a false-positive cross-tenant hit,
    # not merely "no results at all".
    query_vec = await embedder.embed_query("أبحث عن عقار سكني في الرياض")

    results_a = await qdrant_mgr.search_properties(tenant_a, query_vec, limit=5, score_threshold=0.0)
    results_b = await qdrant_mgr.search_properties(tenant_b, query_vec, limit=5, score_threshold=0.0)

    assert len(results_a) == 1, f"expected exactly tenant A's 1 listing, got {len(results_a)}"
    assert results_a[0].payload["text"] == listing_a
    assert len(results_b) == 1, f"expected exactly tenant B's 1 listing, got {len(results_b)}"
    assert results_b[0].payload["text"] == listing_b
    print("PASS: each tenant's search returned only its own listing — no cross-tenant leakage")

    # Direct proof a real similarity signal exists, not just structural
    # isolation: tenant A's own listing should score higher against its own
    # near-duplicate query than a completely unrelated one would.
    unrelated_vec = await embedder.embed_query("ما هي أفضل وصفة لعمل الكنافة؟")
    unrelated_results = await qdrant_mgr.search_properties(tenant_a, unrelated_vec, limit=5, score_threshold=0.0)
    assert unrelated_results[0].score < results_a[0].score, (
        "an unrelated query scored >= a relevant one — vectors may not carry real semantic signal"
    )
    print(
        f"PASS: real estate query score ({results_a[0].score:.3f}) > unrelated query score "
        f"({unrelated_results[0].score:.3f}) — vectors carry genuine semantic signal, not noise"
    )
    return tenant_a, tenant_b


async def scenario_b_real_context_retrieval(tenant_a: uuid.UUID) -> None:
    print("\n=== Scenario B: real RAGRetriever.get_rag_context() call ===")
    rag_retriever.configure()
    context = await rag_retriever.get_rag_context(
        tenant_id=tenant_a,
        user_message="أبحث عن شقة في حي النرجس",
    )
    assert context, "get_rag_context returned empty/falsy — retrieval produced nothing"
    text_repr = context if isinstance(context, str) else str(context)
    assert "النرجس" in text_repr or "شقة" in text_repr, (
        f"retrieved context doesn't mention the relevant listing content: {text_repr[:300]!r}"
    )
    print(f"PASS: real context retrieved via the production code path ({len(text_repr)} chars)")


async def cleanup(tenant_a: uuid.UUID, tenant_b: uuid.UUID) -> None:
    for t in (tenant_a, tenant_b):
        try:
            await qdrant_mgr._c.delete_collection(_tenant_collection(t))
        except Exception as exc:
            print(f"cleanup warning: could not delete collection for {t}: {exc}")
    print("cleanup: synthetic per-tenant Qdrant collections deleted")


async def main() -> None:
    _require_local()
    embedder.configure()
    await qdrant_mgr.start()
    tenant_a = tenant_b = None
    try:
        tenant_a, tenant_b = await scenario_a_tenant_isolation()
        await scenario_b_real_context_retrieval(tenant_a)
        print("\nALL SCENARIOS PASSED")
    finally:
        if tenant_a and tenant_b:
            await cleanup(tenant_a, tenant_b)
        await qdrant_mgr.stop()


if __name__ == "__main__":
    asyncio.run(main())
