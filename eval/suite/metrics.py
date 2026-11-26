"""The metric formulas of docs/a3/CONTRACTS.md. Pure functions, no network.

Hand-computed examples are in eval/tests/test_metrics.py. A metric that cannot be computed
is None, never 1.0 or 0.0.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def matches(chunk: dict, item: dict) -> bool:
    """A retrieved chunk matches a ground-truth item: same doc_id (and doc_version, if the
    item gives one), and the item's page lies in the chunk's page range."""
    if chunk.get("doc_id") != item["doc_id"]:
        return False
    if item.get("doc_version") and chunk.get("doc_version") != item["doc_version"]:
        return False
    start = chunk.get("page_start") or chunk.get("page")
    end = chunk.get("page_end") or start
    return start is not None and start <= item["page"] <= end


def recall_at_k(ranked: Sequence[dict], relevant: Sequence[dict], k: int) -> float | None:
    """Ground-truth items matched within the top k, divided by all items."""
    if not relevant:
        return None
    found = sum(1 for item in relevant if any(matches(c, item) for c in ranked[:k]))
    return found / len(relevant)


def reciprocal_rank(ranked: Sequence[dict], relevant: Sequence[dict]) -> float | None:
    """1 / rank of the first chunk that matches any item; 0 if none is in the list."""
    if not relevant:
        return None
    for rank, chunk in enumerate(ranked, 1):
        if any(matches(chunk, item) for item in relevant):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(ranked: Sequence[dict], relevant: Sequence[dict], k: int = 5) -> float | None:
    """Binary gain, given only to the first chunk that covers each item, so a second chunk
    of the same page adds nothing. The ideal ranking has min(items, k) gains at the top.

    A chunk that is the first to cover two items still gains 1 (binary gain).
    """
    if not relevant:
        return None
    uncovered = list(relevant)
    dcg = 0.0
    for rank, chunk in enumerate(ranked[:k], 1):
        still = [item for item in uncovered if not matches(chunk, item)]
        if len(still) < len(uncovered):
            dcg += 1.0 / math.log2(rank + 1)
        uncovered = still
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return dcg / ideal


def retrieval_scores(ranked: Sequence[dict], relevant: Sequence[dict]) -> dict:
    return {
        "recall_at_3": recall_at_k(ranked, relevant, 3),
        "recall_at_5": recall_at_k(ranked, relevant, 5),
        "reciprocal_rank": reciprocal_rank(ranked, relevant),
        "ndcg_at_5": ndcg_at_k(ranked, relevant, 5),
    }


def mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def difference(after: float | None, before: float | None) -> float | None:
    return None if after is None or before is None else after - before


def percentile(values: Sequence[float], percent: int) -> float | None:
    """Nearest rank: the value at position ceil(percent * n / 100) of the sorted list."""
    if not values:
        return None
    rank = max(1, math.ceil(percent * len(values) / 100))
    return sorted(values)[rank - 1]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    if len(a) != len(b) or norm == 0:
        raise ValueError("cosine needs two vectors of one length, neither all zeros")
    return sum(x * y for x, y in zip(a, b)) / norm


def max_abs_diff(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("the two vectors have different lengths")
    return max(abs(x - y) for x, y in zip(a, b))


def valid_citation_count(raw: Sequence[dict], context: Sequence[dict]) -> int:
    """How many of the model's RAW citations name a chunk that was sent to it.

    A citation matches on source and section exactly, as api/app/trace.py does it. The page
    is not compared: the prompt shows no page numbers.
    """
    sent = {(c.get("source"), c.get("section")) for c in context}
    return sum(1 for c in raw if (c.get("source"), c.get("section")) in sent)
