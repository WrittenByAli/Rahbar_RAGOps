---
name: ragops-loop
description: Run the bounded build loop on Assignment 03 requirements until the verifier passes them. Use when a member asks to implement a part, a phase or specific requirement IDs.
argument-hint: "[part letter | requirement IDs]"
disable-model-invocation: true
---

# Build loop for: $ARGUMENTS

You build; `harness/verify.py` judges. Follow `docs/a3/LOOP-ENGINEERING.md`.

## Before starting
1. If `$ARGUMENTS` is empty, run `python harness/verify.py --next --stage build` and propose a
   scope. Do not start without one.
2. Read the plan phase and contracts for the scope, and the open items in `DECISIONS.md`. If an
   unconfirmed decision changes what you would build, ask the member first.
3. Check the working tree (`git status`) so your changes stay separable from theirs.
4. Start it:
   - a part: `python harness/loop.py start --part <X> --stage build`
   - requirements: `python harness/loop.py start --id <ID> <ID> --stage build`

## Each iteration
1. `python harness/verify.py --id <TARGET> --stage build`; read the quote in its entry.
2. Write the required tests with real assertions. Run them. They must fail for the expected reason.
3. Make the smallest change that passes.
4. Run the target check, then all API, indexer and harness tests. Fix regressions before moving on.
5. Append two lines to `harness/state/progress.md`: what changed, what proves it.
6. Try to finish your turn. The Stop hook advances you to the next target or sends the failure back.

## When to leave the loop (say why, then `python harness/loop.py stop --reason "..."`)
- A step needs a human (see the human-only list in `AGENTS.md`).
- It needs a cluster, model or network that is not available.
- A check or requirement looks wrong. Do **not** edit it; explain the problem.
- The same failure happened twice and you have no new hypothesis.
- The next step would change cluster state or evaluation order (plan section 1.1).

## Final report
List requirements now passing in the **build** stage, those still failing and why, what evidence is
still required, and what each member needs to understand or do. A passing build stage is not
"done": say so.
