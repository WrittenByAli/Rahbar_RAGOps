# ADR 0009: The four verification checks and their thresholds

## Status
Proposed. Implemented; not yet reviewed by both members. Follows D-04 and D-06.

## Context
A1 asks for four checks with a reason each, quarantine of failures and a report per run.
Thresholds must be set on corpus A and unseen on Batch B. Two defects in extraction and
cleaning had to be fixed first, because they would have distorted both the checks and
page numbers:
- **Spreads.** Each PDF page of both handbooks shows half of a two-page spread, but
  `pdfplumber` also returned the other half and merged the halves line by line. 48% of the
  characters of the Student Handbook and 33% of the Rules Handbook lay outside the visible
  page: 57 of 64 and 29 of 58 pages gave different text. The other corpus A PDFs are
  unaffected. Extraction now reads only the visible box, the CropBox within the MediaBox,
  and OCR renders the same box. `EXTRACTOR_VERSION` (now 3) is part of the extraction
  cache key and of the re-embedding fingerprint.
- **Numbered footers** such as "4 Student Handbook 2026" were not recognised and were taken
  for headings (ADR 0006). The cleaner now finds the page-number offset that fits at least
  half of the pages and removes, at page edges only, the recurring line that carries it
  (`CLEANER_VERSION` 2). On corpus A it removes 60 (Student Handbook), 77 (Rules
  Handbook), 33 (Annual Report 2019-20), 84 (Annual Report 2023-24) and 5 (Prospectus)
  lines. One footer remains, on page 69 of the 2023-24 report, where extraction puts it
  away from the page edge. Numbered clause headings stay, because each appears once.

## Decision

### Order of classification
ARCHITECTURE 3.4, in that order. All four checks are recorded for every file.
Three points the table leaves open:
- "Unchanged" compares with the lake as it was before the run.
- "An earlier file in this batch" counts only files that passed. A copy rejected for
  missing metadata must not get the complete original rejected too.
- A file with no `doc_id` gets one from its file name and byte hash, so it never joins a
  family by accident. If its title matches (years ignored) and at least 60% of its windows
  overlap, the report suggests the family. A person adds a sidecar to accept it.

### Quality (`pipeline/thresholds.py`)
A file fails when it cannot be read (including any error while processing it, and a `.md`
or `.txt` file that is not valid UTF-8), or has more than 50% pages without text, fewer than
100 characters per page, more than 5% unreadable characters (replacement, unprintable,
`(cid:N)`), or fewer than 60% word-like tokens. On corpus A, the seven readable documents
have 914 to 6555 characters per page, 96% to 100% word-like tokens, at most 0.2%
unreadable characters and at most 9% empty pages. The two scans, without OCR, have 92%
and 100% empty pages.

### Completeness
Source, title, `doc_version` (YYYY, YYYY-MM, YYYY-MM-DD or YYYY-YY) and page count, each
from the sidecar, front matter or the file itself. Embedded titles that are placeholders
or file names ("Microsoft Word - ...", "final layout.cdr") count as missing. A PDF's
creation date counts as its date. Truncation: no `%%EOF` in the last 1024 bytes, failure
of pypdf's strict parser, no `</html>`, or a declared page count, size or sha256 that
differs. All seven corpus A PDFs and both HTML pages pass these signals. A Markdown or
text file cut short is detected only if its size, hash or page count was declared.

### Recency
Versions are compared as dates inside one `doc_id`. Older than the active version: stale,
quarantined.

### Uniqueness
Exact: equal bytes, or equal text after extraction and cleaning. Near: each document is
cut into windows of **100 words, one every 50**, embedded with `VERIFY_EMBED_MODEL`
(`nomic-embed-text`, independent of the release). A file of a **new** family is a
near-duplicate when **70%** of its windows have a match at **cosine 0.90** or higher in one
other document. The test is one-way (new file into existing document).

## Calibration
Command: `python tools/calibrate_near_dup.py --corpus ../corpus/raw` (seed 42,
`nomic-embed-text` digest `0a109f422b47`, run on 2026-11-26). It prints its results and
commits no result file; re-run it to check these numbers.

- **Positives:** copies of the seven readable corpus A documents with script-made edits.
  Required to be caught: 3% word edits plus synonyms; new name, years and synonyms; 8%
  word edits; passages dropped, swapped and added. Reported only: 25% word edits, and the
  first half alone.
- **Negatives:** all 42 ordered pairs of different documents.

| Windows (words/stride) | Cosine | Lowest required positive | Highest negative | Margin |
| --- | --- | --- | --- | --- |
| **100/50** | **0.90** | 0.830 | 0.538 | **0.292** |
| 60/30 | 0.90 | 0.724 | 0.563 | 0.161 |
| 100/50 | 0.92 | 0.556 | 0.476 | 0.080 |
| 200/100 | 0.92 | 0.500 | 0.423 | 0.077 |
| 100/100 | 0.90 | 0.448 | 0.417 | 0.032 |
| 100/50 | 0.95 | 0.129 | 0.333 | -0.205 |

The first draft values, cosine 0.95 and share 0.80, would have caught 5 of 28 required
copies. The share cut-off 0.70 lies between 0.538 and 0.830.

At 100/50, 0.90 and 0.70:

| Edit | Share of windows matched (min to max) | Caught |
| --- | --- | --- |
| 3% word edits + synonyms | 0.846 to 1.000 | 7 of 7 |
| new name, years, synonyms | 0.905 to 1.000 | 7 of 7 |
| 8% word edits | 0.871 to 1.000 | 7 of 7 |
| passages dropped, swapped, added | 0.830 to 0.909 | 7 of 7 |
| 25% word edits | 0.413 to 0.608 | **0 of 7** |
| first half only | 1.000 | 7 of 7 |

No negative pair is flagged. The highest are: Annual Report 2019-20 into 2023-24 0.538
(0.133 the other way), Rules Handbook into Student Handbook 0.388 (0.337 the other way),
fee page into grading page 0.333. **The 2026 Prospectus rules and the two 2026
handbooks:** Prospectus into either handbook 0.014, Rules Handbook into Prospectus 0.006.
They say similar things in different words, so this method does not see them as copies.

## Options considered
1. **One mean vector per document.** Hides local differences in long documents.
2. **Windows without overlap.** One inserted passage shifts all later windows; margin 0.032.
3. **Both directions must match.** An excerpt of an existing document would then pass. It
   adds nothing new, so it is quarantined instead.
4. **A word n-gram overlap measure.** Needs no model, but the assignment asks for
   similarity by embedding.

## Consequences
- **Expected misses:** a heavy rewrite or paraphrase (0 of 7 above), and the same rules
  restated in other words (the Prospectus case).
- A new document made of an old one plus much new text passes; an excerpt is rejected.
- The copies were made by a script. Human edits may behave differently, and the two
  scanned documents are not part of the calibration.
- Near-duplicate tests run only for a new family. A mislabelled copy given an existing
  `doc_id` and a newer version is accepted as that new version.
- Annual Report 2019-20 into 2023-24 (0.538) is the closest legitimate pair. It is 0.16
  below the cut-off.
- Changing the verification model means calibrating again.
