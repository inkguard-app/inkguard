#!/usr/bin/env python3
"""
Build the MIXED set: generated clinical notes for testing, written by code
rather than by hand, so their wording, formats and names differ from the
messy-notes benchmark and the clinical-preservation set.

Every identifier and every clinical phrase that must survive is placed by
the generator, so its exact position is known. Names come from two sources,
marked separately because the difference matters:

  faker      names from the Faker library (MIT), 30 locales. The tool's name
             registry was built partly from Faker, so these names favour it.
  invented   names assembled from syllables and checked against the tool's
             name registry: names that registry does not hold.

A few names are written in non-Latin scripts; the tool is built for English
and is expected to flag those notes for a person.

    python tools/make_mixed_set.py --out benchmark/mixed --seed 20261018 --n 400

Output: one JSON line per note: {"id", "kind", "text", "ids": [[start, end,
type, source]], "keep": [[start, end]]}. Needs `faker` (benchmark only).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LATIN_LOCALES = ["en_US", "en_GB", "en_IE", "en_AU", "en_CA", "en_NZ", "en_IN", "es_ES", "es_MX",
                 "fr_FR", "de_DE", "it_IT", "pt_BR", "pl_PL", "nl_NL", "sv_SE", "tr_TR", "vi_VN",
                 "fil_PH", "id_ID", "zu_ZA", "yo_NG", "tw_GH", "ha_NG", "ga_IE", "cs_CZ", "fi_FI",
                 "hu_HU", "ro_RO", "da_DK"]
OTHER_SCRIPT_LOCALES = ["ja_JP", "zh_CN", "ko_KR", "ru_RU", "el_GR", "ar_AA", "hi_IN", "he_IL"]

# Clinical phrases that must survive, across specialties (not only neurosurgery).
KEEP = [
    "atrial fibrillation", "ejection fraction of 35%", "troponin 0.04 ng/mL", "metoprolol 25 mg twice daily",
    "apixaban 5 mg", "NYHA class II", "left bundle branch block", "Holter monitor", "TIMI 3 flow",
    "Crohn disease", "Barrett esophagus", "Helicobacter pylori", "Child-Pugh class B", "MELD score of 14",
    "pantoprazole 40 mg", "colonoscopy with polypectomy", "Murphy sign", "McBurney point tenderness",
    "gestational diabetes", "G3P2", "38 weeks gestation", "Apgar scores 8 and 9", "Bishop score of 6",
    "preeclampsia", "magnesium sulfate", "Kawasaki disease", "Tanner stage 3", "weight-for-age 15th percentile",
    "bipolar I disorder", "PHQ-9 of 17", "sertraline 100 mg", "lithium level 0.8 mmol/L", "Cushing syndrome",
    "Graves disease", "HbA1c 8.2%", "metformin 1000 mg", "insulin glargine 20 units", "Addison disease",
    "rotator cuff tear", "Lachman test", "anterior cruciate ligament", "Salter-Harris type II", "Colles fracture",
    "Gleason 3+4=7", "PSA 6.2 ng/mL", "BRCA1 mutation", "HER2-positive", "ECOG performance status 1",
    "stage IIIA non-small cell lung cancer", "pembrolizumab", "Hodgkin lymphoma", "Reed-Sternberg cells",
    "community-acquired pneumonia", "CURB-65 score of 2", "ceftriaxone 1 g", "Gram-negative rods", "Clostridioides difficile",
    "chronic kidney disease stage 3b", "eGFR 38", "creatinine 1.9 mg/dL", "Foley catheter", "Wilms tumor",
    "Parkinson disease", "carbidopa-levodopa", "Glasgow Coma Scale 14", "Bell palsy", "Guillain-Barre syndrome",
    "Hashimoto thyroiditis", "TSH 7.1 mIU/L", "Sjogren syndrome", "Raynaud phenomenon", "Wegener granulomatosis",
    "BP 148/92", "heart rate 104", "SpO2 93% on room air", "temperature 38.4 C", "BMI 31.2",
    "Down syndrome", "Turner syndrome", "Marfan syndrome", "Hirschsprung disease", "Tetralogy of Fallot",
    "Alzheimer dementia", "MoCA 21/30", "donepezil 10 mg", "Lewy body dementia", "Huntington disease",
    "Kerrison rongeur", "Mayfield head holder", "Lenke 1A curve", "Hunt and Hess grade 2", "Cobb angle of 38 degrees",
    "Babinski sign", "Romberg test", "Hoffmann sign", "Spurling test", "Tinel sign at the wrist",
]

FIRST_SYL = ["ka", "lo", "mir", "ven", "tho", "sa", "dri", "bel", "qua", "zen", "ori", "fel", "ny", "rou",
             "esk", "tal", "vi", "mon", "har", "ul", "pes", "gri", "ash", "cor", "lyn", "dax"]
LAST_SYL = ["vor", "ell", "ond", "ristan", "aby", "ques", "orth", "ilde", "mar", "uston", "eck", "owa",
            "ham", "ery", "ingel", "sk", "atti", "rup", "olm", "ewin", "ast", "orrow"]


class Note:
    """A note being written: text pieces with their spans recorded."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.n = 0
        self.ids: list[list] = []
        self.keep: list[list] = []

    def t(self, s: str) -> "Note":
        self.parts.append(s)
        self.n += len(s)
        return self

    def id(self, s: str, typ: str, source: str = "") -> "Note":
        self.ids.append([self.n, self.n + len(s), typ, source])
        return self.t(s)

    def k(self, s: str) -> "Note":
        self.keep.append([self.n, self.n + len(s)])
        return self.t(s)

    def text(self) -> str:
        return "".join(self.parts)


class Gen:
    def __init__(self, seed: int) -> None:
        from faker import Faker
        self.r = random.Random(seed)
        self.fakers = {loc: Faker(loc) for loc in LATIN_LOCALES + OTHER_SCRIPT_LOCALES}
        for i, f in enumerate(self.fakers.values()):
            f.seed_instance(seed + i)
        self.us = self.fakers["en_US"]
        from inkguard.name_registry import NAMES
        self.names = NAMES

    # ---- names -----------------------------------------------------------------
    def invented(self) -> str:
        while True:
            w = "".join(self.r.choice(FIRST_SYL) for _ in range(self.r.choice([1, 2])))
            if len(w) >= 4 and w not in self.names:
                return w.capitalize()

    def invented_last(self) -> str:
        while True:
            w = self.r.choice(FIRST_SYL) + self.r.choice(LAST_SYL)
            if w not in self.names:
                return w.capitalize()

    def person(self) -> tuple[str, str, str]:
        """(first, last, source)"""
        roll = self.r.random()
        if roll < 0.45:
            return self.invented(), self.invented_last(), "invented"
        if roll < 0.92:
            f = self.fakers[self.r.choice(LATIN_LOCALES)]
            return f.first_name(), f.last_name(), "faker"
        f = self.fakers[self.r.choice(OTHER_SCRIPT_LOCALES)]
        return f.first_name(), f.last_name(), "other_script"

    # ---- identifier formats ----------------------------------------------------------
    def date(self) -> str:
        d = self.us.date_between(start_date="-30y", end_date="+5y")
        fmt = self.r.choice(["%m/%d/%Y", "%-m/%-d/%y", "%Y-%m-%d", "%d %b %Y", "%B %-d, %Y", "%d-%b-%Y",
                             "%b %-d", "%-d/%-m/%Y", "%m.%d.%Y"])
        return d.strftime(fmt)

    def phone(self) -> str:
        a, b, c = self.r.randint(201, 989), self.r.randint(200, 999), self.r.randint(0, 9999)
        return self.r.choice([f"({a}) {b}-{c:04d}", f"{a}-{b}-{c:04d}", f"{a}.{b}.{c:04d}",
                              f"+1 {a} {b} {c:04d}", f"{a}{b}{c:04d}", f"{b}-{c:04d}"])

    def mrn(self) -> str:
        return self.r.choice([str(self.r.randint(100000, 99999999)),
                              f"{self.r.choice('ABCDEFGHKMRTZ')}{self.r.randint(100000, 9999999)}",
                              f"{self.r.randint(10, 99)}-{self.r.randint(100000, 999999)}"])

    def address(self) -> str:
        return self.us.street_address()

    def city_zip(self) -> tuple[str, str, str]:
        return self.us.city(), self.us.state_abbr(), self.us.zipcode()

    def email(self, first: str, last: str) -> str:
        user = (first[:1] + last).lower() if first.isascii() and last.isascii() else self.us.user_name()
        return f"{user}{self.r.randint(1, 99)}@{self.r.choice(['example.com', 'example.org', 'mail.example.net'])}"

    # ---- notes --------------------------------------------------------------------
    def note(self, i: int) -> dict:
        kind = self.r.choice(["clinic", "discharge", "phone", "portal", "referral", "radiology", "nursing", "billing"])
        first, last, src = self.person()
        kfirst, klast, ksrc = self.person()
        dr_first, dr_last, dsrc = self.person()
        keep = self.r.sample(KEEP, 4)
        n = Note()
        hdr = self.r.choice(["header", "plain", "caps"])
        if hdr == "header":
            n.t("Patient: ").id(f"{last}, {first}", "NAME", src).t("   MRN: ").id(self.mrn(), "MRN").t("   DOB: ").id(self.date(), "DATE").t("\n")
        elif hdr == "caps":
            n.id(f"{last.upper()}, {first.upper()}", "NAME", src).t("  ").id(self.mrn(), "MRN").t("\n")
        if kind == "clinic":
            n.t("Seen in clinic on ").id(self.date(), "DATE").t(". ").id(f"{first} {last}", "NAME", src)
            n.t(" returns for follow-up of ").k(keep[0]).t(". Exam notable for ").k(keep[1]).t(".\n")
            n.t("Accompanied by daughter ").id(kfirst, "NAME", ksrc).t(". Plan: continue ").k(keep[2])
            n.t("; repeat labs in 3 months. Reviewed ").k(keep[3]).t(".\n")
        elif kind == "discharge":
            n.t("DISCHARGE SUMMARY\nAdmitted: ").id(self.date(), "DATE").t("  Discharged: ").id(self.date(), "DATE")
            n.t("\nHospital course: Mr. ").id(last, "NAME", src).t(" was treated for ").k(keep[0])
            n.t(" and ").k(keep[1]).t(". On discharge, ").k(keep[2]).t(".\nFollow up with Dr. ")
            n.t("").id(f"{dr_first} {dr_last}", "NAME", dsrc).t(" at ").id(self.phone(), "PHONE").t(". Note: ").k(keep[3]).t(".\n")
        elif kind == "phone":
            n.t("Phone note: ").id(first, "NAME", src).t(" called at ").id(self.phone(), "PHONE")
            n.t(" asking about ").k(keep[0]).t(". Wife ").id(f"{kfirst} {klast}", "NAME", ksrc)
            n.t(" also on the line. Advised to continue ").k(keep[1]).t(" and call back if ").k(keep[2]).t(" worsens.\n")
            n.t("Callback number ").id(self.phone(), "PHONE").t(". History: ").k(keep[3]).t(".\n")
        elif kind == "portal":
            n.t("Hi Dr. ").id(dr_last, "NAME", dsrc).t(", this is ").id(f"{first} {last}", "NAME", src)
            n.t(". Since my visit on ").id(self.date(), "DATE").t(" the ").k(keep[0]).t(" is better but I still have ")
            n.k(keep[1]).t(". My new email is ").id(self.email(first, last), "EMAIL").t(".\n")
            n.t("Reply: thanks, keep taking ").k(keep[2]).t("; we will check ").k(keep[3]).t(".\n")
        elif kind == "referral":
            city, st, z = self.city_zip()
            n.t("Referral for ").id(f"{first} {last}", "NAME", src).t(", ").id(self.address(), "ADDRESS").t(", ")
            n.id(city, "CITY").t(", ").t(st).t(" ").id(z, "ZIP").t(".\nReason: ").k(keep[0]).t(" with ").k(keep[1])
            n.t(". Current treatment: ").k(keep[2]).t(". Please also assess ").k(keep[3]).t(".\nReferring: Dr. ")
            n.id(f"{dr_first} {dr_last}", "NAME", dsrc).t(", fax ").id(self.phone(), "PHONE").t(".\n")
        elif kind == "radiology":
            n.t("EXAM DATE: ").id(self.date(), "DATE").t("\nACCESSION: ").id(self.mrn(), "ID")
            n.t("\nFINDINGS: ").k(keep[0]).t(". ").k(keep[1]).t(".\nIMPRESSION: ").k(keep[2]).t("; compare with ")
            n.id(self.date(), "DATE").t(". Discussed with Dr. ").id(dr_last, "NAME", dsrc).t(". ").k(keep[3]).t(".\n")
        elif kind == "nursing":
            n.t("0800: ").id(first, "NAME", src).t(" resting. ").k(keep[0]).t(". Son ").id(kfirst, "NAME", ksrc)
            n.t(" visited. ").k(keep[1]).t(".\n1400: ").k(keep[2]).t(". Pt asks to update ").id(f"{kfirst} {klast}", "NAME", ksrc)
            n.t(" at ").id(self.phone(), "PHONE").t(". ").k(keep[3]).t(".\n")
        else:
            n.t("Claim for ").id(f"{first} {last}", "NAME", src).t(", member ID ").id(
                f"{self.r.choice('XYZW')}{self.r.randint(10**8, 10**9 - 1)}", "HEALTHPLAN")
            n.t(", SSN ").id(f"{self.r.randint(100, 665)}-{self.r.randint(10, 99)}-{self.r.randint(1000, 9999)}", "SSN")
            n.t(". Services: ").k(keep[0]).t(", ").k(keep[1]).t(". Diagnosis: ").k(keep[2]).t(". Note: ").k(keep[3]).t(".\n")
        if self.r.random() < 0.15:
            n.t("Age ").id(str(self.r.randint(90, 104)), "AGE").t(".\n")
        if self.r.random() < 0.10:
            n.t("Remote session from ").id(self.us.ipv4_public(), "IP").t(".\n")
        if self.r.random() < 0.10:
            n.t("Shared photos at ").id(f"https://{self.us.domain_word()}.example.com/u/{self.r.randint(1000, 99999)}", "URL").t(".\n")
        return {"id": f"m{i:04d}", "kind": kind, "text": n.text(), "ids": n.ids, "keep": n.keep}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="benchmark/mixed")
    ap.add_argument("--seed", type=int, default=20261018)
    ap.add_argument("--n", type=int, default=400)
    a = ap.parse_args()
    g = Gen(a.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "notes.jsonl", "w", encoding="utf-8") as fh:
        for i in range(1, a.n + 1):
            note = g.note(i)
            text = note["text"]
            for s, e, *_ in note["ids"] + note["keep"]:
                assert 0 <= s < e <= len(text)
            fh.write(json.dumps(note, ensure_ascii=False) + "\n")
    print(f"{a.n} notes written to {out / 'notes.jsonl'}")


if __name__ == "__main__":
    main()
