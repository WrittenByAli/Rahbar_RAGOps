"""Component level: retrieval on its own, through POST /api/retrieve.

Metrics use the ranked list before the relevance floor. The view after the floor is
reported beside it. The noise test adds seeded typos to the queries (decision D-08), so the
vector store is never written to.
"""

from __future__ import annotations

import random

import httpx

from . import metrics
from .common import post_json

METRIC_KEYS = ("recall_at_3", "recall_at_5", "mrr", "ndcg_at_5")
TYPO_SHARE = 0.3        # share of the long words of a question that get one typo
LIMITATION = ("HTML and Markdown documents have a single page, so for them page recall is "
              "only document recall.")


def add_typos(question: str, seed: int, question_id: str) -> str:
    """The same question with typos. The same seed and id always give the same result.

    About TYPO_SHARE of the words with 4 or more letters (at least one) get one typo:
    two letters swapped, a letter dropped, or a letter typed twice. Numbers and short
    words are left alone.
    """
    rng = random.Random(f"{seed}:{question_id}")
    words = question.split(" ")
    long_words = [i for i, w in enumerate(words) if len(w) >= 4 and w.isalpha()]
    if not long_words:      # every long word has punctuation or digits attached
        long_words = [i for i, w in enumerate(words) if sum(c.isalpha() for c in w) >= 4]
    if not long_words:
        return question
    count = max(1, round(TYPO_SHARE * len(long_words)))
    for index in sorted(rng.sample(long_words, count)):
        words[index] = _typo(words[index], rng)
    return " ".join(words)


def _typo(word: str, rng: random.Random) -> str:
    # Position 0 is kept: people rarely mistype the first letter.
    places = [i for i in range(1, len(word)) if word[i].isalpha()]
    swaps = [i for i in places if i + 1 < len(word) and word[i + 1].isalpha()
             and word[i] != word[i + 1]]
    kind = rng.choice(["swap", "drop", "double"] if swaps else ["drop", "double"])
    if kind == "swap":
        i = rng.choice(swaps)
        return word[:i] + word[i + 1] + word[i] + word[i + 2:]
    i = rng.choice(places)
    if kind == "drop":
        return word[:i] + word[i + 1:]
    return word[:i] + word[i] + word[i:]


def retrieve(client: httpx.Client, target: str, question: str, depth: int) -> dict:
    return post_json(client, f"{target}/api/retrieve", {"question": question, "k": depth})


def aggregate(scores: list[dict]) -> dict:
    """Averages over questions. With no question every metric is None."""
    values = {key: metrics.mean([s["reciprocal_rank" if key == "mrr" else key] for s in scores])
              for key in METRIC_KEYS}
    return {**values, "n_questions": len(scores)}


def run_component(client: httpx.Client, target: str, golden: list[dict], seed: int,
                  depth: int) -> tuple[dict, dict[str, dict], list[dict]]:
    """Returns the component block, per-question details, and the raw responses (for the
    consistency check of the whole run).

    If any request fails, the averages are None: an average over only the questions that
    happen to work would look better than the system is.
    """
    answerable = [q for q in golden if q["category"] == "answerable"]
    clean, floored, noisy, subset = [], [], [], []
    details: dict[str, dict] = {}
    responses: list[dict] = []
    errors: list[dict] = []
    for question in answerable:
        qid = question["id"]
        noisy_text = add_typos(question["question"], seed, qid)
        try:
            plain = retrieve(client, target, question["question"], depth)
            with_typos = retrieve(client, target, noisy_text, depth)
            ranked, noisy_ranked = plain["retrieved"], with_typos["retrieved"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            errors.append({"id": qid, "error": type(exc).__name__})
            continue
        responses += [plain, with_typos]
        scores = metrics.retrieval_scores(ranked, question["relevant"])
        noisy_scores = metrics.retrieval_scores(noisy_ranked, question["relevant"])
        clean.append(scores)
        noisy.append(noisy_scores)
        floored.append(metrics.retrieval_scores(
            [c for c in ranked if c.get("kept")], question["relevant"]))
        if question.get("requires_batch") == "A":
            subset.append(scores)
        details[qid] = {
            "metrics": scores,
            "noisy_question": noisy_text,
            "noisy_metrics": noisy_scores,
            "top5": [{key: c.get(key) for key in
                      ("chunk_id", "doc_id", "doc_version", "page_start", "page_end", "score")}
                     for c in ranked[:5]],
        }

    def block(scores: list[dict]) -> dict:
        if errors:
            return {**dict.fromkeys(METRIC_KEYS), "n_questions": len(scores)}
        return aggregate(scores)

    clean_block, noisy_block = block(clean), block(noisy)
    result = {
        **clean_block,
        "n_questions": len(answerable),
        "list_depth": depth,
        "errors": errors,
        "after_floor": block(floored),
        # Only the answerable questions marked requires_batch "A", so that a run before
        # Batch B and a run after it can be compared on the same questions.
        "corpus_a_subset": block(subset),
        "noise": {
            "method": "typos",
            "seed": seed,
            "word_share": TYPO_SHARE,
            "noisy": noisy_block,
            "delta": {key: metrics.difference(noisy_block[key], clean_block[key])
                      for key in METRIC_KEYS},
            "delta_definition": "noisy minus clean: a negative value means typos made it worse",
        },
        "limitation": LIMITATION,
    }
    return result, details, responses
