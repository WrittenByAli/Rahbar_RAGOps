# RAHBAR — Grounded RAG on Kubernetes

![CI](https://github.com/WrittenByAli/Rahbar_RAGOps/actions/workflows/ci.yml/badge.svg)

RAHBAR is a Retrieval-Augmented Generation (RAG) system that answers questions about FAST-NUCES university policies. It ingests a curated document corpus (PDFs, HTML, Markdown), extracts and chunks the text with structure-aware splitting, embeds the chunks using a local embedding model (Ollama), stores them in a Chroma vector database, and serves a FastAPI endpoint that retrieves relevant passages and generates grounded answers via an LLM. The entire system runs on Kubernetes (minikube) with proper separation of concerns: an indexer Job writes the index, the API reads it, and a chat UI provides the user interface.

## Architecture

```mermaid
graph TB
    User([User]) --> Ingress[nginx Ingress<br/>rahbar.local]
    Ingress -->|/| UI[UI Deployment<br/>nginx:8080]
    Ingress -->|/api| API[API Deployment<br/>FastAPI:8000<br/>3 replicas]
    API --> Chroma[(Chroma StatefulSet<br/>Vector Store<br/>10Gi PVC)]
    API --> Redis[(Redis Deployment<br/>Semantic Cache)]
    API --> Ollama[Ollama<br/>LLM + Embeddings<br/>host.minikube.internal]
    Indexer[Indexer Job] -->|write once| Chroma
    Indexer --> Ollama
    CronJob[Reindex CronJob<br/>daily 2 AM] -.->|creates| Indexer
```

**Key invariant:** The Indexer Job is the only writer to Chroma. The API never indexes on startup — with 3 replicas, that would create 3 racing writers on one PVC.

## Prerequisites

- Docker Desktop with Kubernetes enabled, or minikube (`minikube start --driver=docker`)
- `kubectl` and `kustomize` (bundled with recent kubectl)
- Ollama installed and running: `OLLAMA_HOST=0.0.0.0 ollama serve`
- Python 3.12+ with pip
- GNU Make (via Git Bash, WSL2, or native)

## Quick Start (Clean Clone to Answered Question)

```bash
# 1. Clone and enter the repo
git clone https://github.com/WrittenByAli/Rahbar_RAGOps.git
cd Rahbar_RAGOps

# 2. Start minikube
minikube start --driver=docker
minikube addons enable ingress

# 3. Build and load images into minikube
make images

# 4. Create the namespace and apply manifests
kubectl apply -k k8s/overlays/dev/

# 5. Create the secret (never committed to git)
make secret KEY=your-api-key-here

# 6. Wait for pods to be ready
kubectl wait --for=condition=available deployment/api -n rahbar --timeout=120s

# 7. Run the indexer (dummy mode for Week 1, real mode for Week 2)
kubectl create job --from=cronjob/reindex reindex-now -n rahbar

# 8. Start minikube tunnel for Ingress access
minikube tunnel

# 9. Ask a question
curl -s http://rahbar.local/api/ask -H "Content-Type: application/json" \
  -d '{"question": "What is the attendance requirement?"}' | python -m json.tool

# 10. Open the UI
# Navigate to http://rahbar.local in your browser
```

## kubectl Apply Order

Apply resources in this order for clean first-time deployment:

```bash
kubectl apply -f k8s/base/namespace.yaml
kubectl apply -f k8s/base/configmap.yaml
kubectl apply -f k8s/base/chroma-statefulset.yaml
kubectl apply -f k8s/base/chroma-service.yaml
kubectl apply -f k8s/base/redis-deployment.yaml
kubectl apply -f k8s/base/redis-service.yaml
kubectl apply -f k8s/base/api-deployment.yaml
kubectl apply -f k8s/base/api-service.yaml
kubectl apply -f k8s/base/ui-deployment.yaml
kubectl apply -f k8s/base/ui-service.yaml
kubectl apply -f k8s/base/ingress.yaml
kubectl apply -f k8s/base/hpa.yaml
kubectl apply -f k8s/base/networkpolicy.yaml
kubectl apply -f k8s/base/indexer-job.yaml
kubectl apply -f k8s/base/indexer-cronjob.yaml
```

Or use Kustomize (recommended):

```bash
kubectl apply -k k8s/overlays/dev/   # stub mode
kubectl apply -k k8s/overlays/prod/  # real RAG mode
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/ask` | Ask a question. Body: `{"question": "..."}`. Returns `AskResponse` with answer, citations, retrieved passages. |
| GET | `/healthz` | Liveness probe. Returns 200 if the process is alive. Never touches dependencies. |
| GET | `/readyz` | Readiness probe. Checks Chroma (collection exists, non-empty, embed_model pin), Redis connectivity. Returns 503 on failure. |
| GET | `/metrics` | Prometheus metrics: `rahbar_requests_total`, `rahbar_latency_seconds`. |

## Running the Indexer

The indexer runs as a Kubernetes Job and is the only component that writes to Chroma.

```bash
# Dummy mode (Week 1, no model needed)
kubectl create job indexer-dummy --from=cronjob/reindex -n rahbar -- python -m pipeline.run --dummy

# Real pipeline (Week 2, requires Ollama with nomic-embed-text)
kubectl create job indexer-real --from=cronjob/reindex -n rahbar

# Check indexer logs
kubectl logs job/indexer-real -n rahbar -f

# The CronJob runs automatically at 2 AM daily
kubectl get cronjob reindex -n rahbar
```

## Running Tests

```bash
# Install dependencies
make install

# Run all tests (no cluster, no model, no network)
make test

# Run evaluation against a live cluster
make eval
```

## Project Structure

```
rahbar-starter/
├── api/                    # FastAPI service (Deployment, 3 replicas)
│   ├── app/
│   │   ├── main.py         # App entry, lifespan, middleware
│   │   ├── config.py       # Settings from env vars
│   │   ├── schemas.py      # Pydantic models (AskRequest, AskResponse, etc.)
│   │   ├── routes/         # healthz, readyz, metrics, ask
│   │   ├── retrieval/      # StubRetriever, ChromaRetriever
│   │   ├── providers/llm/  # StubProvider, OllamaProvider, HostedProvider
│   │   └── services/       # answer pipeline, semantic cache
│   └── tests/
├── indexer/                # Indexer Job (batch/v1)
│   ├── pipeline/           # extract, clean, chunk, embed, store
│   └── tests/
├── ui/                     # nginx static chat UI
├── k8s/
│   ├── base/               # All Kubernetes manifests
│   └── overlays/           # dev (stubs) and prod (real) kustomize overlays
├── eval/                   # Golden set, eval runner, baseline
├── corpus/                 # Document sources and fetch script
├── docs/                   # ADRs, pipeline docs, engineering notes
└── .github/workflows/      # CI and CD pipelines
```

## UI Screenshot

<!-- Replace with actual screenshot after deploying -->
![RAHBAR Chat UI](docs/evidence/ui-screenshot.png)

*The chat UI is served by nginx on port 8080 and communicates with the API via the Ingress at `/api/ask`.*

---

## Assignment 03 — RAGOps Parts

## Part A — Data Management

```bash
# Ingest corpus into the data lake (Kubernetes job, namespace rahbar-a3)
kubectl apply -f k8s/a3/jobs/ingest-manual.yaml
kubectl wait --for=condition=complete job/ingest-manual -n rahbar-a3 --timeout=300s
kubectl logs job/ingest-manual -n rahbar-a3
```

## Part B — Offline Testing

```bash
# Run the three-level eval suite (component + module + end-to-end)
python eval/run_suite.py \
  --target http://localhost:18990 \
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
```

## Part C — Change and Release Gate

```bash
# Run the release gate: compare baseline vs candidate
python eval/gate.py \
  --baseline eval/results/20261126T191531Z-s0-baseline-run1.json \
  --candidate eval/results/20261126T213346Z-s1-batchb-candidate.json
# Exit 0 = pass, 1 = fail, 2 = invalid comparison
```

## Part D — Observability

```bash
# Collect traces from the running cluster
kubectl logs -n rahbar-a3 -l app=api --tail=200 | grep '"trace_id"'

# Check metrics endpoint
curl http://localhost:18990/metrics
```

## Part E — Drift and Coverage

```bash
# Coverage check (before: S0 corpus, after: add new golden questions)
python eval/coverage.py \
  --label before \
  --config eval/coverage_config.yaml \
  --live eval/live_queries.jsonl \
  --golden eval/golden.jsonl \
  --out eval/results/coverage

# Drift report between S0 baseline and S1 candidate
python eval/drift.py \
  --before eval/results/20261126T191531Z-s0-baseline-run1.json \
  --after  eval/results/20261126T213346Z-s1-batchb-candidate.json \
  --out eval/results/drift
```

## Part F — Kubernetes

```bash
# Deploy the full A3 cluster (namespace rahbar-a3)
kubectl apply -f k8s/a3/00-namespace.yaml
kubectl apply -f k8s/a3/01-storage.yaml
kubectl apply -f k8s/a3/02-config.yaml
kubectl apply -f k8s/a3/03-release.yaml
kubectl apply -f k8s/a3/04-chroma.yaml
kubectl apply -f k8s/a3/05-redis.yaml
kubectl apply -f k8s/a3/06-api.yaml
kubectl apply -f k8s/a3/08-networkpolicy.yaml

# Wait for API to be ready
kubectl rollout status deployment/api -n rahbar-a3

# Run the in-cluster eval job
kubectl apply -f k8s/eval-job.yaml
kubectl wait --for=condition=complete job/rahbar-eval -n rahbar-a3 --timeout=600s
kubectl logs job/rahbar-eval -n rahbar-a3
```
