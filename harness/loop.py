"""Bounded requirement loop. The Stop hook calls tick(); the verifier decides.

    python harness/loop.py start --part A [--stage build|evidence] [--max-iterations 20]
    python harness/loop.py start --id A2.1 A2.2
    python harness/loop.py status
    python harness/loop.py stop --reason "need a human to write golden questions"

While a loop is active, Claude cannot end its turn until the current target passes.
Every path out is bounded:
  - the target fails max_attempts times            -> escalate to a human
  - max_iterations blocks have been spent          -> stop
  - nothing else in scope is workable by an agent  -> stop and list the blockers
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import verify  # noqa: E402
from checks import PASS, PENDING  # noqa: E402

ROOT = verify.ROOT


def state_path(root: Path) -> Path:
    return root / "harness" / "state" / "loop.json"


def read_state(root: Path) -> dict | None:
    try:
        return json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_state(root: Path, state: dict) -> None:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def log_event(root: Path, event: str, **fields: object) -> None:
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **fields}
    path = root / "harness" / "state" / "loop-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")


def is_active(root: Path) -> bool:
    state = read_state(root)
    return bool(state and state.get("active"))


def _finish(root: Path, state: dict, outcome: str, message: str) -> dict:
    state.update(active=False, outcome=outcome)
    write_state(root, state)
    log_event(root, outcome, target=state.get("target"), message=message)
    return {"block": False, "message": f"loop {outcome}: {message}"}


def start(root: Path, part: str | None, ids: list[str] | None, max_iterations: int,
          max_attempts: int, stage: str = "build") -> tuple[dict | None, str]:
    meta, reqs = verify.load(root)
    scope = verify.select(reqs, part, ids)
    target, blockers = verify.next_target(verify.context(root, meta, False), reqs, scope,
                                          stage=stage)
    if target is None:
        return None, "nothing to loop on. " + "; ".join(blockers[:5])
    state = {"active": True, "stage": stage, "session_id": None,
             "scope": [r["id"] for r in scope],
             "target": target["id"], "attempts": 0, "iterations": 0,
             "max_iterations": max_iterations, "max_attempts": max_attempts, "done": []}
    write_state(root, state)
    log_event(root, "start", target=target["id"], scope=len(scope), stage=stage)
    return state, f"{stage} loop started. First target {target['id']}: {target['title']}"


def tick(root: Path, session_id: str) -> dict:
    """Decide whether the agent may stop. Returns {'block': bool, 'message': str}."""
    state = read_state(root)
    if not state or not state.get("active"):
        return {"block": False, "message": ""}
    if state.get("session_id") is None:
        state["session_id"] = session_id
    elif state["session_id"] != session_id:
        return {"block": False, "message": ""}   # another session owns this loop

    meta, reqs = verify.load(root)
    ctx = verify.context(root, meta, False)
    by_id = {r["id"]: r for r in reqs}
    target = by_id.get(state["target"])
    if target is None:
        return _finish(root, state, "error", f"unknown target {state['target']}")
    stage = state.get("stage", "build")
    status, results = verify.evaluate(ctx, target, stage)

    if state["iterations"] >= state["max_iterations"]:
        return _finish(root, state, "budget_exhausted",
                       f"{state['max_iterations']} iterations used; done={state['done']}")

    if status in (PASS, PENDING, verify.NA):
        state["done"].append(target["id"])
        log_event(root, "target_met", target=target["id"], status=status)
        scope = [by_id[i] for i in state["scope"] if i in by_id]
        following, blockers = verify.next_target(ctx, reqs, scope, exclude=set(state["done"]),
                                                 stage=stage)
        if following is None:
            return _finish(root, state, "scope_exhausted",
                           f"done={state['done']}; blocked={blockers[:5]}")
        state.update(target=following["id"], attempts=0, iterations=state["iterations"] + 1)
        write_state(root, state)
        return {"block": True, "message": (
            f"{target['id']} now {status}. Record it in harness/state/progress.md, then work on "
            f"{following['id']}: {following['title']}. "
            f"Run: python harness/verify.py --id {following['id']} --stage {stage}")}

    state["attempts"] += 1
    state["iterations"] += 1
    why = "; ".join(r.detail for r in results if r.status != PASS)[:900]
    if state["attempts"] > state["max_attempts"]:
        return _finish(root, state, "escalated",
                       f"{target['id']} still failing after {state['max_attempts']} attempts: {why}. "
                       "Tell the user what you tried and what you need from them.")
    write_state(root, state)
    log_event(root, "retry", target=target["id"], attempt=state["attempts"], why=why)
    return {"block": True, "message": (
        f"Loop target {target['id']} is {status} (attempt {state['attempts']}/"
        f"{state['max_attempts']}): {why}. Diagnose the cause before editing; never weaken "
        "a check or hand-write an artifact. If a human is required, run "
        "python harness/loop.py stop --reason \"...\" and say so.")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded requirement loop.")
    sub = parser.add_subparsers(dest="command", required=True)
    begin = sub.add_parser("start")
    scope = begin.add_mutually_exclusive_group(required=True)
    scope.add_argument("--part", choices=verify.PARTS)
    scope.add_argument("--id", nargs="+")
    begin.add_argument("--stage", choices=["build", "evidence"], default="build")
    begin.add_argument("--max-iterations", type=int, default=20)
    begin.add_argument("--max-attempts", type=int, default=3)
    sub.add_parser("status")
    halt = sub.add_parser("stop")
    halt.add_argument("--reason", required=True)
    args = parser.parse_args()

    if args.command == "start":
        state, message = start(ROOT, args.part, args.id, args.max_iterations,
                               args.max_attempts, args.stage)
        print(message)
        return 0 if state else 1
    state = read_state(ROOT)
    if args.command == "status":
        print(json.dumps(state, indent=2) if state else "no loop has run")
        return 0
    if state and state.get("active"):
        print(_finish(ROOT, state, "stopped", args.reason)["message"])
    else:
        print("no active loop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
