"""Payload filters BEFORE the limit + deterministic re-rank: the right listing is in the top 3 even when the embedding ranks it LAST (an English model on Arabic text did exactly that)."""
import asyncio
import unittest
import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from src.ai_workers.rag_engine import ranking
from src.ai_workers.rag_engine.retriever import RAGRetriever
from src.sales import qualification as q
from src.shared.qdrant_client import client as qclient
from src.shared.qdrant_client.client import QdrantManager

REQUEST = "ابغى شقة ٣ غرف في النرجس تحت مليون كاش"
DIM = 8


def vec(i, j=None):
    v = [0.0] * DIM
    v[i] = 1.0
    if j is not None:
        v[j] = 0.2
    return v


QUERY = vec(0)                                   # the "customer" direction


def payload(tenant, lid, typ, district, price, beds, ad):
    return {"tenant_id": str(tenant), "listing_id": lid, "rega_ad_number": ad, "property_type": typ, "price_sar": float(price), "bedrooms": beds, "city": "الرياض",
            "district": district, "status": "VERIFIED_ACTIVE", "summary": f"{typ} {district} {price}"}


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant = uuid.uuid4()
        self.mgr = QdrantManager()
        self.mgr._client = AsyncQdrantClient(":memory:")
        self.mgr._started = True
        self.collection = qclient._tenant_collection(self.tenant)
        await self.mgr._client.create_collection(self.collection, vectors_config=qm.VectorParams(size=DIM, distance=qm.Distance.COSINE))
        # The adversarial index: 9 listings the embedding LOVES (similar to the query) but that are wrong for the customer (villas, other districts, over budget),
        # and the one right apartment whose vector points the OTHER way (rank 10 of 10 unfiltered).
        rows = [(1, payload(self.tenant, "a-right", "apartment", "النرجس", 850_000, 3, "7100000001"), [-1.0] + [0.0] * (DIM - 1))]
        wrong = [("villa", "النرجس", 2_400_000, 5), ("villa", "النرجس", 2_600_000, 6), ("villa", "الياسمين", 1_950_000, 4), ("apartment", "الياسمين", 620_000, 2),
                 ("apartment", "النرجس", 1_900_000, 3), ("land", "النرجس", 700_000, None), ("villa", "النرجس", 900_000, 4), ("apartment", "الملقا", 900_000, 3), ("apartment", "النرجس", 2_000_000, 4)]
        for k, (typ, district, price, beds) in enumerate(wrong, start=2):
            rows.append((k, payload(self.tenant, f"w{k}", typ, district, price, beds, f"71000000{k:02d}"), vec(0, k % (DIM - 1) + 1)))
        await self.mgr._client.upsert(self.collection, points=[qm.PointStruct(id=pid, vector=v, payload=p) for pid, p, v in rows])
        self.constraints = ranking.property_constraints(q.qualify([REQUEST], known_districts=["النرجس", "الياسمين"]))


class FiltersBeforeTheLimit(Base):
    async def test_the_old_unfiltered_top_five_misses_the_apartment(self):
        hits = await self.mgr.search_properties(self.tenant, QUERY, limit=5, score_threshold=0.0)
        self.assertNotIn("a-right", [h.payload["listing_id"] for h in hits])          # the bug this PR fixes

    async def test_constraints_come_from_the_customers_request(self):
        self.assertEqual(self.constraints, {"property_type": "apartment", "budget_max": 1_000_000.0, "district": "النرجس", "rooms": 3})
        args = ranking.qdrant_filter_args(self.constraints)
        self.assertAlmostEqual(args["price_max"], 1_100_000.0)                         # budget + 10 % (OVER_BUDGET_FACTOR), never more
        self.assertEqual(args["property_type"], "apartment")
        self.assertIn("النرجس", args["district_any"])

    async def test_with_filters_the_apartment_is_first_even_though_the_embedding_ranks_it_last(self):
        retriever = RAGRetriever(_embedder=None, _qdrant=self.mgr)
        hits = await retriever._search_properties_safe(self.tenant, QUERY, self.constraints)
        self.assertEqual(hits[0].payload["listing_id"], "a-right")
        self.assertIn("a-right", [h.payload["listing_id"] for h in hits[:3]])
        for h in hits:                                                                   # nothing the customer cannot be offered
            self.assertEqual(h.payload["property_type"], "apartment")
            self.assertLessEqual(h.payload["price_sar"], 1_100_000)

    async def test_it_is_the_same_every_time(self):
        retriever = RAGRetriever(_embedder=None, _qdrant=self.mgr)
        runs = [[h.payload["listing_id"] for h in await retriever._search_properties_safe(self.tenant, QUERY, self.constraints)] for _ in range(20)]
        self.assertEqual(len({tuple(r) for r in runs}), 1)

    async def test_the_filter_never_leaks_another_tenants_listing(self):
        other = uuid.uuid4()
        await self.mgr._client.upsert(self.collection, points=[qm.PointStruct(id=99, vector=vec(0), payload=payload(other, "foreign", "apartment", "النرجس", 800_000, 3, "7199999999"))])
        retriever = RAGRetriever(_embedder=None, _qdrant=self.mgr)
        hits = await retriever._search_properties_safe(self.tenant, QUERY, self.constraints)
        self.assertNotIn("foreign", [h.payload["listing_id"] for h in hits])

    async def test_no_constraints_keeps_the_old_unfiltered_behaviour(self):
        retriever = RAGRetriever(_embedder=None, _qdrant=self.mgr)
        hits = await retriever._search_properties_safe(self.tenant, QUERY, None)
        self.assertLessEqual(len(hits), 5)


class Rerank(unittest.TestCase):
    class H:
        def __init__(self, lid, price, district, beds, score):
            self.payload = {"listing_id": lid, "price_sar": price, "district": district, "bedrooms": beds}
            self.score = score

    def test_within_budget_then_district_then_rooms_then_closest_to_the_ceiling_then_score(self):
        c = {"budget_max": 1_000_000.0, "district": "النرجس", "rooms": 3}
        hits = [self.H("over", 1_050_000, "النرجس", 3, 0.99), self.H("other_district", 900_000, "الياسمين", 3, 0.9), self.H("other_rooms", 900_000, "النرجس", 2, 0.9),
                self.H("cheap", 500_000, "النرجس", 3, 0.5), self.H("best", 950_000, "النرجس", 3, 0.1)]
        self.assertEqual([h.payload["listing_id"] for h in ranking.rerank(hits, c)], ["best", "cheap", "other_rooms", "other_district", "over"])

    def test_ties_break_by_score_then_id_and_input_order_does_not_matter(self):
        c = {"budget_max": 1_000_000.0}
        hits = [self.H("b", 900_000, "x", 3, 0.5), self.H("a", 900_000, "x", 3, 0.5), self.H("c", 900_000, "x", 3, 0.7)]
        self.assertEqual([h.payload["listing_id"] for h in ranking.rerank(hits, c)], ["c", "a", "b"])
        self.assertEqual([h.payload["listing_id"] for h in ranking.rerank(list(reversed(hits)), c)], ["c", "a", "b"])

    def test_drop_contradicting_allows_only_the_defined_margin(self):
        c = {"property_type": "apartment", "budget_max": 1_000_000.0}
        ok = self.H("ok", 1_090_000, "x", 3, 0.1)
        ok.payload["property_type"] = "apartment"
        too_dear = self.H("dear", 1_150_000, "x", 3, 0.9)
        too_dear.payload["property_type"] = "apartment"
        villa = self.H("villa", 900_000, "x", 3, 0.9)
        villa.payload["property_type"] = "villa"
        self.assertEqual([h.payload["listing_id"] for h in ranking.drop_contradicting([ok, too_dear, villa], c)], ["ok"])


class MatcherFirstInTheContext(Base):
    class FakeEmbedder:
        is_configured = True

        def __init__(self, fail=False):
            self.fail = fail

        async def embed_query(self, text):
            if self.fail:
                raise RuntimeError("embedding down")
            return QUERY

    CARD = {"listing_id": "a-right", "type_ar": "شقة", "district": "النرجس", "price": 850_000.0, "area_sqm": None, "bedrooms": 3}

    async def test_the_database_match_is_first_and_nothing_contradicts_it(self):
        retriever = RAGRetriever(_embedder=self.FakeEmbedder(), _qdrant=self.mgr)
        ctx = await retriever.get_rag_context(self.tenant, REQUEST, constraints=self.constraints, priority_listings=[self.CARD])
        self.assertTrue(ctx.startswith("## العقارات المتاحة المطابقة لطلبك (من قاعدة بيانات الشركة"))
        self.assertIn("شقة في حي النرجس، 850,000 ريال", ctx[:300])                       # the first listing line, right under the heading
        self.assertNotIn("2,400,000", ctx)                                                 # the semantic part (filtered) holds only offerable listings, none of the villas
        self.assertNotIn("2,600,000", ctx)

    async def test_the_match_survives_an_embedding_failure(self):
        retriever = RAGRetriever(_embedder=self.FakeEmbedder(fail=True), _qdrant=self.mgr)
        ctx = await retriever.get_rag_context(self.tenant, REQUEST, constraints=self.constraints, priority_listings=[self.CARD])
        self.assertIn("850,000", ctx)

    async def test_without_a_match_and_without_retrieval_there_is_no_context(self):
        retriever = RAGRetriever(_embedder=self.FakeEmbedder(fail=True), _qdrant=self.mgr)
        self.assertIsNone(await retriever.get_rag_context(self.tenant, REQUEST))


def _cached_model(name):
    try:
        from fastembed import TextEmbedding
        return TextEmbedding(model_name=name, local_files_only=True)
    except Exception:                                                                   # not downloaded here: the real-model test is skipped, the filter tests above still prove the guarantee
        return None


class RealEmbeddingTop3(unittest.IsolatedAsyncioTestCase):
    """With a REAL multilingual embedding model available offline, the apartment is in the top 3 of a filtered search for the customer's actual sentence."""
    MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

    async def test_the_apartment_is_in_the_top_three(self):
        model = _cached_model(self.MODEL)
        if model is None:
            self.skipTest(f"{self.MODEL} is not downloaded on this machine")
        tenant = uuid.uuid4()
        mgr = QdrantManager()
        mgr._client, mgr._started = AsyncQdrantClient(":memory:"), True
        listings = [("a-right", "apartment", "النرجس", 850_000, 3, "شقة في الرياض حي النرجس - 3 غرف نوم - السعر 850,000 ريال سعودي"),
                    ("v1", "villa", "النرجس", 2_400_000, 5, "فيلا في الرياض حي النرجس - 5 غرف نوم - السعر 2,400,000 ريال سعودي"),
                    ("v2", "villa", "النرجس", 2_600_000, 6, "فيلا في الرياض حي النرجس - 6 غرف نوم - السعر 2,600,000 ريال سعودي"),
                    ("a2", "apartment", "الياسمين", 620_000, 2, "شقة في الرياض حي الياسمين - 2 غرف نوم - السعر 620,000 ريال سعودي"),
                    ("a3", "apartment", "النرجس", 1_900_000, 4, "شقة في الرياض حي النرجس - 4 غرف نوم - السعر 1,900,000 ريال سعودي")]
        vectors = [v.tolist() for v in model.embed([t for *_, t in listings])]
        coll = qclient._tenant_collection(tenant)
        await mgr._client.create_collection(coll, vectors_config=qm.VectorParams(size=len(vectors[0]), distance=qm.Distance.COSINE))
        await mgr._client.upsert(coll, points=[qm.PointStruct(id=i + 1, vector=v, payload=payload(tenant, lid, typ, d, price, beds, f"71{i:08d}"))
                                               for i, ((lid, typ, d, price, beds, _t), v) in enumerate(zip(listings, vectors))])
        c = ranking.property_constraints(q.qualify([REQUEST], known_districts=["النرجس", "الياسمين"]))
        hits = await RAGRetriever(_embedder=None, _qdrant=mgr)._search_properties_safe(tenant, next(iter(model.embed([REQUEST]))).tolist(), c)
        self.assertIn("a-right", [h.payload["listing_id"] for h in hits[:3]])


if __name__ == "__main__":
    unittest.main()
