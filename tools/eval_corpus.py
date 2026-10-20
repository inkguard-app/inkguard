#!/usr/bin/env python3
"""
Score the engine against the longitudinal test corpus (2014 XML format).

The corpus ships in benchmark/; its notes were written without running this code. Each
record carries its own answer key: every identifier's exact character span.
A span counts as

  caught    no identifying word of it reaches the reader whole (removed
            or masked for review; generic words such as "Dr." or
            "Medical Center", the year or weekday of a date and the unit
            of an age may stay)
  partial   some identifying words left whole, some not (a leak, scored
            as missed in the strict figure)
  missed    every identifying word of it left whole

The held-out half is scored in aggregate only. Its misses are never
printed, so nothing can be tuned to it; tuning works on the other half.

    python3 tools/eval_corpus.py --half tuning -v
    python3 tools/eval_corpus.py --half held-out
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CORPUS = Path(__file__).resolve().parents[1] / "benchmark"

# The identifiers Safe Harbor names outright. Ages count only over 89.
SAFE_HARBOR = {"PATIENT", "USERNAME", "STREET", "CITY", "ZIP", "LOCATION-OTHER",
               "DATE", "PHONE", "FAX", "EMAIL", "URL", "IPADDR", "SSN",
               "MEDICALRECORD", "HEALTHPLAN", "ACCOUNT", "LICENSE", "VEHICLE",
               "DEVICE", "BIOID", "IDNUM", "AGE"}


def over_89(text: str) -> bool:
    m = re.search(r"\d+", text)
    return bool(m) and int(m.group()) > 89


_YEAR = re.compile(r"(?:19|20)\d\d|'\d\d", re.I)
_LOOSE = re.compile(r"(?:mon|tues|wednes|thurs|fri|satur|sun)days?|spring|summer|fall|"
                    r"autumn|winter|weekends?", re.I)


def required(typ: str, val: str) -> bool:
    """Does Safe Harbor itself demand this span go?

    A year on its own may stay, and so may an age of 89 or under; a weekday
    or season fixes no date. The corpus marks all of them, so the strict
    figure is scored only on what the rule requires.
    """
    if typ not in SAFE_HARBOR:
        return False
    if typ == "AGE":
        return over_89(val)
    if typ == "DATE":
        v = val.strip(" .,")
        return not (_YEAR.fullmatch(v) or _LOOSE.fullmatch(v))
    return True


def load(path: Path):
    root = ET.parse(path).getroot()
    text = root.findtext("TEXT") or ""
    tags = [(t.get("TYPE"), int(t.get("start")), int(t.get("end")), t.get("text"))
            for t in root.find("TAGS")]
    return text, tags


# Words that say what a thing is, not who or where. Leaving them in the
# output reveals nothing ("Dr.", "Medical Center", "Department of").
GENERIC = {
    "dr", "mr", "mrs", "ms", "miss", "mx", "prof", "sr", "jr", "rn", "md", "do",
    "np", "pa", "the", "of", "and", "at", "for", "st", "saint", "medical", "center",
    "centre", "hospital", "clinic", "health", "healthcare", "regional", "general",
    "community", "memorial", "university", "family", "practice", "department",
    "dept", "unit", "ward", "services", "service", "group", "associates", "care",
    "rehabilitation", "rehab", "inc", "llc", "ltd", "co", "company", "school",
    "pharmacy", "street", "st.", "ave", "avenue", "road", "rd", "lane", "ln",
    "drive", "apt", "suite", "room", "rm", "bed", "floor",
}
_TOKEN = re.compile(r"[^\W_]+", re.U)


def kept_chars(text: str, clean: str) -> list[bool]:
    """Which characters of the original reach the reader unchanged.

    Aligns the delivered text to the original, so it counts every layer's
    removals, including ones that never appear in the hit list.
    """
    from difflib import SequenceMatcher
    kept = [False] * len(text)
    # Letters inside a placeholder ("[NAME]") are the tool's, not the
    # note's: an alignment that pairs the "A" of "[NAME]" with the "A" of
    # "A. Brightwater" would score a removed initial as left in. Matches that
    # fall inside a placeholder are ignored.
    ph = [(m.start(), m.end()) for m in re.finditer(r"\[[A-Z][A-Z ]*\]", clean)]
    sm = SequenceMatcher(None, text, clean, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                if not any(a <= j < b for a, b in ph):
                    kept[i] = True
    return kept


_WEEKDAY = re.compile(r"(?:mon|tue|tues|wed|wednes|thu|thur|thurs|fri|sat|satur|sun)(?:day)?s?", re.I)
_UNITS = {"y", "yo", "yr", "yrs", "year", "years", "old", "age", "aged", "m", "f"}


def judge(text: str, s: int, e: int, kept: list[bool], typ: str = "") -> int:
    """0 caught, 1 partial (an identifying word left), 2 missed.

    In a date, the year and the weekday may stay (Safe Harbor keeps the
    year; a weekday alone fixes nothing). In an age, the unit may stay.
    """
    def generic(w: str) -> bool:
        w2 = w.lower()
        if w2 in GENERIC:
            return True
        if typ == "DATE" and (re.fullmatch(r"(?:19|20)\d\d", w) or _WEEKDAY.fullmatch(w)):
            return True
        return typ == "AGE" and w2 in _UNITS
    words = [(m.start() + s, m.end() + s) for m in _TOKEN.finditer(text[s:e])
             if not generic(m.group())]
    if not words:
        words = [(s, e)]
    left = [all(kept[a:b]) for a, b in words]
    if not any(left):
        return 0
    return 2 if all(left) else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--half", choices=("tuning", "held-out", "all"), default="tuning")
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("-v", action="store_true", help="show misses (tuning half only)")
    ap.add_argument("-n", type=int, default=0)
    ap.add_argument("--cap", type=int, default=15, help="misses shown per type")
    a = ap.parse_args()

    from inkguard.phi_scanner import warmup
    from inkguard.policy import evaluate

    manifest = json.loads((a.corpus / "manifest.json").read_text())
    rows = [r for r in manifest if a.half == "all" or r["half"] == a.half]
    if a.n:
        rows = rows[:a.n]
    show = a.v and a.half == "tuning"

    t0 = time.time()
    warmup()
    per = defaultdict(lambda: [0, 0, 0])            # caught, partial, missed
    req = [0, 0, 0]
    req_by = defaultdict(lambda: [0, 0, 0])
    misses = defaultdict(list)
    extra = 0
    extras = []
    blocked = 0
    held_ids = 0                                     # required identifiers in notes not processed
    with_org = [0, 0, 0]                             # Safe Harbor + ORGANIZATION (employers)
    case_req = defaultdict(lambda: [0, 0])           # per patient: caught, total (for the CI)
    from collections import Counter
    why = Counter()
    by_case = defaultdict(list)
    for r in rows:
        by_case[r["case"]].append(r)
    done = 0
    reads: list[int] = []
    from inkguard import contract
    for case, recs in sorted(by_case.items()):
        recs = sorted(recs, key=lambda x: x["record"])
        loaded = [load(a.corpus / "records" / f"{r['record']}.xml") for r in recs]
        # The tool's own path: the whole patient file, read until it
        # settles (at least three times).
        results, ctx = contract.process_patient([t for t, _ in loaded],
                                                patient_key=f"case-{case}", mode="mask")
        reads.append(ctx.reads)
        for r, (text, tags), res in zip(recs, loaded, results):
            done += 1
            print(f"  {done}/{len(rows)}", end="\r", file=sys.stderr)
            if res.action.value == "blocked":
                blocked += 1          # nothing released: no leak, but no note
                why[(res.error or "no reason")[:90]] += 1
                held_ids += sum(1 for typ, _, _, val in tags if required(typ, val))
                continue
            kept = kept_chars(text, res.clean_text)
            gold = [False] * len(text)
            for typ, s, e, val in tags:
                for i in range(s, e):
                    gold[i] = True
                k = judge(text, s, e, kept, typ)
                per[typ][k] += 1
                if required(typ, val):
                    req[k] += 1
                    req_by[typ][k] += 1
                    case_req[case][0] += (k == 0)
                    case_req[case][1] += 1
                if required(typ, val) or typ == "ORGANIZATION":
                    with_org[k] += 1
                if k and show and required(typ, val) and len(misses[typ]) < a.cap:
                    ctx = text[max(0, s - 40):e + 40].replace("\n", " ")
                    misses[typ].append(f"{'PART' if k == 1 else 'MISS'} {val!r}  …{ctx}…")
            for h in res.hits or []:
                if not any(gold[h.start:h.end]):
                    extra += 1
                    if show and len(extras) < 25:
                        extras.append(f"{h.entity_type} {h.text!r}")
    print(file=sys.stderr)

    def line(name, c, p, m):
        tot = c + p + m
        return f"  {name:18s} {c:>5d}/{tot:<5d} {c / tot:6.1%}   partial {p:<4d} missed {m}"

    print(f"Corpus: {a.half}   records {len(rows)}   cases {len(by_case)}   "
          f"blocked {blocked}   {time.time() - t0:.0f}s   "
          f"reads per patient file: {min(reads)}-{max(reads)}")
    for typ, (c, p, m) in sorted(per.items(), key=lambda kv: -sum(kv[1])):
        print(line(typ, c, p, m))
    allc = [sum(v[i] for v in per.values()) for i in range(3)]
    print(line("ALL MARKED", *allc))
    print(line("SAFE HARBOR*", *req))
    print("  * only what the rule requires: bare years, ages 89 and under, weekdays")
    print("    and seasons, and non-Safe-Harbor types (staff, places of care) left out.")
    print("    Not yet scored as required: ORGANIZATION (it mixes employers, which Safe")
    print("    Harbor lists, with other companies), and years that reveal an age over 89.")
    print(line("WITH ORGANIZATION", *with_org))
    # How sure is the headline? Resample whole patients (identifiers in one
    # patient's file are not independent), 2,000 times, fixed seed.
    import random
    rng = random.Random(20261017)
    cases = list(case_req.values())
    if cases:
        rates = []
        for _ in range(2000):
            pick = [rng.choice(cases) for _ in cases]
            tot = sum(t for _, t in pick)
            rates.append(sum(c for c, _ in pick) / tot if tot else 0)
        rates.sort()
        print(f"  95% interval (patient bootstrap, 2,000 resamples): "
              f"{rates[50]:.1%} to {rates[1949]:.1%}")
    released_ids = sum(req)
    if released_ids + held_ids:
        print(f"  notes processed {len(rows) - blocked} of {len(rows)}; required identifiers "
              f"inside notes not processed: {held_ids} of {released_ids + held_ids} "
              f"({held_ids / (released_ids + held_ids):.1%})")
    print("  Safe Harbor leaks by type (partial + missed):")
    for typ, (c, p_, m) in sorted(req_by.items(), key=lambda kv: -(kv[1][1] + kv[1][2])):
        if p_ + m:
            print(f"    {typ:16s} {p_ + m:>5d} of {c + p_ + m}")
    print(f"  removed but not in the key: {extra}")
    for w, n in why.most_common(6):
        print(f"  blocked x{n}: {w}")
    if show:
        for typ, xs in misses.items():
            print(f"\n{typ}")
            for x in xs:
                print("   ", x)
        print("\nnot in the key:")
        for x in extras:
            print("   ", x)


if __name__ == "__main__":
    main()
