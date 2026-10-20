#!/usr/bin/env python3
"""
Build inkguard/name_registry.py — the NAME GUARD.

Used for one purpose: keeping the clinical vocabulary from ever claiming a
word that is somebody's name. Absence from this list says nothing about
whether a word is a name; it is never used to DETECT names.

Sources (all build-time only; the output is plain data):
  - Faker name providers, every locale Faker ships (MIT), including the
    romanized Japanese and Chinese name lists and an ASCII transliteration
    of Cyrillic and Greek names (anyascii, ISC).
  - 1990 US Census surnames (rank <= 30,000) and first names (public domain).
  - 2000 US Census surnames borne by at least 1,000 people (public domain;
    census2000.csv, via fivethirtyeight/data), when that file is present.
  - A curated seed of common transliterated names from naming traditions
    that Faker only ships in native script (Arabic, South Asian, Korean,
    Vietnamese, West African). Written for this project.

Usage: python tools/build_name_registry.py --census DIR
"""
from __future__ import annotations

import argparse
import os
import re

from faker import Faker
from faker.config import AVAILABLE_LOCALES

CURATED = """
mohammed muhammad mohamed ahmed ahmad mahmoud mustafa mostafa hassan hasan hussein husain
ali omar umar osman usman othman khalid khaled tariq bilal hamza idris ibrahim yusuf yousef
youssef ismail ismael abdullah abdallah abdulrahman abdelrahman karim kareem rashid rasheed
saeed said sayed salem saleh salah faisal fahad fahd nasser nasir mansour majid walid waleed
ziad zaid zayd anas amr amir ameer jamal kamal nabil nadim rami sami samir tamer yasser yasir
haddad khoury khouri nassar saad sabbagh shalhoub sharbatly alghamdi alharbi alqahtani
alotaibi alzahrani alshehri aldosari almutairi alanazi fatima fatimah aisha aysha ayesha
khadija maryam mariam zainab zaynab noor nour huda hoda layla leila salma samira yasmin
yasmeen rania reem dina lina hana amal iman eman asma nadia sara farah ghada
singh kaur kumar sharma verma gupta patel shah mehta desai joshi iyer iyengar nair menon
pillai reddy rao naidu chowdhury choudhury chaudhary banerjee chatterjee mukherjee
bhattacharya ghosh bose dutta sen das roy saha mishra pandey tiwari dubey trivedi
chaturvedi srivastava agarwal aggarwal bansal goel jain kapoor khanna malhotra chopra
arora sethi bhatia gill sandhu dhillon grewal sidhu randhawa krishnan krishnamurthy
venkatesh venkataraman subramanian subramaniam raman ramesh suresh mahesh ganesh rajesh
rakesh mukesh dinesh naresh sanjay vijay ajay amit anil sunil rahul rohit vikram arjun
deepak prakash anand ashok arun kiran priya anjali sunita lakshmi deepa divya pooja neha
kavita anita geeta meena rekha shanti usha asha radha sita
kim lee park choi jung jeong kang cho yoon yun jang lim han oh seo shin kwon hwang ahn song
jeon hong ko moon yang son bae baek heo nam sim noh ha kwak seong cha joo woo gu ryu
nguyen tran le pham hoang huynh phan vu vo dang bui do ho ngo duong ly
okafor okonkwo okeke nwosu nwachukwu eze obi chukwu adeyemi adebayo adewale babatunde
oluwaseun olumide oyelaran afolabi balogun ogunleye mensah owusu boateng asante agyeman
osei kwame kofi kwabena ama akua abena yaw mbeki ndlovu dlamini nkosi zulu
""".split()


def ascii_name(s: str) -> str | None:
    s = s.strip().lower()
    if s.isascii():
        out = s
    else:
        try:
            from anyascii import anyascii
        except ImportError:
            return None
        out = anyascii(s).lower()
    return out if re.fullmatch(r"[a-z][a-z'\-]+", out) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", required=True)
    ap.add_argument("--out", default="inkguard/name_registry.py")
    args = ap.parse_args()

    names: set[str] = set()
    used = []
    for loc in AVAILABLE_LOCALES:
        try:
            f = Faker(loc)
        except Exception:
            continue
        used.append(loc)
        cased_script = loc.split("_")[0] in {"ru", "uk", "bg", "el", "be", "sr", "mk", "kk"}
        for prov in f.providers:
            for attr in dir(prov):
                if not (attr.startswith(("first_names", "last_names",
                                         "first_romanized_names", "last_romanized_names"))):
                    continue
                v = getattr(prov, attr, None)
                if not isinstance(v, (dict, list, tuple, set)):
                    continue
                for x in (v.keys() if isinstance(v, dict) else v):
                    x = str(x)
                    if not x.isascii() and not cased_script and "romanized" not in attr:
                        continue          # native CJK/Arabic/etc: not a Latin token
                    a = ascii_name(x)
                    if a and len(a) >= 2:
                        names.add(a)
    # Census surnames up to rank 30,000. The tail below that is real but
    # vanishingly rare (MEDIAN is rank 61,934, TONSIL 49,003, THRILL
    # 67,160), and there the medical sense of the word dominates by
    # orders of magnitude. The FULL census list is still applied when the
    # open vocabulary is built (tools/build_open_lexicon.py), where the
    # stricter guard costs nothing.
    for fn in ("dist.all.last", "dist.male.first", "dist.female.first"):
        p = os.path.join(args.census, fn)
        for line in open(p):
            parts = line.split()
            if not parts:
                continue
            if fn == "dist.all.last" and int(parts[3]) > 30000:
                continue
            names.add(parts[0].lower())
    # 2000 US Census surnames (public domain; via fivethirtyeight/data),
    # those borne by at least 1,000 people. Without them the open
    # vocabulary could claim real surnames the 1990 list lacks.
    p2000 = os.path.join(args.census, "census2000.csv")
    if os.path.exists(p2000):
        import csv
        for r in csv.DictReader(open(p2000)):
            if int(r["count"]) >= 1000:
                names.add(r["name"].lower())
    names |= set(CURATED)

    with open(args.out, "w") as out:
        out.write('"""\nNAME REGISTRY — GENERATED by tools/build_name_registry.py.\n\n'
                  f'{len(names)} Latin-script first and last names: Faker providers for\n'
                  f'{len(used)} locales incl. romanized Japanese/Chinese and transliterated\n'
                  'Cyrillic/Greek (MIT); 1990 US Census surnames and first names and 2000\n'
                  'US Census surnames with 1,000+ bearers (public domain); and a curated\n'
                  'seed of common transliterated names.\n\n'
                  'Used for ONE purpose: to keep the clinical vocabulary from ever\n'
                  'claiming a word that is somebody\'s name. It is NOT used to detect\n'
                  'names — absence from this list says nothing about a word.\n"""\n\n'
                  'NAMES: frozenset[str] = frozenset({\n')
        for n in sorted(names):
            out.write(f'    "{n}",\n')
        out.write("})\n")
    print(f"{len(names)} names from {len(used)} locales + census + curated")


def _pack() -> None:
    """Store the list as data (inkguard/data/name_registry.json.gz) with a short loader."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    from inkguard._data import pack_module
    pack_module("name_registry", "tools/build_name_registry.py")


if __name__ == "__main__":
    main()
    _pack()
