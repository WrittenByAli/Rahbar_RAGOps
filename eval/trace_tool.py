"""Print the exact context and prompt that produced an answer (requirement D2).

    python eval/trace_tool.py <trace_id> [--traces PATH ...] [--prompt-out FILE]

--traces takes files (trace files or saved `kubectl logs` output) and directories (every
*.jsonl file below them). The default is the repository's traces/ folder.

The prompt is printed only after its SHA-256 has been recomputed and matches the record.
For a cache hit the model was not called, so the prompt comes from cache.source_trace_id.
--prompt-out writes the prompt byte for byte; the terminal copy may differ in line endings.

Exit codes:
    0  printed (also for a record where the model was never called, so there is no prompt)
    2  bad arguments, or a file cannot be read or written
    3  trace id not found
    4  prompt hash mismatch, or the record lacks what is needed to verify it
    5  cache hit whose source trace cannot be resolved
    6  record contradicts itself, or two different records share a trace id
    7  --prompt-out was given but the model was never called, so there is no prompt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from tracelib import (
    DEFAULT_TRACES,
    LoadedTraces,
    TraceInputError,
    as_dict,
    as_list,
    cache_status,
    load_traces,
    use_utf8_output,
)

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_NOT_FOUND = 3
EXIT_HASH_MISMATCH = 4
EXIT_CACHE_SOURCE = 5
EXIT_INCONSISTENT = 6
EXIT_NO_PROMPT = 7

# The record keys of docs/a3/CONTRACTS.md. Any other key is printed too, under "other fields".
CONTRACT_KEYS = frozenset(
    "kind schema trace_id ts query rewritten_query embedding collection retrieved "
    "filters_applied reranker reranked_order context prompt llm response citations_raw "
    "citations guardrail_actions latency_ms cache release_id git_sha pod".split()
)


class TraceError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- find and verify
def lookup_record(loaded: LoadedTraces, trace_id: str) -> dict | None:
    if trace_id in loaded.conflicts:
        raise TraceError(
            EXIT_INCONSISTENT,
            f"two different records have trace id {trace_id}; cannot tell which one is real",
        )
    return loaded.by_id.get(trace_id)


def find_record(loaded: LoadedTraces, trace_id: str) -> dict:
    record = lookup_record(loaded, trace_id)
    if record is None:
        message = (
            f"trace id {trace_id} not found in {len(loaded.by_id)} trace record(s) "
            f"from {len(loaded.files)} file(s)"
        )
        broken = loaded.skipped["broken_trace_line"]
        if broken:
            message += (
                f"; {broken} unreadable trace line(s) were skipped "
                "(cut off, or saved with `kubectl logs --prefix`)"
            )
        raise TraceError(EXIT_NOT_FOUND, message)
    return record


def cache_chain(loaded: LoadedTraces, record: dict) -> list[dict]:
    """From this record to the one that built the prompt. One item unless it is a cache hit."""
    chain = [record]
    seen = {record["trace_id"]}
    while cache_status(chain[-1]) == "hit":
        current = chain[-1]
        name = current["trace_id"]
        if current.get("prompt") is not None or current.get("context"):
            raise TraceError(
                EXIT_INCONSISTENT,
                f"trace {name} is a cache hit but carries its own prompt or context",
            )
        source_id = as_dict(current.get("cache")).get("source_trace_id")
        if not isinstance(source_id, str) or not source_id:
            raise TraceError(
                EXIT_CACHE_SOURCE, f"trace {name} is a cache hit with no source_trace_id"
            )
        if source_id in seen:
            raise TraceError(
                EXIT_CACHE_SOURCE, f"cache source loop: trace {name} points back to {source_id}"
            )
        source = lookup_record(loaded, source_id)
        if source is None:
            raise TraceError(
                EXIT_CACHE_SOURCE,
                f"trace {name} was served from the cache, but its source trace {source_id} is "
                "not in the input (pass the file that holds it with --traces)",
            )
        seen.add(source_id)
        chain.append(source)
    return chain


def prompt_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verified_prompt(record: dict) -> dict | None:
    """The record's prompt after checking it, or None when the model was never called."""
    name = record["trace_id"]
    prompt = record.get("prompt")
    context = as_list(record.get("context"))
    if prompt is None:
        if context:
            raise TraceError(
                EXIT_INCONSISTENT, f"trace {name} lists context sent to the model but no prompt"
            )
        if as_dict(record.get("response")).get("answered_from_context") is True:
            raise TraceError(
                EXIT_INCONSISTENT,
                f"trace {name} says it answered from context but has no prompt",
            )
        return None
    text = as_dict(prompt).get("text")
    recorded = as_dict(prompt).get("sha256")
    if not isinstance(text, str) or not isinstance(recorded, str):
        raise TraceError(
            EXIT_HASH_MISMATCH, f"trace {name}: cannot verify, prompt.text or prompt.sha256 missing"
        )
    try:
        actual = prompt_sha256(text)
    except UnicodeEncodeError as exc:
        raise TraceError(
            EXIT_HASH_MISMATCH, f"trace {name}: cannot verify, prompt is not valid Unicode ({exc})"
        ) from exc
    if actual != recorded:
        raise TraceError(
            EXIT_HASH_MISMATCH,
            f"PROMPT HASH MISMATCH in trace {name}: the stored text is not the prompt that was "
            f"hashed.\n  recorded   {recorded}\n  recomputed {actual}",
        )
    for chunk in context:
        chunk_text = as_dict(chunk).get("text")
        if not isinstance(chunk_text, str) or chunk_text not in text:
            raise TraceError(
                EXIT_INCONSISTENT,
                f"trace {name}: context chunk {as_dict(chunk).get('chunk_id')} is not in the "
                "recorded prompt, so context[] is not the exact text sent",
            )
    return prompt


# ---------------------------------------------------------------- printing
def show(value: Any) -> str:
    """Text as it is; anything else as JSON, so a null reads as null."""
    if isinstance(value, str):
        return value.replace("\n", "\n    ")  # keep later lines under their label
    return json.dumps(value, ensure_ascii=False)


def listing(title: str, items: list) -> list[str]:
    if not items:
        return [f"{title}: none"]
    return [f"{title}:", *(f"  {json.dumps(item, ensure_ascii=False)}" for item in items)]


def candidate_line(item: Any) -> str:
    chunk = as_dict(item)
    if chunk.get("kept") is False:
        state = f"DROPPED by {show(chunk.get('dropped_by'))}"
    else:
        state = "kept" if chunk.get("kept") is True else "kept unknown"
    return (
        f"  rank {show(chunk.get('rank'))}  score {show(chunk.get('score'))}  {state}\n"
        f"    chunk {show(chunk.get('chunk_id'))}  doc {show(chunk.get('doc_id'))}  "
        f"version {show(chunk.get('doc_version'))}  pages "
        f"{show(chunk.get('page_start'))}-{show(chunk.get('page_end'))}\n"
        f"    source {show(chunk.get('source'))}  section {show(chunk.get('section'))}"
    )


def header(record: dict) -> list[str]:
    """Everything except the prompt and context that helps to find the cause of an answer."""
    collection = as_dict(record.get("collection"))
    embedding = as_dict(record.get("embedding"))
    response = as_dict(record.get("response"))
    cache = as_dict(record.get("cache"))
    latency = as_dict(record.get("latency_ms"))
    retrieved = as_list(record.get("retrieved"))
    lines = [
        f"time:       {show(record.get('ts'))}",
        f"question:   {show(record.get('query'))}",
        f"rewritten:  {show(record.get('rewritten_query'))}",
        f"release:    {show(record.get('release_id'))}  git {show(record.get('git_sha'))}  "
        f"pod {show(record.get('pod'))}",
        f"collection: {show(collection.get('name'))}  version {show(collection.get('version'))}",
        f"embedding:  {show(embedding.get('model'))}  version {show(embedding.get('version'))}",
        f"llm:        {show(record.get('llm'))}",  # whole object: model, params, attempt
        f"cache:      {show(cache.get('status'))}  source {show(cache.get('source_trace_id'))}",
        f"answer:     {show(response.get('text'))}",
        f"            answered_from_context {show(response.get('answered_from_context'))}  "
        f"confidence {show(response.get('confidence'))}",
        f"raw model output: {show(response.get('raw_output'))}",
        *listing("citations written by the model", as_list(record.get("citations_raw"))),
        *listing("citations returned to the user", as_list(record.get("citations"))),
        *listing("guardrail actions", as_list(record.get("guardrail_actions"))),
        *listing("filters applied", as_list(record.get("filters_applied"))),
        f"reranker:   {show(record.get('reranker'))}",
        f"order sent: {show(record.get('reranked_order'))}",
        f"retrieved candidates: {len(retrieved)}",
        *(candidate_line(item) for item in retrieved),
        "latency_ms: " + " ".join(f"{stage}={show(ms)}" for stage, ms in latency.items()),
    ]
    # For example an error message: never hide something that could explain the answer.
    extra = [{key: value} for key, value in record.items() if key not in CONTRACT_KEYS]
    if extra:
        lines += listing("other fields in the record", extra)
    return lines


def report(chain: list[dict], prompt: dict | None) -> str:
    asked, source = chain[0], chain[-1]
    lines = [f"========== TRACE {asked['trace_id']} ==========", *header(asked)]
    if len(chain) > 1:
        lines += [
            "",
            f"========== SOURCE TRACE {source['trace_id']} ==========",
            "This answer came from the cache. The prompt was built by the source trace.",
            "cache chain: " + " -> ".join(record["trace_id"] for record in chain),
        ]
        answers = {str(as_dict(record.get("response")).get("text")) for record in chain}
        if len(answers) > 1:
            lines.append("WARNING: the cached answer differs from the source trace's answer.")
        lines += header(source)
    lines.append("")
    if prompt is None:
        lines.append(
            "No model call was made, so there is no prompt and no context. The candidates, "
            "filters and guardrail actions above show what happened."
        )
        return "\n".join(lines)

    context = as_list(source.get("context"))
    lines.append(
        f"========== CONTEXT SENT TO THE MODEL: {len(context)} chunk(s), "
        f"from trace {source['trace_id']} =========="
    )
    for number, chunk in enumerate(context, 1):
        lines += [
            f"----- chunk {number}: {show(chunk.get('chunk_id'))} | "
            f"{show(chunk.get('source'))} | {show(chunk.get('section'))} | "
            f"page {show(chunk.get('page'))} -----",
            chunk["text"],
            f"----- end of chunk {number} -----",
        ]
    lines += [
        "",
        f"========== FULL PROMPT: template {show(prompt.get('template_version'))}, "
        f"from trace {source['trace_id']} ==========",
        prompt["text"],
        "========== END OF PROMPT ==========",
        f"sha256 {prompt['sha256']}: verified against the text above",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- command
def main(argv: list[str] | None = None) -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(
        description="Print the exact context and prompt that produced an answer."
    )
    parser.add_argument("trace_id")
    # "extend": a repeated --traces adds files; the argparse default would drop the earlier ones
    parser.add_argument("--traces", nargs="+", action="extend", help="files or directories")
    parser.add_argument("--prompt-out", help="also write the exact prompt bytes to this file")
    args = parser.parse_args(argv)

    try:
        loaded = load_traces(args.traces or [DEFAULT_TRACES])
        chain = cache_chain(loaded, find_record(loaded, args.trace_id))
        prompt = verified_prompt(chain[-1])
        if prompt is None and len(chain) > 1:
            raise TraceError(
                EXIT_CACHE_SOURCE,
                f"cache source trace {chain[-1]['trace_id']} has no prompt",
            )
    except TraceInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    except TraceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.code

    print(report(chain, prompt))
    if not args.prompt_out:
        return EXIT_OK
    if prompt is None:
        print("ERROR: no prompt to write: the model was never called", file=sys.stderr)
        return EXIT_NO_PROMPT
    data = prompt["text"].encode("utf-8")
    try:
        Path(args.prompt_out).write_bytes(data)  # bytes: no newline translation on Windows
    except OSError as exc:
        print(f"ERROR: cannot write {args.prompt_out}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    print(f"prompt written to {args.prompt_out} ({len(data)} bytes)", file=sys.stderr)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
