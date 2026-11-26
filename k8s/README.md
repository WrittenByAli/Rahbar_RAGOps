# Kubernetes layer (Assignment 03)

All Assignment 03 objects live in namespace `rahbar-a3`, in `k8s/a3/`, plus two files the
assignment names: `k8s/eval-job.yaml` and `k8s/gate-thresholds.yaml`. They run next to the
Assignment 02 deployment (`k8s/base`, namespace `rahbar`), which is never touched. `k8s/base` and
`k8s/overlays` are the A2 tree that CI applies on kind; do not apply them for A3.

Models run on the host, not in the cluster: Ollama on the host with `nomic-embed-text` and
`qwen2.5:3b`, reached from pods at `host.minikube.internal:11434`
(`OLLAMA_HOST=0.0.0.0` on the host, and the firewall must allow it).

## 1. Fresh cluster

```bash
minikube start --driver=docker --container-runtime=containerd --cni=calico --memory=7400 --cpus=4
minikube addons enable metrics-server
ollama pull nomic-embed-text && ollama pull qwen2.5:3b       # on the host

# images, built from the working tree (the indexer image has tesseract and poppler)
docker build -t rahbar-api:a3 api
docker build -t rahbar-indexer:a3 indexer
docker build -f eval/Dockerfile --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t rahbar-eval:a3 eval
minikube image load rahbar-api:a3
minikube image load rahbar-indexer:a3
minikube image load rahbar-eval:a3

kubectl apply -f k8s/a3/            # namespace, PVCs, config, Chroma, Redis, API, policies, CronJob, shell
kubectl apply -f k8s/gate-thresholds.yaml
kubectl -n rahbar-a3 wait --for=condition=Ready pod/chroma-0 pod/datalake-shell --timeout=180s
```

`--cni=calico` is required, otherwise NetworkPolicies are silently not enforced. The `api` pods
stay not-ready until the first ingestion has built the collection; that is correct.

On Git Bash for Windows, run `export MSYS_NO_PATHCONV=1` first, and use RELATIVE local paths with
`kubectl cp` (a drive letter such as `C:` is read as a pod name).

## 2. Seeding the data lake (corpus A only)

```bash
python corpus/fetch.py                # corpus A into corpus/raw/ (never committed), and sidecars exist
for f in corpus/raw/*; do b=$(basename "$f"); [ "$b" = _index.json ] && continue
  kubectl cp -n rahbar-a3 -c shell "corpus/raw/$b" "datalake-shell:/data/lake/inbox/A/$b"
  kubectl cp -n rahbar-a3 -c shell "corpus/sidecars/$b.meta.yaml" "datalake-shell:/data/lake/inbox/A/$b.meta.yaml"
done
kubectl apply -f k8s/a3/jobs/ingest-manual.yaml
kubectl -n rahbar-a3 wait --for=condition=complete job/ingest-manual --timeout=900s
```

Every file travels with its `*.meta.yaml` beside it. Ingest the two undergraduate rules versions
in the same batch so one supersedes the other.

**Batch B** (planted problems) is NOT part of seeding. It must not enter the lake before the
baseline is saved (plan section 1.1). When the time comes:

```bash
python eval/batch_b/make_batch_b.py   # builds the generated files and sidecars under eval/batch_b/docs/
for f in eval/batch_b/docs/*; do b=$(basename "$f")
  kubectl cp -n rahbar-a3 -c shell "$f" "datalake-shell:/data/lake/inbox/B/$b"
done
kubectl -n rahbar-a3 delete job ingest-manual --ignore-not-found
kubectl apply -f k8s/a3/jobs/ingest-manual.yaml
```

## 3. Re-running Jobs

A finished Job cannot be re-run by applying it again. Delete it first:

```bash
kubectl -n rahbar-a3 delete job ingest-manual --ignore-not-found
kubectl apply -f k8s/a3/jobs/ingest-manual.yaml
kubectl -n rahbar-a3 logs job/ingest-manual

kubectl -n rahbar-a3 delete job rahbar-eval --ignore-not-found
kubectl apply -f k8s/eval-job.yaml
kubectl -n rahbar-a3 logs job/rahbar-eval
```

Exit 3 from the ingestion Job means another run holds the lock. The ingestion Job is the only
writer to Chroma. Results are on the `rahbar-evaldata` PVC. `kubectl cp` cannot read a finished
Job's pod, so copy results from the tool pod:

```bash
kubectl cp -n rahbar-a3 -c shell datalake-shell:/data/evaldata/suite ./suite-out
```

The eval Job runs the suite, then the gate when `/results/baseline/baseline.json` exists, and exits
with the gate's code (0 pass, 1 fail, 2 invalid). `backoffLimit: 0`, so a failure is not retried.
To change the target or label, edit `EVAL_TARGET` and `EVAL_LABEL` in `k8s/eval-job.yaml`.

## 4. Checks

```bash
kubectl -n rahbar-a3 get pods,pvc,netpol
kubectl -n rahbar-a3 port-forward svc/api 8080:80          # then http://localhost:8080/readyz and /metrics
kubectl -n rahbar-a3 logs deploy/api | tail                # trace records, one JSON object per line
```

## 5. Consistency (release map, index targets)

`rahbar-index-targets` lists the collections the Job maintains. Its first entry must equal
`rahbar-release-v1`: collection `rahbar_v1`, `nomic-embed-text`, chunk size 400, overlap 50. Both
are in `k8s/a3/03-release.yaml`. A release ConfigMap is immutable: add `rahbar-release-vN`, never
edit one. A target missing from the list goes stale, and a rollback to it would serve old text.
This is checked by hand today.

## 6. Candidate (release v2, prepared, not built)

`api-candidate` has `replicas: 0`. Its release map `rahbar-release-v2` names `mxbai-embed-large` and
collection `rahbar_v2` (decision D-02). To run it: pull the model on the host, add the v2 line shown
in `k8s/a3/03-release.yaml` to `rahbar-index-targets`, re-run the ingestion Job, then
`kubectl -n rahbar-a3 scale deploy/api-candidate --replicas=1`. Its pods carry `app: api-candidate`
and never match the live Service `api`.

## Notes

- Resource requests and limits are PROVISIONAL until measured under load (plan T8.6).
- The thresholds in `gate-thresholds.yaml` are PROVISIONAL until set from the baseline runs.
- No HPA in this tree. All three PVCs are ReadWriteOnce; pods share them only because minikube has
  one node.
- Not yet covered: the instructor has not confirmed that a host-run Ollama is acceptable (D-03).
