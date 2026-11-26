"""The indexer Job's entry point. It runs once and exits (section 4.2).

    python -m pipeline.run --dummy        # three fake chunks, no model needed (CI, k8s)
    python -m pipeline.run                # same as "ingest"
    python -m pipeline.run ingest [--source DIR] [--batch NAME] [--prove-changed DOC_ID]
    python -m pipeline.run delete  --doc-id ID
    python -m pipeline.run restore --doc-id ID --version V
    python -m pipeline.run count   --doc-id ID
    python -m pipeline.run status
    python -m pipeline.run quarantine-proof

Exit code 0 means the command did what it says; anything else fails the Job, and
Kubernetes retries it up to the Job's backoffLimit. Quarantining a file is a normal
result of ingestion, not a failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from .admin import count_chunks, delete_document, quarantine_proof, restore_version, status
from .config import IndexerSettings, get_settings
from .ingest import EmptyLake, Services, discover, real_services, run_ingest
from .lock import LockHeld, LockLost
from .store import ModelMismatch
from .sync import ForeignCollection

__all__ = ["main", "discover"]

EXIT_FAILED = 1
EXIT_NOT_IMPLEMENTED = 2
EXIT_LOCKED = 3
# Errors with a message that says what to do. Anything else shows its traceback.
_EXPECTED = (EmptyLake, LookupError, ForeignCollection, ModelMismatch, LockLost,
             FileNotFoundError, ValueError)


def log(msg: str, **fields: object) -> None:
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                      "logger": "rahbar.indexer", "msg": msg, **fields}), flush=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Maintain the RAHBAR index, then exit.")
    parser.add_argument("--dummy", action="store_true", help="write three fake chunks")
    commands = parser.add_subparsers(dest="command")
    ingest = commands.add_parser("ingest", help="verify new files and update the index")
    ingest.add_argument("--source", action="append", type=Path,
                        help="read documents from this folder instead of the inbox and "
                             "CORPUS_DIR; the folder is not changed")
    ingest.add_argument("--batch", help="batch name for the report")
    ingest.add_argument("--prove-changed", metavar="DOC_ID",
                        help="write proofs/changed.json for this run")
    ingest.add_argument("--adopt-collection", action="store_true",
                        help="let this lake take over a collection another lake wrote")
    for name, text in (("delete", "remove a document from the lake and the index"),
                       ("restore", "make an archived version active again"),
                       ("count", "count a document's chunks in the vector store")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--doc-id", required=True)
        if name == "restore":
            command.add_argument("--version", required=True)
    commands.add_parser("status", help="show the lake and the target collections")
    commands.add_parser("quarantine-proof", help="write proofs/quarantine.json")
    return parser


def _ingest(settings: IndexerSettings, services: Services, args: argparse.Namespace) -> int:
    result = run_ingest(settings, services, sources=getattr(args, "source", None),
                        batch=getattr(args, "batch", None),
                        prove_changed=getattr(args, "prove_changed", None),
                        adopt=getattr(args, "adopt_collection", False))
    _log_verification(result.verification)
    for report in result.ingest:
        log("indexed", run_id=result.run_id, collection=report["collection"],
            lake_state_id=report["lake_state_id"], **report["counts"])
    for problem in result.problems:
        log("proof does not hold", problem=problem)
    return EXIT_FAILED if result.problems else 0


def _log_verification(report: dict) -> None:
    for entry in report["documents"]:
        reasons = {name: check["reason"] for name, check in entry["checks"].items()
                   if check["status"] == "fail"}
        log("verified", file=entry["file"], doc_id=entry["doc_id"],
            decision=entry["decision"], **({"failed": reasons} if reasons else {}))


def _run(settings: IndexerSettings, services: Services, args: argparse.Namespace) -> int:
    if args.command in (None, "ingest"):
        return _ingest(settings, services, args)
    if args.command == "delete":
        proof = delete_document(settings, services, args.doc_id)
        print(json.dumps(proof, indent=2))
        clean = all(t["count_after"] == 0 and t["orphan_chunks_after"] == 0
                    for t in proof["targets"])
        return 0 if clean else EXIT_FAILED
    if args.command == "restore":
        proof = restore_version(settings, services, args.doc_id, args.version)
        print(json.dumps(proof, indent=2))
        return 0 if proof["retrieved_versions_after"] == [args.version] else EXIT_FAILED
    if args.command == "count":
        print(json.dumps(count_chunks(settings, services, args.doc_id), indent=2))
    elif args.command == "status":
        print(json.dumps(status(settings, services), indent=2))
    else:
        print(json.dumps(quarantine_proof(settings, services), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")
    settings = get_settings()
    log("indexer starting", command=args.command or "ingest", dummy=args.dummy,
        embed_model=settings.embed_model, collection=settings.collection,
        chroma=f"{settings.chroma_host}:{settings.chroma_port}",
        lake_dir=settings.lake_dir, reports=str(settings.reports_path),
        pod=os.environ.get("HOSTNAME", "-"))
    if not args.dummy and not os.environ.get("LAKE_DIR"):
        log("LAKE_DIR is not set: this data lake is temporary and will not survive the pod")
    started = time.perf_counter()
    try:
        if args.dummy:
            from .dummy import write_dummy_index
            from .store import get_client

            code = 0
            log("dummy index written", chunks=write_dummy_index(settings, get_client(settings)))
        else:
            code = _run(settings, real_services(settings), args)
    except NotImplementedError as exc:
        log("indexer stage not implemented yet", error=str(exc))
        return EXIT_NOT_IMPLEMENTED
    except LockHeld as exc:
        log("another ingestion run is active; nothing was done", error=str(exc))
        return EXIT_LOCKED
    except _EXPECTED as exc:
        if isinstance(exc, EmptyLake):
            _log_verification(exc.result.verification)
        log("indexer failed", error=f"{type(exc).__name__}: {exc}")
        return EXIT_FAILED
    log("indexer finished", exit=code, seconds=round(time.perf_counter() - started, 1))
    return code


if __name__ == "__main__":
    sys.exit(main())
