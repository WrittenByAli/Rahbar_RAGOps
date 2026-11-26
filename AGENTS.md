# AGENTS.md: RAHBAR, Assignment 03 (RAGOps)

A grounded RAG system over FAST-NUCES policies, running on Kubernetes (built in Assignment 02).
Assignment 03 adds the RAGOps layer: data verification and incremental update, three-level offline
testing, a release gate, tracing, coverage checking and controlled rollout. Group of two; 185 marks;
CS4032.

**Status: planned, not implemented.** Do not describe a planned feature as existing.

## Start every session
1. `python harness/verify.py --fast --summary`, then read the tail of `harness/state/progress.md`.
2. `python harness/verify.py --next --stage build` to find the next piece of work.
3. Read only what that work needs (map below). The assignment PDF is authoritative over every file
   here.

## Map
| Need | Read |
|---|---|
| What done means | `harness/requirements.toml` (source), `docs/a3/REQUIREMENTS.md` (view) |
| What is broken in A2 | `docs/a3/GAP-ANALYSIS.md` |
| Order of work | `docs/a3/IMPLEMENTATION-PLAN.md`, **section 1.1 before any ingestion or evaluation** |
| Design | `docs/a3/ARCHITECTURE.md` |
| Data shapes and metric formulas | `docs/a3/CONTRACTS.md` |
| How to loop | `docs/a3/LOOP-ENGINEERING.md` |
| Open choices | `docs/a3/DECISIONS.md` |
| Paper | `docs/a3/PAPER-NOTES.md` |

Code: `api/` (FastAPI query pipeline), `indexer/` (ingestion, the only writer to Chroma), `eval/`,
`k8s/`, `ui/`, `corpus/` (`corpus/raw/` is never committed). A2 documents under `docs/` are
history; **some of their numbers do not match their own result files** (see the gap analysis).

## Rules that may not be broken
**Integrity**
- Every number comes from a run of this pipeline and a committed result file. Never type, edit,
  estimate or "tidy" anything under `eval/results/` or `traces/`. Run the script.
- Never invent a result, a log, a screenshot or a measurement. If something was not run, say so.
- A failing or disappointing result is reported as it is. "Reject" is a valid release decision.

**Human-only** (ask the member; do not do it, draft it as theirs, or sign it)
- Golden questions labelled `human`, and every `reviewed_by`.
- `eval/judge_labels.jsonl` (hand labels).
- `harness/state/attestations.toml` and `harness/members.toml`.
- Root-cause lines, drift causes sign-off, report text and short answers, screenshots.

**Do not move the goalposts**
- Never weaken, skip or delete a check or test to get green. If a check looks wrong, stop and say
  why.
- Do not change assignment requirements ("There will be no credit if the given requirements are
  changed").
- Gate thresholds are committed before a candidate runs and are not edited to rescue it.
- Never copy retriever output into ground truth, or live queries into the golden set.

**System invariants**
- The ingestion Job is the only writer to the vector store. The API and the eval Job never write.
- No indexing at API startup. Traces for every query, including abstentions, hits and errors.
- Pipeline configuration is versioned per release; never edit a released ConfigMap.
- The demo runs on Kubernetes. Docker Compose or `docker run` earns zero for the demo.
- Chroma server and client stay at the same version (1.0.12).
- No secrets in git, logs or traces.

**Git**
- Commit or push only when a member asks. Never change git identity, use `--author`,
  `--no-verify` or force-push. Both members commit their own work.

## Working method
- Tests first, with the test names in `requirements.toml`. See them fail for the right reason.
- Smallest change that passes; then re-run the full tests. No earlier requirement may regress.
- Two identical failures: stop editing, find the cause. Three: escalate to the member.
- "Done" = build and evidence checks pass, the other member reviewed it, both can explain it.
  Report partial work as partial.
- Both members must be able to explain every line. Prefer plain code, few abstractions and no new
  dependency without a reason. When you finish a piece, explain how it works and what could break.
- End a session by appending to `harness/state/progress.md`: done, evidence, open, next.

## Commands (exist today)
```bash
python harness/verify.py [--part A] [--id A2.1] [--stage build|evidence] [--fast] [--next]
python harness/verify.py --lint --spec "../PDF Assignment_03.pdf"
python harness/verify.py --render
python harness/loop.py start --part A --stage build | status | stop --reason "..."
python harness/capture.py <name> -- <command ...>
python -m pytest harness/tests -q
cd api && python -m pytest -q            # and the same in indexer/
python -m ruff check api indexer eval harness .claude/hooks
```
Windows is the development platform and `make` may be absent: no required step may depend on it.
Commands for features that do not exist yet are in the plan and are marked as planned.
