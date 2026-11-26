"""Requirement D2: a command takes a trace id and prints the exact context and prompt."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import trace_tool
from trace_samples import (
    cache_hit,
    context_entry,
    make_record,
    no_model_call,
    prompt_for,
    prompt_object,
    sha256_of,
    write_lines,
)

TOOL = Path(__file__).resolve().parents[1] / "trace_tool.py"

# Urdu, an arrow, a non-breaking space and a smart quote: cp1252 cannot encode the first two.
NON_ASCII = (
    "3.1 حاضری → Attendance\n80 % is the student’s minimum."
)

JUNK = [
    "",
    "INFO:     Uvicorn running on http://0.0.0.0:8000",
    '{"ts": "2026-11-26T10:00:00Z", "level": "INFO", "msg": "answered"}',
    '["a", "json", "array"]',
    "42",
    "{not json",
]


def non_ascii_record(trace_id: str) -> dict:
    question = "What is the حاضری rule?"
    context = [context_entry("chunk-u", NON_ASCII)]
    return make_record(
        trace_id,
        query=question,
        context=context,
        prompt=prompt_object(prompt_for(question, context)),
    )


def run_tool(capsys, *args: str) -> tuple[int, str, str]:
    code = trace_tool.main([str(a) for a in args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_rebuilds_exact_prompt_and_verifies_hash(tmp_path, capsys):
    record = non_ascii_record("t-exact")
    traces = tmp_path / "traces"
    write_lines(traces / "pod-a.jsonl", [*JUNK, record, make_record("t-other")])
    out_file = tmp_path / "prompt.txt"

    code, out, err = run_tool(capsys, "t-exact", "--traces", traces, "--prompt-out", out_file)

    assert code == 0, err
    prompt = record["prompt"]["text"]
    assert prompt in out
    assert record["context"][0]["text"] in out
    assert record["prompt"]["sha256"] in out and "verified" in out
    # Byte exact: the file is exactly what was hashed (no added newline, no CRLF).
    assert out_file.read_bytes() == prompt.encode("utf-8")
    assert sha256_of(out_file.read_bytes().decode("utf-8")) == record["prompt"]["sha256"]


def test_cache_hit_resolves_to_source_prompt(tmp_path, capsys):
    source = non_ascii_record("t-source")
    hit = cache_hit("t-hit", "t-source", ts="2026-11-26T11:00:00Z")
    traces = tmp_path / "traces"
    # Two replicas write two files: the source trace is in the other pod's file.
    write_lines(traces / "pod-a.jsonl", [source])
    write_lines(traces / "pod-b.jsonl", [hit])
    out_file = tmp_path / "prompt.txt"

    code, out, err = run_tool(capsys, "t-hit", "--traces", traces, "--prompt-out", out_file)

    assert code == 0, err
    assert "t-source" in out
    assert source["prompt"]["text"] in out
    assert source["context"][0]["text"] in out
    assert out_file.read_bytes() == source["prompt"]["text"].encode("utf-8")
    # Both the asked-for trace and its source are described, so the answer can be root-caused.
    assert "TRACE t-hit" in out and "SOURCE TRACE t-source" in out
    assert "2026-11-26T11:00:00Z" in out and "2026-11-26T10:00:00Z" in out


def test_cache_hit_follows_a_chain_of_hits(tmp_path, capsys):
    source = make_record("t-source")
    rows = [source, cache_hit("t-mid", "t-source"), cache_hit("t-hit", "t-mid")]
    path = write_lines(tmp_path / "t.jsonl", rows)

    code, out, err = run_tool(capsys, "t-hit", "--traces", path)

    assert code == 0, err
    assert "t-hit -> t-mid -> t-source" in out
    assert source["prompt"]["text"] in out


def test_cached_answer_that_differs_from_its_source_is_reported(tmp_path, capsys):
    hit = cache_hit("t-hit", "t-source")
    hit["response"] = {**hit["response"], "text": "A different answer."}
    path = write_lines(tmp_path / "t.jsonl", [make_record("t-source"), hit])

    code, out, _ = run_tool(capsys, "t-hit", "--traces", path)

    assert code == 0
    assert "WARNING" in out and "differs" in out


def test_hash_mismatch_fails_loudly_and_prints_no_prompt(tmp_path, capsys):
    record = make_record("t-bad")
    record["prompt"]["text"] += " "  # one byte more than what was hashed
    path = write_lines(tmp_path / "t.jsonl", [record])
    out_file = tmp_path / "prompt.txt"

    code, out, err = run_tool(capsys, "t-bad", "--traces", path, "--prompt-out", out_file)

    assert code == trace_tool.EXIT_HASH_MISMATCH
    assert "HASH MISMATCH" in err
    assert record["prompt"]["sha256"] in err and sha256_of(record["prompt"]["text"]) in err
    assert "Question: When is" not in out
    assert not out_file.exists()


@pytest.mark.parametrize("missing", ["sha256", "text"])
def test_prompt_that_cannot_be_verified_is_a_mismatch(tmp_path, capsys, missing):
    record = make_record("t-unverifiable")
    del record["prompt"][missing]
    path = write_lines(tmp_path / "t.jsonl", [record])

    code, _, err = run_tool(capsys, "t-unverifiable", "--traces", path)

    assert code == trace_tool.EXIT_HASH_MISMATCH
    assert "cannot verify" in err


def test_hash_mismatch_in_the_cache_source_fails_the_hit(tmp_path, capsys):
    source = make_record("t-source")
    source["prompt"]["sha256"] = "0" * 64
    path = write_lines(tmp_path / "t.jsonl", [source, cache_hit("t-hit", "t-source")])

    code, _, err = run_tool(capsys, "t-hit", "--traces", path)

    assert code == trace_tool.EXIT_HASH_MISMATCH
    assert "t-source" in err


def test_missing_trace_id_is_not_found(tmp_path, capsys):
    path = write_lines(tmp_path / "t.jsonl", [*JUNK, make_record("t-present")])

    code, out, err = run_tool(capsys, "t-absent", "--traces", path)

    assert code == trace_tool.EXIT_NOT_FOUND
    assert "t-absent" in err and "not found" in err
    assert out == ""


def test_a_cut_off_trace_line_is_mentioned_when_the_id_is_not_found(tmp_path, capsys):
    cut = json.dumps(make_record("t-cut"))[:200]
    path = write_lines(tmp_path / "t.jsonl", [cut, make_record("t-whole")])

    code, _, err = run_tool(capsys, "t-cut", "--traces", path)

    assert code == trace_tool.EXIT_NOT_FOUND
    assert "1 unreadable trace line" in err


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([cache_hit("t-hit", "t-gone")], "t-gone"),
        ([cache_hit("t-hit", None)], "no source_trace_id"),
        ([cache_hit("t-hit", "t-hit")], "loop"),
        ([cache_hit("t-hit", "t-two"), cache_hit("t-two", "t-hit")], "loop"),
        ([cache_hit("t-hit", "t-source"), no_model_call("t-source")], "has no prompt"),
    ],
)
def test_unresolved_cache_source(tmp_path, capsys, rows, reason):
    path = write_lines(tmp_path / "t.jsonl", rows)

    code, out, err = run_tool(capsys, "t-hit", "--traces", path)

    assert code == trace_tool.EXIT_CACHE_SOURCE
    assert reason in err
    assert "FULL PROMPT" not in out


def test_no_model_call_still_shows_the_dropped_candidates(tmp_path, capsys):
    path = write_lines(tmp_path / "t.jsonl", [no_model_call("t-abstain")])

    code, out, err = run_tool(capsys, "t-abstain", "--traces", path)

    assert code == 0, err
    assert "No model call" in out
    assert "chunk-low-1" in out and "DROPPED by relevance_floor" in out
    assert "0.41" in out and "0.55" in out


def test_prompt_out_without_a_model_call_writes_nothing(tmp_path, capsys):
    path = write_lines(tmp_path / "t.jsonl", [no_model_call("t-abstain")])
    out_file = tmp_path / "prompt.txt"

    code, _, err = run_tool(capsys, "t-abstain", "--traces", path, "--prompt-out", out_file)

    assert code == trace_tool.EXIT_NO_PROMPT
    assert "no prompt" in err
    assert not out_file.exists()


def test_header_carries_what_is_needed_to_find_a_cause(tmp_path, capsys):
    action = {
        "rail": "retrieval.injection",
        "action": "drop_chunk",
        "rule_id": "ignore-instructions",
        "chunk_id": "chunk-evil",
        "doc_id": "planted-doc",
    }
    record = make_record("t-header", guardrail_actions=[action])
    record["retrieved"].append(candidate_dropped_by_rail())
    path = write_lines(tmp_path / "t.jsonl", [record])

    code, out, err = run_tool(capsys, "t-header", "--traces", path)

    assert code == 0, err
    for expected in (
        record["query"],
        "v1",
        "rahbar_v1",
        "nomic-embed-text",
        "digest-embed",
        "qwen2.5:3b",
        "digest-llm",
        '"seed": 42',
        record["response"]["text"],
        record["response"]["raw_output"],
        "2.4 Academic Warning",
        "ignore-instructions",
        "planted-doc",
        "relevance_floor",
        "DROPPED by retrieval.injection",
        "ug-academic-rules",
        "2026-06",
        "generate=900",
        "total=940",
    ):
        assert expected in out, expected


def candidate_dropped_by_rail() -> dict:
    return {
        "chunk_id": "chunk-evil",
        "doc_id": "planted-doc",
        "doc_version": "1",
        "source": "evil.pdf",
        "section": "1",
        "page": 1,
        "page_start": 1,
        "page_end": 1,
        "score": 0.6,
        "rank": 3,
        "kept": False,
        "dropped_by": "retrieval.injection",
    }


def test_fields_beyond_the_contract_are_shown_not_hidden(tmp_path, capsys):
    record = no_model_call("t-extra", error="TimeoutError: embedding service")
    record["llm"] = {**record["llm"], "prompt_tokens": 4096}
    path = write_lines(tmp_path / "t.jsonl", [record])

    code, out, err = run_tool(capsys, "t-extra", "--traces", path)

    assert code == 0, err
    assert "TimeoutError: embedding service" in out
    assert '"prompt_tokens": 4096' in out


def test_an_answer_from_context_without_a_prompt_is_inconsistent(tmp_path, capsys):
    # "No model call" would be the wrong story: the record says the context produced an answer.
    record = make_record("t-lost", prompt=None, context=[])
    path = write_lines(tmp_path / "t.jsonl", [record])

    code, out, err = run_tool(capsys, "t-lost", "--traces", path)

    assert code == trace_tool.EXIT_INCONSISTENT
    assert "answered from context" in err
    assert "No model call" not in out


def test_context_that_is_not_in_the_prompt_is_inconsistent(tmp_path, capsys):
    record = make_record("t-incons")
    record["context"][1]["text"] = "Text the model never saw."
    path = write_lines(tmp_path / "t.jsonl", [record])

    code, _, err = run_tool(capsys, "t-incons", "--traces", path)

    assert code == trace_tool.EXIT_INCONSISTENT
    assert "chunk-b" in err


def test_context_without_a_prompt_is_inconsistent(tmp_path, capsys):
    path = write_lines(tmp_path / "t.jsonl", [make_record("t-noprompt", prompt=None)])

    code, _, err = run_tool(capsys, "t-noprompt", "--traces", path)

    assert code == trace_tool.EXIT_INCONSISTENT
    assert "no prompt" in err


def test_cache_hit_with_its_own_prompt_is_inconsistent(tmp_path, capsys):
    hit = make_record("t-hit", cache={"status": "hit", "source_trace_id": "t-source"})
    path = write_lines(tmp_path / "t.jsonl", [make_record("t-source"), hit])

    code, _, err = run_tool(capsys, "t-hit", "--traces", path)

    assert code == trace_tool.EXIT_INCONSISTENT
    assert "cache hit" in err


def test_same_record_in_two_files_is_fine_but_two_different_ones_are_not(tmp_path, capsys):
    record = make_record("t-twice")
    write_lines(tmp_path / "same" / "stdout.jsonl", [record])
    write_lines(tmp_path / "same" / "volume.jsonl", [record])
    write_lines(tmp_path / "differ" / "a.jsonl", [record])
    write_lines(tmp_path / "differ" / "b.jsonl", [make_record("t-twice", query="Edited?")])

    code, _, err = run_tool(capsys, "t-twice", "--traces", tmp_path / "same")
    assert code == 0, err

    code, _, err = run_tool(capsys, "t-twice", "--traces", tmp_path / "differ")
    assert code == trace_tool.EXIT_INCONSISTENT
    assert "two different records" in err


def test_line_separator_inside_a_string_does_not_split_the_record(tmp_path, capsys):
    # PDF text can hold U+2028. Written unescaped it is legal JSON, and only "\n" ends a line.
    question = "What happens?"
    context = [context_entry("chunk-ls", "4.1 Rule\nfirst second")]
    record = make_record(
        "t-ls",
        query=question,
        context=context,
        prompt=prompt_object(prompt_for(question, context)),
    )
    path = write_lines(tmp_path / "t.jsonl", [json.dumps(record, ensure_ascii=False)])

    code, out, err = run_tool(capsys, "t-ls", "--traces", path)

    assert code == 0, err
    assert record["prompt"]["text"] in out


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8-sig"])
def test_files_saved_by_windows_tools_can_be_read(tmp_path, capsys, encoding):
    # Windows PowerShell 5.1 saves `kubectl logs > file` as UTF-16 with a byte order mark.
    path = tmp_path / "logs.txt"
    path.write_text(json.dumps(make_record("t-enc")) + "\r\n", encoding=encoding)

    code, _, err = run_tool(capsys, "t-enc", "--traces", path)

    assert code == 0, err


def test_a_repeated_traces_option_adds_files_and_drops_none(tmp_path, capsys):
    one = write_lines(tmp_path / "one.jsonl", [make_record("t-source")])
    two = write_lines(tmp_path / "two.jsonl", [cache_hit("t-hit", "t-source")])

    code, _, err = run_tool(capsys, "t-hit", "--traces", one, "--traces", two)

    assert code == 0, err


def test_default_traces_folder_is_used_without_the_option(tmp_path, capsys, monkeypatch):
    write_lines(tmp_path / "traces" / "pod.jsonl", [make_record("t-default")])
    monkeypatch.setattr(trace_tool, "DEFAULT_TRACES", tmp_path / "traces")

    code, _, err = run_tool(capsys, "t-default")

    assert code == 0, err


def test_unknown_traces_path_is_an_input_error(tmp_path, capsys):
    code, _, err = run_tool(capsys, "t-any", "--traces", tmp_path / "nowhere")

    assert code == trace_tool.EXIT_BAD_INPUT
    assert "nowhere" in err


def test_exit_codes_are_distinct_and_non_zero():
    codes = [
        trace_tool.EXIT_BAD_INPUT,
        trace_tool.EXIT_NOT_FOUND,
        trace_tool.EXIT_HASH_MISMATCH,
        trace_tool.EXIT_CACHE_SOURCE,
        trace_tool.EXIT_INCONSISTENT,
        trace_tool.EXIT_NO_PROMPT,
    ]
    assert len(set(codes)) == len(codes)
    assert all(code > 1 for code in codes)  # 1 is what an unexpected Python error returns


def test_script_prints_non_ascii_on_a_cp1252_console(tmp_path):
    """Run the real command with the encoding a redirected Windows console would use."""
    record = non_ascii_record("t-console")
    path = write_lines(tmp_path / "t.jsonl", [record])
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONUTF8", None)

    run = subprocess.run(
        [sys.executable, str(TOOL), "t-console", "--traces", str(path)],
        capture_output=True,
        env=env,
        cwd=tmp_path,
        timeout=60,
    )

    assert run.returncode == 0, run.stderr.decode("utf-8", errors="replace")
    printed = run.stdout.decode("utf-8").replace("\r\n", "\n")
    assert record["prompt"]["text"] in printed
