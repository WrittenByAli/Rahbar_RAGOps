"""SessionStart: load project state into context, so no session starts from memory.

Runs the fast verifier (no pytest, nothing slow), shows the loop state and the tail of
the handoff log. Whatever it prints becomes context for the agent.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or ".")
    try:
        print("RAGOps harness state (fast mode: test-backed items show as not passed).")
        run = subprocess.run(
            [sys.executable, str(root / "harness" / "verify.py"), "--fast", "--summary"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=root, timeout=60, check=False)
        print(run.stdout.strip() or run.stderr.strip()[:500])
        sys.path.insert(0, str(root / "harness"))
        from loop import read_state

        state = read_state(root)
        if state and state.get("active"):
            print(f"ACTIVE LOOP: target {state['target']}, attempt {state['attempts']}, "
                  f"iteration {state['iterations']}/{state['max_iterations']}.")
        log = root / "harness" / "state" / "progress.md"
        if log.is_file():
            tail = log.read_text(encoding="utf-8").splitlines()[-25:]
            print("--- last handoff notes ---")
            print("\n".join(tail))
        print("Read AGENTS.md rules first. Next item: python harness/verify.py --next")
    except Exception as exc:  # noqa: BLE001 - never block a session from starting
        print(f"session_start.py could not summarise state: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
