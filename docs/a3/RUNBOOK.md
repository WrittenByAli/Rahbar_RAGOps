# Runbook: cluster, screenshots, demo, report

**Every command that refers to a file or flag this assignment adds is PLANNED and has never been
run.** Correct this document during implementation. `k8s/README.md` (to be written) becomes the
authoritative fresh-cluster procedure.

## 1. Fresh cluster (target procedure)
```bash
minikube start --driver=docker --cni=calico --cpus=4 --memory=6144   # size from F5 measurements
minikube addons enable metrics-server
ollama list                                  # generator, embedding, candidate and judge models
docker build -t rahbar-api:a3 api
docker build -t rahbar-indexer:a3 indexer
docker build -t rahbar-eval:a3 -f eval/Dockerfile .
minikube image load rahbar-api:a3            # repeat for each image; add ui if used
kubectl apply -k k8s/overlays/a3
kubectl create secret generic rahbar-secret -n rahbar --from-literal=LLM_API_KEY=unused
kubectl apply -f k8s/tools/datalake-shell.yaml
python corpus/fetch.py                       # needs network once; the snapshot is then reused
kubectl cp <relative-folder> rahbar/datalake-shell:/data/lake/inbox/A   # RELATIVE path on Windows
kubectl apply -f k8s/jobs/ingest-job.yaml
kubectl wait --for=condition=complete job/ingest-manual -n rahbar --timeout=1800s
kubectl rollout status deployment/api -n rahbar
```
The API stays unready until the collection exists: expected. Never put a real key in a command you
will screenshot.

Re-running a Job:
```bash
kubectl delete job rahbar-eval -n rahbar --ignore-not-found
kubectl apply -f k8s/eval-job.yaml
kubectl logs -n rahbar job/rahbar-eval -f
```
Copying results out: from `datalake-shell` (or a similar tool pod mounting the volume), never from
the finished Job pod.

## 2. Screenshot checklist
Each shows the **command and its output, uncropped**, labelled with part and item, from your own
cluster and runs. Take each when the state first exists; several states are hard to recreate.

| Label | Show | When | Planned command |
|---|---|---|---|
| A1 | Batch B verification report: pass or fail per check, with reasons | T6.1 | print the report table (all rows, reasons readable) |
| A2 | Second run on unchanged corpus: `chunks_embedded: 0` | T5.8 and T6.1 | `kubectl logs job/ingest-manual -n rahbar` |
| (A extra) | delete count by doc id; restore demo | T6.2 | `... run.py count --doc-id <id>` before and after |
| B1 | Evaluation summary with all three levels | T5.8 | `kubectl logs job/rahbar-eval -n rahbar` |
| C1 | Gate failing, with the failed rules | T7.5 | gate command, then `echo $?` |
| C2 | Gate passing | T7.5 | same |
| D1 | One full trace record | any | one line, pretty-printed in full |
| D2 | Trace tool rebuilding the prompt for a trace id | any | `python eval/trace_tool.py <id>` |
| E1 | Coverage before (include exit code) | T9.3 | coverage command |
| E2 | Coverage after | T9.6 | same |
| F1 | `kubectl get all,pvc,configmap,cronjob,job -n rahbar` | T8 end | exactly that |
| F2 | Logs of a completed ingestion Job | T5.8 | `kubectl logs` |
| F3 | Logs of a completed evaluation Job | T5.8 | `kubectl logs` |
| F4 | Gate result changing after a ConfigMap edit (before, edit, after) | T8.5 | `kubectl get configmap ... -o yaml`, Job logs, image id |
| F5 | `kubectl rollout status` | T8.3 | |
| F6 | `kubectl rollout undo`, then a trace showing the previous collection and embedding version | T8.4 | undo, status, trace |

A long output may need several screenshots; keep the command visible in the first. A file written
by `harness/capture.py` supports a screenshot but does not replace it.

## 3. Demo
Both members attend and bring a laptop with the pipeline running **on your local Kubernetes
cluster**. Plain `docker run` or Docker Compose earns **0** for the demo (`G.8`).

**The day before:** full rehearsal; images and models present locally; corpus snapshot present;
disk space checked; results committed and pushed; know how long the eval Job takes.

**Preflight on the day:**
```bash
minikube status
kubectl get pods -n rahbar            # all Ready
ollama list
curl.exe -s http://localhost:8000/readyz     # through port-forward
```

**The three required actions:**
1. *Eval as a Job:* delete, `kubectl apply -f k8s/eval-job.yaml`, follow logs, explain the exit
   status. If it is long, start it first and explain while it runs.
2. *Open a trace:* ask a question, take `trace_id`, run the trace tool, walk through retrieved
   chunks, filters, prompt, citations, rail actions, latency.
3. *Ingest unseen documents:* copy them to a new inbox folder, run the ingestion Job, read the
   verification report aloud, ask a question only the new document answers, show its trace.
   Documents without metadata are quarantined with a reason: that is correct. Show the sidecar
   format, add one **using facts printed in the document**, re-ingest. If a file type is
   unsupported, the report must say so rather than the Job crashing.

Afterwards remove demo documents with `delete`, show their count is 0, and remember the corpus
state changed, so old baselines no longer apply.

**Likely questions** (each member answers any of them): why four checks and not the paper's five;
what makes the Job the only writer, and where that is weak; why `rollout undo` restores the
collection; why the gate cannot be satisfied by abstaining; what p95 over about 30 samples means;
which numbers vary between runs and why.

## 4. Report (at most 8 pages; screenshots in the appendix)
Written by the members. The agent may check facts against result files; it does not write the
conclusions. Cite the result file beside each table.

| Page budget | Content | Requirement |
|---|---|---|
| 0.5 | Names and roll numbers (first page), repository URL, one-paragraph system summary | `R.0` |
| 1 | Figure 3 table: stage, what we do, file, what is missing. Stages: ingest, verify, update data lake, update retrieval sources, offline testing, live testing, check coverage | `R.1` |
| 0.75 | Five qualities: measured, threshold, evidence (see `PAPER-NOTES.md`; say what is not measured) | `R.2` |
| 3.25 | Results A to F; compare table at three levels; gate decision **release or reject, with reasons**; resource numbers; the five root-cause lines | `R.3`, `D5`, `F5` |
| 2 | Short answers (a) to (f) | `R.4` |
| 0.5 | Contribution table; AI statement of 2 to 3 lines | `R.5` |

What each short answer needs before you can write it:

| | Evidence to look at | Trap |
|---|---|---|
| (a) catches and misses | Batch B report against the manifest | count from the report, not from what you intended to catch |
| (b) module fine, others not | compare report, per-question drift | if you did not see it, the question allows explaining why |
| (c) archive or delete | restore proof; what rollback, old citations and old traces need | say what breaks specifically in your design |
| (d) least trusted metric | judge agreement, p95 sample size, single-page documents | give a reason with a number |
| (e) hardest paper idea | `PAPER-NOTES.md` departures, your experience | say what you changed |
| (f) object to responsibility | `ARCHITECTURE.md` section 6, verified against the real manifests | do not claim one object enforces the single writer |

**AI statement:** AI tools were used for planning this harness and possibly for implementation.
Write 2 to 3 truthful lines yourselves, covering what was generated and what you verified. Update
`docs/AI-USAGE.md` as you go.
