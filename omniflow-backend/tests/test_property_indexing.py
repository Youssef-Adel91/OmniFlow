"""Indexing: Qdrant lifecycle in the gateway, real error surfacing, and what the RAG search can see.

The RAG test uses the real QdrantManager against qdrant-client's in-memory engine and a deterministic
(hashing) embedder, so no network, model download or database is needed.
"""
import hashlib
import math
import unittest
import uuid
from unittest.mock import AsyncMock, PropertyMock, patch

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from src.ai_workers.rag_engine.embedder import embedder
from src.shared.db.models import PropertyListing
from src.shared.qdrant_client import client as qclient
from src.shared.qdrant_client.client import QdrantManager, _tenant_collection
from src.shared.services import vector_sync
from src.shared.services.vector_sync import IndexResult, describe_index_error


def hash_embed(text: str) -> list[float]:
    """Bag-of-words hashing embedding: same words -> same direction (cosine ~1), unrelated -> ~0."""
    vec = [0.0] * qclient.EMBEDDING_DIM
    for word in text.split():
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % len(vec)] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


async def fake_embed_batch(texts):
    return [hash_embed(t) for t in texts]


def listing(tenant, status, city="الرياض", district="النرجس", price=850000):
    return PropertyListing(
        listing_id=uuid.uuid4(), tenant_id=tenant, rega_ad_number=f"R-{uuid.uuid4().hex[:6]}", property_type="apartment",
        status=status, is_verified=False, city=city, district=district, price=price, area_sqm=180, bedrooms=3,
        bathrooms=2, description_ar=f"شقة مميزة {district}")


class QdrantLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_started_is_lazy_and_idempotent(self):
        mgr = QdrantManager()
        with patch.object(QdrantManager, "start", new_callable=AsyncMock) as start:
            start.side_effect = lambda: setattr(mgr, "_started", True)
            await mgr.ensure_started()
            await mgr.ensure_started()
        self.assertEqual(start.await_count, 1)

    async def test_sync_functions_start_qdrant_themselves(self):
        mgr = QdrantManager()
        tenant = uuid.uuid4()
        calls = []

        async def fake_start():
            calls.append("start")
            mgr._client, mgr._started = AsyncQdrantClient(":memory:"), True

        mgr.start = fake_start
        with patch.object(vector_sync, "qdrant_mgr", mgr):
            await vector_sync.delete_listings_from_qdrant([str(uuid.uuid4())], tenant)
            await vector_sync.delete_listing_from_qdrant(str(uuid.uuid4()), tenant)
        self.assertEqual(calls, ["start"])               # started once on first use, never "QdrantManager not started"

    async def test_the_real_cause_is_returned_not_swallowed(self):
        mgr = QdrantManager()                            # never started and cannot start
        mgr.start = AsyncMock(side_effect=ConnectionError("qdrant:6333 refused"))
        with patch.object(vector_sync, "qdrant_mgr", mgr), \
             patch.object(type(embedder), "is_configured", new_callable=PropertyMock, return_value=True), \
             patch.object(embedder, "embed_batch", fake_embed_batch):
            res = await vector_sync.sync_listings_batch([listing(uuid.uuid4(), "VERIFIED_ACTIVE")], uuid.uuid4())
        self.assertEqual(res.ids, [])
        self.assertEqual(res.error, "ConnectionError: qdrant:6333 refused")

    async def test_embedder_failure_is_reported_and_configure_runs_off_the_loop(self):
        threads = []
        import threading

        def configure():
            threads.append(threading.current_thread() is threading.main_thread())
            raise RuntimeError("model download failed")

        with patch.object(type(embedder), "is_configured", new_callable=PropertyMock, return_value=False), \
             patch.object(embedder, "configure", configure):
            res = await vector_sync.sync_listings_batch([listing(uuid.uuid4(), "VERIFIED_ACTIVE")], uuid.uuid4())
        self.assertEqual(res.error, "RuntimeError: model download failed")
        self.assertEqual(threads, [False])

    def test_error_text_is_bounded_and_never_empty(self):
        self.assertEqual(describe_index_error(ValueError("x" * 1000)).startswith("ValueError: "), True)
        self.assertLessEqual(len(describe_index_error(ValueError("x" * 1000))), 300)
        self.assertEqual(describe_index_error(RuntimeError()), "RuntimeError")
        self.assertEqual(IndexResult([]).error, None)

    async def test_gateway_lifespan_starts_qdrant_and_survives_its_failure(self):
        from src.gateway import main as gw

        started, stopped = [], []

        async def ok_start():
            started.append(1)

        for start in (ok_start, AsyncMock(side_effect=ConnectionError("down"))):
            with patch.object(gw.kafka_producer, "start", AsyncMock()), patch.object(gw.kafka_producer, "stop", AsyncMock()), \
                 patch.object(gw.redis_mgr, "start", AsyncMock()), patch.object(gw.redis_mgr, "stop", AsyncMock()), \
                 patch.object(gw, "setup_opentelemetry"), patch.object(gw, "setup_sentry"), \
                 patch("src.shared.services.property_import.resume_stale_jobs", AsyncMock(return_value=0)), \
                 patch.object(gw.settings, "app_env", "development", create=True), \
                 patch.object(qclient.qdrant_mgr, "start", start), \
                 patch.object(qclient.qdrant_mgr, "stop", AsyncMock(side_effect=lambda: stopped.append(1))):
                async with gw.lifespan(gw.create_app()):
                    pass
        self.assertEqual((started, stopped), ([1], [1, 1]))


class RagVisibilityTests(unittest.IsolatedAsyncioTestCase):
    """An indexed + approved listing is found by the RAG search; "pending verification" is not."""

    async def asyncSetUp(self):
        self.mgr = QdrantManager()
        self.mgr._client, self.mgr._started = AsyncQdrantClient(":memory:"), True
        self.t1, self.t2 = uuid.uuid4(), uuid.uuid4()
        for t in (self.t1, self.t2):                     # in-memory mode can't auto-create (get_collection raises ValueError)
            await self.mgr._c.create_collection(
                _tenant_collection(t), vectors_config=qmodels.VectorParams(size=qclient.EMBEDDING_DIM, distance=qmodels.Distance.COSINE))
        for p in (patch.object(vector_sync, "qdrant_mgr", self.mgr),
                  patch.object(type(embedder), "is_configured", new_callable=PropertyMock, return_value=True),
                  patch.object(embedder, "embed_batch", fake_embed_batch)):
            p.start()
            self.addCleanup(p.stop)

    async def search(self, tenant, like):
        vec = hash_embed(vector_sync._build_listing_text(like))
        hits = await self.mgr.search_properties(tenant, vec, limit=10, score_threshold=0.3)
        return {h.id for h in hits}

    async def test_only_indexed_and_approved_listings_are_visible_to_the_bot(self):
        approved = listing(self.t1, "VERIFIED_ACTIVE")
        pending = listing(self.t1, "PENDING_VERIFICATION", city="جدة", district="الشاطئ")
        other_tenant = listing(self.t2, "VERIFIED_ACTIVE", city="الدمام", district="الفيصلية")
        for items, t in (([approved, pending], self.t1), ([other_tenant], self.t2)):
            res = await vector_sync.sync_listings_batch(items, t)
            self.assertEqual((sorted(res.ids), res.error), (sorted(str(i.listing_id) for i in items), None))

        self.assertEqual(await self.search(self.t1, approved), {str(approved.listing_id)})
        self.assertNotIn(str(pending.listing_id), await self.search(self.t1, pending))   # indexed, but not approved -> invisible
        self.assertNotIn(str(other_tenant.listing_id), await self.search(self.t1, other_tenant))   # other tenant -> invisible
        self.assertEqual(await self.search(self.t2, other_tenant), {str(other_tenant.listing_id)})

        pending.status = "VERIFIED_ACTIVE"                                       # approved (e.g. activation) + re-indexed
        res = await vector_sync.sync_listings_batch([pending], self.t1)
        self.assertEqual(res.error, None)
        self.assertIn(str(pending.listing_id), await self.search(self.t1, pending))

        pending.status = "SUSPENDED"
        await vector_sync.sync_listings_batch([pending], self.t1)
        self.assertNotIn(str(pending.listing_id), await self.search(self.t1, pending))


if __name__ == "__main__":
    unittest.main()
