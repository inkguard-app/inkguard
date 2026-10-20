#!/usr/bin/env python3
"""
Score Inkguard on test sets from OTHER places than this project's own
benchmark, and on a new generated set, separately and pooled.

  mixed      generated notes (tools/make_mixed_set.py): every identifier and
             every clinical phrase placed by code; names from Faker and
             invented names the tool has never seen.
  presidio   Microsoft presidio-research synthetic PII sentences
             (synth_dataset_v2.json, MIT): general-domain text, labelled.
  aci        ACI-Bench clinical notes (Yim et al., Sci Data 2023, CC BY 4.0):
             realistic visit notes; the patient's first and last name are
             known from the metadata, so every occurrence can be scored.
  tw         Taiwan synthetic clinical notes with PHI labels (CC BY 4.0):
             Traditional Chinese mixed with English. The tool is built for
             English; this set shows what happens outside that.

The data is not part of this repository. Download it (the commands are in
benchmark/EXTERNAL.md) into one folder and pass that folder:

    python tools/eval_external.py DATA_DIR [--sets mixed,presidio,aci,tw]

Scoring is the same as for the main benchmark: an identifier is removed
only if none of its identifying words reaches the reader (the year of a
date and the first three digits of a ZIP code may stay, as Safe Harbor
allows); a clinical phrase is kept only if all of it survives. Over-removal
is the share of words outside every labelled identifier that the tool
removed. Notes the tool flags for a person are scored on their drafts; a
note it stops entirely releases nothing.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from eval_corpus import judge, kept_chars  # noqa: E402

_WORD = re.compile(r"[A-Za-z0-9À-ɏͰ-ϿЀ-ӿ֐-ۿऀ-ॿ぀-ヿ㐀-鿿가-힯]+")

# ----------------------------------------------------------------------------- loaders
# Each loader yields notes: {"set", "id", "text", "ids": [(s, e, type, required, source)],
# "keep": [(s, e)], "score_over_removal": bool}


def load_mixed(d: Path):
    for line in open(d / "mixed" / "notes.jsonl", encoding="utf-8"):
        r = json.loads(line)
        ids = []
        for s, e, typ, src in r["ids"]:
            req = True
            if typ == "AGE":
                req = int(re.sub(r"\D", "", r["text"][s:e]) or 0) > 89
            ids.append((s, e, typ, req, src or ""))
        yield {"set": "mixed", "id": r["id"], "text": r["text"], "ids": ids,
               "keep": [tuple(k) for k in r["keep"]], "score_over_removal": True}


_PRESIDIO_REQUIRED = {"PERSON": "NAME", "PHONE_NUMBER": "PHONE", "EMAIL_ADDRESS": "EMAIL",
                      "CREDIT_CARD": "ACCOUNT", "IBAN_CODE": "ACCOUNT", "US_SSN": "SSN",
                      "IP_ADDRESS": "IP", "US_DRIVER_LICENSE": "LICENSE", "DOMAIN_NAME": "URL",
                      "ZIP_CODE": "ZIP", "STREET_ADDRESS": "ADDRESS", "DATE_TIME": "DATE", "AGE": "AGE"}


def load_presidio(d: Path):
    data = json.load(open(d / "presidio" / "synth_dataset_v2.json", encoding="utf-8"))
    for i, r in enumerate(data):
        text = r["full_text"]
        ids = []
        for sp in r["spans"]:
            s, e, t = sp["start_position"], sp["end_position"], sp["entity_type"]
            typ = _PRESIDIO_REQUIRED.get(t, t)
            req = t in _PRESIDIO_REQUIRED
            if t == "STREET_ADDRESS":
                # Only the street line is required; a state or country after
                # it may stay under Safe Harbor.
                m = re.match(r"[^\n,]*", text[s:e])
                e = s + len(m.group().rstrip())
            if t == "AGE":
                req = int(re.sub(r"\D", "", text[s:e]) or 0) > 89
            if t == "DATE_TIME":
                # Required only when it carries a day or a month.
                req = bool(re.search(r"\d{1,2}[/-]\d{1,2}|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec",
                                     text[s:e], re.I))
            ids.append((s, e, typ, req, ""))
        yield {"set": "presidio", "id": f"p{i:04d}", "text": text, "ids": ids, "keep": [],
               "score_over_removal": True}


def load_aci(d: Path):
    csv.field_size_limit(10**9)
    for split in ("train", "valid"):
        notes = list(csv.DictReader(open(d / "aci" / f"{split}.csv", encoding="utf-8")))
        meta = {m["encounter_id"]: m for m in csv.DictReader(open(d / "aci" / f"{split}_metadata.csv", encoding="utf-8"))}
        for r in notes:
            m = meta.get(r["encounter_id"], {})
            text = r["note"]
            ids = []
            for part in ("patient_firstname", "patient_familyname"):
                name = (m.get(part) or "").strip()
                if len(name) < 2:
                    continue
                for hit in re.finditer(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text):
                    ids.append((hit.start(), hit.end(), "NAME", True, ""))
            yield {"set": "aci", "id": f"{split}-{r['encounter_id']}", "text": text, "ids": ids,
                   "keep": [], "score_over_removal": False}


_TW_REQUIRED = {"NAME": "NAME", "DOCTOR": "NAME_CLINICIAN", "DATE": "DATE", "MRN": "MRN",
                "IDNO": "ID", "PHONE": "PHONE", "ADDRESS": "ADDRESS", "HOSPITAL": "FACILITY"}


def load_tw(d: Path):
    for line in open(d / "tw" / "test.jsonl", encoding="utf-8"):
        r = json.loads(line)
        ids = []
        for p in r["phi"]:
            t = p.get("type")
            typ = _TW_REQUIRED.get(t, t)
            ids.append((p["start"], p["end"], typ, t in ("NAME", "DATE", "MRN", "IDNO", "PHONE", "ADDRESS"), ""))
        yield {"set": "tw", "id": r["id"], "text": r["text"], "ids": ids, "keep": [],
               "score_over_removal": False}


LOADERS = {"mixed": load_mixed, "presidio": load_presidio, "aci": load_aci, "tw": load_tw}

# ----------------------------------------------------------------------------- scoring


def caught(text: str, s: int, e: int, typ: str, kept: list[bool]) -> int:
    if typ == "AGE" and re.sub(r"\D", "", text[s:e]) == "90":
        # "90" may stay as part of "90 or older", which is the rule's own
        # generalisation; only 91 and over show what was removed.
        return 0
    if typ == "ZIP":
        digits = [i for i in range(s, e) if text[i].isdigit()]
        tail = digits[3:]
        if not tail:
            return 0
        return 0 if not any(kept[i] for i in tail) else (2 if all(kept[i] for i in tail) else 1)
    if not _WORD.search(text[s:e]):
        return 0
    if not re.search(r"[A-Za-z0-9]", text[s:e]):
        # Non-Latin script: judge by characters.
        left = [kept[i] for i in range(s, e) if not text[i].isspace()]
        return 0 if not any(left) else (2 if all(left) else 1)
    return judge(text, s, e, kept, typ)


def run(notes, evaluate):
    rows, keep_rows, over, status = [], [], [], Counter()
    for n in notes:
        text = n["text"]
        r = evaluate(text)
        st = r.status
        status[(n["set"], st)] += 1
        if st == "stop":
            clean = ""
        else:
            clean = r.clean_text
        kept = kept_chars(text, clean) if clean else [False] * len(text)
        for s, e, typ, req, src in n["ids"]:
            rows.append((n["set"], n["id"], typ, req, src, caught(text, s, e, typ, kept), st))
        for s, e in n["keep"]:
            keep_rows.append((n["set"], n["id"], all(kept[s:e])))
        if n["score_over_removal"] and st != "stop":
            inside = [False] * len(text)
            for s, e, *_ in n["ids"]:
                for i in range(s, e):
                    inside[i] = True
            for m in _WORD.finditer(text):
                if not any(inside[m.start():m.end()]):
                    over.append((n["set"], n["id"], not all(kept[m.start():m.end()])))
    return rows, keep_rows, over, status


def interval(groups: dict, stat, reps: int = 2000, seed: int = 7):
    keys = list(groups)
    rnd = random.Random(seed)
    vals = []
    for _ in range(reps):
        pick = [groups[rnd.choice(keys)] for _ in keys]
        vals.append(stat([x for g in pick for x in g]))
    vals.sort()
    return vals[int(0.025 * reps)], vals[int(0.975 * reps) - 1]


def rate(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def report(rows, keep_rows, over, status, sets):
    print("Inkguard on external and generated sets, by the software alone, before human review\n")
    for name in sets + ["ALL"]:
        rs = [r for r in rows if (name == "ALL" or r[0] == name) and r[3]]
        if not rs:
            continue
        removed = [r[5] == 0 for r in rs]
        by_note = defaultdict(list)
        for r in rs:
            by_note[(r[0], r[1])].append(r[5] == 0)
        lo, hi = interval(by_note, rate)
        notes = sum(v for (s, st), v in status.items() if name in (s, "ALL") or name == "ALL")
        held = sum(v for (s, st), v in status.items() if (name == "ALL" or s == name) and st != "clear")
        released_leaks = sum(1 for r in rs if r[6] == "clear" and r[5] != 0)
        print(f"== {name}: {notes} notes; flagged or stopped for a person: {held}")
        print(f"  required identifiers removed  {sum(removed)}/{len(removed)}  {rate(removed):.1%}  "
              f"(95% CI {lo:.1%}-{hi:.1%})")
        print(f"  identifiers left readable in notes with no flags: {released_leaks}")
        by_type = defaultdict(list)
        for r in rs:
            by_type[r[2]].append(r[5] == 0)
        for t, xs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
            print(f"    {t:15} {sum(xs):5}/{len(xs):<5} {rate(xs):6.1%}")
        by_src = defaultdict(list)
        for r in rs:
            if r[4]:
                by_src[r[4]].append(r[5] == 0)
        for src, xs in sorted(by_src.items()):
            print(f"    names: {src:12} {sum(xs):5}/{len(xs):<5} {rate(xs):6.1%}")
        ks = [k[2] for k in keep_rows if name == "ALL" or k[0] == name]
        if ks:
            print(f"  clinical phrases kept whole   {sum(ks)}/{len(ks)}  {rate(ks):.1%}")
        ov = [o[2] for o in over if name == "ALL" or o[0] == name]
        if ov:
            print(f"  over-removal: words outside identifiers removed  {sum(ov)}/{len(ov)}  {rate(ov):.1%}")
        info = [r for r in rows if (name == "ALL" or r[0] == name) and not r[3]]
        if info:
            by_t = defaultdict(list)
            for r in info:
                by_t[r[2]].append(r[5] == 0)
            print("  not required by Safe Harbor (for information): "
                  + ", ".join(f"{t} {sum(x)}/{len(x)}" for t, x in sorted(by_t.items())))
        print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data", type=Path)
    ap.add_argument("--sets", default="mixed,presidio,aci,tw")
    a = ap.parse_args()
    from inkguard import contract
    sets = [s for s in a.sets.split(",") if s]

    def evaluate(text):
        return contract.deidentify(text, mode="mask")

    notes = [n for s in sets for n in LOADERS[s](a.data)]
    rows, keep_rows, over, status = run(notes, evaluate)
    report(rows, keep_rows, over, status, sets)


if __name__ == "__main__":
    main()
