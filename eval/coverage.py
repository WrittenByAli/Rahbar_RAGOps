"""Query coverage: how close is each live query to some golden question? (E2, E4.2)

    python eval/coverage.py --label before [--config eval/coverage_config.yaml]

The threshold, its justification and the required share are read from the config file only.
Write it and commit it BEFORE the first coverage run, choosing the threshold from the
module-level similarity numbers, never from a coverage share you have seen:

    threshold_similarity: <number above 0, at most 1>
    threshold_justification: <why, written by you>
    required_share: <number above 0, at most 1>

The result records git_sha and config_sha256, so the order can be checked in git history.

For each live query the most similar golden question is found by embedding similarity. A
query is covered when that similarity is at or above the threshold.

Uncovered queries are grouped by similarity to each other. The groups get no name: a person
names them, with --topic-names {"c1": "hostel life", ...} (a JSON file) on a second run.

Exit code 0: share covered is at or above the required share. 1: below it. 2: error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

import httpx
import yaml

from suite.common import (
    EVAL_DIR,
    SuiteError,
    embed,
    git_sha,
    label_part,
    load_json,
    load_jsonl,
    sha256_file,
    stamp,
    use_utf8_output,
    utc_now,
    write_new_json,
)
from suite.metrics import cosine, ratio

EXIT_COVERED, EXIT_BELOW, EXIT_ERROR = 0, 1, 2
EPSILON = 1e-9
CONFIG_KEYS = ("threshold_similarity", "threshold_justification", "required_share")


def load_config(path: str) -> dict:
    try:
        with open(path, encoding="utf-8-sig") as handle:
            config = yaml.safe_load(handle)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise SuiteError(f"cannot read the coverage config {path}: {exc}") from exc
    if not isinstance(config, dict) or set(config) != set(CONFIG_KEYS):
        raise SuiteError(f"{path} must hold exactly the keys {', '.join(CONFIG_KEYS)}")
    for key in ("threshold_similarity", "required_share"):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not 0 < value <= 1:
            raise SuiteError(f"{path}: {key} must be a number above 0 and at most 1")
    if not str(config["threshold_justification"] or "").strip():
        raise SuiteError(f"{path}: threshold_justification must not be empty")
    return config


def _normalised(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.lower()))


def nearest_golden(live: list[dict], golden: list[dict], live_vectors: list,
                   golden_vectors: list, threshold: float,
                   near_identical: float) -> list[dict]:
    """For each live query, the golden question with the highest cosine similarity."""
    matched = []
    for query, vector in zip(live, live_vectors):
        similarity, best = max(
            ((cosine(vector, other), index) for index, other in enumerate(golden_vectors)),
            key=lambda pair: pair[0])
        nearest = golden[best]
        matched.append({
            "id": query["id"], "query": query["query"], "expected": query.get("expected"),
            "topic": query.get("topic"), "golden_id": nearest["id"],
            "golden_question": nearest["question"], "similarity": similarity,
            "covered": similarity >= threshold - EPSILON,
            # The same words, or almost: such a match only shows that a query was copied.
            "near_identical": (similarity >= near_identical - EPSILON
                               or _normalised(query["query"]) ==
                               _normalised(nearest["question"])),
        })
    return matched


def share_covered(matched: list[dict]) -> float | None:
    return ratio(sum(1 for m in matched if m["covered"]), len(matched))


def cluster_uncovered(matched: list[dict], vectors: dict[str, list],
                      cluster_threshold: float) -> list[dict]:
    """Single-link grouping: two uncovered queries are in one group if a chain of pairs,
    each at least cluster_threshold similar, connects them. Largest group first."""
    groups: list[list[dict]] = []
    for row in (m for m in matched if not m["covered"]):
        near = [g for g in groups if any(
            cosine(vectors[row["id"]], vectors[other["id"]]) >= cluster_threshold
            for other in g)]
        merged = [member for group in near for member in group] + [row]
        groups = [g for g in groups if g not in near] + [merged]
    groups.sort(key=lambda group: -len(group))
    return [{
        "cluster_id": f"c{number}",
        "name": None,           # written by a person (--topic-names)
        "size": len(group),
        "query_ids": [m["id"] for m in group],
        "queries": [m["query"] for m in group],
        "topics_in_live_file": dict(Counter(str(m["topic"]) for m in group)),
    } for number, group in enumerate(groups, 1)]


def build_report(args: argparse.Namespace, client: httpx.Client) -> dict:
    config = load_config(args.config)
    threshold = config["threshold_similarity"]
    cluster_threshold = (threshold if args.cluster_threshold is None
                         else args.cluster_threshold)
    sha = git_sha()
    live = load_jsonl(args.live, required=("id", "query"))
    golden = load_jsonl(args.golden, required=("id", "question"))
    if len({q["id"] for q in live}) != len(live):
        raise SuiteError(f"{args.live}: an id is repeated")
    vectors = embed(client, args.ollama_url, args.embed_model,
                    [g["question"] for g in golden] + [q["query"] for q in live])
    golden_vectors, live_vectors = vectors[:len(golden)], vectors[len(golden):]
    matched = nearest_golden(live, golden, live_vectors, golden_vectors,
                             threshold, args.near_identical)
    others = [m for m in matched if not m["near_identical"]]
    topics = cluster_uncovered(
        matched, {q["id"]: v for q, v in zip(live, live_vectors)}, cluster_threshold)
    if args.topic_names:
        names = load_json(args.topic_names)
        if set(names) - {t["cluster_id"] for t in topics}:
            raise SuiteError(f"{args.topic_names} names a cluster that does not exist")
        for topic in topics:
            topic["name"] = names.get(topic["cluster_id"])
    return {
        "label": args.label,
        "git_sha": sha,
        "config_sha256": sha256_file(args.config),
        "threshold_similarity": threshold,
        "threshold_justification": str(config["threshold_justification"]).strip(),
        "required_share": config["required_share"],
        "share_covered": share_covered(matched),
        "covered_count": sum(1 for m in matched if m["covered"]),
        "live_count": len(matched),
        "near_identical_similarity": args.near_identical,
        "near_identical_count": len(matched) - len(others),
        # Near-identical queries are removed from the numerator and the denominator.
        "share_covered_excluding_near_identical": share_covered(others),
        "cluster_similarity": cluster_threshold,
        "embed_model": args.embed_model,
        "golden_count": len(golden),
        "golden_sha256": sha256_file(args.golden),
        "live_queries_sha256": sha256_file(args.live),
        "matches": matched,
        "uncovered_topics": topics,
    }


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    env = os.environ.get
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", required=True, help="'before' or 'after' for E4.1")
    parser.add_argument("--config", default=env(
        "COVERAGE_CONFIG", str(EVAL_DIR / "coverage_config.yaml")), help="[COVERAGE_CONFIG]")
    parser.add_argument("--near-identical", type=float, default=0.98)
    parser.add_argument("--cluster-threshold", type=float, default=None,
                        help="default: the coverage threshold")
    parser.add_argument("--topic-names", help="JSON file: cluster id to the name you chose")
    parser.add_argument("--live", default=str(EVAL_DIR / "live_queries.jsonl"))
    parser.add_argument("--golden", default=env("EVAL_GOLDEN", str(EVAL_DIR / "golden.jsonl")))
    parser.add_argument("--ollama-url", default=env("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--embed-model", default=env("EMBED_MODEL", "nomic-embed-text"))
    parser.add_argument("--out", default=env(
        "COVERAGE_OUT", str(EVAL_DIR / "results" / "coverage")), help="[COVERAGE_OUT]")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    use_utf8_output()
    args = parse_args(argv)
    own = client is None
    client = client or httpx.Client(timeout=300.0)
    try:
        report = build_report(args, client)
        now = utc_now()
        path = write_new_json(args.out, f"{stamp(now)}-{label_part(args.label)}",
                              {"run_at": now.isoformat(timespec="seconds"), **report})
    except SuiteError as exc:
        print(f"coverage: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - a crash must not look like "below" (exit 1)
        print(f"coverage: error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        if own:
            client.close()
    share = report["share_covered"]
    excluding = report["share_covered_excluding_near_identical"]
    print(f"covered: {report['covered_count']} of {report['live_count']} = {share:.1%} "
          f"at similarity >= {report['threshold_similarity']}")
    print("without near-identical matches "
          f"({report['near_identical_count']} removed): "
          f"{'null' if excluding is None else f'{excluding:.1%}'}")
    for topic in report["uncovered_topics"]:
        print(f"uncovered {topic['cluster_id']} ({topic['size']}) "
              f"{topic['name'] or '[name it]'}: {topic['queries'][0]}")
    required = report["required_share"]
    below = share < required - EPSILON
    print(f"required share {required:.1%}: {'BELOW' if below else 'met'}")
    print(f"result: {path}")
    return EXIT_BELOW if below else EXIT_COVERED


if __name__ == "__main__":
    sys.exit(main())
