#!/usr/bin/env python3
"""
Build inkguard/lexicon_open.py — the clinical vocabulary from OPENLY LICENSED,
CITABLE sources only.

WHY
---
Every shipped word list comes from public or openly licensed sources, so
that each entry can be cited. This builder uses these ontologies and
vocabularies, each one openly licensed and citable:

  Uberon        anatomy            CC BY 3.0   Mungall et al., Genome Biol 2012
  Disease Ont.  disease            CC0 1.0     Schriml et al., NAR 2022
  MONDO         disease            CC BY 4.0   Vasilevsky et al., medRxiv 2022
  MAxO          medical actions    CC BY 4.0   Carmody et al., 2023
  WordNet 3.0   medical lexicog-   WordNet licence (permissive, notice)
                raphy files        Miller, Commun ACM 1995
  NCI Thesaurus anatomy, disease,  CC BY 4.0   (NCI EVS; OBO edition)
                drugs, procedures,
                devices, microbes
  ICD-10-CM     diagnoses          public domain (CMS)

Excluded on purpose: the Human Phenotype Ontology (custom licence whose
terms we could not confirm), UMLS/SNOMED (licence required),
and any textbook.

NAME SAFETY
-----------
An ontology is full of people: every eponym (Hodgkin, Foley, Kocher) is
somebody's surname. No token that is a first or last name in either
name source below may enter the vocabulary unconditionally:

  inkguard/name_registry.py      Faker name providers, every locale (MIT);
                                 1990 US Census names; 2000 US Census
                                 surnames with 1,000+ bearers
  2000 US Census surnames        every surname with 100+ bearers,
                                 public domain (census2000.csv)
  1990 US Census first names     public domain

Such tokens go to OPEN_EPONYMS_AMBIGUOUS: clinical only when a qualifier
follows ("Foley catheter", never bare "Foley"). Everything that enters
OPEN_VOCAB is still the WEAK tier in the engine: a title or relation word
in front of it ("Dr. Fluke") always wins.

Geographic tokens (NCIt "Geographic Area" branch: countries, states,
cities that name diseases — Lyme, Ebola, Marburg) never enter the
vocabulary; a disease named after a place must not shield the place.

Usage:
  python tools/build_open_lexicon.py --src DIR --icd icd10cm.csv
  (DIR holds uberon-basic.obo doid.obo mondo.obo maxo.obo ncit.obo and
   wordnet/data.noun, and the census files census2000.csv dist.male.first
   dist.female.first)
"""
from __future__ import annotations

import argparse
import collections
import csv
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from wordfreq import zipf_frequency  # noqa: E402

_SYN = re.compile(r'^synonym: "((?:[^"\\]|\\.)*)" (\w+)')
_TOKEN = re.compile(r"[A-Za-z][A-Za-z']*[A-Za-z]")

NCIT_BRANCHES = {
    "NCIT:C12219": "anatomy",        # Anatomic Structure, System, or Substance
    "NCIT:C7057": "disease",         # Disease, Disorder or Finding
    "NCIT:C1909": "drug",            # Pharmacologic Substance
    "NCIT:C25218": "procedure",      # Intervention or Procedure
    "NCIT:C19238": "device",         # Diagnostic, Therapeutic, or Research Equipment
    "NCIT:C14329": "microbe",        # Microorganism
    "NCIT:C12913": "cell",           # Abnormal Cell
}
NCIT_GEO = "NCIT:C16632"             # Geographic Area — excluded, and its tokens blocked

CASE_INFORMATIVE = ("uberon", "doid", "mondo", "maxo")   # lowercase labels

_GENUS = re.compile(r"(?:coccus|cocci|bacter|bacterium|monas|bacillus|myces|plasma|"
                    r"spora|virus|viridae|bacteriaceae|ella|iella|ia|ium|us|um)$")

QUALIFIER_WORDS = {
    "syndrome", "disease", "sign", "test", "procedure", "operation", "maneuver",
    "reflex", "tumor", "tumour", "lymphoma", "sarcoma", "carcinoma", "catheter",
    "clamp", "forceps", "retractor", "scissors", "needle", "tube", "palsy",
    "anomaly", "malformation", "phenomenon", "classification", "scale", "score",
    "criteria", "fracture", "disorder", "ulcer", "node", "nodes", "cell", "cells",
    "body", "bodies", "canal", "duct", "gland", "ligament", "space", "valve",
    "shunt", "stent", "incision", "repair", "flap", "technique", "approach",
}


def parse_obo(path: str) -> dict:
    terms, cur = {}, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("[Term]"):
                cur = {"syn": [], "is_a": [], "obsolete": False}
                continue
            if line.startswith("["):
                cur = None
                continue
            if cur is None:
                continue
            if line.startswith("id: "):
                cur["id"] = line[4:].strip()
                terms[cur["id"]] = cur
            elif line.startswith("name: "):
                cur["name"] = line[6:].strip()
            elif line.startswith("synonym: "):
                m = _SYN.match(line)
                if m and m.group(2) in ("EXACT", "RELATED", "NARROW"):
                    cur["syn"].append(m.group(1).replace('\\"', '"'))
            elif line.startswith("def: "):
                # Definitions are curated scientific prose. Lowercase words
                # in them attest vocabulary the labels miss ("genitofemoral"
                # appears only inside definitions of Uberon terms). Text
                # imported from Wikipedia (CC BY-SA) is skipped.
                m = re.match(r'^def: "((?:[^"\\]|\\.)*)" \[(.*)\]', line)
                if m and "wikipedia" not in m.group(2).lower():
                    cur.setdefault("defs", []).append(m.group(1))
            elif line.startswith("is_a: "):
                cur["is_a"].append(line[6:].split()[0])
            elif line.startswith("is_obsolete: true"):
                cur["obsolete"] = True
    return {k: v for k, v in terms.items() if not v["obsolete"] and "name" in v}


def descendants(terms: dict, root: str) -> set:
    kids = collections.defaultdict(list)
    for t in terms.values():
        for p in t["is_a"]:
            kids[p].append(t["id"])
    out, stack = set(), [root]
    while stack:
        x = stack.pop()
        if x in out:
            continue
        out.add(x)
        stack.extend(kids.get(x, ()))
    return out


def load_census(src: str) -> set:
    """EVERY surname in the 2000 US Census surname file (151,671 names
    borne by 100+ people) plus the 1990 first-name files. Together with
    inkguard/name_registry.py this is the guard: the weak vocabulary tier must
    never claim a real surname."""
    names = set()
    p2000 = os.path.join(src, "census2000.csv")
    if os.path.exists(p2000):
        for r in csv.DictReader(open(p2000)):
            names.add(r["name"].lower())
    # The 1990 surname file's tail (rank > 30,000) is NOT used here: it
    # holds names with a few dozen bearers that are also common medical
    # words (LIPOMA is a 1990 census surname). The 2000 file, which lists
    # every surname with 100+ bearers, is the authority for real surnames;
    # the name registry already holds 1990 ranks 1-30,000.
    for fn in ("dist.male.first", "dist.female.first"):
        p = os.path.join(src, fn)
        if os.path.exists(p):
            for line in open(p):
                if line.strip():
                    names.add(line.split()[0].lower())
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--icd", required=True)
    ap.add_argument("--out", default="inkguard/lexicon_open.py")
    args = ap.parse_args()

    from inkguard.name_registry import NAMES
    census = load_census(args.src)
    person_names = set(NAMES) | census
    print(f"name guard: {len(NAMES)} registry + {len(census)} census = {len(person_names)}")

    # token -> set(sources); proper/common case votes from case-informative sources
    attest: dict[str, set] = collections.defaultdict(set)
    proper = collections.Counter()
    common = collections.Counter()
    before_qualifier: set[str] = set()
    geo: set[str] = set()

    compounds: dict[str, set] = collections.defaultdict(set)

    def ingest(strings, source, case_informative, lowercase_labels=False):
        for s in strings:
            # Hyphenated eponym compounds: "Beckwith-Wiedemann", "Guillain-
            # Barre", "Stevens-Johnson". Written bare in notes, without the
            # word "syndrome"; each half alone is a surname.
            for m in re.finditer(r"\b([A-Z][a-z]+(?:-[A-Z][a-z]+)+)\b", s):
                compounds[m.group(1).lower()].add(source)
            toks = _TOKEN.findall(s)
            for i, t in enumerate(toks):
                base = re.sub(r"'s$", "", t)
                low = base.lower()
                if len(low) < 3 or "'" in low:
                    continue
                attest[low].add(source)
                # Uberon, DOID, MONDO and MAxO write labels in lowercase, so
                # a capital even on the FIRST word marks a proper noun
                # ("Schweigger-Seidel sheath"). ICD is sentence-case: there
                # only a capital after the first word counts.
                if case_informative and (i > 0 or lowercase_labels):
                    if base[0].isupper() and base[1:].islower():
                        proper[low] += 1
                    elif base.islower():
                        common[low] += 1
                if i + 1 < len(toks) and toks[i + 1].lower() in QUALIFIER_WORDS \
                        and base[0].isupper():
                    before_qualifier.add(low)

    def ingest_defs(strings, source):
        for s in strings:
            for t in _TOKEN.findall(s):
                if t.islower() and len(t) >= 4 and "'" not in t:
                    attest[t].add(source)
                    common[t] += 1

    for fn, source in (("uberon-basic.obo", "uberon"), ("doid.obo", "doid"),
                       ("mondo.obo", "mondo"), ("maxo.obo", "maxo")):
        terms = parse_obo(os.path.join(args.src, fn))
        ingest((s for t in terms.values() for s in [t["name"], *t["syn"]]), source, True,
               lowercase_labels=True)
        ingest_defs((d for t in terms.values() for d in t.get("defs", ())), source + "-def")
        print(f"{source}: {len(terms)} terms")

    # WordNet 3.0 (Princeton; permissive licence with notice): medical
    # lexicographer files only, lowercase lemmas only (WordNet capitalizes
    # proper nouns, so people and places are excluded by construction).
    wn = os.path.join(args.src, "wordnet", "data.noun")
    if os.path.exists(wn):
        lexfiles = {"08": "body", "26": "state", "04": "act", "06": "artifact",
                    "27": "substance", "22": "process"}
        n_wn = 0
        for line in open(wn, encoding="utf-8", errors="replace"):
            if line.startswith("  "):
                continue
            parts = line.split()
            if len(parts) < 5 or parts[1] not in lexfiles:
                continue
            for k in range(int(parts[3], 16)):
                lemma = parts[4 + 2 * k]
                if lemma.islower() or lemma.replace("_", "").replace("-", "").islower():
                    for t in re.split(r"[_\-]", lemma):
                        if t.isalpha() and t.islower() and len(t) >= 4:
                            attest[t].add("wordnet")
                            common[t] += 1
                            n_wn += 1
        print(f"wordnet: {n_wn} lemma tokens from medical lexicographer files")

    ncit = parse_obo(os.path.join(args.src, "ncit.obo"))
    for tid in descendants(ncit, NCIT_GEO):
        t = ncit[tid] if tid in ncit else None
        if t:
            for s in [t["name"], *t["syn"]]:
                geo.update(x.lower() for x in _TOKEN.findall(s))
    keep = set()
    for root in NCIT_BRANCHES:
        keep |= descendants(ncit, root)
    keep -= descendants(ncit, NCIT_GEO)
    ingest((s for tid in keep if tid in ncit
            for s in [ncit[tid]["name"], *ncit[tid]["syn"]]), "ncit", False)
    ingest_defs((d for tid in keep if tid in ncit for d in ncit[tid].get("defs", ())),
                "ncit-def")
    print(f"ncit: {len(keep)} terms in clinical branches, {len(geo)} geographic tokens blocked")

    with open(args.icd) as f:
        for row in csv.reader(f):
            if row and row[0] and row[0][0] <= "T":
                ingest([row[3], row[5]], "icd10cm", True)

    vocab: dict[str, str] = {}
    amb: dict[str, str] = {}
    dropped = collections.Counter()
    trace = set(os.environ.get("LEXICON_TRACE", "").split(","))
    for low, srcs in attest.items():
        if low in trace:
            print("TRACE", low, sorted(srcs), "proper", proper[low], "common", common[low],
                  "geo", low in geo, "name", low in person_names,
                  "before_q", low in before_qualifier)
        if not low.isalpha() and "-" not in low:
            dropped["non-alpha"] += 1
            continue
        tag = ",".join(sorted(srcs))
        eponymish = (proper[low] > 0 and common[low] == 0) or \
                    (low in before_qualifier and common[low] == 0)
        if low in geo:
            dropped["geographic"] += 1
            continue
        if low in person_names:
            # Only a name that ontologies actually use as an eponym BEFORE a
            # clinical noun ("Foley catheter") earns a place in the
            # ambiguous set; any other collision is simply left out.
            if eponymish and low in before_qualifier:
                amb[low] = tag
            else:
                dropped["name collision"] += 1
            continue
        if len(low) < 4:
            dropped["short"] += 1
            continue
        if zipf_frequency(low, "en") >= 4.0:
            dropped["common English"] += 1      # the frequency layer owns these
            continue
        if eponymish and not _GENUS.search(low):
            # A proper noun in the case-informative ontologies (Brugada,
            # Takada, Babinski) is a surname somewhere even when no name
            # list we hold contains it. It is clinical only with a
            # qualifier. Genera (Enterococcus, Klebsiella) are capitalized
            # by convention, not because they are people, and stay.
            amb[low] = tag
            continue
        vocab[low] = tag

    for c, srcs in compounds.items():
        parts = c.split("-")
        # Only compounds whose every part is itself an eponym the builder
        # knows, and which are not geographic ("Rocky-Mountain" never).
        if all(p in amb or p in person_names or p in vocab for p in parts) \
                and not any(p in geo for p in parts):
            vocab[c] = ",".join(sorted(srcs)) + ",compound"
    print(f"OPEN_VOCAB {len(vocab)}   OPEN_EPONYMS_AMBIGUOUS {len(amb)}   dropped {dict(dropped)}")
    counts = collections.Counter(s for v in vocab.values() for s in v.split(","))
    print("vocab tokens attested per source:", dict(counts))

    with open(args.out, "w") as out:
        out.write('"""\nOpen clinical vocabulary — GENERATED by tools/build_open_lexicon.py.\n'
                  'Do not hand-edit. Every entry names the openly licensed source(s)\n'
                  'that attest it; see THIRD_PARTY_NOTICES.md for licences and citations.\n\n'
                  'OPEN_VOCAB is the WEAK tier: clinical unless a name title or relation\n'
                  'word stands before it. OPEN_EPONYMS_AMBIGUOUS are eponyms that are also\n'
                  'personal names: clinical only with a qualifier ("Foley catheter").\n"""\n\n')
        out.write("OPEN_VOCAB: dict[str, str] = {\n")
        for k in sorted(vocab):
            out.write(f'    "{k}": "{vocab[k]}",\n')
        out.write("}\n\nOPEN_EPONYMS_AMBIGUOUS: dict[str, str] = {\n")
        for k in sorted(amb):
            out.write(f'    "{k}": "{amb[k]}",\n')
        out.write("}\n")
    return 0


def _pack() -> None:
    """Store the list as data (inkguard/data/lexicon_open.json.gz) with a short loader."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    from inkguard._data import pack_module
    pack_module("lexicon_open", "tools/build_open_lexicon.py")


if __name__ == "__main__":
    _rc = main()
    _pack()
    sys.exit(_rc)
