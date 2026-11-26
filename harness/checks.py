"""Check types used by harness/verify.py.

Each check takes (ctx, spec) and returns a Result. A check reads artifacts that the
implementation's own scripts produce (docs/a3/CONTRACTS.md). A missing artifact is
always a failure: nothing here may pass vacuously.

Limits: these checks validate shape and internal consistency. They cannot prove an
artifact was not typed by hand. The guard hook, the provenance fields and human review
cover that gap.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PASS, FAIL, SKIP, PENDING = "PASS", "FAIL", "SKIP", "PENDING_HUMAN"

FOUR_CHECKS = ("quality", "completeness", "recency", "uniqueness")
PLANTED_MINIMUM = {
    "exact_duplicate": 2,
    "near_duplicate": 1,
    "truncated_or_corrupt": 1,
    "missing_metadata": 1,
    "newer_version": 1,
    "injection": 1,
}
SUITE_METRICS = (
    "component.recall_at_3",
    "component.recall_at_5",
    "component.mrr",
    "component.ndcg_at_5",
    "end_to_end.faithfulness",
    "end_to_end.abstention_correctness",
    "end_to_end.citation_validity",
    "end_to_end.latency_ms_p50",
    "end_to_end.latency_ms_p95",
)


@dataclass
class Context:
    root: Path
    fast: bool = False
    members: tuple[str, ...] = ("M1", "M2")
    base_commit: str = ""


@dataclass
class Result:
    status: str
    detail: str


class CheckError(Exception):
    """An artifact is missing or malformed. Reported as FAIL with this message."""


def ok(detail: str = "ok") -> Result:
    return Result(PASS, detail)


def fail(detail: str) -> Result:
    return Result(FAIL, detail)


def need(condition: object, message: str) -> None:
    if not condition:
        raise CheckError(message)


# ---------------------------------------------------------------- loaders
def _path(ctx: Context, rel: str) -> Path:
    p = ctx.root / rel
    need(p.is_file(), f"missing {rel}")
    return p


def _glob(ctx: Context, pattern: str) -> list[Path]:
    return sorted(p for p in ctx.root.glob(pattern) if p.is_file())


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CheckError(f"{path.name}: not valid JSON ({exc})") from exc


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise CheckError(f"{path.name}:{n}: not valid JSON ({exc})") from exc
        need(isinstance(row, dict), f"{path.name}:{n}: not a JSON object")
        rows.append(row)
    return rows


def _all_json(ctx: Context, pattern: str) -> list[tuple[Path, dict]]:
    found = [(p, _json(p)) for p in _glob(ctx, pattern)]
    need(found, f"no files match {pattern}")
    return found


_MISSING = object()


def lookup(data: Any, dotted: str) -> list[Any]:
    """Resolve 'a.b' or 'list[].key'. Returns every matched value; _MISSING where absent."""
    values = [data]
    for part in dotted.split("."):
        each = part.endswith("[]")
        key = part[:-2] if each else part
        nxt: list[Any] = []
        for value in values:
            child = value.get(key, _MISSING) if isinstance(value, dict) else _MISSING
            if each and isinstance(child, list):
                nxt.extend(child)
            else:
                nxt.append(child)
        values = nxt
    return values


def _git(ctx: Context, *args: str) -> tuple[int, str]:
    try:
        run = subprocess.run(["git", "-C", str(ctx.root), *args], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return run.returncode, run.stdout


def _require_provenance(ctx: Context, data: dict, name: str) -> None:
    for key in ("run_id", "started_at", "git_sha", "golden_sha256", "corpus_state_id",
                "release_id"):
        need(data.get(key), f"{name}: provenance field '{key}' is missing")
    sha = str(data["git_sha"])
    need(re.fullmatch(r"[0-9a-f]{7,40}", sha), f"{name}: git_sha '{sha}' is not a commit id")
    code, _ = _git(ctx, "cat-file", "-e", f"{sha}^{{commit}}")
    need(code == 0, f"{name}: git_sha {sha} does not exist in this repository")


# ---------------------------------------------------------------- code checks
def _run(ctx: Context, argv: list[str], cwd: str) -> Result:
    argv = [sys.executable if a == "{python}" else a for a in argv]
    try:
        run = subprocess.run(argv, cwd=ctx.root / cwd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return fail(f"could not run: {exc}")
    if run.returncode == 0:
        return ok("command passed")
    tail = " | ".join((run.stdout + run.stderr).strip().splitlines()[-3:])
    return fail(f"exit {run.returncode}: {tail[:400]}")


def check_command(ctx: Context, spec: dict) -> Result:
    if ctx.fast:
        return Result(SKIP, "command skipped in --fast mode")
    return _run(ctx, spec["argv"], spec.get("cwd", "."))


def check_tests(ctx: Context, spec: dict) -> Result:
    rel = spec["file"]
    source = _path(ctx, rel).read_text(encoding="utf-8")
    missing = [n for n in spec["names"]
               if not re.search(rf"^\s*(async\s+)?def\s+{re.escape(n)}\s*\(", source, re.M)]
    need(not missing, f"{rel} lacks required tests: {', '.join(missing)}")
    if ctx.fast:
        return Result(SKIP, "tests present; not run in --fast mode")
    top = rel.split("/")[0]
    # api/ and indexer/ carry their own pytest config, so run from inside them.
    cwd, target = (top, rel[len(top) + 1:]) if top in ("api", "indexer") else (".", rel)
    return _run(ctx, ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider", target], cwd)


def check_contains(ctx: Context, spec: dict) -> Result:
    text = _path(ctx, spec["path"]).read_text(encoding="utf-8")
    missing = [p for p in spec["patterns"] if not re.search(p, text, re.I | re.M)]
    need(not missing, f"{spec['path']} lacks: {', '.join(missing)}")
    lines = len([ln for ln in text.splitlines() if ln.strip()])
    need(lines >= spec.get("min_lines", 0),
         f"{spec['path']} has {lines} lines, needs {spec.get('min_lines')}")
    return ok()


def check_forbid(ctx: Context, spec: dict) -> Result:
    files = [p for g in spec["globs"] for p in _glob(ctx, g) if "tests" not in p.parts]
    need(files, f"no source files match {spec['globs']} (cannot prove absence)")
    hits = []
    for path in files:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if any(re.search(p, line) for p in spec["patterns"]):
                hits.append(f"{path.relative_to(ctx.root).as_posix()}:{n}")
    need(not hits, f"forbidden write calls: {', '.join(hits[:6])}")
    return ok(f"{len(files)} files clean")


def check_readme_parts(ctx: Context, spec: dict) -> Result:
    text = _path(ctx, spec["path"]).read_text(encoding="utf-8")
    sections = re.split(r"^(?=##\s)", text, flags=re.M)
    missing = []
    for part in spec["parts"]:
        if not any(re.match(rf"##\s+Part\s+{part}\b", s) and "```" in s for s in sections):
            missing.append(part)
    need(not missing, f"README lacks a '## Part X' section with a command: {', '.join(missing)}")
    return ok()


# ---------------------------------------------------------------- data checks
def check_golden(ctx: Context, spec: dict) -> Result:
    rows = _jsonl(_path(ctx, spec["path"]))
    ids, questions = set(), set()
    for row in rows:
        rid = row.get("id")
        need(rid and rid not in ids, f"golden: missing or repeated id {rid!r}")
        ids.add(rid)
        question = str(row.get("question", "")).strip().lower()
        need(question and question not in questions, f"golden {rid}: empty or repeated question")
        questions.add(question)
        need(row.get("category") in ("answerable", "unanswerable"), f"golden {rid}: bad category")
        need(row.get("origin") in ("human", "synthetic"), f"golden {rid}: origin not labelled")
        need(isinstance(row.get("hard"), bool), f"golden {rid}: 'hard' must be true or false")
        if row["category"] == "answerable":
            relevant = row.get("relevant") or []
            need(relevant and all(r.get("doc_id") and isinstance(r.get("page"), int)
                                  for r in relevant),
                 f"golden {rid}: answerable needs ground-truth doc_id and page")

    def count(pred: Callable[[dict], bool]) -> int:
        return sum(1 for r in rows if pred(r))

    if spec.get("only", "composition") == "composition":
        actual = {
            "total": (len(rows), 25),
            "answerable": (count(lambda r: r["category"] == "answerable"), 15),
            "unanswerable": (count(lambda r: r["category"] == "unanswerable"), 5),
            "hard": (count(lambda r: r["hard"]), 5),
        }
    else:
        actual = {
            "human": (count(lambda r: r["origin"] == "human"), 8),
            "synthetic_reviewed": (
                count(lambda r: r["origin"] == "synthetic" and r.get("reviewed_by")), 8),
        }
    short = [f"{k}={have}/{want}" for k, (have, want) in actual.items() if have < want]
    need(not short, "golden set short: " + ", ".join(short))
    return ok(", ".join(f"{k}={have}" for k, (have, _) in actual.items()))


def check_pairs(ctx: Context, spec: dict) -> Result:
    rows = _jsonl(_path(ctx, spec["path"]))
    need(all(r.get("a") and r.get("b") for r in rows), "pairs: every row needs 'a' and 'b'")
    for kind in ("paraphrase", "unrelated"):
        have = sum(1 for r in rows if r.get("type") == kind)
        need(have >= spec["min"], f"pairs: {kind}={have}, needs {spec['min']}")
    return ok(f"{len(rows)} pairs")


def check_live_queries(ctx: Context, spec: dict) -> Result:
    rows = _jsonl(_path(ctx, spec["path"]))
    need(all(r.get("id") and r.get("query") for r in rows), "live queries: id and query required")
    need(len({r["query"].strip().lower() for r in rows}) == len(rows), "live queries repeat")
    inside = sum(1 for r in rows if r.get("expected") == "in_domain")
    outside = sum(1 for r in rows if r.get("expected") == "out_of_coverage")
    need(len(rows) >= 40 and inside >= 26 and outside >= 12,
         f"live queries: total={len(rows)}/40 in_domain={inside}/~28 uncovered={outside}/12")
    return ok(f"total={len(rows)} in_domain={inside} uncovered={outside}")


def check_batch_b(ctx: Context, spec: dict) -> Result:
    manifest = _path(ctx, spec["path"])
    docs = _json(manifest).get("documents", [])
    need(len(docs) >= 10, f"batch B has {len(docs)} documents, needs 10")
    absent = [d.get("file") for d in docs
              if not d.get("file") or not (manifest.parent / d["file"]).is_file()]
    need(not absent, f"batch B files missing: {absent[:5]}")
    for problem, want in PLANTED_MINIMUM.items():
        have = sum(1 for d in docs if d.get("planted") == problem)
        need(have >= want, f"batch B: planted '{problem}'={have}, needs {want}")
    return ok(f"{len(docs)} documents")


def check_verification_report(ctx: Context, spec: dict) -> Result:
    reports = _all_json(ctx, spec["glob"])
    batch = spec.get("batch")
    if batch:
        reports = [(p, r) for p, r in reports if r.get("batch") == batch]
        need(reports, f"no verification report for batch {batch}")
    for path, report in reports:
        need(report.get("run_id") and report.get("started_at"), f"{path.name}: run_id/started_at")
        for doc in report.get("documents", []):
            name = f"{path.name}:{doc.get('file')}"
            for check in FOUR_CHECKS:
                entry = (doc.get("checks") or {}).get(check) or {}
                need(entry.get("status") in ("pass", "fail"), f"{name}: {check} has no pass/fail")
                need(str(entry.get("reason", "")).strip(), f"{name}: {check} has no reason")
            statuses = [doc["checks"][c]["status"] for c in FOUR_CHECKS]
            decision = doc.get("decision")
            need(decision in ("accepted", "quarantined", "unchanged"), f"{name}: bad decision")
            need((decision == "quarantined") == ("fail" in statuses),
                 f"{name}: decision '{decision}' contradicts check results {statuses}")
    biggest = max(len(r.get("documents", [])) for _, r in reports)
    need(biggest >= spec.get("min_documents", 1),
         f"largest report covers {biggest} documents, needs {spec.get('min_documents')}")
    return ok(f"{len(reports)} report(s)")


def check_ingest_zero(ctx: Context, spec: dict) -> Result:
    runs = [r for _, r in _all_json(ctx, spec["glob"])]
    runs.sort(key=lambda r: str(r.get("started_at", "")))
    for i, later in enumerate(runs):
        counts = later.get("counts", {})
        if counts.get("chunks_embedded") != 0 or not counts.get("documents_unchanged"):
            continue
        for earlier in runs[:i]:
            if (earlier.get("collection") == later.get("collection")
                    and earlier.get("counts", {}).get("chunks_embedded", 0) > 0
                    and earlier.get("lake_state_id") == later.get("lake_state_id")):
                return ok(f"{later.get('run_id')} embedded 0 chunks after {earlier.get('run_id')}")
    return fail("no run embeds 0 chunks after an earlier embedding run on the same lake state")


def check_proof(ctx: Context, spec: dict) -> Result:
    data = _json(_path(ctx, spec["path"]))
    kind = spec["kind"]
    if kind == "quarantine":
        items = data.get("documents", [])
        need(items, "quarantine proof lists no documents")
        need(all(d.get("in_quarantine") is True and d.get("indexed_chunks") == 0 for d in items),
             "a failed document is not quarantined or still has chunks")
    elif kind == "changed":
        before, after = data.get("before", {}), data.get("after", {})
        target = data.get("changed_doc_id")
        need(target in before and target in after, "changed_doc_id missing from before/after")
        need(sorted(before[target]) != sorted(after[target]), "target document chunks unchanged")
        others = (set(before) | set(after)) - {target}
        need(others, "proof must include at least one other document")
        moved = [d for d in others if sorted(before.get(d, [])) != sorted(after.get(d, []))]
        need(not moved, f"other documents changed too: {moved[:5]}")
    elif kind == "deleted":
        need(data.get("doc_id"), "doc_id missing")
        need(data.get("count_before", 0) > 0, "count_before must be positive")
        need(data.get("count_after") == 0, "chunks remain for the deleted doc_id")
        need(data.get("orphan_chunks_after") == 0, "orphan chunks remain in the collection")
    elif kind == "restored":
        old, new = data.get("restored_version"), data.get("replaced_version")
        need(old and new and old != new, "restored_version and replaced_version required")
        need(data.get("retrieved_versions_before") == [new], "before: only new should retrieve")
        need(data.get("retrieved_versions_after") == [old], "after: only restored should retrieve")
        need(data.get("chunks_embedded") == 0, "restore must not re-embed")
    elif kind == "threshold_flip":
        a, b = data.get("run_a", {}), data.get("run_b", {})
        need(a.get("image_id") and a.get("image_id") == b.get("image_id"), "image changed")
        need(a.get("thresholds_sha256") != b.get("thresholds_sha256"), "thresholds unchanged")
        need({a.get("decision"), b.get("decision")} == {"pass", "fail"}, "decision did not flip")
    elif kind == "rejected_not_released":
        need(data.get("gate_decision") == "fail", "gate_decision must be 'fail'")
        need(data.get("candidate_release"), "candidate_release missing")
        before = data.get("live_release_before")
        need(before and before == data.get("live_release_after"), "live release changed")
        need(data["candidate_release"] != before, "candidate equals the live release")
    elif kind == "undo":
        stages = [data.get(k, {}) for k in ("before", "released", "after_undo")]
        for stage in stages:
            for key in ("release_id", "collection", "embed_model", "embed_version", "trace_id"):
                need(stage.get(key), f"undo proof: '{key}' missing")
        before, released, after = stages
        for key in ("collection", "embed_model", "embed_version"):
            need(after[key] == before[key], f"after undo, {key} differs from the previous release")
        # The assignment requires the candidate to have its own collection. Whether the
        # embedding model also changes depends on the C1 choice (docs/a3/DECISIONS.md D-02).
        need(released["collection"] != before["collection"], "release did not change collection")
        need(len({s["trace_id"] for s in stages}) == 3, "each stage needs its own trace")
    else:
        return fail(f"unknown proof kind {kind}")
    return ok(f"{kind} proof consistent")


def check_result(ctx: Context, spec: dict) -> Result:
    files = _glob(ctx, spec["glob"])
    need(files, f"no files match {spec['glob']}")
    latest = files[-1]
    data = _json(latest)
    if spec.get("provenance", True) and "suite" in spec["glob"]:
        _require_provenance(ctx, data, latest.name)
    absent = []
    for key in spec["keys"]:
        values = lookup(data, key)
        if not values or any(v is _MISSING or v is None or v == "" for v in values):
            absent.append(key)
    need(not absent, f"{latest.name} lacks: {', '.join(absent)}")
    return ok(latest.name)


def check_stochasticity(ctx: Context, spec: dict) -> Result:
    path, data = _all_json(ctx, spec["glob"])[-1]
    run_ids = data.get("run_ids", [])
    need(len(set(run_ids)) == 2, f"{path.name}: needs two different run_ids")
    need(data.get("seed") is not None, f"{path.name}: seed not recorded")
    metrics = data.get("metrics", {})
    for name in SUITE_METRICS:
        entry = metrics.get(name, {})
        need(all(k in entry for k in ("run1", "run2", "diff")), f"{path.name}: {name} not compared")
    need(len(str(data.get("explanation", ""))) >= 80, f"{path.name}: explanation too short")
    return ok(path.name)


def check_compare(ctx: Context, spec: dict) -> Result:
    path, data = _all_json(ctx, spec["glob"])[-1]
    need(data.get("change"), f"{path.name}: the one change is not described")
    base, cand = data.get("baseline", {}), data.get("candidate", {})
    for key in ("golden_sha256", "corpus_state_id"):
        need(base.get(key) and base.get(key) == cand.get(key), f"{path.name}: {key} differs")
    for level in ("module", "component", "end_to_end"):
        need(base.get(level) and cand.get(level), f"{path.name}: level '{level}' missing")
    return ok(path.name)


def check_gate_results(ctx: Context, spec: dict) -> Result:
    decisions: dict[str, str] = {}
    for path, data in _all_json(ctx, spec["glob"]):
        decision = data.get("decision")
        expected = {"pass": 0, "fail": 1}.get(decision)
        need(expected is not None, f"{path.name}: decision must be pass or fail")
        need(data.get("exit_code") == expected, f"{path.name}: exit_code contradicts decision")
        need(data.get("rules") and data.get("thresholds_sha256"), f"{path.name}: rules/hash")
        need(data.get("candidate_run_id") and data.get("baseline_run_id"), f"{path.name}: run ids")
        decisions[str(data.get("candidate_release"))] = decision
    failing = {c for c, d in decisions.items() if d == "fail"}
    passing = {c for c, d in decisions.items() if d == "pass"}
    need(failing and passing - failing, "need a failing candidate and a different passing one")
    return ok(f"fail={sorted(failing)} pass={sorted(passing)}")


def check_coverage_rise(ctx: Context, spec: dict) -> Result:
    by_label = {d.get("label"): d for _, d in _all_json(ctx, spec["glob"])}
    before, after = by_label.get("before"), by_label.get("after")
    need(before and after, "need coverage results labelled 'before' and 'after'")
    need(before.get("threshold_similarity") == after.get("threshold_similarity"),
         "similarity threshold changed between before and after")
    need(before.get("live_queries_sha256") == after.get("live_queries_sha256") is not None,
         "live query log changed between before and after")
    added = after.get("golden_count", 0) - before.get("golden_count", 0)
    need(added >= spec["min_added"], f"only {added} golden questions added")
    need(after["share_covered"] > before["share_covered"], "coverage did not rise")
    return ok(f"{before['share_covered']} -> {after['share_covered']}")


def check_jsonl_fields(ctx: Context, spec: dict) -> Result:
    files = _glob(ctx, spec["glob"])
    need(files, f"no files match {spec['glob']}")
    records = [(p, r) for p in files for r in _jsonl(p) if r.get("kind") == "trace"]
    need(len(records) >= spec["min_records"],
         f"{len(records)} trace records, needs {spec['min_records']}")
    for path, record in records:
        tid = record.get("trace_id", "?")
        for key in spec.get("present", []):
            need(all(v is not _MISSING for v in lookup(record, key)),
                 f"{path.name} trace {tid}: '{key}' absent")
        for key in spec.get("nonnull", []):
            need(all(v is not _MISSING and v is not None for v in lookup(record, key)),
                 f"{path.name} trace {tid}: '{key}' null or absent")
    need(any(r.get("retrieved") for _, r in records), "no trace has any retrieved chunk")
    ids = [r.get("trace_id") for _, r in records]
    need(len(ids) == len(set(ids)), "trace ids repeat")
    return ok(f"{len(records)} traces")


def check_guardrail_evidence(ctx: Context, spec: dict) -> Result:
    files = _glob(ctx, spec["glob"])
    need(files, f"no files match {spec['glob']}")
    for path in files:
        for record in _jsonl(path):
            for action in record.get("guardrail_actions") or []:
                if (str(action.get("rail", "")).startswith("retrieval")
                        and action.get("action") and action.get("doc_id")):
                    return ok(f"trace {record.get('trace_id')} logged {action['action']}")
    return fail("no trace records a retrieval rail action with a doc_id")


# ---------------------------------------------------------------- cluster checks
def check_capture(ctx: Context, spec: dict) -> Result:
    rel = f"eval/results/cluster/{spec['name']}.txt"
    text = _path(ctx, rel).read_text(encoding="utf-8", errors="replace")
    head = text.splitlines()[:6]
    for tag in ("# captured:", "# command:", "# exit:", "# git:"):
        need(any(line.startswith(tag) for line in head), f"{rel}: not written by harness/capture.py")
    body = "\n".join(ln for ln in text.splitlines() if not ln.startswith("# "))
    missing = [p for p in spec.get("patterns", []) if not re.search(p, body, re.I)]
    need(not missing, f"{rel}: output lacks {', '.join(missing)}")
    return ok(rel)


def _manifests(ctx: Context) -> list[dict]:
    try:
        import yaml
    except ImportError as exc:
        raise CheckError("PyYAML is needed for k8s checks: pip install pyyaml") from exc
    docs = []
    for path in _glob(ctx, "k8s/**/*.yaml"):
        try:
            loaded = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError as exc:
            raise CheckError(f"{path.name}: invalid YAML ({exc})") from exc
        for doc in loaded:
            if isinstance(doc, dict) and doc.get("kind"):
                docs.append({**doc, "_file": path.relative_to(ctx.root).as_posix()})
    need(docs, "no manifests under k8s/")
    return docs


def _find(docs: list[dict], kind: str, name: str | None = None) -> list[dict]:
    return [d for d in docs if d["kind"] == kind
            and (name is None or d.get("metadata", {}).get("name") == name)
            # overlay patches are fragments, not whole objects
            and "overlays/" not in d["_file"]]


def _pod_spec(doc: dict) -> dict:
    spec = doc.get("spec", {})
    if doc["kind"] == "CronJob":
        spec = spec.get("jobTemplate", {}).get("spec", {})
    return spec.get("template", {}).get("spec", {})


def _pod_labels(doc: dict) -> dict:
    return doc.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})


def _claims(doc: dict) -> set[str]:
    return {v["persistentVolumeClaim"]["claimName"]
            for v in _pod_spec(doc).get("volumes", []) or [] if "persistentVolumeClaim" in v}


def _configmaps_used(doc: dict) -> set[str]:
    pod, names = _pod_spec(doc), set()
    for volume in pod.get("volumes", []) or []:
        if "configMap" in volume:
            names.add(volume["configMap"].get("name"))
    for container in pod.get("containers", []):
        for source in container.get("envFrom", []) or []:
            if "configMapRef" in source:
                names.add(source["configMapRef"].get("name"))
    return names


def _rule_core_workloads(docs: list[dict]) -> None:
    need(_find(docs, "Deployment", "api"), "no Deployment 'api'")
    need(_find(docs, "Service", "api"), "no Service 'api'")
    stores = _find(docs, "StatefulSet", "chroma")
    need(stores and stores[0]["spec"].get("volumeClaimTemplates"),
         "no chroma StatefulSet with volumeClaimTemplates")


def _rule_datalake_pvc(docs: list[dict]) -> None:
    need(_find(docs, "PersistentVolumeClaim", "rahbar-datalake"), "no PVC 'rahbar-datalake'")
    users = [d for d in docs if d["kind"] in ("Job", "CronJob") and "rahbar-datalake" in _claims(d)]
    need(users, "no Job or CronJob mounts rahbar-datalake")


def _rule_ingest_jobs(docs: list[dict]) -> None:
    def is_ingest(d: dict) -> bool:
        meta = d.get("metadata", {})
        return str(meta.get("name", "")).startswith(("ingest", "reindex"))

    jobs = [d for d in _find(docs, "Job") if is_ingest(d)]
    crons = [d for d in _find(docs, "CronJob") if is_ingest(d)]
    need(jobs, "no ingestion Job (name starting 'ingest')")
    need(crons, "no ingestion CronJob")
    need(all(c["spec"].get("concurrencyPolicy") == "Forbid" for c in crons),
         "CronJob must set concurrencyPolicy: Forbid")
    for doc in jobs + crons:
        for container in _pod_spec(doc).get("containers", []):
            need("--dummy" not in (container.get("args") or []), "ingestion still runs --dummy")
        need("rahbar-datalake" in _claims(doc), f"{doc['_file']}: does not mount the data lake")


def _rule_chroma_ingress_restricted(docs: list[dict]) -> None:
    policies = [d for d in _find(docs, "NetworkPolicy")
                if d["spec"].get("podSelector", {}).get("matchLabels", {}).get("app") == "chroma"]
    need(policies, "no NetworkPolicy selects app=chroma")
    for policy in policies:
        for rule in policy["spec"].get("ingress", []) or []:
            sources = rule.get("from")
            need(sources, "chroma ingress rule without 'from' admits every pod")
            need(all(s.get("podSelector") != {} for s in sources),
                 "chroma ingress still admits every pod (podSelector: {})")


def _rule_eval_job(docs: list[dict]) -> None:
    jobs = [d for d in _find(docs, "Job") if d["_file"] == "k8s/eval-job.yaml"]
    need(jobs, "k8s/eval-job.yaml must contain a Job")
    job = jobs[0]
    need(job["spec"].get("backoffLimit") == 0, "eval Job needs backoffLimit: 0")
    need(_pod_spec(job).get("restartPolicy") == "Never", "eval Job needs restartPolicy: Never")
    need(_claims(job), "eval Job writes to no volume")
    need("rahbar-gate-thresholds" in _configmaps_used(job), "eval Job does not read the thresholds")
    need(_find(docs, "ConfigMap", "rahbar-gate-thresholds"), "no ConfigMap rahbar-gate-thresholds")


def _rule_candidate_isolated(docs: list[dict]) -> None:
    need(_find(docs, "Deployment", "api-candidate"), "no Deployment 'api-candidate'")
    need(_find(docs, "Service", "api-candidate"), "no Service 'api-candidate'")
    live = _find(docs, "Service", "api")
    need(live, "no Service 'api'")
    selector = live[0]["spec"].get("selector", {})
    labels = _pod_labels(_find(docs, "Deployment", "api-candidate")[0])
    need(not all(labels.get(k) == v for k, v in selector.items()),
         "candidate pods match the live Service selector and would take user traffic")


def _rule_release_pinned(docs: list[dict]) -> None:
    live = _find(docs, "Deployment", "api")
    need(live, "no Deployment 'api'")
    strategy = live[0]["spec"].get("strategy", {}).get("rollingUpdate", {})
    need(strategy.get("maxUnavailable") == 0, "maxUnavailable must be 0")
    releases = [n for n in _configmaps_used(live[0]) if re.fullmatch(r"rahbar-release-v\d+", n or "")]
    need(len(releases) == 1, "pod template must reference exactly one versioned release ConfigMap")
    maps = _find(docs, "ConfigMap", releases[0])
    need(maps and maps[0].get("immutable") is True, f"{releases[0]} must exist and be immutable")
    need({"CHROMA_COLLECTION", "EMBED_MODEL"} <= set(maps[0].get("data", {})),
         f"{releases[0]} must pin CHROMA_COLLECTION and EMBED_MODEL")


def _rule_traces_volume(docs: list[dict]) -> None:
    live = _find(docs, "Deployment", "api")
    need(live and "rahbar-traces" in _claims(live[0]), "api does not mount PVC rahbar-traces")
    need(_find(docs, "PersistentVolumeClaim", "rahbar-traces"), "no PVC rahbar-traces")


def _rule_resources_everywhere(docs: list[dict]) -> None:
    workloads = [d for k in ("Deployment", "StatefulSet", "Job", "CronJob") for d in _find(docs, k)]
    names = {d["metadata"]["name"] for d in workloads}
    need({"api", "chroma", "api-candidate"} <= names, "api, api-candidate and chroma expected")
    bad = []
    for doc in workloads:
        pod = _pod_spec(doc)
        for container in pod.get("containers", []) + pod.get("initContainers", []):
            res = container.get("resources", {})
            for section in ("requests", "limits"):
                if not {"cpu", "memory"} <= set(res.get(section, {})):
                    bad.append(f"{doc['metadata']['name']}/{container['name']}:{section}")
    need(not bad, f"missing cpu/memory: {', '.join(bad[:6])}")


K8S_RULES: dict[str, Callable[[list[dict]], None]] = {
    "core_workloads": _rule_core_workloads,
    "datalake_pvc": _rule_datalake_pvc,
    "ingest_jobs": _rule_ingest_jobs,
    "chroma_ingress_restricted": _rule_chroma_ingress_restricted,
    "eval_job": _rule_eval_job,
    "candidate_isolated": _rule_candidate_isolated,
    "release_pinned_in_pod_template": _rule_release_pinned,
    "traces_volume": _rule_traces_volume,
    "resources_everywhere": _rule_resources_everywhere,
}


def check_k8s(ctx: Context, spec: dict) -> Result:
    K8S_RULES[spec["rule"]](_manifests(ctx))
    return ok(spec["rule"])


# ---------------------------------------------------------------- repository and human checks
def check_tracked(ctx: Context, spec: dict) -> Result:
    code, out = _git(ctx, "ls-files", "--", f":(glob){spec['glob']}")
    need(code == 0, "git ls-files failed")
    count = len(out.split())
    need(count >= spec["min"], f"{count} tracked files match {spec['glob']}, needs {spec['min']}")
    return ok(f"{count} tracked")


def load_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise CheckError(f"{path.name}: invalid TOML ({exc})") from exc


def check_git_authors(ctx: Context, spec: dict) -> Result:
    members = load_toml(ctx.root / "harness" / "members.toml").get("members", {})
    emails = {k: {e.lower() for e in members.get(k, {}).get("git_emails", [])} for k in ctx.members}
    need(all(emails.values()), "fill git_emails for both members in harness/members.toml")
    need(not set.intersection(*emails.values()), "members share a git email")
    code, out = _git(ctx, "log", "--no-merges", "--format=%ae", f"{ctx.base_commit}..HEAD")
    need(code == 0, f"git log from base commit {ctx.base_commit} failed")
    seen = {line.strip().lower() for line in out.splitlines() if line.strip()}
    counts = {k: bool(v & seen) for k, v in emails.items()}
    silent = [k for k, present in counts.items() if not present]
    need(not silent, f"no Assignment 03 commits yet from: {', '.join(silent)}")
    return ok("both members have commits")


def check_manual(ctx: Context, spec: dict, req_id: str) -> Result:
    data = load_toml(ctx.root / "harness" / "state" / "attestations.toml")
    entry = data.get("attest", {}).get(req_id)
    if not entry:
        return Result(PENDING, "awaiting human attestation in harness/state/attestations.toml")
    signers = set(entry.get("by", []))
    required = set(ctx.members) if spec.get("both") else set()
    if not signers or not signers <= set(ctx.members) or not required <= signers:
        return Result(PENDING, f"attestation needs signers {sorted(required) or 'a member'}")
    if not entry.get("date") or not str(entry.get("note", "")).strip():
        return Result(PENDING, "attestation needs a date and a note")
    return ok(f"attested by {', '.join(sorted(signers))}")


CHECKS: dict[str, Callable[[Context, dict], Result]] = {
    "batch_b": check_batch_b,
    "capture": check_capture,
    "command": check_command,
    "compare": check_compare,
    "contains": check_contains,
    "coverage_rise": check_coverage_rise,
    "forbid": check_forbid,
    "gate_results": check_gate_results,
    "git_authors": check_git_authors,
    "golden": check_golden,
    "guardrail_evidence": check_guardrail_evidence,
    "ingest_zero": check_ingest_zero,
    "jsonl_fields": check_jsonl_fields,
    "k8s": check_k8s,
    "live_queries": check_live_queries,
    "pairs": check_pairs,
    "proof": check_proof,
    "readme_parts": check_readme_parts,
    "result": check_result,
    "stochasticity": check_stochasticity,
    "tests": check_tests,
    "tracked": check_tracked,
    "verification_report": check_verification_report,
}


# "build" checks need only the source tree. "evidence" checks need authored data or the
# artifacts of real runs. "human" checks need a signature. A spec may override with
# stage = "..." (used where writing a file must wait for a measurement).
BUILD_TYPES = {"command", "tests", "forbid", "k8s", "contains", "readme_parts"}


def stage_of(spec: dict) -> str:
    if spec.get("stage"):
        return spec["stage"]
    if spec["type"] == "manual":
        return "human"
    return "build" if spec["type"] in BUILD_TYPES else "evidence"


def run_check(ctx: Context, spec: dict, req_id: str) -> Result:
    try:
        if spec["type"] == "manual":
            return check_manual(ctx, spec, req_id)
        return CHECKS[spec["type"]](ctx, spec)
    except CheckError as exc:
        return fail(str(exc))
    except Exception as exc:  # noqa: BLE001 - a malformed artifact must never crash or pass
        return fail(f"{spec.get('type')}: {type(exc).__name__}: {exc}")
