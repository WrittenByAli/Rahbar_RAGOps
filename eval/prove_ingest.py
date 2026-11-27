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

It validates each proof's shape against the harness contract before copying, so a
bad copy (truncated kubectl cp, wrong file) is caught early.

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
        for check in FOUR_CHECKS:
            entry = checks.get(check) or {}
            if entry.get("status") not in ("pass", "fail"):
                errors.append(f"{path.name}/{f}: {check} has no pass/fail status")
            if not str(entry.get("reason", "")).strip():
                errors.append(f"{path.name}/{f}: {check} has no reason text")
    return errors


def validate_ingest(data: dict, path: Path) -> list[str]:
    errors: list[str] = []
    for key in ("run_id", "collection", "counts"):
        if not data.get(key):
            errors.append(f"{path.name}: missing {key}")
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
        for key in ("changed_doc_id", "before", "after"):
            if not data.get(key):
                errors.append(f"changed.json: missing {key}")
        target = data.get("changed_doc_id", "")
        if target and target not in (data.get("before") or {}):
            errors.append(f"changed.json: changed_doc_id '{target}' not in before")
    elif name == "deleted":
        for key in ("doc_id", "count_before", "count_after", "orphan_chunks_after"):
            if data.get(key) is None:
                errors.append(f"deleted.json: missing {key}")
        if data.get("count_after") != 0:
            errors.append("deleted.json: count_after must be 0")
        if data.get("orphan_chunks_after") != 0:
            errors.append("deleted.json: orphan_chunks_after must be 0")
    elif name == "restored":
        for key in ("restored_version", "replaced_version",
                    "retrieved_versions_before", "retrieved_versions_after"):
            if not data.get(key):
                errors.append(f"restored.json: missing {key}")
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


def run(src: Path) -> int:
    failures = 0
    failures += copy_tree(src / "verification", DEST / "verification", validate_verification, "verification")
    failures += copy_tree(src / "ingest",        DEST / "ingest",        validate_ingest,        "ingest")
    failures += copy_tree(src / "proofs",        DEST / "proofs",        validate_proof,         "proofs")
    if failures:
        return _fail(f"{failures} validation error(s) — no invalid files were copied")
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
