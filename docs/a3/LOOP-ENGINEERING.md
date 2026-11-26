# Loop engineering

A loop is useful only if something outside the model decides when it is finished. Here that is
`harness/verify.py`. Every loop below states its **goal, signal, memory, stop conditions and
escalation**.

## The five loops

| Loop | Goal | Signal (who judges) | Cadence | Stops when | Escalates when |
|---|---|---|---|---|---|
| L0 edit | each file is well-formed | `post_edit.py`: ruff, parsers | every edit | clean | - |
| L1 requirement, **build** | one requirement's code is correct | named tests and static checks | minutes | build checks pass | 3 failed attempts |
| L1 requirement, **evidence** | the real system produced the proof | artifact checks | per run | evidence checks pass | needs a human, cluster or model that is unavailable |
| L2 phase gate | the phase is truly done | verifier, then `spec-auditor` (a checker that cannot edit), then the other member | per phase | all three agree and both can explain it | any disagreement |
| L3 release (the product's loop) | only good changes reach users | the release gate and traces | per change | released, or rejected | bad release: `rollout undo` |
| L4 coverage and drift (the product's loop) | tests stay representative | coverage script, drift report | per data change | coverage at or above the required share | below it: expand golden, re-baseline |

L3 and L4 are Figure 3 itself: offline testing, then release, live testing and coverage, feeding
back into planning. L0 to L2 are how the agent builds them.

## L1 in detail

```
observe : python harness/verify.py --next [--stage build]
orient  : read the requirement's quote, the plan task and the contract
red     : write the required tests; run them; watch them fail for the right reason
act     : the smallest change that can pass
verify  : python harness/verify.py --id <ID> --stage build
          then the full suite: no earlier requirement may regress
record  : two lines in harness/state/progress.md; commit (the member commits, or asks you to)
repeat
```

On failure: read the whole error, state a cause hypothesis, change **one** thing. The same failure
twice means the hypothesis was wrong: go back to observing, not editing.

### Running it mechanically
```
/ragops-loop A            # the skill; or by hand:
python harness/loop.py start --part A --stage build --max-iterations 20 --max-attempts 3
python harness/loop.py status
python harness/loop.py stop --reason "..."
```
While a loop is active, the Stop hook runs the verifier whenever the agent tries to finish. A
failing target blocks the stop and returns the failure text; a passing one advances to the next
ready requirement. It always ends: attempts exhausted (`escalated`), iteration budget spent
(`budget_exhausted`), nothing workable (`scope_exhausted`), or an explicit `stop`. The loop binds to
one session. Claude Code also limits consecutive stop blocks on its side. `loop-log.jsonl` records
each event.

Run **build** loops unattended-ish. Run **evidence** work step by step with a member watching: it
changes cluster state, and the order in plan section 1.1 matters more than speed.

Not included on purpose: an unattended outer script that restarts the agent until everything is
green. Many items need humans, several steps are destructive or order-dependent, and an
agent under pressure to turn checks green is the failure this harness is built to prevent.

## Keeping the signal honest

| Temptation | Defence | What the defence cannot do |
|---|---|---|
| type a result file | guard denies writes to `eval/results/`, `traces/`; provenance needs a real commit | a determined script could still write one; review result diffs |
| weaken a check or threshold | checker and thresholds frozen during loops; thresholds committed before candidates run | outside a loop, edits are possible: require review of `harness/` changes |
| tests that assert nothing | required test names are only a floor; `spec-auditor` and the other member read tests | names alone prove nothing |
| tune on the test | calibrate the verifier on corpus A, the floor on non-golden pairs, fix the coverage threshold before reading the share; never edit golden to recover a score | needs discipline; git order is the evidence |
| vacuous pass | missing metric is failure; always-abstain test; the harness test asserts nothing passes on an empty repo | new checks need their own red test |
| fake the human part | guard denies attestations, judge labels, members file, new `human` labels | a human can still sign without doing the work. That is on you |

The harness checks **form and consistency**. It does not prove the pipeline is good, and a green
verifier is not a grade.

## Changing the harness
The harness is code. To change a check: stop any loop, first write a failing harness test that
shows the problem, change the check, run `pytest harness/tests` and
`python harness/verify.py --lint --spec <pdf>`, and have the other member review it. When the agent
makes the same mistake twice, prefer a mechanical fix (a check, a hook, a test) over another
sentence of instructions.
