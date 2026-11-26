# The harness

The scaffolding that lets two people and a coding agent build Assignment 03 without losing track
or fooling themselves.

## Layers
| Layer | Files | Job |
|---|---|---|
| Instructions | `AGENTS.md`, `CLAUDE.md` | short rules and a map; detail lives in `docs/a3/` |
| Definition of done | `harness/requirements.toml` | 80 requirements (76 quoting the PDF) with checks |
| Sensor | `harness/verify.py`, `checks.py` | decides pass or fail from code and artifacts |
| Controller | `harness/loop.py`, `.claude/hooks/stop_loop.py` | bounded iteration until the sensor passes |
| Guardrails | `.claude/hooks/guard.py`, `.claude/settings.json` | block integrity violations, ask before destructive actions |
| Fast feedback | `.claude/hooks/post_edit.py` | lint and parse on every edit |
| Memory | `harness/state/progress.md`, `loop-log.jsonl`, `session_start.py` | state survives context loss |
| Evidence | `harness/capture.py` | records real command output with provenance |
| Roles | `.claude/skills/`, `.claude/agents/` | loop, evidence and viva procedures; read-only reviewers |
| Self-test | `harness/tests/` | proves checks fail on bad input and pass on good |

## Setup (each member, once)
1. Open **this repository folder** as the project (not its parent), so `.claude/` and `CLAUDE.md`
   load. Accept the workspace trust prompt; project `allow` rules wait for it.
2. Python 3.11 or newer on PATH as `python` (the hooks call `python`; on macOS or Linux provide that
   name). `pip install -r api/requirements-dev.txt -r indexer/requirements-dev.txt pyyaml`.
3. `python -m pytest harness/tests -q` must pass.
4. Fill your entry in `harness/members.toml` and set your own git identity.
5. Check the hooks are live: ask the agent to write a file under `traces/`. It must be refused. If
   it is not, run `/hooks` and fix that before trusting anything else.

## Daily use
```bash
python harness/verify.py --fast --summary
python harness/verify.py --next --stage build
python harness/verify.py --part A                 # full: runs tests too
```
Statuses: `PASS`; `FAIL`; `SKIP` (test not run because of `--fast`; never counts as a pass);
`PENDING_HUMAN` (automatic checks fine, signature missing). Exit 0 only when everything selected is
`PASS`.

## Known limits
- Checks validate shape and consistency, **not truth or quality**. A hand-made artifact that fits
  the shape could pass. Review result diffs.
- The guard matches command text. It stops mistakes and lazy shortcuts, not determined evasion.
  It lets a call through if the hook itself crashes, and prints that it did.
- Permission rules match command text too.
- Test-name checks prove a function exists and its file passes, not that it asserts anything useful.
- At planning time the hooks were tested by feeding them the documented JSON as a subprocess and by
  unit tests. They have **not** been observed firing inside a live Claude Code session, which is why
  setup step 5 exists.
- `k8s` checks read YAML files, not a running cluster. Cluster truth comes from captured evidence.

## Changing it
See "Changing the harness" in `docs/a3/LOOP-ENGINEERING.md`. Both members review harness changes.
