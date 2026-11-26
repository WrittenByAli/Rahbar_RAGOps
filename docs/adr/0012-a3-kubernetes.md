# ADR 0012: Assignment 03 Kubernetes layer in its own tree and namespace

## Status
Proposed. Deployed on one minikube cluster; not yet reviewed by both members.

## Context
Assignment 03 needs PVCs for the lake, traces and results, an immutable release ConfigMap, a
candidate Deployment, Jobs and NetworkPolicies. A2 (`k8s/base`, namespace `rahbar`) is still
running, and CI applies `k8s/overlays/dev` on kind, so both must keep working.

## Decision
- A separate, plain tree `k8s/a3/` in namespace `rahbar-a3`, applied with `kubectl apply -f k8s/a3/`.
  `k8s/eval-job.yaml` and `k8s/gate-thresholds.yaml` sit at `k8s/` because the assignment and the
  checker name them. Jobs are in `k8s/a3/jobs/` so applying the directory does not start them.
- A2 is never modified. No kustomize: two students can read every file.
- `api` pods reference `rahbar-config` plus exactly one immutable `rahbar-release-vN`. The
  candidate uses `rahbar-release-v2` and labels `app: api-candidate`, so the live Service does not
  select it. It starts at 0 replicas because its model and collection do not exist yet.
- NetworkPolicy: default deny; Chroma admits only `api`, `api-candidate` and `indexer` pods; the
  eval Job reaches the API and Ollama; DNS for all. Ingestion has no internet (the corpus is
  copied in).
- Non-root (uid 10001, `fsGroup`), read-only root filesystem, all capabilities dropped, no
  service account token, `emptyDir` for `/tmp`.
- Resources are provisional and replaced by measurements (F5).

## Consequences
- Single writer is layered, not enforced by one object: only the ingestion image has write code
  (source scan), `Forbid` and the lake lock serialise runs, and NetworkPolicy limits who can reach
  Chroma. NetworkPolicy cannot tell a read from a write.
- The checker scans every YAML under `k8s/` except overlays, so A2 objects in `k8s/base` with the
  same names are also seen. Rules that look at all matching objects (ingestion Jobs, Chroma
  policies) can fail on A2 files. That is reported, not hidden.
- Host Ollama is still an unconfirmed interpretation (D-03).
