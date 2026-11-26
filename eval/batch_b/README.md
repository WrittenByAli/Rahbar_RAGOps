# Batch B: documents with planted problems

A second batch of 12 documents for the verification and incremental-update work
(requirement AB.1). `manifest.json` is the answer key: what each file plants and
which corpus A document it targets. The verification code must not read it, and
its thresholds are set on corpus A, never on these files. Whatever gets through
is a result to report.

**Both members must review every file.** AB.1 needs their signature, which only
a member can give. Nothing here counts as reviewed yet.

## Build the generated files

```bash
python corpus/fetch.py                  # corpus A into corpus/raw/, if not there yet
python eval/batch_b/make_batch_b.py     # needs pypdf and pyyaml (indexer/requirements.txt)
```

Six documents are derived from corpus A. The repository is public, so they and
their sidecars are git-ignored and rebuilt by the script. They are missing from
a fresh clone, and the AB.1 file check fails there, until the script has run.
Copy `docs/` to the ingestion inbox only after building, sidecars included.

The same source bytes and library versions give the same output bytes. The
script warns if a source differs from `corpus/MANIFEST.md` (the web pages can
change between downloads) and stops if text it depends on has changed. The
generated files still hold third-party text, so do not share them.

## What each document plants

| File in `docs/` | Planted | Why it can happen for real | Built |
|---|---|---|---|
| `annual-report-2020-21.pdf` | exact duplicate: same bytes | Last year's file is uploaded under this year's name. Also tests that annual reports are not treated as versions of each other | generated |
| `academic-regulations-booklet.pdf` | exact duplicate: same text, different bytes | The same PDF is saved again by another tool and catalogued as a new booklet. A raw file hash does not match | generated |
| `grading-guide-for-students.html` | near duplicate | An office re-issues the grading page under its own title with light copy-editing | generated |
| `how-your-grades-work.md` | near duplicate, **deliberately hard** | Someone writes a plain-language guide: same facts, all new wording, no tables, no website menus | generated |
| `academic-rules-handbook-revised.pdf` | truncated | A download stops at 60%. It claims to be a newer handbook, so accepting it would damage a good document | generated |
| `notice-copy.md` | missing metadata | A notice is pasted into a file: no title, date or version | committed |
| `fee-structure-2027-28.html` | newer version of `fee-structure` | The yearly fee revision: a few amounts change and the rest stays | generated |
| `id-card-replacement-procedure.md` | injection | An instruction to assistants is written like a help-desk rule, next to the charges it tries to override | committed |
| `industrial-internship-guidelines.md` | clean | New policy, metadata in front matter | committed |
| `makerspace-equipment-loans.html` | clean | New policy as HTML with a table | committed |
| `student-cloud-credits.md` | clean | New policy with a sidecar | committed |
| `student-locker-allocation.md` | clean | New policy, metadata in front matter | committed |

Notes for review:

- The committed documents are invented and say so near the top. None covers
  student housing, which corpus A leaves out on purpose.
- The two exact duplicates have no visible marker, because adding text would
  break what they test. The truncated file cannot hold one. All three are marked
  in the sidecar's `source` field.
- The rewritten grading guide is our own wording, stored inside the script. Check
  it against the grading page: if a fact drifted, it is no longer a duplicate.
- The injected text is harmless: it only asks for a wrong fee, with no citation
  and no mention of itself. It has complete metadata and plausible text, so
  checks on file quality, completeness, version or duplication have no reason to
  reject it. Confirm what happens from the real report.
- `.gitattributes` keeps LF line endings, so file hashes match on every machine.
