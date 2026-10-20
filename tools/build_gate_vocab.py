#!/usr/bin/env python3
"""
Build inkguard/lexicon_gate.py — vocabulary used ONLY by the agate
(inkguard/agate.py) to say "the tool knows what this word is". It is
never used to decide that something is not PHI during detection.

  ABBREVIATIONS  abbreviations the ontologies TAG as abbreviations:
                 NCI Thesaurus synonyms with term type AB, in the same
                 clinical branches the open lexicon uses (CC BY 4.0), and
                 MONDO synonyms typed ABBREVIATION (CC BY 4.0). Stored in
                 capitals; they explain a word only when it is written in
                 capitals.
  BRAND_WORDS    words of drug and device brand names: openFDA Drugs@FDA
                 and NDC brand names, and FDA GUDID device brand names
                 (all public domain). A GUDID word must occur in at least
                 three brand names, so one-off product strings add nothing.

Every word that is in ANY name list the tool holds is dropped from
BRAND_WORDS (registry, 1990 and 2000 census, international lists), so
this vocabulary can never vouch for a surname.

  python tools/build_gate_vocab.py SRC_DIR
  (SRC_DIR holds ncit.obo, mondo.obo, uberon-basic.obo, the census/name
   lists, fda/ with drugsfda.json.zip, ndc.json.zip, gudid_brandnames.txt,
   and extra/ with efo.obo, cl-basic.obo and the Meta-Inventory CSV)

The output also holds a wider vocabulary (EXTRA_VOCAB), eponyms, the
Meta-Inventory abbreviations, instrument eponyms, two-word clinical terms
and place names; each is described where it is built below.
"""
from __future__ import annotations

import collections
import csv
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

ABBR = re.compile(r"^[A-Z][A-Z0-9\-]{1,7}$")
WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*[A-Za-z]")


def ncit_abbreviations(path: Path) -> set[str]:
    from build_open_lexicon import NCIT_BRANCHES, NCIT_GEO
    is_a, abbr = collections.defaultdict(list), collections.defaultdict(set)
    cur = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("[Term]"):
                cur = None
            elif line.startswith("id: "):
                cur = line[4:].strip()
            elif cur and line.startswith("is_a: "):
                is_a[line[6:].split()[0]].append(cur)
            elif cur and line.startswith("synonym: ") and 'NCIT_P383="AB"' in line:
                m = re.match(r'^synonym: "([^"]+)"', line)
                if m and ABBR.match(m.group(1)) and sum(c.isalpha() for c in m.group(1)) >= 2:
                    abbr[cur].add(m.group(1))

    def desc(root):
        out, stack = set(), [root]
        while stack:
            x = stack.pop()
            if x not in out:
                out.add(x)
                stack.extend(is_a.get(x, ()))
        return out
    keep = set()
    for r in NCIT_BRANCHES:
        keep |= desc(r)
    keep -= desc(NCIT_GEO)
    return {a for t in keep for a in abbr.get(t, ())}


def mondo_abbreviations(path: Path) -> set[str]:
    out = set()
    rx = re.compile(r'^synonym: "([^"]+)" EXACT ABBREVIATION')
    for line in open(path, encoding="utf-8"):
        m = rx.match(line)
        if m and ABBR.match(m.group(1)) and sum(c.isalpha() for c in m.group(1)) >= 2:
            out.add(m.group(1))
    return out


def name_lists(src: Path) -> set[str]:
    from inkguard.name_registry import NAMES
    names = set(NAMES)
    for f in ("dist.all.last", "dist.female.first", "dist.male.first"):
        p = src / f
        if p.exists():
            names |= {l.split()[0].lower() for l in open(p) if l.strip()}
    c = src / "census2000.csv"
    if c.exists():
        names |= {r["name"].lower() for r in csv.DictReader(open(c))}
    for f in ("intl_surnames.txt", "intl_first.txt"):
        p = src / f
        if p.exists():
            names |= {l.strip().lower() for l in open(p) if l.strip()}
    return names


def brand_words(src: Path) -> tuple[set[str], dict]:
    fda = src / "fda"
    drug = collections.Counter()
    z = zipfile.ZipFile(fda / "drugsfda.json.zip")
    for r in json.load(z.open(z.namelist()[0]))["results"]:
        for p in r.get("products", []):
            for w in WORD.findall(p.get("brand_name") or ""):
                drug[w.lower()] += 1
    z = zipfile.ZipFile(fda / "ndc.json.zip")
    for r in json.load(z.open(z.namelist()[0]))["results"]:
        for w in WORD.findall(r.get("brand_name") or ""):
            drug[w.lower()] += 1
    dev = collections.Counter()
    for line in open(fda / "gudid_brandnames.txt", encoding="utf-8", errors="ignore"):
        for w in set(WORD.findall(line)):
            dev[w.lower()] += 1
    words = {w for w in drug if len(w) >= 4} | {w for w, n in dev.items() if n >= 3 and len(w) >= 4}
    return words, {"drug_words": len(drug), "device_words_3plus": sum(1 for n in dev.values() if n >= 3)}, \
        {w for w in drug if len(w) >= 4}


def places() -> set[str]:
    """Cities, towns and counties — the geographic units SMALLER than a
    state that Safe Harbor removes. From GeoNames (CC BY 4.0) via the
    geonamescache package (MIT): every US place of 500+ people, every
    world city of 15,000+, every US county. States and countries are left
    out: Safe Harbor lets them stay."""
    import geonamescache
    g = geonamescache.GeonamesCache()
    out = set()
    import json as _j
    import os as _o
    data = _j.load(open(_o.path.join(_o.path.dirname(geonamescache.__file__), "data", "cities500.json")))
    for c in (data.values() if isinstance(data, dict) else data):
        if c.get("countrycode") == "US":
            out.add(c["name"].lower())
    for c in g.get_cities().values():
        out.add(c["name"].lower())
    for c in g.get_us_counties():
        n = c["name"] if isinstance(c, dict) else str(c)
        n = re.sub(r"\s+(County|Parish|Borough|Census Area|Municipality|city)$", "", n)
        out.add(n.lower())
    states = {v["name"].lower() for v in g.get_us_states().values()}
    countries = {v["name"].lower() for v in g.get_countries().values()}
    out -= states | countries
    return {x for x in out if re.fullmatch(r"[a-z][a-z .'\-]{2,40}", x)}


def clinical_bigrams(src: Path, names: set[str]) -> set[str]:
    """Two-word anatomy, disease and procedure terms from Uberon (CC BY 3.0)
    and the NCI Thesaurus clinical branches (CC BY 4.0) in which one word
    is also a name — "vena cava", "fascia lata", "pars plana". The single
    words alone cannot vouch for them; the pair can."""
    from build_open_lexicon import parse_obo, descendants, NCIT_BRANCHES, NCIT_GEO
    strings = []
    ub = parse_obo(str(src / "uberon-basic.obo"))
    strings += [x for t in ub.values() for x in [t["name"], *t["syn"]]]
    nc = parse_obo(str(src / "ncit.obo"))
    keep = set()
    for r in NCIT_BRANCHES:
        keep |= descendants(nc, r)
    keep -= descendants(nc, NCIT_GEO)
    strings += [x for tid in keep if tid in nc for x in [nc[tid]["name"], *nc[tid]["syn"]]]
    out = set()
    for st in strings:
        toks = re.findall(r"[A-Za-z]+", st.lower())
        for a, b in zip(toks, toks[1:]):
            if len(a) >= 3 and len(b) >= 3 and (a in names or b in names):
                out.add(f"{a} {b}")
    return out


_EPONYM_HEAD = re.compile(
    r"^(?:syndrome|disease|sign|test|procedure|operation|maneuver|reflex|palsy|anomaly|"
    r"phenomenon|classification|scale|score|criteria|fracture|disorder|ulcer|nodes?|cells?|"
    r"body|bodies|canal|duct|gland|ligament|space|valve|shunt|stent|incision|repair|flap|"
    r"technique|approach|method|type|virus|dystrophy|atrophy|ataxia|tumou?r|lymphoma|sarcoma|"
    r"carcinoma|anemia|deficiency|stain|staining|assay|index|forceps|clamp|retractor|"
    r"catheter|needle)$", re.I)


def extra_vocabulary(src: Path, names: set[str]) -> tuple[set[str], set[str]]:
    """Vocabulary across all subspecialties. Single words of every term and
    synonym in:
      - the NCI Thesaurus, every top-level branch except Conceptual Entity
        (organisations, places): activities and procedures, drugs, foods and
        chemicals, devices, genes and gene products, organisms, properties,
        chemotherapy regimens, molecular abnormalities (CC BY 4.0);
      - the Experimental Factor Ontology (Apache 2.0);
      - the Cell Ontology (CC BY 4.0).
    Minus every word in any name list (and, in main(), every place name).

    Returns (words, eponyms). An EPONYM is a word the sources themselves
    use as a person's name: written possessive ("Darier's"), in a
    hyphenated name chain before a disease word ("Shaheen-Alkuraya
    syndrome"), or capitalised right before one and never written in
    lowercase. Those are surnames the name lists do not hold; the gate
    reads one only when a clinical word explains it."""
    from build_open_lexicon import parse_obo, descendants, NCIT_GEO
    strings: list[str] = []
    nc = parse_obo(str(src / "ncit.obo"))
    roots = [t["id"] for t in nc.values() if not t["is_a"]]
    keep = set()
    for r in roots:
        if r in ("NCIT:C20181", "NCIT:C28428"):        # Conceptual Entity, Retired
            continue
        keep |= descendants(nc, r)
    keep -= descendants(nc, NCIT_GEO)
    strings += [x for tid in keep if tid in nc for x in [nc[tid].get("name", ""), *nc[tid]["syn"]]]
    for f in ("extra/efo.obo", "extra/cl-basic.obo"):
        p = src / f
        if p.exists():
            o = parse_obo(str(p))
            strings += [x for t in o.values() for x in [t.get("name", ""), *t["syn"]]]
    words: set[str] = set()
    lower_seen: set[str] = set()
    eponym_like: set[str] = set()
    before_head: set[str] = set()
    for st in strings:
        toks = re.findall(r"[A-Za-z][A-Za-z']*[A-Za-z]", st)
        for i, t in enumerate(toks):
            w = re.sub(r"'s$", "", t.lower())
            if len(w) >= 4:
                words.add(w)
            if t.endswith("'s"):
                eponym_like.add(w)
            if t.islower():
                lower_seen.add(w)
            if (i + 1 < len(toks) and _EPONYM_HEAD.match(toks[i + 1])
                    and t[:1].isupper() and not t.isupper()):
                before_head.add(w)
        for m in re.finditer(r"((?:[A-Z][a-z']+-)+[A-Z][a-z']+)\s+([A-Za-z]+)", st):
            if _EPONYM_HEAD.match(m.group(2)):
                eponym_like |= {re.sub(r"'s$", "", x.lower()) for x in m.group(1).split("-")}
    eponym_like |= before_head - lower_seen
    words -= names
    return words, words & eponym_like


def inventory_abbreviations(src: Path) -> tuple[set[str], dict]:
    """Medical Abbreviation and Acronym Meta-Inventory (Grossman Liu et al.,
    Sci Data 2021; CC BY 4.0). Accepted short forms:
      - any attested in a CLINICAL-NOTE source (Vanderbilt clinic notes and
        discharge summaries, Stetson sign-out notes, Columbia obstetric notes,
        Berman pathology); two-letter forms only if two such sources agree;
      - from the literature sources (UMLS SPECIALIST LRABR, ADAM), only forms
        of four or more characters, so that two- and three-letter initials
        are never vouched for.
    Records whose only source is Wikipedia (CC BY-SA) are not used."""
    import csv as _csv
    p = src / "extra" / "mi_Metainventory_Version1.0.0.csv"
    if not p.exists():
        return set(), {}
    clinical_src = {"Vanderbilt Clinic Notes", "Vanderbilt Discharge Sums", "Stetson",
                    "Columbia", "Berman"}
    by_sf: dict[str, set] = collections.defaultdict(set)
    for row in _csv.DictReader(open(p, encoding="utf-8", errors="ignore"), delimiter="|"):
        sf = re.sub(r"[.\s]", "", row["SF"] or "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9/&+\-]{1,9}", sf) or sum(c.isalpha() for c in sf) < 2:
            continue
        by_sf[sf].add(row["Source"])
    out = set()
    for sf, srcs in by_sf.items():
        clin = srcs & clinical_src
        lit = srcs & {"UMLS", "ADAM"}
        if clin and (len(sf) > 2 or len(clin) >= 2):
            out.add(sf)
        elif lit and len(sf) >= 4:
            out.add(sf)
    return out, {"short_forms_seen": len(by_sf), "accepted": len(out)}


def device_eponyms(src: Path, names: set[str]) -> set[str]:
    """Instrument and device eponyms: words in 3+ FDA GUDID device brand
    names that ARE also surnames (Allis, Babcock, Kocher, Bookwalter,
    Kerrison). The gate reads one as an instrument only in surgical
    context with no person cue beside it."""
    # Only a surname standing right before an instrument noun counts:
    # "Allis Clamp", "Kerrison Rongeur" — not "Smith & Nephew", "Johnson &
    # Johnson", which are makers.
    instrument = re.compile(
        r"^(?:clamps?|forceps|retractors?|rongeurs?|scissors|elevators?|dissectors?|curettes?|"
        r"needles?|hooks?|speculum|specula|tenaculum|dilators?|catheters?|tubes?|drains?|"
        r"blades?|knives|knife|osteotomes?|chisels?|gouges?|rasps?|spreaders?|holders?|"
        r"frames?|tables?|headrests?|pins?|tongs|probes?|sounds?|bougies?|stents?|valves?|"
        r"shunts?|cannulas?|trocars?|sutures?|staplers?|plates?|screws?|nails?|wires?|"
        r"mallets?|punches|punch|snares?|baskets?|graspers?|pliers|cutters?|awls?)$", re.I)
    dev = collections.Counter()
    for line in open(src / "fda" / "gudid_brandnames.txt", encoding="utf-8", errors="ignore"):
        toks = WORD.findall(line)
        for a, b in zip(toks, toks[1:]):
            if instrument.match(b):
                dev[a.lower()] += 1
    # Common surnames and any first name are never vouched for: "Anderson"
    # beside a retractor is as likely the assistant as the instrument.
    common = set()
    p = src / "dist.all.last"
    if p.exists():
        for line in open(p):
            parts = line.split()
            if len(parts) >= 4 and int(parts[3]) <= 5000:
                common.add(parts[0].lower())
    for f in ("dist.female.first", "dist.male.first", "intl_first.txt"):
        q = src / f
        if q.exists():
            common |= {l.split()[0].lower() for l in open(q) if l.strip()}
    return {w for w, n in dev.items() if n >= 2 and len(w) >= 4 and w in names and w not in common}


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "vocab_src")
    abbr = ncit_abbreviations(src / "ncit.obo") | mondo_abbreviations(src / "mondo.obo")
    names = name_lists(src)
    brands, stats, drug_only = brand_words(src)
    # Drug brands that are ALSO names ("Allegra"): kept apart. The gate
    # accepts one only with medication context around it.
    brand_names = drug_only & names
    brands -= names
    out = ROOT / "inkguard" / "lexicon_gate.py"
    with open(out, "w") as fh:
        fh.write('"""GENERATED by tools/build_gate_vocab.py — do not edit.\n\n'
                 "Used ONLY by the agate to recognise a word it may leave in.\n"
                 "ABBREVIATIONS: NCI Thesaurus (term type AB, clinical branches; CC BY 4.0)\n"
                 "and MONDO (ABBREVIATION synonyms; CC BY 4.0). BRAND_WORDS: openFDA Drugs@FDA\n"
                 "and NDC brand names, FDA GUDID device brand names (public domain), minus\n"
                 "every word in any name list the tool holds.\n"
                 '"""\n')
        for name, items in (("ABBREVIATIONS", sorted(abbr)), ("BRAND_WORDS", sorted(brands)),
                            ("DRUG_BRANDS_THAT_ARE_NAMES", sorted(brand_names))):
            fh.write(f"_{name} = (\n")
            for i in range(0, len(items), 12):
                fh.write("    " + repr(" ".join(items[i:i + 12]) + " ") + "\n")
            fh.write(f")\n{name} = frozenset(\"\".join(_{name}).split())\n\n")
        ev_all, ev_epo = extra_vocabulary(src, names)
        ev_all -= set(places())
        try:
            from wordfreq import zipf_frequency
        except Exception:                                  # pragma: no cover
            zipf_frequency = None
        # Common English is never an eponym here ("images", "caffeine").
        epo = sorted(w for w in ev_epo & ev_all
                     if zipf_frequency is None or zipf_frequency(w, "en") < 3.0)
        ev = sorted(ev_all - set(epo))
        fh.write("# All subspecialties: NCIt (all branches but Conceptual Entity), EFO, CL.\n")
        fh.write("_EXTRA = (\n")
        for i in range(0, len(ev), 12):
            fh.write("    " + repr(" ".join(ev[i:i + 12]) + " ") + "\n")
        fh.write(")\nEXTRA_VOCAB = frozenset(\"\".join(_EXTRA).split())\n\n")
        fh.write("# Surnames the same sources use as eponyms (Darier, Alkuraya): gate reads\n"
                 "# one only with a clinical word explaining it.\n_EPO = (\n")
        for i in range(0, len(epo), 12):
            fh.write("    " + repr(" ".join(epo[i:i + 12]) + " ") + "\n")
        fh.write(")\nEXTRA_EPONYMS = frozenset(\"\".join(_EPO).split())\n\n")
        inv, inv_stats = inventory_abbreviations(src)
        fh.write("# Medical Abbreviation and Acronym Meta-Inventory (CC BY 4.0), filtered.\n")
        fh.write("_INV = (\n")
        iv = sorted(inv)
        for i in range(0, len(iv), 12):
            fh.write("    " + repr(" ".join(iv[i:i + 12]) + " ") + "\n")
        fh.write(")\nINVENTORY_ABBREVIATIONS = frozenset(\"\".join(_INV).split())\n\n")
        de = sorted(device_eponyms(src, names))
        fh.write("# Instrument eponyms: GUDID brand words that are also surnames.\n")
        fh.write("_DEV = (\n")
        for i in range(0, len(de), 12):
            fh.write("    " + repr(" ".join(de[i:i + 12]) + " ") + "\n")
        fh.write(")\nDEVICE_EPONYMS = frozenset(\"\".join(_DEV).split())\n\n")
        bi = sorted(clinical_bigrams(src, names))
        fh.write("# Two-word clinical terms with a name-colliding word: Uberon, NCIt.\n")
        fh.write("CLINICAL_BIGRAMS = frozenset((\n")
        for x in bi:
            fh.write(f"    {x!r},\n")
        fh.write("))\n\n")
        # Places contain spaces ("fort wayne"), so one per line.
        pl = sorted(places())
        fh.write("# Cities, towns, counties: GeoNames (CC BY 4.0) via geonamescache (MIT).\n")
        fh.write("PLACES = frozenset((\n")
        for x in pl:
            fh.write(f"    {x!r},\n")
        fh.write("))\n")
    print(f"places {len(pl)}; clinical bigrams {len(bi)}; extra vocabulary {len(ev)} "
          f"(+{len(epo)} eponyms); "
          f"inventory abbreviations {inv_stats}; device eponyms {len(de)}")
    for w in ("peritomy", "barbotage", "cottonoid", "morcellized", "seldinger", "shiley", "dexon",
              "webril", "hemitransfixion", "submucoperichondrial", "liliequist"):
        print(f"  {w}: {'yes' if w in ev else 'no'}")
    for a in ("VCUG", "KUB", "RPOC", "JKD", "JD", "SBO", "PTU"):
        print(f"  {a}: {'yes' if a in inv else 'no'}")
    for w in ("allis", "babcock", "kocher", "bookwalter", "kerrison", "smith", "johnson"):
        print(f"  {w}: {'yes' if w in de else 'no'}")
    print(f"abbreviations {len(abbr)}; brand words {len(brands)} ({stats}); "
          f"drug brands that are names {len(brand_names)}")
    for w in ("BPH", "EGD", "UPJ", "KUB", "TURP", "HEENT", "PERRLA"):
        print(f"  {w}: {'yes' if w in abbr else 'no'}")
    for w in ("bookwalter", "veress", "amplatz", "marlex", "endocatch", "versastep",
              "surgidac", "seamguard", "lortab", "rapide", "ellik", "smith", "kaehler"):
        print(f"  {w}: {'yes' if w in brands else 'no'}")


def _pack() -> None:
    """Store the list as data (inkguard/data/lexicon_gate.json.gz) with a short loader."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    from inkguard._data import pack_module
    pack_module("lexicon_gate", "tools/build_gate_vocab.py")


if __name__ == "__main__":
    main()
    _pack()
