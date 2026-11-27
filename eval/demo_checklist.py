"""Demo rehearsal checklist for requirement G.7.

Checks the cluster readiness and prints a step-by-step guide for the demo:
  - run the evaluation Job
  - open a trace (D2)
  - ingest an unseen batch via the ingestion Job

Usage:
    python eval/demo_checklist.py [--namespace rahbar-a3]

The script does NOT modify the cluster. It only reads state via kubectl and
prints instructions. Run it once before the demo to verify everything is in order.

Exit codes: 0 all pre-checks passed, 1 one or more pre-checks failed, 2 error.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval"


def _run(cmd: list[str], *, timeout: int = 30) -> tuple[int, str]:
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    return result.returncode, (result.stdout + result.stderr).strip()


def _ok(msg: str) -> None:
    print(f"  ✓  {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗  {msg}", file=sys.stderr)


def _header(title: str) -> None:
    width = 72
    print(f"\n{'=' * width}")
    print(f"  {title}")
    print('=' * width)


def check_cluster(ns: str) -> int:
    """Return the number of pre-check failures."""
    _header("Pre-checks")
    failures = 0

    # 1. Namespace exists
    code, out = _run(["kubectl", "get", "namespace", ns])
    if code == 0:
        _ok(f"namespace {ns} exists")
    else:
        _fail(f"namespace {ns} not found — run: kubectl apply -f k8s/a3/00-namespace.yaml")
        failures += 1

    # 2. Chroma pod ready
    code, out = _run(["kubectl", "get", "pod", "chroma-0", "-n", ns,
                       "-o", "jsonpath={.status.conditions[?(@.type=='Ready')].status}"])
    if code == 0 and "True" in out:
        _ok("chroma-0 is Ready")
    else:
        _fail("chroma-0 is not Ready — apply k8s/a3/ and wait")
        failures += 1

    # 3. API pods ready
    code, out = _run(["kubectl", "get", "pods", "-n", ns, "-l", "app=api",
                       "-o", "jsonpath={.items[*].status.conditions[?(@.type=='Ready')].status}"])
    if code == 0 and "True" in out:
        _ok("api pods are Ready")
    else:
        _fail("no api pods are Ready — run ingest first so a collection exists")
        failures += 1

    # 4. Baseline eval result exists
    baseline_files = sorted((EVAL_DIR / "results").glob("*s0*baseline*.json"))
    if baseline_files:
        _ok(f"baseline eval result exists: {baseline_files[0].name}")
    else:
        _fail("no baseline eval result found in eval/results/ — run the eval Job first")
        failures += 1

    # 5. Gate thresholds ConfigMap is immutable
    code, out = _run(["kubectl", "get", "configmap", "gate-thresholds", "-n", ns,
                       "-o", "jsonpath={.immutable}"])
    if code == 0 and "true" in out.lower():
        _ok("gate-thresholds ConfigMap is immutable")
    else:
        _fail("gate-thresholds ConfigMap not found or not immutable")
        failures += 1

    # 6. Trace files exist locally
    trace_files = sorted(ROOT.glob("traces/**/*.jsonl"))
    total = sum(1 for f in trace_files for _ in f.open(encoding="utf-8", errors="replace"))
    if total > 0:
        _ok(f"{total} trace records found in {len(trace_files)} file(s)")
    else:
        _fail("no traces found — capture some with harness/capture.py save-api-trace")
        failures += 1

    return failures


def print_guide(ns: str) -> None:
    _header("Demo steps  (G.7 — both members must be able to explain each step)")

    print(f"""
Step 1: Run the evaluation Job
─────────────────────────────
  kubectl -n {ns} delete job rahbar-eval --ignore-not-found
  kubectl apply -f k8s/eval-job.yaml
  kubectl -n {ns} wait --for=condition=complete job/rahbar-eval --timeout=1800s
  kubectl -n {ns} logs job/rahbar-eval

  After it finishes, copy results from the datalake-shell:
  kubectl cp -n {ns} -c shell \\
      datalake-shell:/data/evaldata/suite ./demo-eval-results

Step 2: Open a trace (D2)
─────────────────────────
  # Pick any trace_id from the trace files, e.g. the most recent:
  python eval/trace_tool.py --traces traces/ <TRACE_ID>

  # Or list all trace IDs to choose one:
  python -c "
import json, glob, sys
for f in glob.glob('traces/**/*.jsonl', recursive=True):
    for line in open(f, encoding='utf-8', errors='replace'):
        try:
            t = json.loads(line)
            if t.get('kind') == 'trace':
                print(t['trace_id'], t.get('ts',''), t.get('query','')[:60])
        except json.JSONDecodeError:
            pass
  "

Step 3: Ingest an unseen batch via the ingestion Job
─────────────────────────────────────────────────────
  # Put new documents in the inbox (Batch B docs, or any new .md/.html/.pdf with sidecar):
  for f in eval/batch_b/docs/*; do b=$(basename "$f")
    kubectl cp -n {ns} -c shell "$f" "datalake-shell:/data/lake/inbox/B/$b"
  done

  # Delete any finished ingest Job, then re-apply:
  kubectl -n {ns} delete job ingest-manual --ignore-not-found
  kubectl apply -f k8s/a3/jobs/ingest-manual.yaml
  kubectl -n {ns} wait --for=condition=complete job/ingest-manual --timeout=900s
  kubectl -n {ns} logs job/ingest-manual

  # The job exit code is 0 on success, 3 if another run holds the lock:
  kubectl -n {ns} get job ingest-manual -o jsonpath='{{{{.status.conditions}}}}'

Step 4: Show a trace from the new batch (confirms guardrail D4)
────────────────────────────────────────────────────────────────
  # Query the API about something from Batch B:
  curl -s http://localhost:8080/api/ask \\
       -H "Content-Type: application/json" \\
       -d '{{"question": "What is the ID card replacement fee?"}}'
  # (run kubectl port-forward svc/api 8080:80 -n {ns} first)

  # Tail the trace log and find the guardrail action:
  kubectl -n {ns} logs deploy/api | tail -20 | python -c "
import json, sys
for line in sys.stdin:
    try:
        t = json.loads(line)
        if t.get('guardrail_actions'):
            print(json.dumps(t['guardrail_actions'], indent=2))
    except json.JSONDecodeError:
        pass
  "
""")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--namespace", default="rahbar-a3",
                        help="Kubernetes namespace (default: rahbar-a3)")
    args = parser.parse_args(argv)
    failures = check_cluster(args.namespace)
    print_guide(args.namespace)
    if failures:
        print(f"\n{failures} pre-check(s) failed — fix them before the demo.", file=sys.stderr)
        return 1
    print("\nAll pre-checks passed. Good luck with the demo!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
