# CS4032 Assignment 03 — RAGOps Submission

**Course:** CS4032 — Software Construction and Development  
**Assignment:** 03 — RAGOps: Operating and Managing Retrieval-Augmented Generation Pipelines  
**Group:**

| Name | Roll Number | Role |
|---|---|---|
| Muhammad Ali | 24I-3158 | M1 |
| Shahzad Ahmad | 24I-3090 | M2 |

**Repository:** https://github.com/WrittenByAli/Rahbar_RAGOps  
**Namespace:** `rahbar-a3` (minikube, docker driver)

---

## Contents of this folder

| File | Description |
|---|---|
| `24I-3090_24I-3158_Report.pdf` | Submission report (8 pages, R.0–R.5) |
| `README.md` | This file — run instructions for every part |

All result files, traces, manifests and captures are committed in the repository under:
- `eval/results/` — all JSON result files
- `eval/results/cluster/` — kubectl capture files (evidence for Part F)
- `eval/results/proofs/` — admin operation proofs (changed, deleted, restored, undo)
- `eval/results/release/` — gate and release decision files
- `traces/` — raw JSONL trace samples
- `k8s/a3/` — all Kubernetes manifests
- `harness/state/` — attestations and progress

---

## Prerequisites

```bash
# Minikube (docker driver)
minikube start --driver=docker

# Ollama running on host (models must be pulled)
OLLAMA_HOST=0.0.0.0 ollama serve
ollama pull nomic-embed-text
ollama pull qwen2.5:3b

# Python 3.12+
pip install -r api/requirements.txt
pip install -r indexer/requirements.txt
pip install -r eval/requirements.txt
```

---

## Part A — Data Management

```bash
# Apply A3 manifests (first time only)
kubectl apply -f k8s/a3/00-namespace.yaml
kubectl apply -f k8s/a3/01-storage.yaml
kubectl apply -f k8s/a3/02-config.yaml
kubectl apply -f k8s/a3/03-release.yaml
kubectl apply -f k8s/a3/04-chroma.yaml
kubectl apply -f k8s/a3/05-redis.yaml
kubectl apply -f k8s/a3/06-api.yaml
kubectl apply -f k8s/a3/08-networkpolicy.yaml

# Run ingestion (Batch A)
kubectl apply -f k8s/a3/jobs/ingest-manual.yaml
kubectl wait --for=condition=complete job/ingest-manual -n rahbar-a3 --timeout=600s
kubectl logs job/ingest-manual -n rahbar-a3

# Run ingestion (Batch B)
kubectl apply -f k8s/a3/jobs/ingest-batch-b.yaml
kubectl wait --for=condition=complete job/ingest-batch-b -n rahbar-a3 --timeout=600s
kubectl logs job/ingest-batch-b -n rahbar-a3

# Verification report is in eval/results/verification/
# Proof files are in eval/results/proofs/{changed,deleted,restored}.json
```

---

## Part B — Offline Testing (Eval Suite)

```bash
# Port-forward the API
kubectl port-forward svc/api -n rahbar-a3 8080:80 &

# Run the full three-level eval suite (S0 baseline)
python eval/run_suite.py \
  --target http://localhost:8080 \
  --label s0-baseline-run1 \
  --seed 42 \
  --judge-model qwen2.5:3b \
  --golden eval/golden.jsonl \
  --pairs eval/module_pairs.jsonl \
  --out eval/results

# Stochasticity check (run twice, compare)
python eval/stochasticity.py \
  --runs eval/results/20261126T191531Z-s0-baseline-run1.json \
        eval/results/20261126T191740Z-s0-baseline-run2.json

# Results are committed in eval/results/
```

---

## Part C — Release Gate

```bash
# Run the gate: compare baseline vs candidate
python eval/gate.py \
  --baseline eval/results/20261126T191531Z-s0-baseline-run1.json \
  --candidate eval/results/20261127T013551Z-v1-cand-candidate.json
# Exit 0 = pass, 1 = fail

# Gate thresholds are in k8s/gate-thresholds.yaml
# Committed gate results: eval/results/gate/
# Release decision: eval/results/release/rejected.json (FAIL — latency_ms_p95 exceeded)
# Undo proof:       eval/results/release/undo.json
```

---

## Part D — Observability

```bash
# View traces from the running API
kubectl logs -n rahbar-a3 -l app=api --tail=200

# Reconstruct a specific trace (exact context + prompt)
python -m eval.trace_tool <trace_id>
# Example:
python -m eval.trace_tool 457ca53e9ed146d18ef2a3683e548511

# Metrics endpoint
kubectl port-forward svc/api -n rahbar-a3 8080:80 &
curl -4 http://localhost:8080/metrics

# Time-window summary
python eval/trace_summary.py \
  --traces traces/api-traces.jsonl \
  --out eval/results/trace_summary

# Trace samples: eval/results/cluster/d1-traces.txt
# Trace tool output: eval/results/cluster/api-trace-answer.txt
```

---

## Part E — Drift and Coverage

```bash
# Coverage check (before adding new golden questions)
python eval/coverage.py \
  --label before \
  --config eval/coverage_config.yaml \
  --live eval/live_queries.jsonl \
  --golden eval/golden.jsonl \
  --out eval/results/coverage

# Drift report (S0 → S1 after Batch B)
python eval/drift.py \
  --before eval/results/20261126T191531Z-s0-baseline-run1.json \
  --after  eval/results/20261126T213346Z-s1-batchb-candidate.json \
  --out eval/results/drift

# Coverage check exits non-zero when below threshold (0.55)
python eval/coverage.py ... --fail-below 0.55
echo $?   # 0 = above threshold, 1 = below

# Results committed in eval/results/coverage/ and eval/results/drift/
```

---

## Part F — Kubernetes

```bash
# Deploy fresh cluster from k8s/ (F6)
kubectl apply -f k8s/a3/00-namespace.yaml
kubectl apply -f k8s/a3/01-storage.yaml
kubectl apply -f k8s/a3/02-config.yaml
kubectl apply -f k8s/a3/03-release.yaml
kubectl apply -f k8s/a3/04-chroma.yaml
kubectl apply -f k8s/a3/05-redis.yaml
kubectl apply -f k8s/a3/06-api.yaml
kubectl apply -f k8s/a3/08-networkpolicy.yaml
kubectl rollout status deployment/api -n rahbar-a3

# In-cluster eval job (F2.3)
kubectl apply -f k8s/eval-job.yaml
kubectl wait --for=condition=complete job/rahbar-eval -n rahbar-a3 --timeout=600s
kubectl logs job/rahbar-eval -n rahbar-a3

# Rolling update to candidate (F3.2)
kubectl set image deployment/api api=rahbar-api:v2-cand -n rahbar-a3
kubectl rollout status deployment/api -n rahbar-a3

# Rollback (F3.3)
kubectl rollout undo deployment/api -n rahbar-a3
kubectl rollout status deployment/api -n rahbar-a3

# Change a gate threshold without rebuild (F2.4)
kubectl edit configmap rahbar-gate-thresholds -n rahbar-a3
# Then re-run the eval job and observe changed outcome

# All cluster captures: eval/results/cluster/
# Fresh-cluster proof: eval/results/cluster/fresh-cluster-ready.txt
# Rollout undo proof:  eval/results/cluster/rollout-undo.txt
# Undo trace proof:    eval/results/release/undo.json
```

---

## Verifying the Harness

```bash
# Quick check (no cluster needed)
python harness/verify.py --fast --summary

# Full check with cluster
python harness/verify.py --summary

# Expected: PASS=55, SKIP=25 (test-suite checks), PENDING_HUMAN=0, FAIL=0
```
