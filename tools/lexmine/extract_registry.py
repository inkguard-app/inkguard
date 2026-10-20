#!/usr/bin/env python3
"""
Extract device and drug brand vocabulary from free government registries.

Sources (US Government works, public domain; no registration):
  GUDID   - FDA device registry, pipe-delimited `device.txt`, brandName column
  NDC     - FDA National Drug Code directory, PROPRIETARYNAME column

WHY A REGISTRY: device and drug brand names -- Veress, Foley, Dexon,
Betadine, Myoview -- were a large source of over-redaction on the
benchmark, and a registry lists the names the makers use.

THE HARD PART IS NOT EXTRACTION, IT IS AMBIGUITY.

"Foley" is a catheter AND the surname of 50,852 people in the US census.
"Betadine" is a brand and nobody's surname. Filing both the same way is
wrong in one direction or the other: treat Foley as plain clinical
vocabulary and a patient named Foley never gets redacted; treat Betadine
as ambiguous and it gets destroyed in every operative note.

Two independent signals decide it, neither of them a judgement call and
neither derived from the lexicon or the model being corrected:

  1. US Census surname frequency (public domain). In the list at all
     means at least 100 Americans carry it -> AMBIGUOUS, protected only
     when a device qualifier follows ("Foley catheter").
  2. English word frequency (wordfreq). A common English word is not a
     useful lexicon entry and is usually registry noise -- brand names
     like "Surgical Direct" and "Bite Tray" tokenize into ordinary
     words that would add nothing and dilute the vocabulary.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

from wordfreq import zipf_frequency

SURNAMES_CSV = Path(__file__).parent / "registry" / "surnames.csv"

# Above this, a token is ordinary English: registry noise, not vocabulary.
# "surgical" 4.4, "direct" 5.2, "french" 5.1 all sit above it;
# "betadine" 1.6 and "veress" 1.0 sit far below.
COMMON_ENGLISH_ZIPF = 3.2

MIN_TOKEN_LEN = 4


def load_surnames() -> set[str]:
    if not SURNAMES_CSV.exists():
        sys.exit(f"missing {SURNAMES_CSV} — fetch the census surname list first")
    with open(SURNAMES_CSV) as f:
        return {r["name"].lower() for r in csv.DictReader(f) if r.get("name")}


def find_column(header: list[str], candidates: tuple[str, ...]) -> int:
    lowered = [h.strip().strip('"').lower() for h in header]
    for cand in candidates:
        if cand in lowered:
            return lowered.index(cand)
    for i, h in enumerate(lowered):
        if any(c in h for c in candidates):
            return i
    return -1


def read_brand_column(path: Path, delimiter: str,
                      candidates: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    with open(path, errors="ignore", newline="") as f:
        reader = csv.reader(f, delimiter=delimiter, quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration:
            return values
        col = find_column(header, candidates)
        if col < 0:
            sys.exit(f"no brand-name column in {path}; header was {header[:12]}")
        print(f"  using column {col}: {header[col]!r}", file=sys.stderr)
        for row in reader:
            if len(row) > col and row[col].strip():
                values.append(row[col].strip())
    return values


def classify(brands: list[str], surnames: set[str]) -> dict:
    token_counts = Counter()
    for b in brands:
        for tok in re.findall(r"[A-Za-z][A-Za-z\-']+", b):
            t = tok.lower().strip("-'")
            if len(t) >= MIN_TOKEN_LEN:
                token_counts[t] += 1

    unambiguous, ambiguous, dropped_common = [], [], []
    for t, n in token_counts.items():
        if zipf_frequency(t, "en") >= COMMON_ENGLISH_ZIPF:
            dropped_common.append(t)
        elif t in surnames:
            ambiguous.append(t)
        else:
            unambiguous.append(t)

    return {
        "brand_strings": len(brands),
        "distinct_tokens": len(token_counts),
        "unambiguous": sorted(unambiguous),
        "ambiguous_surname_collision": sorted(ambiguous),
        "dropped_common_english": sorted(dropped_common),
        "token_counts": {t: token_counts[t] for t in sorted(token_counts)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gudid", help="GUDID delimited device.txt")
    ap.add_argument("--ndc", help="FDA NDC product.txt")
    ap.add_argument("--gudid-xml", help="GUDID XML release (slower, fallback)")
    ap.add_argument("--outdir", default="mined")
    args = ap.parse_args()

    if not any([args.gudid, args.ndc, args.gudid_xml]):
        ap.error("give at least one of --gudid / --ndc / --gudid-xml")

    surnames = load_surnames()
    print(f"census surnames loaded: {len(surnames):,}", file=sys.stderr)
    outdir = Path(args.outdir)
    outdir.mkdir(exist_ok=True)

    jobs = []
    if args.gudid:
        jobs.append(("gudid", Path(args.gudid), "|",
                     ("brandname", "brand_name", "devicename")))
    if args.ndc:
        jobs.append(("ndc", Path(args.ndc), "\t",
                     ("proprietaryname", "proprietary_name", "brandname")))

    for name, path, delim, cols in jobs:
        print(f"\n=== {name}: {path}", file=sys.stderr)
        brands = read_brand_column(path, delim, cols)
        res = classify(brands, surnames)
        res["source"] = name
        (outdir / f"registry_{name}.json").write_text(json.dumps(res, indent=1))
        print(f"  brand strings        : {res['brand_strings']:,}")
        print(f"  distinct tokens      : {res['distinct_tokens']:,}")
        print(f"  unambiguous (brands) : {len(res['unambiguous']):,}")
        print(f"  ambiguous (surnames) : {len(res['ambiguous_surname_collision']):,}")
        print(f"  dropped (common Eng) : {len(res['dropped_common_english']):,}")
        print(f"  sample unambiguous   : {res['unambiguous'][:12]}")
        print(f"  sample surname-coll. : {res['ambiguous_surname_collision'][:12]}")

    if args.gudid_xml:
        raw = Path(args.gudid_xml).read_text(errors="ignore")
        brands = re.findall(r"<brandName>(.*?)</brandName>", raw)
        res = classify(brands, surnames)
        res["source"] = "gudid_xml"
        (outdir / "registry_gudid_xml.json").write_text(json.dumps(res, indent=1))
        print(f"\n=== gudid_xml: {len(brands):,} brand strings")
        print(f"  unambiguous (brands) : {len(res['unambiguous']):,}")
        print(f"  ambiguous (surnames) : {len(res['ambiguous_surname_collision']):,}")
        print(f"  dropped (common Eng) : {len(res['dropped_common_english']):,}")
        print(f"  sample unambiguous   : {res['unambiguous'][:14]}")
        print(f"  sample surname-coll. : {res['ambiguous_surname_collision'][:14]}")


if __name__ == "__main__":
    main()
