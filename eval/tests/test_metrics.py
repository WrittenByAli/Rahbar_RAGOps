"""Every expected number here is worked out by hand in the comment above it."""

import math

import pytest
from suite import metrics

RELEVANT = [{"doc_id": "a", "page": 3}, {"doc_id": "b", "page": 1}]


def chunk(doc_id, start, end=None, version="v1"):
    return {"doc_id": doc_id, "doc_version": version, "page_start": start,
            "page_end": end or start}


# rank 1: wrong document, ranks 2 and 3: two chunks of a p3, rank 4: b p1, rank 5: wrong page
RANKED = [chunk("x", 1), chunk("a", 3), chunk("a", 2, 4), chunk("b", 1), chunk("a", 9)]


def test_match_needs_document_version_and_page_range():
    item = {"doc_id": "a", "doc_version": "v2", "page": 7}
    assert metrics.matches(chunk("a", 6, 8, "v2"), item)
    assert not metrics.matches(chunk("a", 6, 8, "v1"), item)
    assert not metrics.matches(chunk("a", 8, 9, "v2"), item)
    assert not metrics.matches(chunk("b", 7, 7, "v2"), item)
    # no page range: the chunk's page is used; no page at all never matches
    assert metrics.matches({"doc_id": "a", "doc_version": "v2", "page": 7}, item)
    assert not metrics.matches({"doc_id": "a", "doc_version": "v2"}, item)


def test_recall_hand_computed():
    # top 3 holds a p3 only: 1 of 2. Top 5 holds both: 2 of 2.
    assert metrics.recall_at_k(RANKED, RELEVANT, 3) == 0.5
    assert metrics.recall_at_k(RANKED, RELEVANT, 5) == 1.0
    assert metrics.recall_at_k([], RELEVANT, 5) == 0.0
    assert metrics.recall_at_k(RANKED, [], 5) is None


def test_mrr_hand_computed():
    # first match at rank 2: 1/2. No match in the list: 0. Mean of the two: 0.25.
    first = metrics.reciprocal_rank(RANKED, RELEVANT)
    second = metrics.reciprocal_rank([chunk("x", 1)], RELEVANT)
    assert (first, second) == (0.5, 0.0)
    assert metrics.mean([first, second]) == 0.25
    assert metrics.mean([]) is None


def test_ndcg_hand_computed():
    # gains at ranks 2 and 4: 1/log2(3) + 1/log2(5) = 0.63093 + 0.43068 = 1.06161
    # ideal, 2 items at ranks 1 and 2: 1 + 0.63093 = 1.63093.  1.06161/1.63093 = 0.65092
    assert metrics.ndcg_at_k(RANKED, RELEVANT, 5) == pytest.approx(0.65092, abs=1e-5)


def test_ndcg_ideal_is_capped_at_five_items():
    # six items, the top 5 each cover a new one: the best possible, so exactly 1.0
    items = [{"doc_id": "a", "page": page} for page in range(1, 7)]
    ranked = [chunk("a", page) for page in range(1, 6)]
    assert metrics.ndcg_at_k(ranked, items, 5) == pytest.approx(1.0)


def test_two_chunks_same_gold_page_count_once():
    one_item = [{"doc_id": "a", "page": 3}]
    # both chunks cover the only item: recall stays 1, and only rank 1 gets a gain
    both = [chunk("a", 3), chunk("a", 3)]
    assert metrics.recall_at_k(both, one_item, 5) == 1.0
    assert metrics.ndcg_at_k(both, one_item, 5) == 1.0
    # with two items, a repeat at rank 2 adds nothing: 1 / 1.63093 = 0.61315
    assert metrics.ndcg_at_k(both, RELEVANT, 5) == pytest.approx(
        1 / (1 + 1 / math.log2(3)))
    assert metrics.recall_at_k(both, RELEVANT, 5) == 0.5


def test_percentile_nearest_rank():
    values = [50, 10, 40, 20, 30]
    # n=5: p50 is rank ceil(2.5)=3, p95 is rank ceil(4.75)=5
    assert metrics.percentile(values, 50) == 30
    assert metrics.percentile(values, 95) == 50
    # n=20: p95 is rank 19, not the maximum
    assert metrics.percentile(list(range(1, 21)), 95) == 19
    assert metrics.percentile([], 95) is None


def test_cosine_and_max_abs_diff():
    # (1,0) and (1,1): 1 / sqrt(2)
    assert metrics.cosine([1, 0], [1, 1]) == pytest.approx(0.707107, abs=1e-6)
    assert metrics.max_abs_diff([0.1, 0.5], [0.1, 0.2]) == pytest.approx(0.3)
    with pytest.raises(ValueError):
        metrics.cosine([0, 0], [1, 1])


def test_citation_count_uses_source_and_section_not_page():
    context = [{"source": "s.pdf", "section": "1", "page": 2}]
    raw = [{"source": "s.pdf", "section": "1", "page": 99},
           {"source": "s.pdf", "section": "2", "page": 2}]
    assert metrics.valid_citation_count(raw, context) == 1
    assert metrics.ratio(0, 0) is None


def test_nothing_after_the_cut_off_counts():
    # the only match is at rank 6: it counts for MRR (depth is the list) but not at k=5
    ranked = [chunk("x", n) for n in range(1, 6)] + [chunk("a", 3)]
    one_item = [{"doc_id": "a", "page": 3}]
    assert metrics.ndcg_at_k(ranked, one_item, 5) == 0.0
    assert metrics.recall_at_k(ranked, one_item, 5) == 0.0
    assert metrics.reciprocal_rank(ranked, one_item) == pytest.approx(1 / 6)
