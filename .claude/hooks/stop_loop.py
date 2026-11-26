"""Stop hook: while a requirement loop is active, 'done' is decided by the verifier.

Does nothing unless `python harness/loop.py start` was run. All exits are bounded; see
harness/loop.py. Claude Code also caps consecutive stop blocks on its side.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")
        sys.path.insert(0, str(root / "harness"))
        from loop import tick

        outcome = tick(root, str(payload.get("session_id", "")))
    except Exception as exc:  # noqa: BLE001 - on any error, let the agent stop
        print(f"stop_loop.py error, loop not enforced: {exc}", file=sys.stderr)
        return 0
    if outcome["block"]:
        print(json.dumps({"decision": "block", "reason": outcome["message"]}))
    elif outcome["message"]:
        print(json.dumps({"systemMessage": outcome["message"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
