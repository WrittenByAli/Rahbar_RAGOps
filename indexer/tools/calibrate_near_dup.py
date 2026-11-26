"""Choose the near-duplicate thresholds from corpus A only (D-06).

Positives are copies of corpus A documents with seeded edits. Negatives are every ordered
pair of different corpus A documents. For each window size and cosine limit it prints the
lowest share a positive reaches and the highest share a negative reaches; a share limit
must lie between the two. Results: docs/adr/0009-verification-checks.md.

    cd indexer
    python tools/calibrate_near_dup.py --corpus ../corpus/raw --ollama http://127.0.0.1:11434

All four window settings take about a quarter of an hour on a laptop GPU, almost all of
it embedding. --windows runs a subset, for example --windows 100/50 100/100.

It writes nothing. It never reads Batch B: thresholds tuned on the test documents would
say nothing about them.

It compares some twenty million pairs of windows, so it uses numpy (installed with
chromadb-client) for the matrix of cosines, and first checks that this gives the same
numbers as the pipeline's own function.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import thresholds  # noqa: E402 - needs the line above
from pipeline.clean import clean  # noqa: E402
from pipeline.config import get_settings  # noqa: E402
from pipeline.embed import embed, model_digest  # noqa: E402
from pipeline.extract import extract  # noqa: E402
from pipeline.neardup import best_similarities, share_at_least, split_windows, unit  # noqa: E402
from pipeline.verify import normalised_text  # noqa: E402

SEED = 42
WINDOWS = [(100, 50), (100, 100), (60, 30), (200, 100)]      # (words, stride)
SIMILARITIES = [0.90, 0.92, 0.94, 0.95, 0.96, 0.97]
SYNONYMS = {
    "students": "learners", "student": "learner", "must": "shall", "shall": "will",
    "university": "institution", "course": "module", "courses": "modules",
    "semester": "term", "examination": "assessment", "required": "needed",
    "campus": "site", "may": "can", "program": "programme", "faculty": "staff",
}
NEW_SENTENCE = "This provision was reviewed by the committee and remains in force."


def synonyms(words: list[str]) -> list[str]:
    return [SYNONYMS.get(w.lower(), w) for w in words]


def random_edits(words: list[str], rate: float, rng: random.Random) -> list[str]:
    """Replace, drop or add a word at about `rate` of the positions."""
    out: list[str] = []
    for word in words:
        if rng.random() >= rate:
            out.append(word)
            continue
        kind = rng.choice(("replace", "drop", "add"))
        if kind == "replace":
            out.append(rng.choice(words))
        elif kind == "add":
            out += [word, rng.choice(words)]
    return out


def restructure(words: list[str], rng: random.Random) -> list[str]:
    """Drop about 10% of the passages, swap some neighbours and add new sentences. This
    shifts where every later window starts."""
    blocks = [words[i:i + 120] for i in range(0, len(words), 120)]
    kept = [b for b in blocks if rng.random() >= 0.10] or blocks
    for i in range(0, len(kept) - 1, 7):
        kept[i], kept[i + 1] = kept[i + 1], kept[i]
    out: list[str] = []
    for number, block in enumerate(kept):
        out += block
        if number % 4 == 1:
            out += NEW_SENTENCE.split()
    return out


def new_identity(words: list[str]) -> list[str]:
    """Another name and other years, plus the synonyms."""
    text = " ".join(synonyms(words))
    text = re.sub(r"\b(19|20)(\d\d)\b", lambda m: str(int(m[0]) + 1), text)
    text = text.replace("FAST-NUCES", "the Institute").replace("NUCES", "the Institute")
    return text.split()


def perturbations(text: str, rng: random.Random) -> dict[str, tuple[str, bool]]:
    """name -> (text, must_be_caught)."""
    words = text.split()
    return {
        "light edits (3% of words + synonyms)":
            (" ".join(random_edits(synonyms(words), 0.03, rng)), True),
        "new name, new years, synonyms": (" ".join(new_identity(words)), True),
        "medium edits (8% of words)": (" ".join(random_edits(words, 0.08, rng)), True),
        "passages dropped, swapped, added": (" ".join(restructure(words, rng)), True),
        "heavy rewrite (25% of words), reported only":
            (" ".join(random_edits(synonyms(words), 0.25, rng)), False),
        "first half only (an excerpt), reported only":
            (" ".join(words[:len(words) // 2]), False),
    }


def fast_best(windows: list, others: list) -> list[float]:
    """The pipeline's best_similarities() as one matrix product."""
    import numpy

    return (numpy.asarray(windows) @ numpy.asarray(others).T).max(axis=1).tolist()


def load_corpus(folder: Path) -> tuple[dict[str, str], list[str]]:
    """{file name: normalised cleaned text} of the readable documents, and the rest."""
    readable, unreadable = {}, []
    for path in sorted(folder.glob("*")):
        if path.suffix.lower() not in (".pdf", ".html", ".md"):
            continue
        document = extract(str(path), "", False)
        failed = sum(1 for page in document.pages if page.ocr_error)
        text = normalised_text(clean(document).pages)
        if failed > len(document.pages) / 2 or not text:
            unreadable.append(f"{path.name} ({failed} of {len(document.pages)} pages "
                              "need OCR, which is not available here)")
        else:
            readable[path.name] = text
    return readable, unreadable


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--corpus", type=Path, required=True, help="corpus/raw")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="nomic-embed-text")
    parser.add_argument("--windows", nargs="+", metavar="WORDS/STRIDE",
                        default=[f"{size}/{stride}" for size, stride in WINDOWS])
    args = parser.parse_args()
    settings = replace(get_settings(), ollama_url=args.ollama, embed_model=args.model)

    corpus, unreadable = load_corpus(args.corpus)
    print(f"model {args.model}@{model_digest(args.model, settings)[:12]}  seed {SEED}")
    print(f"{len(corpus)} readable documents; left out: {unreadable or 'none'}")
    for name, text in corpus.items():
        print(f"  {len(text.split()):6d} words  {name}")
    copies = {name: perturbations(text, random.Random(f"{SEED}:{name}"))
              for name, text in corpus.items()}

    summary = []
    for size, stride in (tuple(map(int, w.split("/"))) for w in args.windows):
        cache: dict[str, list[list[float]]] = {}

        def vectors(text: str) -> list[list[float]]:
            if text not in cache:
                cache[text] = [unit(v) for v in embed(split_windows(text, size, stride),
                                                      settings)]
            return cache[text]

        # best cosine per window: positives are copy -> original, negatives a -> b
        first, second = list(corpus)[:2]
        a, b = vectors(corpus[first])[:20], vectors(corpus[second])[:40]
        assert max(abs(x - y) for x, y in zip(fast_best(a, b), best_similarities(a, b))) < 1e-9

        positives = {(name, kind): (fast_best(vectors(copy), vectors(corpus[name])),
                                    required)
                     for name, kinds in copies.items()
                     for kind, (copy, required) in kinds.items()}
        negatives = {(a, b): fast_best(vectors(corpus[a]), vectors(corpus[b]))
                     for a in corpus for b in corpus if a != b}

        print(f"\n=== windows of {size} words, one every {stride} "
              f"({sum(len(vectors(text)) for text in corpus.values())} windows in the corpus)")
        for t in SIMILARITIES:
            low = min((share_at_least(s, t), key) for key, (s, required) in positives.items()
                      if required)
            high = max((share_at_least(s, t), key) for key, s in negatives.items())
            summary.append((size, stride, t, low[0], high[0]))
            print(f"cosine {t:.2f}: lowest required positive {low[0]:.3f} "
                  f"({low[1][1]}; {low[1][0]}) | highest negative {high[0]:.3f} "
                  f"({high[1][0]} -> {high[1][1]})")

        if (size, stride) == (thresholds.WINDOW_WORDS, thresholds.WINDOW_STRIDE):
            report_chosen(positives, negatives)

    print("\n=== margin = lowest required positive - highest negative")
    for size, stride, t, positive, negative in sorted(summary, key=lambda r: r[3] - r[4],
                                                      reverse=True)[:8]:
        print(f"  windows {size}/{stride}, cosine {t:.2f}: {positive:.3f} - {negative:.3f} "
              f"= {positive - negative:.3f}")
    return 0


def report_chosen(positives: dict, negatives: dict) -> None:
    t, cut = thresholds.NEAR_DUP_SIMILARITY, thresholds.NEAR_DUP_SHARE
    print(f"\n--- chosen values: cosine {t}, share {cut} ---")
    kinds = sorted({kind for _, kind in positives})
    for kind in kinds:
        shares = [share_at_least(s, t) for (_, k), (s, _) in positives.items() if k == kind]
        caught = sum(1 for value in shares if value >= cut)
        print(f"  {kind:48s} share min {min(shares):.3f} max {max(shares):.3f}  "
              f"caught {caught}/{len(shares)}")
    ranked = sorted(((share_at_least(s, t), a, b) for (a, b), s in negatives.items()),
                    reverse=True)
    print(f"  negatives flagged: {sum(1 for r in ranked if r[0] >= cut)} of {len(ranked)}; "
          "highest:")
    for share, a, b in ranked[:6]:
        print(f"    {share:.3f}  {a} -> {b}")
    print("  pairs the corpus notes warn about (share of the first found in the second):")
    for a_part, b_part in (("prospectus", "academic-rules-handbook"),
                           ("academic-rules-handbook", "prospectus"),
                           ("prospectus", "student-handbook"),
                           ("annual-report-2023", "annual-report-2019"),
                           ("annual-report-2019", "annual-report-2023")):
        for (a, b), s in negatives.items():
            if a.startswith(a_part) and b.startswith(b_part):
                print(f"    {share_at_least(s, t):.3f}  {a} -> {b}")


if __name__ == "__main__":
    sys.exit(main())
