#!/usr/bin/env python3
"""
Build inkguard/lexicon_care_open.py: words of care settings, services, symptoms,
units, vaccines and mental-health terms from openly licensed sources, for the
WEAK vocabulary tier (clinical_lexicon.ICD_VOCAB: a title or a relation word
before a word always wins over it).

Not used by 0.25.0: this vocabulary was built and measured and not adopted
(see inkguard/clinical_lexicon.py), so its output is not shipped. The
script is kept for the record.

Sources (see THIRD_PARTY_NOTICES.md):
  MeSH 2025 descriptors and entry terms  NLM, public domain (NLM terms:
        acknowledge NLM, state the version, no endorsement). Supplementary
        concept records (chemicals, brand-like names) are NOT used.
  HL7 Terminology v3 RoleCode, ActCode    HL7, CC0 (HL7-owned code systems only)
  OMRSE                                   CC BY 4.0
  CDC CVX                                 US Government work, public domain
  Units of Measurement Ontology (UO)      CC BY 4.0
  Symptom Ontology (SYMP)                 CC0 1.0
  Mental Functioning / Mental Disease cores (MF, MFOMD)  CC BY 3.0

Safety: every word that is in ANY name list the tool holds (the name
registry, which includes the census surname lists, and the first-name
lists), or that is a place in the gazetteer, is dropped. A word the
vocabulary already holds is not added again. Only words of three letters or
more, letters only.

    python tools/build_care_vocab.py CACHE_DIR      # downloads into CACHE_DIR
"""
from __future__ import annotations

import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RAW = "https://raw.githubusercontent.com"
SOURCES = {
    "mesh": f"{RAW}/biopragmatics/obo-db-ingest/main/export/mesh/2025/mesh.obo",
    "hl7-rolecode": f"{RAW}/HL7/UTG/master/input/sourceOfTruth/v3/codeSystems/v3-RoleCode.xml",
    "hl7-actcode": f"{RAW}/HL7/UTG/master/input/sourceOfTruth/v3/codeSystems/v3-ActCode.xml",
    "omrse": f"{RAW}/mcwdsi/OMRSE/main/src/ontology/omrse-edit.owl",
    "cvx": f"{RAW}/biopragmatics/obo-db-ingest/main/export/cvx/cvx.obo",
    "uo": f"{RAW}/bio-ontology-research-group/unit-ontology/master/uo.obo",
    "symp": f"{RAW}/DiseaseOntology/SymptomOntology/main/src/ontology/symp.obo",
    "mf": f"{RAW}/jannahastings/mental-functioning-ontology/master/ontology/internal/MF-core.owl",
    "mfomd": f"{RAW}/jannahastings/mental-functioning-ontology/master/ontology/internal/MD-core.owl",
}
WORD = re.compile(r"[A-Za-z][a-z]{2,}(?:-[a-z]{2,})*")


def fetch(name: str, cache: Path) -> str:
    p = cache / (name + ".txt")
    if not p.exists():
        with urllib.request.urlopen(SOURCES[name], timeout=120) as r:      # noqa: S310
            p.write_bytes(r.read())
    return p.read_text(encoding="utf-8", errors="replace")


def obo_terms(text: str, keep_id=lambda i: True) -> list[str]:
    out, cur_ok = [], False
    for line in text.splitlines():
        if line.startswith("[Term]"):
            cur_ok = False
        elif line.startswith("id: "):
            cur_ok = keep_id(line[4:].strip())
        elif cur_ok and line.startswith("name: "):
            out.append(line[6:].strip())
        elif cur_ok and line.startswith("synonym: ") and ("EXACT" in line or "RELATED" in line):
            m = re.match(r'synonym: "([^"]+)"', line)
            if m:
                out.append(m.group(1))
    return out


# MeSH branches that name living things, places, groups of people or
# publications, not care: plants and animals are first names ("Aster",
# "Calla"), and geographic headings are places.
_MESH_EXCLUDE_UNDER = {"Plants", "Animals", "Geographic Locations", "Named Groups", "Persons",
                       "Humanities", "Publication Characteristics", "Information Science",
                       "Population Groups", "Occupational Groups", "Chemicals and Drugs",
                       "Organic Chemicals", "Inorganic Chemicals", "Heterocyclic Compounds",
                       "Polycyclic Compounds", "Amino Acids, Peptides, and Proteins",
                       "Carbohydrates", "Lipids", "Nucleic Acids, Nucleotides, and Nucleosides",
                       "Complex Mixtures", "Biological Factors", "Enzymes and Coenzymes"}


def mesh_terms(text: str) -> list[str]:
    names, parents, syns = {}, {}, {}
    cur = None
    for line in text.splitlines():
        if line.startswith("[Term]"):
            cur = None
        elif line.startswith("id: "):
            cur = line[4:].strip()
            if not cur.startswith("mesh:D"):
                cur = None
            else:
                parents[cur], syns[cur] = [], []
        elif cur and line.startswith("name: "):
            names[cur] = line[6:].strip()
        elif cur and line.startswith("is_a: "):
            parents[cur].append(line[6:].split()[0])
        elif cur and line.startswith("synonym: "):
            m = re.match(r'synonym: "([^"]+)"', line)
            if m:
                syns[cur].append(m.group(1))
    bad: dict[str, bool] = {}

    def excluded(i: str, depth: int = 0) -> bool:
        if i in bad:
            return bad[i]
        if depth > 40:
            return False
        r = names.get(i) in _MESH_EXCLUDE_UNDER or any(
            excluded(p, depth + 1) for p in parents.get(i, []))
        bad[i] = r
        return r
    out = []
    for i, n in names.items():
        if not excluded(i):
            out.append(n)
            out += syns.get(i, [])
    return out


_STOP = frozenset("""and for the with des del della der den die das von van von la le les du de
    road drive street lane avenue court way place rise flat quarter hill hills park view close
    crescent terrace row walk square gardens green grove mount saint san santa""".split())


def owl_labels(text: str) -> list[str]:
    labs = re.findall(r'rdfs:label[^"]*"([^"]+)"', text)
    labs += re.findall(r"<rdfs:label[^>]*>([^<]+)</rdfs:label>", text)
    return labs


def hl7_display(text: str) -> list[str]:
    return re.findall(r'<display value="([^"]+)"', text) + re.findall(r'display="([^"]+)"', text)


def main() -> None:
    cache = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/care-vocab")
    cache.mkdir(parents=True, exist_ok=True)
    phrases: dict[str, list[str]] = {
        "mesh": mesh_terms(fetch("mesh", cache)),
        "hl7": hl7_display(fetch("hl7-rolecode", cache)) + hl7_display(fetch("hl7-actcode", cache)),
        "omrse": owl_labels(fetch("omrse", cache)),
        "cvx": obo_terms(fetch("cvx", cache)),
        "uo": obo_terms(fetch("uo", cache)),
        "symp": obo_terms(fetch("symp", cache)),
        "mf": owl_labels(fetch("mf", cache)) + owl_labels(fetch("mfomd", cache)),
    }
    from inkguard.name_registry import NAMES
    from inkguard.clinical_lexicon import ICD_VOCAB, _ALL_CLINICAL_TERMS
    try:
        from inkguard.agate import _places
        places = _places()
    except Exception:  # noqa: BLE001
        places = frozenset()
    words: dict[str, set[str]] = defaultdict(set)
    for src, items in phrases.items():
        for ph in items:
            for w in WORD.findall(ph):
                low = w.lower()
                words[low].add(src)
    out = {}
    dropped = 0
    for w, srcs in sorted(words.items()):
        if (w in NAMES or w in places or w in _STOP or any(p in NAMES for p in w.split("-"))):
            dropped += 1
            continue
        if w in ICD_VOCAB or w in _ALL_CLINICAL_TERMS:
            continue
        out[w] = ",".join(sorted(srcs))
    target = ROOT / "inkguard" / "lexicon_care_open.py"
    lines = ['"""GENERATED by tools/build_care_vocab.py — do not edit.',
             "",
             "Words of care settings, services, symptoms, units, vaccines and mental-health",
             "terms from openly licensed sources (MeSH 2025 descriptors, courtesy of the U.S.",
             "National Library of Medicine; HL7 Terminology, CC0; OMRSE, UO, CC BY 4.0;",
             "CDC CVX, public domain; Symptom Ontology, CC0; MF/MFOMD, CC BY 3.0). The",
             "WEAK tier only, minus every word in any name list or the gazetteer. Each",
             'entry names its sources. See THIRD_PARTY_NOTICES.md."""',
             "", "CARE_OPEN: dict[str, str] = {"]
    lines += [f"    {w!r}: {s!r}," for w, s in out.items()]
    lines += ["}", ""]
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(out)} new words written; {dropped} dropped as names or places; "
          f"by source: " + ", ".join(f"{k} {len(v)}" for k, v in phrases.items()))


if __name__ == "__main__":
    main()
