"""Compare embedding models on Arabic property search: where does the RIGHT listing land in the ranking, and what does each model cost?

    python -m scripts.eval_embeddings                       # the candidate models below
    python -m scripts.eval_embeddings --models BAAI/bge-small-en-v1.5 intfloat/multilingual-e5-large

Needs the models' weights (fastembed downloads them from Hugging Face on first use: the machine must be able to reach huggingface.co). Nothing here touches Qdrant or PostgreSQL:
a fixed in-memory corpus (listing texts built exactly like `vector_sync._build_listing_text`) and a fixed set of customer requests (the golden scenarios + the live-test sentence).

Reported per model: dimension, weights size, load time, indexing speed (listings/s), query latency (ms), peak memory (MB, process RSS), and for the requests:
the 1-based RANK of the correct listing among ALL listings with NO filter (what the old retrieval depended on), the mean rank, and the top-1 / top-3 / top-5 rates.
With the payload filters of this PR the correct listing is offerable by construction; this table measures how much the EMBEDDING adds on top (ties, requests with no hard needs).
"""
from __future__ import annotations

import argparse
import resource
import statistics
import sys
import time

import numpy as np

CANDIDATES = [
    "BAAI/bge-small-en-v1.5",                                              # the current default: English
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",        # 384-d, multilingual, small
    "intfloat/multilingual-e5-large",                                      # 1024-d, multilingual, large
]

TYPE_AR = {"apartment": "شقة", "villa": "فيلا", "land": "أرض", "office": "مكتب"}


def text(typ, district, beds, price, area):
    parts = [TYPE_AR[typ], f"في الرياض حي {district}"]
    if beds:
        parts.append(f"{beds} غرف نوم")
    parts += [f"مساحة {area}م²", f"السعر {price:,.0f} ريال سعودي"]
    return " - ".join(parts)


# (id, type, district, bedrooms, price, area)
CORPUS = [
    ("apt-narjis-3", "apartment", "النرجس", 3, 850_000, 140), ("apt-narjis-4", "apartment", "النرجس", 4, 1_900_000, 220), ("apt-narjis-2", "apartment", "النرجس", 2, 720_000, 105),
    ("apt-yasmin-2", "apartment", "الياسمين", 2, 620_000, 95), ("apt-yasmin-3", "apartment", "الياسمين", 3, 980_000, 150), ("apt-malqa-3", "apartment", "الملقا", 3, 900_000, 145),
    ("apt-olaya-1", "apartment", "العليا", 1, 450_000, 70), ("apt-hittin-3", "apartment", "حطين", 3, 1_250_000, 160),
    ("villa-narjis-5", "villa", "النرجس", 5, 2_400_000, 420), ("villa-narjis-6", "villa", "النرجس", 6, 2_600_000, 480), ("villa-yasmin-4", "villa", "الياسمين", 4, 1_950_000, 350),
    ("villa-malqa-5", "villa", "الملقا", 5, 3_100_000, 450), ("villa-hittin-4", "villa", "حطين", 4, 2_250_000, 380),
    ("land-narjis", "land", "النرجس", 0, 700_000, 600), ("land-yasmin", "land", "الياسمين", 0, 540_000, 450), ("land-malqa", "land", "الملقا", 0, 1_300_000, 750),
    ("office-olaya", "office", "العليا", 0, 1_100_000, 180), ("office-malqa", "office", "الملقا", 0, 950_000, 150),
]

# (customer request, ids that are the right answer)
REQUESTS = [
    ("ابغى شقة ٣ غرف في النرجس تحت مليون كاش", {"apt-narjis-3"}),
    ("أبغى شقة ٣ غرف في النرجس بسعر مناسب", {"apt-narjis-3"}),
    ("ابغى شقة قريبة من الملقا ميزانيتي 900 ألف", {"apt-malqa-3"}),
    ("أبحث عن فيلا في الياسمين", {"villa-yasmin-4"}),
    ("عندكم فيلا في النرجس؟", {"villa-narjis-5", "villa-narjis-6"}),
    ("شقة غرفتين في الياسمين تحت 700 ألف", {"apt-yasmin-2"}),
    ("ابغى ارض في النرجس", {"land-narjis"}),
    ("أحتاج مكتب في العليا", {"office-olaya"}),
    ("شقة غرفة وحدة في العليا", {"apt-olaya-1"}),
    ("I am looking for a 3-bedroom apartment in Al Narjis under one million", {"apt-narjis-3"}),
]


def peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0      # Linux: KiB


def evaluate(model_name: str) -> dict:
    from fastembed import TextEmbedding

    e5 = "e5" in model_name.lower()
    t0 = time.perf_counter()
    model = TextEmbedding(model_name=model_name)
    load_s = time.perf_counter() - t0
    docs = [text(t, d, b, p, a) for _id, t, d, b, p, a in CORPUS]
    t0 = time.perf_counter()
    dvec = np.array([v for v in model.embed([f"passage: {d}" if e5 else d for d in docs])])
    index_s = time.perf_counter() - t0
    qlat, ranks = [], []
    for request, right in REQUESTS:
        t0 = time.perf_counter()
        qv = np.array(next(iter(model.embed([f"query: {request}" if e5 else request]))))
        qlat.append((time.perf_counter() - t0) * 1000)
        sims = dvec @ qv / (np.linalg.norm(dvec, axis=1) * np.linalg.norm(qv))
        order = [CORPUS[i][0] for i in np.argsort(-sims)]
        ranks.append(min(order.index(r) + 1 for r in right))
    return {"model": model_name, "dim": int(dvec.shape[1]), "load_s": load_s, "docs_per_s": len(docs) / index_s, "query_ms": statistics.mean(qlat), "peak_mb": peak_mb(),
            "ranks": ranks, "mean_rank": statistics.mean(ranks), "top1": sum(r == 1 for r in ranks) / len(ranks), "top3": sum(r <= 3 for r in ranks) / len(ranks), "top5": sum(r <= 5 for r in ranks) / len(ranks)}


def size_gb(model_name: str) -> float | None:
    from fastembed import TextEmbedding

    for m in TextEmbedding.list_supported_models():
        if m["model"] == model_name:
            return m.get("size_in_GB")
    return None


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", nargs="*", default=CANDIDATES)
    args = ap.parse_args(argv)
    rows = []
    for name in args.models:
        print(f"-- {name}", flush=True)
        try:
            rows.append(evaluate(name) | {"size_gb": size_gb(name)})
        except Exception as exc:  # noqa: BLE001 — report and continue with the next model
            print(f"   FAILED: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
    print("\n| model | dim | weights GB | load s | docs/s | query ms | peak MB | mean rank | top-1 | top-3 | top-5 |\n|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['model']} | {r['dim']} | {r['size_gb']} | {r['load_s']:.1f} | {r['docs_per_s']:.1f} | {r['query_ms']:.0f} | {r['peak_mb']:.0f} | {r['mean_rank']:.2f} | {r['top1']:.0%} | {r['top3']:.0%} | {r['top5']:.0%} |")
    print("\nper-request rank of the correct listing (1 = best):")
    for r in rows:
        print(f"  {r['model']}: {r['ranks']}")
    print("\nrequests:")
    for i, (request, right) in enumerate(REQUESTS):
        print(f"  [{i}] {request}  -> {sorted(right)}")
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
