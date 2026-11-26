"""One command that runs the whole Part B suite and writes one timestamped result file.

    python eval/run_suite.py --target http://localhost:8000 --label s0-run1 --seed 42

The API under test must run with EVAL_DEBUG_ENABLED=true. This script only reads: it never
writes to the vector store.

corpus_state_id says which corpus the index held. It is taken from the API only, so it
cannot be stale or typed by hand: /api/retrieve returns the lake_state_id that the ingestion
Job records on the collection.
  - The key is there with a value: that value is the id.
  - The key is there and null: the collection is being written, or does not record what it
    was built from. The run stops (exit 2). Run the ingestion Job and try again.
  - The API does not send the key (an older image): the id is
    "index:<collection>@<index_generation>", which belongs to one collection, so the gate
    refuses to compare it with a run on another collection.
The collection must look the same in every response from the first to the last.

Exit code: 0 when a result file was written (whatever the numbers are), 2 otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from suite import component, end_to_end, module
from suite.common import (
    EVAL_DIR,
    MODULE_METRICS,
    SUITE_METRICS,
    SuiteError,
    get,
    git_sha,
    label_part,
    load_jsonl,
    sha256_file,
    sha256_text,
    stamp,
    use_utf8_output,
    utc_now,
    write_new_json,
)
from suite.judge import SYSTEM_PROMPT


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    env = os.environ.get
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default=env("EVAL_TARGET"),
                        help="base URL of the API under test [EVAL_TARGET]")
    parser.add_argument("--label", default=env("EVAL_LABEL"),
                        help="part of the result file name [EVAL_LABEL]")
    parser.add_argument("--seed", type=int, default=int(env("EVAL_SEED", "42")),
                        help="typo and judge seed [EVAL_SEED, default 42]")
    parser.add_argument("--golden", default=env("EVAL_GOLDEN", str(EVAL_DIR / "golden.jsonl")),
                        help="[EVAL_GOLDEN]")
    parser.add_argument("--pairs", default=str(EVAL_DIR / "module_pairs.jsonl"))
    parser.add_argument("--out", default=env("EVAL_OUT", str(EVAL_DIR / "results" / "suite")),
                        help="folder for the result file [EVAL_OUT]")
    parser.add_argument("--ollama-url", default=env("OLLAMA_URL", "http://127.0.0.1:11434"),
                        help="[OLLAMA_URL]")
    parser.add_argument("--judge-model", default=env("JUDGE_MODEL", "qwen2.5:3b"),
                        help="[JUDGE_MODEL]")
    parser.add_argument("--depth", type=int, default=10,
                        help="length of the ranked list for component metrics (MRR depth)")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per request")
    parser.add_argument("--result-path-file",
                        help="also write the path of the result file here, for a script")
    args = parser.parse_args(argv)
    if not args.target or not args.label:
        parser.error("--target and --label are required")
    if not 5 <= args.depth <= 50:
        parser.error("--depth must be between 5 and 50")
    args.target = args.target.rstrip("/")
    return args


def load_golden(path: str) -> list[dict]:
    rows = load_jsonl(path, required=("id", "question", "category"))
    if len({r["id"] for r in rows}) != len(rows):
        raise SuiteError(f"{path}: an id is repeated")
    for row in rows:
        if row["category"] not in ("answerable", "unanswerable"):
            raise SuiteError(f"{path}: {row['id']} has category {row['category']!r}")
        relevant = row.get("relevant") or []
        if row["category"] == "answerable" and not (
                relevant and all(r.get("doc_id") and isinstance(r.get("page"), int)
                                 for r in relevant)):
            raise SuiteError(f"{path}: {row['id']} needs ground truth with doc_id and page")
    return rows


def golden_version(golden: list[dict]) -> str | None:
    """The highest added_in value ("v1", "v2", ...)."""
    versions = [str(r["added_in"]) for r in golden if r.get("added_in")]
    return max(versions, key=lambda v: (len(v), v)) if versions else None


def corpus_state(collection: dict) -> tuple[str, str]:
    """(corpus_state_id, where it came from). See the module docstring."""
    if "lake_state_id" in collection:
        if not collection["lake_state_id"]:
            raise SuiteError(
                f"collection '{collection['name']}' has no lake_state_id: a write is in "
                "progress, or it was not recorded. Run the ingestion Job and try again.")
        return (collection["lake_state_id"],
                "lake_state_id from /api/retrieve (collection metadata)")
    if collection.get("version") is None:
        raise SuiteError("the API could not read the collection's index_generation")
    return (f"index:{collection['name']}@{collection['version']}",
            "index_generation from /api/retrieve; comparable within this collection only")


def check_same_collection(first: dict, retrievals: list[dict], traces: list[dict]) -> None:
    """Every response must describe the collection as the first one did. A value that
    becomes null, appears or disappears is a change. A trace has the name and the version."""
    from_trace = {key: first.get(key) for key in ("name", "version")}
    for expected, seen in ([(first, r["collection"]) for r in retrievals]
                           + [(from_trace, t["collection"]) for t in traces]):
        if seen != expected:
            raise SuiteError("the collection's name, lake_state_id or index_generation "
                             f"changed during the run: {expected} then {seen}")


def one_value(values: list, what: str):
    """The single value seen during the run. A change in the middle makes the run invalid."""
    distinct = []
    for value in values:
        if value is not None and value not in distinct:
            distinct.append(value)
    if len(distinct) > 1:
        raise SuiteError(f"{what} changed during the run: {distinct}")
    return distinct[0] if distinct else None


def probe_index(client: httpx.Client, args: argparse.Namespace, question: str) -> dict:
    """What the API says about its release, collection and embedding model."""
    try:
        reply = component.retrieve(client, args.target, question, args.depth)
        if not (reply["release_id"] and reply["collection"]["name"]
                and reply["embedding"]["model"]):
            raise ValueError("release_id, collection or embedding model is empty")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise SuiteError(f"{args.target}/api/retrieve does not work: {exc}") from exc
    return reply


def model_digest(client: httpx.Client, ollama_url: str, model: str) -> str | None:
    wanted = model if ":" in model else f"{model}:latest"
    try:
        response = client.get(f"{ollama_url.rstrip('/')}/api/tags")
        response.raise_for_status()
        return next((m.get("digest") for m in response.json()["models"]
                     if wanted in (m.get("name"), m.get("model"))), None)
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def peak_memory_mib() -> float | None:
    """This process's peak memory. Only on Linux, which is where the Job runs."""
    if not sys.platform.startswith("linux"):
        return None
    import resource
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def _shown(value: object) -> str:
    if value is None:
        return "null"
    return f"{value:.4g}" if isinstance(value, float) else str(value)


def summary_table(result: dict) -> str:
    e2e, comp = result["end_to_end"], result["component"]
    notes = {
        "component.recall_at_5": f"n={comp['n_questions']}, list depth {comp['list_depth']}",
        "end_to_end.faithfulness": f"n={e2e['faithfulness_n']} answers, "
                                   f"judge errors {e2e['faithfulness_judge_errors']}",
        "end_to_end.abstention_correctness": f"n={e2e['abstention_n']}",
        "end_to_end.citation_validity": f"n={e2e['citation_n']} raw citations",
        "end_to_end.latency_ms_p95": f"n={e2e['latency_n']}, nearest rank",
        "end_to_end.answerable_answer_rate": f"n={e2e['answerable_n']}",
    }
    lines = [f"run {result['run_id']}  release {result['release_id']}  seed {result['seed']}",
             f"{'metric':<38} {'value':>10}  note"]
    for name in (*MODULE_METRICS, *SUITE_METRICS, "end_to_end.answerable_answer_rate"):
        lines.append(f"{name:<38} {_shown(get(result, name)):>10}  {notes.get(name, '')}")
    for name, value in comp["noise"]["delta"].items():
        lines.append(f"{'noise delta ' + name:<38} {_shown(value):>10}  noisy minus clean")
    subset = comp["corpus_a_subset"]
    lines.append(f"{'corpus A subset recall_at_5':<38} "
                 f"{_shown(subset['recall_at_5']):>10}  n={subset['n_questions']}")
    lines.append(f"latency of answered requests only: "
                 f"p50 {_shown(e2e['latency_ms_p50_answered'])} ms, "
                 f"p95 {_shown(e2e['latency_ms_p95_answered'])} ms, "
                 f"n={e2e['latency_answered_n']}")
    lines.append(f"request errors: end to end {e2e['errors']}, retrieval {len(comp['errors'])}")
    return "\n".join(line.rstrip() for line in lines)


def run_suite(args: argparse.Namespace, client: httpx.Client) -> tuple[Path, dict]:
    started = utc_now()
    sha = git_sha()                     # fail before the long part, not after it
    golden = load_golden(args.golden)
    pairs = load_jsonl(args.pairs, required=("type", "a", "b"))
    golden_sha = sha256_file(args.golden)

    first = probe_index(client, args, golden[0]["question"])
    state_id, state_source = corpus_state(first["collection"])

    print("component level: retrieval", flush=True)
    component_block, details, responses = component.run_component(
        client, args.target, golden, args.seed, args.depth)

    print("end to end: asking the golden questions", flush=True)
    end_to_end.warm_up(client, args.target)
    rows = [end_to_end.ask_question(client, args.target, q) for q in golden]
    records = [end_to_end.question_record(row) for row in rows]
    print("end to end: judging the answers", flush=True)
    end_to_end.judge_records(client, args.ollama_url, args.judge_model, args.seed, records)

    # The index and the release must be the same from the first request to the last.
    responses = [first, *responses, probe_index(client, args, golden[0]["question"])]
    traces = [row["trace"] for row in rows if row["trace"]]
    everything = responses + traces
    release_id = one_value([r.get("release_id") for r in everything], "release_id")
    check_same_collection(first["collection"], responses, traces)
    embed_model = one_value([r["embedding"]["model"] for r in everything], "embedding model")
    llm_model = one_value([t["llm"]["model"] for t in traces], "LLM model")

    print("module level: embedding model", flush=True)
    chunk_texts: dict[str, str] = {}
    for trace in traces:
        for chunk in trace.get("context") or []:
            chunk_texts.setdefault(chunk["chunk_id"], chunk["text"])
    module_block = {
        "embed_model": embed_model,
        "embed_model_digest": model_digest(client, args.ollama_url, embed_model),
        **module.pair_similarities(client, args.ollama_url, embed_model, pairs),
        **module.timing_and_determinism(
            client, args.ollama_url, embed_model,
            [chunk_texts[key] for key in sorted(chunk_texts)]),
        "embed_chunks_source": "context[] of this run's traces, ordered by chunk id",
    }

    for record in records:
        detail = details.get(record["id"], {})
        record.update({"metrics": {}, **detail})

    run_id = f"{stamp(started)}-{label_part(args.label)}"
    sample = traces[0] if traces else {}
    result = {
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "git_sha": sha,
        "golden_sha256": golden_sha,
        "corpus_state_id": state_id,
        "release_id": release_id,
        "seed": args.seed,
        "golden_version": golden_version(golden),
        "config": {
            "target": args.target,
            "corpus_state_source": state_source,
            "collection": first["collection"],
            "embedding": first["embedding"],
            "top_k": len(sample["retrieved"]) if sample.get("retrieved") else None,
            "relevance_floor": next((f["value"] for f in first.get("filters_applied", [])
                                     if f["name"] == "relevance_floor"), None),
            "llm": {"model": llm_model, "version": get(sample, "llm.version"),
                    "params": get(sample, "llm.params")},
            "prompt_template_version": next(
                (t["prompt"]["template_version"] for t in traces if t.get("prompt")), None),
            "api_git_sha": sample.get("git_sha"),
            "judge": {"model": args.judge_model,
                      "digest": model_digest(client, args.ollama_url, args.judge_model),
                      "temperature": 0, "seed": args.seed,
                      "prompt_sha256": sha256_text(SYSTEM_PROMPT),
                      "same_model_as_generator": args.judge_model == llm_model},
            "module_pairs_sha256": sha256_file(args.pairs),
            "peak_memory_mib": peak_memory_mib(),
        },
        "summary_table": "",
        "module": module_block,
        "component": component_block,
        "end_to_end": end_to_end.summarise(records),
        "questions": records,
    }
    result["summary_table"] = summary_table(result)
    path = write_new_json(args.out, run_id, result)
    return path, result


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    use_utf8_output()
    args = parse_args(argv)
    own = client is None
    client = client or httpx.Client(timeout=args.timeout)
    try:
        path, result = run_suite(args, client)
        if args.result_path_file:
            Path(args.result_path_file).write_text(str(path), encoding="utf-8")
    except SuiteError as exc:
        print(f"suite error: {exc}", file=sys.stderr)
        return 2
    finally:
        if own:
            client.close()
    print(result["summary_table"])
    print(f"corpus_state_id: {result['corpus_state_id']} "
          f"({result['config']['corpus_state_source']})")
    memory = result["config"]["peak_memory_mib"]
    if memory is not None:
        print(f"peak_memory_mib: {memory}")
    print(f"result: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
