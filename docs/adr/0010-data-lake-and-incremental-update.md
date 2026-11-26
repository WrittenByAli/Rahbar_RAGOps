# ADR 0010: A data lake as the desired state, and reconcile instead of rebuild

## Status
Proposed. Implemented; not yet reviewed by both members. Follows D-04, D-05 and D-14.

## Context
The Assignment 02 indexer deleted the collection and embedded every chunk on every run
(finding F-05). Assignment 03 needs: nothing embedded for an unchanged corpus, one changed
document replaced alone, deletes without orphans, archive and restore of old versions, and
only one writer.

## Decision
- **The lake manifest is the desired state of retrieval.** New files arrive in
  `inbox/<batch>/` and leave it for `lake/` or `quarantine/`. A missing file means nothing.
  A document goes only through `run.py delete`.
- **`CORPUS_DIR` and `--source` folders are only read.** CI indexes `eval/fixtures` this way
  from a read-only mount. The lake records the hashes of files that were replaced by a
  corrected copy or deleted. Such a file from a read-only folder is ignored ("unchanged",
  action `ignored`, reason in the report), so the folder cannot undo either. The inbox may
  deliver it again on purpose. A cluster with a permanent lake should still set
  `CORPUS_DIR=""` and deliver through the inbox only.
- **Order:** extract, clean, verify the whole batch, update the lake, then chunk and write.
  Inbox files are removed only after the manifest is saved and the verification report is
  written, so a killed run sees them again.
- **Crash safety in the lake:** each version folder's `meta.json` says which manifest entry
  it belongs to. A folder the manifest does not own is moved aside (`~orphaned`), and a
  version set aside for a correction that was never recorded is moved back.
- **One bad file never stops a run.** Any error while reading it becomes its quality
  failure. Two files with the same `doc_id` and version but different text in one batch:
  the first (by file name) is judged normally and the second fails recency.
- **The lake keeps extracted text, not cleaned text.** Each use cleans it again. A new
  cleaner reaches old documents on the next run, and `content_sha256` is recalculated.
- **Reconcile** compares, for each version, the expected chunk ids and metadata with what
  the collection holds. Same: no write. Another fingerprint (chunker version, cleaner
  version, size, overlap, embedding model, model digest): embed every chunk of that
  version. Otherwise embed only the missing ids. All new chunks of all documents are
  upserted first, then all metadata updates (which hide superseded versions), then all
  deletes. Chunks of unknown `(doc_id, version)` pairs are swept.
- **Superseded versions stay in the collection** with `superseded=true`, so the API's
  existing filter hides them and restore is a metadata update.
- **Restore pins the choice.** A corrected copy of an archived version stays archived. A
  version never seen before is accepted, becomes active and ends the pin. Delivering a known
  version again is "unchanged" and changes nothing.
- **`index_generation`** is random, changes before the first write and after the last, and
  does not change if nothing is written. `write_in_progress` marks a run between the two; a
  later run that finds it replaces the generation even if it has nothing to write.
- **`lake_state_id`** in each target's metadata is a hash of the active documents it was
  built from, the same value as in the ingest report. Two collections with equal values
  hold the same documents. It is removed before the first write and set after the last.
  Ingestion sets it after reconciling the whole collection. Delete and restore look at one
  document, so they set it only on a collection that matched the previous state; otherwise
  they remove it, and the next ingestion sets it.
- **Guards against destroying an index.** A collection of another embedding model is never
  rewritten (only the dummy index or an empty collection is replaced). Chunks are never
  swept from a collection that does not carry this lake's `lake_id`, including one with
  none such as the Assignment 02 index, unless run with `--adopt-collection`. A lake that
  never held a document touches no collection.
- **A newer extractor** is part of the fingerprint and of the extraction cache key, and the
  lake extracts its originals again (keeping the old text if that goes worse).
- **Lock:** `state/ingest.lock`, created with `O_EXCL`, refreshed by a heartbeat. After
  `LOCK_STALE_SECONDS` (300) without a refresh, another run takes it over.
- **Targets:** `INDEX_TARGETS` (YAML list; every entry gives collection, embed model, chunk
  size and overlap). Unset means the single `CHROMA_COLLECTION`.
- **Reports** go to `REPORT_DIR`, by default `<LAKE_DIR>/reports`. `LAKE_DIR` defaults to a
  temporary folder, which suits CI; the cluster must set it to the data-lake volume.
- **A "document" in ingest counts is one version** of a document.

## Options considered
1. **Mirror a source folder** (delete what disappears). A rejected update leaves the folder,
   and the good version would be deleted with it.
2. **A separate index ledger file.** A second record of the collection that can disagree
   with it. Chunk metadata already says what is stored.
3. **`flock` for the lock.** The kernel frees it when the process dies, but it needs
   different code on Windows and its behaviour depends on the volume type.

## Consequences
- An unchanged run still reads every lake document and every chunk's metadata, and chunks
  in memory. At this corpus size that takes seconds.
- A changed embedding model re-embeds everything in that target, as it must.
- A run that fails while embedding writes nothing, and repeats all embedding next time.
- Files quarantined from a read-only folder are quarantined again on every run.
- The lock relies on the volume's file times. It was tested on one machine, not on a
  volume shared between nodes.
- A version is compared by date. Two different labels with the same date (for example
  `2023` and `2023-24`) are ordered as text.
- **Known edge case, left as it is:** recency compares with the **active** version, as
  ARCHITECTURE 3.4 says. After restoring 2023 while 2026 is archived, a never-seen 2025
  version counts as newer: it is accepted, becomes active and ends the pin, although 2026
  is already known and later than it. Whoever restores should know that the next unseen
  version newer than the restored one takes over.
- In the quarantine proof, rejected redeliveries of text that is already indexed are listed
  under `already_indexed` with their real chunk count, when an earlier run accepted that
  text and all those chunks were in the store before the rejected file arrived.
