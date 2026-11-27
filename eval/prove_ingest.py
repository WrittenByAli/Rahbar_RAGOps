"""Derive A-part proof files from ingest evidence copied from the cluster PVC.

The ingestion Job writes verification reports and proof files to the data-lake PVC
(under <LAKE_DIR>/reports/). After the Job completes, copy them to your local
eval/results/ with kubectl cp, then run this script to validate and register them.

Usage:

    # 1. Copy from the cluster (run once per ingest batch):
    kubectl cp -n rahbar-a3 -c shell \\
        datalake-shell:/data/lake/reports/verification/ ./tmp-reports/verification/
    kubectl cp -n rahbar-a3 -c shell \\
        datalake-shell:/data/lake/reports/ingest/ ./tmp-reports/ingest/
    kubectl cp -n rahbar-a3 -c shell \\
        datalake-shell:/data/lake/reports/proofs/ ./tmp-reports/proofs/

    # 2. Register (validates shape and copies to eval/results/):
    python eval/prove_ingest.py --src ./tmp-reports

The script copies:
  verification/*.json  ->  eval/results/verification/
  ingest/*.json        ->  eval/results/ingest/
  proofs/*.json        ->  eval/results/proofs/

It validates each proof's shape before copying, so a bad copy (truncated kubectl cp,
wrong file) is caught early. The rules mirror check_verification_report, check_ingest_zero
and check_proof in harness/checks.py and must stay in step with them: harness/verify.py
remains the authoritative judge, this script is only the pre-flight gate.

Exit codes: 0 all files accepted, 1 validation failed, 2 argument or file error.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "eval" / "results"

FOUR_CHECKS = ("quality", "completeness", "recency", "uniqueness")
MIN_DOCUMENTS = 10          # A1.2: the largest verification report must cover this many


def _fail(msg: str, code: int = 1) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return code


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def validate_verification(data: dict, path: Path) -> list[str]:
    errors: list[str] = []
    if not data.get("run_id"):
        errors.append(f"{path.name}: missing run_id")
    if not data.get("started_at"):
        errors.append(f"{path.name}: missing started_at")
    for doc in data.get("documents", []):
        f = doc.get("file", "?")
        checks = doc.get("checks") or {}
        statuses = []
        for check in FOUR_CHECKS:
            entry = checks.get(check) or {}
            statuses.append(entry.get("status"))
            if entry.get("status") not in ("pass", "fail"):
                errors.append(f"{path.name}/{f}: {check} has no pass/fail status")
            if not str(entry.get("reason", "")).strip():
                errors.append(f"{path.name}/{f}: {check} has no reason text")
        decision = doc.get("decision")
        if decision not in ("accepted", "quarantined", "unchanged"):
            errors.append(f"{path.name}/{f}: decision '{decision}' is not "
                          "accepted/quarantined/unchanged")
        elif (decision == "quarantined") != ("fail" in statuses):
            errors.append(f"{path.name}/{f}: decision '{decision}' contradicts "
                          f"check results {statuses}")
    return errors


def validate_ingest(data: dict, path: Path) -> list[str]:
    errors: list[str] = []
    for key in ("run_id", "collection", "counts"):
        if not data.get(key):
            errors.append(f"{path.name}: missing {key}")
    if not data.get("lake_state_id"):
        errors.append(f"{path.name}: missing lake_state_id (A2.1 pairs runs by it)")
    counts = data.get("counts") or {}
    for key in ("chunks_embedded", "documents_unchanged"):
        if key not in counts:
            errors.append(f"{path.name}: counts.{key} missing (A2.1 needs it)")
    return errors


def validate_proof(data: dict, path: Path) -> list[str]:
    errors: list[str] = []
    name = path.stem
    if name == "quarantine":
        docs = data.get("documents", [])
        if not docs:
            errors.append("quarantine.json: documents array is empty")
        for d in docs:
            if d.get("in_quarantine") is not True:
                errors.append(f"quarantine.json: {d.get('file','')} has in_quarantine != true")
            if d.get("indexed_chunks") != 0:
                errors.append(f"quarantine.json: {d.get('file','')} still has indexed chunks")
    elif name == "changed":
        before, after = data.get("before") or {}, data.get("after") or {}
        target = data.get("changed_doc_id")
        if not target:
            errors.append("changed.json: missing changed_doc_id")
        elif target not in before or target not in after:
            errors.append(f"changed.json: changed_doc_id '{target}' missing from before/after")
        elif sorted(before[target]) == sorted(after[target]):
            errors.append(f"changed.json: chunks of '{target}' are unchanged")
        others = (set(before) | set(after)) - {target}
        if not others:
            errors.append("changed.json: proof must include at least one other document")
        moved = [d for d in others if sorted(before.get(d, [])) != sorted(after.get(d, []))]
        if moved:
            errors.append(f"changed.json: other documents changed too: {moved[:5]}")
    elif name == "deleted":
        if not data.get("doc_id"):
            errors.append("deleted.json: missing doc_id")
        if not isinstance(data.get("count_before"), int) or data["count_before"] <= 0:
            errors.append("deleted.json: count_before must be a positive count")
        if data.get("count_after") != 0:
            errors.append("deleted.json: count_after must be 0")
        if data.get("orphan_chunks_after") != 0:
            errors.append("deleted.json: orphan_chunks_after must be 0")
    elif name == "restored":
        old, new = data.get("restored_version"), data.get("replaced_version")
        if not (old and new and old != new):
            errors.append("restored.json: restored_version and replaced_version "
                          "must both be set and differ")
        if data.get("retrieved_versions_before") != [new]:
            errors.append(f"restored.json: retrieved_versions_before must be exactly [{new!r}], "
                          f"got {data.get('retrieved_versions_before')!r}")
        if data.get("retrieved_versions_after") != [old]:
            errors.append(f"restored.json: retrieved_versions_after must be exactly [{old!r}], "
                          f"got {data.get('retrieved_versions_after')!r}")
        if data.get("chunks_embedded") != 0:
            errors.append("restored.json: chunks_embedded must be 0 (restore re-uses existing chunks)")
    return errors


def copy_tree(src: Path, dest: Path, validator, label: str) -> int:
    files = sorted(src.glob("*.json")) if src.is_dir() else []
    if not files:
        print(f"  skip {label}: no .json files in {src}")
        return 0
    errors: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            errors.append(f"{f.name}: cannot parse JSON: {exc}")
            continue
        errs = validator(data, f)
        if errs:
            errors.extend(errs)
            continue
        shutil.copy2(f, dest / f.name)
        _ok(f"{label}/{f.name}")
    if errors:
        for e in errors:
            print(f"  FAIL {e}", file=sys.stderr)
        return len(errors)
    return 0


def _load_dir(folder: Path) -> list[dict]:
    loaded: list[dict] = []
    for f in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            loaded.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
    return loaded


def _zero_embed_pair(runs: list[dict]) -> bool:
    """A 0-embed run following a real one on the same collection and lake state (A2.1)."""
    ordered = sorted(runs, key=lambda r: str(r.get("started_at", "")))
    for i, later in enumerate(ordered):
        counts = later.get("counts") or {}
        if counts.get("chunks_embedded") != 0 or not counts.get("documents_unchanged"):
            continue
        for earlier in ordered[:i]:
            if (earlier.get("collection") == later.get("collection")
                    and (earlier.get("counts") or {}).get("chunks_embedded", 0) > 0
                    and earlier.get("lake_state_id") == later.get("lake_state_id")):
                return True
    return False


def cross_file_notes() -> None:
    """Conditions the harness checks across files; a single file cannot satisfy them."""
    reports = _load_dir(DEST / "verification")
    if reports:
        biggest = max(len(r.get("documents", [])) for r in reports)
        tag = "ok  " if biggest >= MIN_DOCUMENTS else "NOTE"
        print(f"  {tag}A1.2: largest verification report covers {biggest} document(s), "
              f"needs {MIN_DOCUMENTS}")
    runs = _load_dir(DEST / "ingest")
    if runs:
        if _zero_embed_pair(runs):
            print("  ok  A2.1: a re-run embedded 0 chunks on the same lake state")
        else:
            print("  NOTE A2.1: no 0-embed re-run yet — run the ingestion Job a second "
                  "time without changing the lake")


def run(src: Path) -> int:
    failures = 0
    failures += copy_tree(src / "verification", DEST / "verification", validate_verification, "verification")
    failures += copy_tree(src / "ingest",        DEST / "ingest",        validate_ingest,        "ingest")
    failures += copy_tree(src / "proofs",        DEST / "proofs",        validate_proof,         "proofs")
    if failures:
        return _fail(f"{failures} validation error(s) — no invalid files were copied")
    cross_file_notes()
    try:
        dest_display = DEST.relative_to(ROOT).as_posix()
    except ValueError:
        dest_display = str(DEST)
    print(f"done: results are in {dest_display}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", required=True, metavar="DIR",
                        help="directory with verification/, ingest/ and proofs/ sub-dirs "
                             "(copied from the cluster PVC)")
    args = parser.parse_args(argv)
    src = Path(args.src)
    if not src.is_dir():
        return _fail(f"--src {src} is not a directory", code=2)
    return run(src)


if __name__ == "__main__":
    sys.exit(main())
