---
name: ragops-evidence
description: Produce real cluster evidence and screenshots for one part of Assignment 03, step by step with a member watching. Use for evidence-stage work and screenshot preparation.
argument-hint: "[part letter]"
disable-model-invocation: true
---

# Evidence run for part: $ARGUMENTS

This changes cluster state, so it is **not** an unattended loop. One step, show the result, then
continue.

1. Read plan section 1.1 and state which evaluation state the system is in now (S0, S1, ...), with
   the proof: lake status, golden hash, live release. If you cannot prove it, stop.
2. List what is missing: `python harness/verify.py --part $ARGUMENTS --stage evidence`.
3. Check the preconditions: cluster up, pods ready, models listed, code committed (results record a
   git SHA).
4. For each step: say what it will change, and whether it can be undone. Ask before anything that
   deletes, restores, releases or rolls back.
5. Run pipeline scripts for result files. Record cluster output only with
   `python harness/capture.py <name> -- <command>`.
6. Tell the member exactly which screenshot to take **now** (label from `docs/a3/RUNBOOK.md`),
   especially before a state that is hard to recreate. You cannot take or substitute screenshots.
7. If output is unexpected, report it as it is and diagnose. Never rerun until it looks good
   without saying how many times you ran it and why.
8. Finish: rerun the verifier, restore any temporary thresholds or demo data, list files to commit,
   and add a line to `harness/state/progress.md`.
