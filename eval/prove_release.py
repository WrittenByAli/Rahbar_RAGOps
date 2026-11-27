"""Assemble eval/results/release/undo.json from three real cluster traces.

The undo proof (requirement C3 / F3.4) requires three traces captured on the cluster:
one before the rollout, one after rolling out the candidate, and one after rolling back.
This script reads the trace files, extracts the specified records, validates the proof
shape, and writes eval/results/release/undo.json.

Usage (run from repo root after capturing traces with harness/capture.py):

    python eval/prove_release.py \\
        --before   <trace-id-before-rollout> \\
        --released <trace-id-while-candidate-was-live> \\
        --after    <trace-id-after-rollout-undo> \\
        [--traces  PATH ...]   # defaults to traces/

Each trace-id is the UUID from the trace record's "trace_id" field.
The script verifies that:
  - all three traces are found in the trace files
  - before and after_undo share the same collection and embedding
  - released used a different collection from before

Exit codes: 0 written, 1 validation failed, 2 argument or file error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

sys.path.insert(0, str(HERE))
from tracelib import load_traces, trace_files

RESULT_DIR = ROOT / "eval" / "results" / "release"
OUT = RESULT_DIR / "undo.json"


def _fail(msg: str, code: int = 1) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return code


def _extract(record: dict) -> dict:
    """Pull the fields needed for one undo-proof stage from a trace record."""
    emb = record.get("embedding") or {}
    col = record.get("collection") or {}
    return {
        "release_id": record.get("release_id", ""),
        "collection":  col.get("name", ""),
        "embed_model": emb.get("model", ""),
        "embed_version": emb.get("version", ""),
        "trace_id":    record.get("trace_id", ""),
    }


def build_proof(before_id: str, released_id: str, after_id: str,
                trace_paths: list[str]) -> int:
    files = trace_files(trace_paths or [str(ROOT / "traces")])
    loaded = load_traces(files)

    missing = [tid for tid in (before_id, released_id, after_id)
               if tid not in loaded.by_id]
    if missing:
        return _fail(f"trace IDs not found: {', '.join(missing)}\n"
                     f"  searched {len(files)} file(s): {', '.join(str(f) for f in files[:3])}")

    before   = _extract(loaded.by_id[before_id])
    released = _extract(loaded.by_id[released_id])
    after    = _extract(loaded.by_id[after_id])

    errors: list[str] = []
    for stage_name, stage in (("before", before), ("released", released), ("after_undo", after)):
        for key in ("release_id", "collection", "embed_model", "embed_version", "trace_id"):
            if not stage.get(key):
                errors.append(f"{stage_name}.{key} is empty")

    for key in ("collection", "embed_model", "embed_version"):
        if after[key] != before[key]:
            errors.append(
                f"after_undo.{key} '{after[key]}' != before.{key} '{before[key]}' "
                "— the undo did not restore the previous release"
            )
    if released["collection"] == before["collection"]:
        errors.append(
            f"released.collection == before.collection ('{before['collection']}') — rollout did not change collection"
        )
    if len({before["trace_id"], released["trace_id"], after["trace_id"]}) != 3:
        errors.append("all three trace_ids must be distinct")

    if errors:
        for e in errors:
            print(f"  FAIL: {e}", file=sys.stderr)
        return _fail("proof is not valid — fix the issues above")

    proof = {
        "kind": "undo",
        "before":     before,
        "released":   released,
        "after_undo": after,
    }
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    try:
        out_display = OUT.relative_to(ROOT).as_posix()
    except ValueError:
        out_display = str(OUT)
    print(f"wrote {out_display}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before",   required=True, metavar="TRACE_ID",
                        help="trace id from before the rolling update")
    parser.add_argument("--released", required=True, metavar="TRACE_ID",
                        help="trace id while the candidate was live")
    parser.add_argument("--after",    required=True, metavar="TRACE_ID",
                        help="trace id after kubectl rollout undo")
    parser.add_argument("--traces",   nargs="*", default=[], metavar="PATH",
                        help="trace file(s) or director(ies); default: traces/")
    args = parser.parse_args(argv)
    return build_proof(args.before, args.released, args.after, args.traces)


if __name__ == "__main__":
    sys.exit(main())
