"""Tests for the harness itself. A checker nobody has tested is not evidence.

Every important check gets a pair: it fails on a bad artifact and passes on a good one.
All fixtures live in tmp_path; nothing here writes to the real eval/ or traces/.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / ".claude" / "hooks"))

import guard
import loop
import verify
from checks import FAIL, PASS, PENDING, Context, run_check


def write(root: Path, rel: str, content: object) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, list):
        content = "".join(json.dumps(row) + "\n" for row in content)
    elif isinstance(content, dict):
        content = json.dumps(content)
    path.write_text(str(content), encoding="utf-8")
    return path


def status(root: Path, spec: dict, rid: str = "X") -> str:
    return run_check(Context(root=root), spec, rid).status


# ---------------------------------------------------------------- the red baseline
def test_no_requirement_passes_on_an_empty_repository(tmp_path: Path) -> None:
    _, reqs = verify.load(ROOT)
    ctx = Context(root=tmp_path, fast=True, base_commit="0000000")
    passed = [r["id"] for r in reqs if verify.evaluate(ctx, r)[0] == PASS]
    assert passed == [], f"these checks pass vacuously: {passed}"


def test_requirements_file_is_internally_consistent() -> None:
    _, reqs = verify.load(ROOT)
    structural = [p for p in verify.lint(ROOT, reqs, None) if "IMPLEMENTATION-PLAN" not in p]
    assert structural == []


# ---------------------------------------------------------------- golden set
def golden(human: int, synthetic_reviewed: int, answerable: int = 18) -> list[dict]:
    rows = []
    for i in range(26):
        answer = i < answerable
        rows.append({
            "id": f"g{i}", "question": f"question {i}", "hard": i < 6,
            "category": "answerable" if answer else "unanswerable",
            "origin": "human" if i < human else "synthetic",
            "reviewed_by": "M1" if human <= i < human + synthetic_reviewed else None,
            "relevant": [{"doc_id": "d", "page": 1}] if answer else [],
        })
    return rows


@pytest.mark.parametrize(("human", "reviewed", "expected"),
                         [(10, 9, PASS), (7, 9, FAIL), (10, 7, FAIL)])
def test_golden_provenance(tmp_path: Path, human: int, reviewed: int, expected: str) -> None:
    write(tmp_path, "eval/golden.jsonl", golden(human, reviewed))
    spec = {"type": "golden", "path": "eval/golden.jsonl", "only": "provenance"}
    assert status(tmp_path, spec) == expected


def test_golden_composition_needs_fifteen_answerable(tmp_path: Path) -> None:
    spec = {"type": "golden", "path": "eval/golden.jsonl", "only": "composition"}
    write(tmp_path, "eval/golden.jsonl", golden(10, 9, answerable=14))
    assert status(tmp_path, spec) == FAIL
    write(tmp_path, "eval/golden.jsonl", golden(10, 9))
    assert status(tmp_path, spec) == PASS


# ---------------------------------------------------------------- verification and ingest
def report_doc(fail_check: str | None, decision: str) -> dict:
    return {"file": "a.pdf", "decision": decision, "checks": {
        c: {"status": "fail" if c == fail_check else "pass", "reason": "because"}
        for c in ("quality", "completeness", "recency", "uniqueness")}}


@pytest.mark.parametrize(("doc", "expected"), [
    (report_doc(None, "accepted"), PASS),
    (report_doc("uniqueness", "quarantined"), PASS),
    (report_doc("uniqueness", "accepted"), FAIL),      # failed a check yet indexed
    (report_doc(None, "quarantined"), FAIL),
])
def test_verification_report_decisions(tmp_path: Path, doc: dict, expected: str) -> None:
    write(tmp_path, "eval/results/verification/r.json",
          {"run_id": "r", "started_at": "t", "documents": [doc]})
    spec = {"type": "verification_report", "glob": "eval/results/verification/*.json"}
    assert status(tmp_path, spec) == expected


def test_ingest_zero_needs_a_real_earlier_embedding_run(tmp_path: Path) -> None:
    spec = {"type": "ingest_zero", "glob": "eval/results/ingest/*.json"}

    def run(name: str, started: str, embedded: int, unchanged: int) -> None:
        write(tmp_path, f"eval/results/ingest/{name}.json", {
            "run_id": name, "started_at": started, "collection": "c", "lake_state_id": "s",
            "counts": {"chunks_embedded": embedded, "documents_unchanged": unchanged}})

    run("b", "2", 0, 9)
    assert status(tmp_path, spec) == FAIL                 # zero with nothing before it
    run("a", "1", 120, 0)
    assert status(tmp_path, spec) == PASS


# ---------------------------------------------------------------- proofs and gate
def test_undo_proof_requires_previous_collection_and_embedding(tmp_path: Path) -> None:
    def stage(coll: str, model: str, trace: str) -> dict:
        return {"release_id": coll, "collection": coll, "embed_model": model,
                "embed_version": model + "@1", "trace_id": trace}

    proof = {"before": stage("v1", "m1", "t1"), "released": stage("v2", "m2", "t2"),
             "after_undo": stage("v2", "m2", "t3")}
    spec = {"type": "proof", "kind": "undo", "path": "p.json"}
    write(tmp_path, "p.json", proof)
    assert status(tmp_path, spec) == FAIL
    proof["after_undo"] = stage("v1", "m1", "t3")
    write(tmp_path, "p.json", proof)
    assert status(tmp_path, spec) == PASS


def test_gate_results_need_a_fail_and_a_different_pass(tmp_path: Path) -> None:
    spec = {"type": "gate_results", "glob": "eval/results/gate/*.json"}

    def result(name: str, candidate: str, decision: str, code: int) -> None:
        write(tmp_path, f"eval/results/gate/{name}.json", {
            "decision": decision, "exit_code": code, "rules": [1], "thresholds_sha256": "h",
            "candidate_run_id": "c", "baseline_run_id": "b", "candidate_release": candidate})

    result("1", "bad", "fail", 1)
    assert status(tmp_path, spec) == FAIL
    result("2", "good", "pass", 0)
    assert status(tmp_path, spec) == PASS
    result("3", "liar", "pass", 1)
    assert status(tmp_path, spec) == FAIL                 # exit code contradicts decision


# ---------------------------------------------------------------- k8s rules
CANDIDATE_YAML = """
apiVersion: apps/v1
kind: Deployment
metadata: {{name: api-candidate}}
spec:
  template:
    metadata: {{labels: {{app: {label}}}}}
    spec: {{containers: []}}
---
apiVersion: v1
kind: Service
metadata: {{name: api-candidate}}
spec: {{selector: {{app: {label}}}}}
---
apiVersion: v1
kind: Service
metadata: {{name: api}}
spec: {{selector: {{app: api}}}}
"""


@pytest.mark.parametrize(("label", "expected"), [("api", FAIL), ("api-candidate", PASS)])
def test_candidate_must_not_receive_live_traffic(tmp_path: Path, label: str,
                                                 expected: str) -> None:
    pytest.importorskip("yaml")
    write(tmp_path, "k8s/c.yaml", CANDIDATE_YAML.format(label=label))
    assert status(tmp_path, {"type": "k8s", "rule": "candidate_isolated"}) == expected


# ---------------------------------------------------------------- humans only
def test_manual_check_needs_both_signatures(tmp_path: Path) -> None:
    spec = {"type": "manual", "both": True}
    assert status(tmp_path, spec, "D5") == PENDING
    entry = '[attest."D5"]\nby = {by}\ndate = "d"\nnote = "n"\n'
    write(tmp_path, "harness/state/attestations.toml", entry.format(by='["M1"]'))
    assert status(tmp_path, spec, "D5") == PENDING
    write(tmp_path, "harness/state/attestations.toml", entry.format(by='["M1", "M2"]'))
    assert status(tmp_path, spec, "D5") == PASS


def test_capture_file_must_come_from_the_capture_tool(tmp_path: Path) -> None:
    spec = {"type": "capture", "name": "rollout-status", "patterns": ["successfully rolled out"]}
    write(tmp_path, "eval/results/cluster/rollout-status.txt", "successfully rolled out\n")
    assert status(tmp_path, spec) == FAIL
    write(tmp_path, "eval/results/cluster/rollout-status.txt",
          "# captured: t\n# command: c\n# exit: 0\n# git: s\nsuccessfully rolled out\n")
    assert status(tmp_path, spec) == PASS


# ---------------------------------------------------------------- guard hook
def decision(tmp_path: Path, tool: str, **tool_input: str) -> str | None:
    verdict = guard.decide({"tool_name": tool, "tool_input": tool_input}, tmp_path)
    return verdict[0] if verdict else None


@pytest.mark.parametrize("rel", ["eval/results/suite/x.json", "traces/a.jsonl",
                                 "harness/state/attestations.toml", "eval/judge_labels.jsonl",
                                 "harness/members.toml"])
def test_guard_denies_protected_files(tmp_path: Path, rel: str) -> None:
    assert decision(tmp_path, "Write", file_path=str(tmp_path / rel), content="x") == "deny"


def test_guard_allows_ordinary_edits(tmp_path: Path) -> None:
    for rel in ("indexer/pipeline/verify.py", "eval/trace_tool.py", "api/app/trace.py",
                "k8s/gate-thresholds.yaml"):
        assert decision(tmp_path, "Write", file_path=str(tmp_path / rel), content="x") is None


def test_guard_blocks_new_human_labels_but_allows_other_golden_edits(tmp_path: Path) -> None:
    path = str(tmp_path / "eval" / "golden.jsonl")
    assert decision(tmp_path, "Write", file_path=path, content='{"origin": "synthetic"}') is None
    assert decision(tmp_path, "Write", file_path=path, content='{"origin": "human"}') == "deny"
    assert decision(tmp_path, "Edit", file_path=path, old_string='"reviewed_by": null',
                    new_string='"reviewed_by": "M1"') == "deny"
    assert decision(tmp_path, "Edit", file_path=path,
                    old_string='"origin": "human", "q": "a"',
                    new_string='"origin": "human", "q": "b"') is None


@pytest.mark.parametrize(("command", "expected"), [
    ("git push --force origin main", "deny"),
    ("git push -f", "deny"),
    ('git commit --author="Other <o@example.com>" -m x', "deny"),
    ("git config user.email other@example.com", "deny"),
    ("echo '{}' > eval/results/suite/fake.json", "deny"),
    ("cat x >> traces/live.jsonl", "deny"),
    ("Set-Content -Path eval\\results\\gate\\a.json -Value '{}'", "deny"),
    ("rm harness/state/attestations.toml", "deny"),
    ("minikube delete", "ask"),
    ("kubectl delete pvc rahbar-datalake -n rahbar", "ask"),
    ("git reset --hard HEAD~1", "ask"),
    ("git push origin feature/x", None),
    ("git config user.email", None),
    ("kubectl delete job rahbar-eval -n rahbar --ignore-not-found", None),
    ("kubectl cp rahbar/evidence-shell:data/results/. eval/results/suite/", None),
    ("grep trace_id traces/a.jsonl > out.txt", None),
    ("ls traces/ 2>/dev/null", None),
    ("python harness/verify.py --id A2.1 > verify.log", None),
])
def test_guard_shell_rules(tmp_path: Path, command: str, expected: str | None) -> None:
    assert decision(tmp_path, "Bash", command=command) == expected


def test_guard_freezes_the_checker_during_a_loop(tmp_path: Path) -> None:
    target = str(tmp_path / "harness" / "checks.py")
    assert decision(tmp_path, "Write", file_path=target, content="x") is None
    loop.write_state(tmp_path, {"active": True})
    assert decision(tmp_path, "Write", file_path=target, content="x") == "deny"
    progress = str(tmp_path / "harness" / "state" / "progress.md")
    assert decision(tmp_path, "Write", file_path=progress, content="x") is None


def test_guard_process_emits_the_documented_json(tmp_path: Path) -> None:
    payload = {"tool_name": "Write", "tool_input": {
        "file_path": str(tmp_path / "traces" / "x.jsonl"), "content": "{}"}}
    run = subprocess.run(
        [sys.executable, str(ROOT / ".claude" / "hooks" / "guard.py")],
        input=json.dumps(payload).encode(), capture_output=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(tmp_path)}, check=False)
    out = json.loads(run.stdout)["hookSpecificOutput"]
    assert run.returncode == 0
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"


# ---------------------------------------------------------------- loop controller
def mini_repo(tmp_path: Path) -> None:
    write(tmp_path, "harness/requirements.toml", """
[meta]
base_commit = "x"
[[req]]
id = "T.1"
part = "P"
phase = 0
kind = "auto"
owner = "M1"
origin = "plan"
title = "first"
checks = [ { type = "contains", path = "one.txt", patterns = ["done"] } ]
[[req]]
id = "T.2"
part = "P"
phase = 1
kind = "auto"
owner = "M1"
origin = "plan"
title = "second"
depends = ["T.1"]
checks = [ { type = "contains", path = "two.txt", patterns = ["done"] } ]
""")


def test_loop_advances_only_when_the_check_passes(tmp_path: Path) -> None:
    mini_repo(tmp_path)
    state, _ = loop.start(tmp_path, "P", None, max_iterations=10, max_attempts=3)
    assert state and state["target"] == "T.1"
    assert loop.tick(tmp_path, "s1")["block"] is True
    assert loop.read_state(tmp_path)["target"] == "T.1"
    write(tmp_path, "one.txt", "done")
    assert loop.tick(tmp_path, "s1")["block"] is True
    assert loop.read_state(tmp_path)["target"] == "T.2"
    write(tmp_path, "two.txt", "done")
    assert loop.tick(tmp_path, "s1")["block"] is False
    assert loop.read_state(tmp_path)["outcome"] == "scope_exhausted"


def test_loop_escalates_instead_of_spinning(tmp_path: Path) -> None:
    mini_repo(tmp_path)
    loop.start(tmp_path, "P", None, max_iterations=10, max_attempts=2)
    blocks = [loop.tick(tmp_path, "s1")["block"] for _ in range(3)]
    assert blocks == [True, True, False]
    assert loop.read_state(tmp_path)["outcome"] == "escalated"


def test_loop_respects_the_iteration_budget(tmp_path: Path) -> None:
    mini_repo(tmp_path)
    loop.start(tmp_path, "P", None, max_iterations=1, max_attempts=9)
    assert [loop.tick(tmp_path, "s1")["block"] for _ in range(2)] == [True, False]
    assert loop.read_state(tmp_path)["outcome"] == "budget_exhausted"


def test_loop_ignores_other_sessions(tmp_path: Path) -> None:
    mini_repo(tmp_path)
    loop.start(tmp_path, "P", None, max_iterations=5, max_attempts=3)
    loop.tick(tmp_path, "owner")
    assert loop.tick(tmp_path, "someone-else") == {"block": False, "message": ""}


# ---------------------------------------------------------------- stages
def test_build_stage_ignores_evidence_but_all_does_not(tmp_path: Path) -> None:
    req = {"id": "T", "checks": [
        {"type": "contains", "path": "code.txt", "patterns": ["done"]},
        {"type": "result", "glob": "eval/results/x/*.json", "keys": ["k"]}]}
    write(tmp_path, "code.txt", "done")
    ctx = Context(root=tmp_path)
    assert verify.evaluate(ctx, req, "build")[0] == PASS
    assert verify.evaluate(ctx, req, "evidence")[0] == FAIL
    assert verify.evaluate(ctx, req, "all")[0] == FAIL


def test_stage_without_checks_is_not_a_pass(tmp_path: Path) -> None:
    req = {"id": "T", "checks": [{"type": "manual"}]}
    assert verify.evaluate(Context(root=tmp_path), req, "build")[0] == verify.NA


def test_thresholds_are_not_a_build_task() -> None:
    _, reqs = verify.load(ROOT)
    gate_config = next(r for r in reqs if r["id"] == "C2.1")
    assert verify.specs_for(gate_config, "build") == []
