# ADR 0008: An exact-match cache, namespaced by release and index

## Status
Proposed · 2026-11-26. Follows the recommended default of decision D-09, which neither member has
confirmed yet. If accepted, it replaces the caching behaviour in ADR 0003. Redis stays.

## Context
Reading the Assignment 02 code showed four problems:

- The key was a hash of the question only. A candidate and the live API will share one Redis, so
  the candidate could be given the live release's answers, and the release gate would compare
  live with itself. After a rollback, an answer cached by the withdrawn release could return.
- Nothing cleared the cache after ingestion. A deleted or changed document could still be
  answered from it for up to an hour.
- The similarity lookup never ran in the application, because no question vector was passed.
  Only unit tests reached it.
- ADR 0003 also describes a generation id check and a rule for follow-up questions. Neither is
  in the code.

## Decision
1. **It is an exact-match cache.** Two questions share an entry only if they are equal after
   lower-casing and collapsing white space. The class is now `AnswerCache`; `SemanticCache` stays
   as an alias for the Assignment 02 tests. The similarity lookup and `CACHE_THRESHOLD` remain,
   limited to the same namespace, and the application does not use them.
2. **Key:** `cache:<release id>:<collection>:<index generation>:<prompt version>:<sha256 of the
   normalised question>`. Each part is percent-encoded, so a `:` inside a value cannot make two
   different namespaces look the same.
3. **Index generation** is the collection metadata value `index_generation`, read from Chroma on
   every request. It is `"0"` if absent, and with the stub retriever. If it cannot be read, that
   request does not use the cache. Guessing could return an answer from before a re-index.
4. **`bypass_cache`** on a request, and **`CACHE_ENABLED=false`**, each skip both read and write.
5. A cache error never stops an answer. Redis calls have a two-second time limit, so a Redis
   that stops replying cannot hang a request.
6. As before, the cache is not used with the stub retriever.

## Options considered
1. **Make the similarity lookup work.** It needs a tuned threshold and more tests, and a false
   hit would corrupt both evaluation results and traces.
2. **Flush Redis after ingestion and releases.** The ingestion Job would need Redis access, and a
   missed flush would go unnoticed. It also cannot separate a candidate and live running together.
3. **One Redis for each release.** More objects to run, and a re-index would still be unhandled.
4. **Hash every pipeline setting into the key.** It would catch a forgotten release id, but the
   list of settings would need its own upkeep and the key would be harder to explain.

## Consequences
- **The rule we rely on:** any change that can alter an answer gets a new `RELEASE_ID`. Two
  deployments that differ only in, say, `TOP_K`, the model or the code, and keep the same release
  id, collection, generation and prompt version, would still share entries.
- The ingestion Job must change `index_generation` after every change that can alter an answer,
  including deletes and status-only changes such as supersede and restore. It must keep the other
  metadata, such as `embed_model`.
- If a run writes to the index and dies before changing the generation, old entries stay readable
  until they expire. A later run that finds nothing left to do must not assume the generation was
  changed. Changing it before the first write and again after the last one would close this.
- With Chroma, each request makes one more metadata call. A cache hit now needs Chroma: if it is
  down, the request fails instead of being served from the cache.
- Unreachable entries use Redis memory until their one-hour time to live ends.
- The namespace stops reuse across releases, not within one. Evaluation and trace demonstrations
  must still send `bypass_cache`.

## Not changed here
- **Nothing writes `index_generation` yet.** `indexer/pipeline/store.py` recreates the
  collection without it, so the API always reads `"0"`. Invalidation after a re-index works only
  once the Phase 2 ingestion Job changes the value. Until then, old answers can still be served
  until they expire.
- `/readyz` still requires Redis, even when the cache is disabled.
- `README.md` and `docs/PIPELINE.md` still say "semantic cache".
