#!/usr/bin/env python3
"""
Provenance-tracking term miner. Kept for the record: it does not run as it
stands, because it imports tools/mine_lexicon.py, which is not in this
repository.

It is not the source of the shipped vocabulary. Every term in
inkguard/data/lexicon_data_v2.json.gz comes from FDA GUDID, FDA drug
listings or ICD-10-CM, and the source of each term is listed in
tools/lexmine/lexicon_data_v2_sources.csv.gz.

What the script does:

1. PER-SOURCE PROVENANCE. Every term records which source it came from
   and how often it occurred there. Sources are mined SEPARATELY and
   never pooled, so any single source's contribution can be identified,
   audited, or removed later without re-mining the rest.

2. HARVEST CATEGORIES aimed at surgical devices, instruments and brands
   that the name finder read as people -- "Veress", "Foley", "Dexon",
   "French".

Only single terms and their counts are stored; no sentences, definitions
or passages.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))

# Reuse the existing safety vocabulary rather than forking it.
from tools.mine_lexicon import (  # noqa: E402
    DISEASE_QUALIFIERS, CONDITION_SUFFIXES, DRUG_SUFFIXES,
    STOPWORDS, KNOWN_AMBIGUOUS,
)

# --- new harvest vocabulary -------------------------------------------

# "Veress needle", "Foley catheter", "Dexon suture". The head noun is
# the evidence; the capitalized modifier is what NER mistakes for a
# surname and what we need in the lexicon.
DEVICE_NOUNS = {
    "needle", "catheter", "suture", "clamp", "retractor", "forceps",
    "scope", "drain", "stent", "graft", "prosthesis", "trocar",
    "cannula", "speculum", "dilator", "scissors", "probe", "tube",
    "sponge", "solution", "scrub", "dressing", "bandage", "splint",
    "wire", "mesh", "plate", "screw", "balloon", "guidewire", "sheath",
    "port", "blade", "elevator", "rongeur", "curette", "bougie",
    "shunt", "valve", "filter", "coil", "clip", "stapler", "electrode",
    "prep", "gauze", "tourniquet", "trephine", "osteotome", "chisel",
}

# Anatomical head nouns: "superior mesenteric artery", "inguinal canal".
ANATOMY_NOUNS = {
    "artery", "arteries", "vein", "veins", "nerve", "nerves", "muscle",
    "muscles", "ligament", "tendon", "bone", "gland", "duct", "sinus",
    "fossa", "foramen", "process", "ramus", "plexus", "node", "nodes",
    "canal", "cavity", "membrane", "fascia", "lobe", "cortex", "septum",
    "ventricle", "atrium", "sulcus", "gyrus", "notch", "tubercle",
    "condyle", "trunk", "branch", "aponeurosis", "bursa", "sheath",
    "hiatus", "recess", "triangle", "space",
}

# Single-token anatomy and procedure shapes. Distinctive enough that a
# suffix match is reliable; generic endings (-um, -is) are excluded on
# purpose because they would sweep in ordinary English.
ANATOMY_SUFFIXES = (
    "pharynx", "larynx", "thorax", "physis", "ostium", "mediastinum",
    "peritoneum", "pericardium", "retroperitoneum", "omentum",
    "mesentery", "meninges", "ganglion", "medulla", "epiglottis",
)
PROCEDURE_SUFFIXES = (
    "ectomy", "ostomy", "otomy", "plasty", "scopy", "pexy", "rrhaphy",
    "desis", "centesis", "tripsy", "gram", "graphy",
)

# Measurement/sizing conventions that are capitalized but are units,
# not people. "14 French" was a frequent false positive on the benchmark.
SIZING_UNITS = {"french", "charriere", "gauge", "fr"}


def harvest_eponyms(text: str) -> Counter:
    qual = "|".join(sorted(DISEASE_QUALIFIERS))
    pattern = re.compile(
        rf"\b([A-Z][a-z]{{2,}}(?:[-–][A-Z][a-z]{{2,}})*)\s+(?:{qual})\b"
    )
    counts = Counter()
    for m in pattern.finditer(text):
        for part in re.split(r"[-–]", m.group(1)):
            p = part.lower()
            if len(p) >= 3 and p not in STOPWORDS:
                counts[p] += 1
    return counts


def harvest_modifier_noun(text: str, head_nouns: set[str],
                          require_capital: bool) -> Counter:
    """Capture the MODIFIER in '<modifier> <head noun>' constructions."""
    nouns = "|".join(sorted(head_nouns))
    if require_capital:
        pattern = re.compile(rf"\b([A-Z][a-z]{{2,}})\s+(?:{nouns})\b")
    else:
        pattern = re.compile(rf"\b([A-Za-z][a-z]{{3,}})\s+(?:{nouns})\b")
    counts = Counter()
    for m in pattern.finditer(text):
        t = m.group(1).lower()
        if len(t) >= 3 and t not in STOPWORDS:
            counts[t] += 1
    return counts


def harvest_by_suffix(text: str, suffixes, min_len: int = 6) -> Counter:
    counts = Counter()
    for tok in re.findall(r"\b[A-Za-z][A-Za-z\-]{4,}\b", text):
        t = tok.lower().strip("-")
        if len(t) < min_len or t in STOPWORDS:
            continue
        if any(t.endswith(s) for s in suffixes):
            counts[t] += 1
    return counts


def mine_source(name: str, paths: list[str], floor: int) -> dict:
    text = ""
    for p in paths:
        text += Path(p).read_text(errors="ignore") + "\n"

    raw = {
        "eponyms": harvest_eponyms(text),
        "conditions": harvest_by_suffix(text, CONDITION_SUFFIXES),
        "drugs": harvest_by_suffix(text, DRUG_SUFFIXES),
        "devices": harvest_modifier_noun(text, DEVICE_NOUNS, require_capital=True),
        "anatomy_multiword": harvest_modifier_noun(text, ANATOMY_NOUNS, require_capital=False),
        "anatomy_single": harvest_by_suffix(text, ANATOMY_SUFFIXES),
        "procedures": harvest_by_suffix(text, PROCEDURE_SUFFIXES),
    }

    out = {
        "source": name,
        "corpus_chars": len(text),
        "frequency_floor": floor,
        "terms": {},
        "counts": {},
    }
    for category, counter in raw.items():
        kept = sorted(t for t, c in counter.items() if c >= floor)
        out["terms"][category] = kept
        out["counts"][category] = {t: counter[t] for t in kept}
    return out


def load_baseline() -> set[str]:
    """Terms the engine already knows, so a new mining pass reports only
    what it adds. Reads the open vocabulary (lexicon_open.py)."""
    from inkguard import lexicon_open as L
    return set(L.OPEN_VOCAB) | set(L.OPEN_EPONYMS_AMBIGUOUS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--files", nargs="+", required=True)
    ap.add_argument("--floor", type=int, default=3)
    ap.add_argument("--outdir", default="mined")
    args = ap.parse_args()

    res = mine_source(args.source, args.files, args.floor)
    baseline = load_baseline()

    # Marginal yield: what does THIS source add that we did not have?
    total_new = 0
    res["new_vs_baseline"] = {}
    for cat, terms in res["terms"].items():
        new = sorted(t for t in terms if t not in baseline)
        res["new_vs_baseline"][cat] = new
        total_new += len(new)
    res["total_terms"] = sum(len(v) for v in res["terms"].values())
    res["total_new"] = total_new

    outdir = Path(args.outdir)
    outdir.mkdir(exist_ok=True)
    (outdir / f"{args.source}.json").write_text(json.dumps(res, indent=1))

    print(f"=== {args.source} ({res['corpus_chars']:,} chars, floor={args.floor}) ===")
    for cat in res["terms"]:
        print(f"  {cat:20s} {len(res['terms'][cat]):5d} kept"
              f"   {len(res['new_vs_baseline'][cat]):5d} NEW")
    print(f"  {'TOTAL':20s} {res['total_terms']:5d} kept   {total_new:5d} NEW")
    for cat in ("devices", "anatomy_single", "procedures", "eponyms"):
        sample = res["new_vs_baseline"].get(cat, [])[:18]
        if sample:
            print(f"\n  new {cat}: {', '.join(sample)}")


if __name__ == "__main__":
    main()
