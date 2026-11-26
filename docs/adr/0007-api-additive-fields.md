# ADR 0007: Optional fields added to the ask API

## Status
Proposed · 2026-11-26. Waiting for review by both members: `api/app/schemas.py` asks for the
partner's review before the contract changes.

## Context
`schemas.py` fixes the request and response shapes, because the UI, `eval/run_eval.py` and the
tests depend on them. Assignment 03 needs two things that the Assignment 02 shapes cannot express:

- Evaluation must be able to ask a question without getting a cached answer. A latency measured
  on cache hits, or a release scored on stored answers, tells us nothing about the pipeline.
- Each request needs an identifier that tracing can use.

## Decision
Add three optional fields. No field is removed, renamed or given another type.

| Where | Field | Default | What it does today |
|---|---|---|---|
| request | `bypass_cache` | `false` | the cache is neither read nor written for this request |
| request | `include_trace` | `false` | accepted and ignored. Returning the trace is not built yet |
| response | `trace_id` | `null` in the schema | a new random id for each request, including abstentions and cache hits |

The record behind `trace_id` (`api/app/trace.py`) lives only in memory during the request.
Nothing saves it, so **the id cannot be looked up yet**.

`trace_id` is separate from `request_id`. A caller can choose `request_id` with the
`X-Request-ID` header, so it need not be unique.

On a cache hit the response now carries this request's `request_id` and `trace_id`. Assignment 02
returned the stored response unchanged, so its `request_id` was that of an earlier request and
did not match the `X-Request-ID` response header.

A cache entry keeps the `trace_id` of the request that produced the answer. A later hit records
it as `cache.source_trace_id`.

The shape of a returned citation is unchanged, but its `page` now comes from the best-ranked
chunk, among those sent to the model, that has the cited source and section. Assignment 02
returned the page the model wrote, although the prompt shows no page numbers. When one section
spans several chunks, this page may not be that of the sentence the model used.

## Options considered
1. **An HTTP header such as `Cache-Control: no-cache`.** It would work, but it is not part of the
   schema, so it is easier to miss in code review and in the API documentation.
2. **Set `CACHE_ENABLED=false` while evaluating.** This needs a different configuration and a
   restart, so the pipeline being tested would differ from the one that serves. The setting
   exists, but evaluation should not depend on it.
3. **Use `request_id` as the trace id.** Rejected: the caller controls it.
4. **A second route, `/api/v2/ask`.** Too much for three optional fields.

## Consequences
- Existing callers keep working. The defaults give the old behaviour, and the extra response
  field can be ignored. `eval/run_eval.py` and `eval/offline_eval.py` need no change to run.
- `eval/run_eval.py` does not send `bypass_cache`. The new evaluation runner must send it.
- An older API image silently ignores request fields it does not know. A caller who sends
  `bypass_cache` to it can still get a cached answer. A response without `trace_id` is a sign of
  an old image.
- Any caller can set `bypass_cache` and force a model call. The API has no authentication, and
  the same load can already be caused by changing the question text.
- `include_trace` will expose the full prompt. When it is built, it should sit behind a setting
  that is off by default (`docs/a3/ARCHITECTURE.md`, section 4).
- Citation validity needs the model's raw citations. The response still returns only the filtered
  ones, so a caller over HTTP **cannot measure raw validity until the trace is returned**.
