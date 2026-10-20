#!/usr/bin/env python3
"""
Turn a note written with inline markers into a labelled record in the same
file format as the i2b2/n2c2 2014 de-identification corpus, and CHECK it.

Write a note like this (a .txt file):

    Patient: {{PATIENT:Martha Ellison}}, MRN {{MEDICALRECORD:4481203}}.
    Seen {{DATE:4/2/2071}} by Dr. {{DOCTOR:Sam Rivera}} at {{HOSPITAL:Lakeview General}}.

Run:   python3 make_record.py notes_dir out_dir

Each <patient>-<record>.txt becomes <patient>-<record>.xml with the plain text
and every identifier's exact character span. Every span is checked: the
characters at [start, end) must equal the labelled text, or the file is
refused. Offsets are never counted by hand.

Categories (the 2014 scheme: 7 groups, 25 types):
  NAME:      PATIENT DOCTOR USERNAME
  PROFESSION: PROFESSION
  LOCATION:  ROOM DEPARTMENT HOSPITAL ORGANIZATION STREET CITY STATE COUNTRY ZIP
             LOCATION-OTHER
  AGE:       AGE
  DATE:      DATE
  CONTACT:   PHONE FAX EMAIL URL IPADDR
  ID:        SSN MEDICALRECORD HEALTHPLAN ACCOUNT LICENSE VEHICLE DEVICE BIOID IDNUM
"""
import re
import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr

GROUP = {
    "PATIENT": "NAME", "DOCTOR": "NAME", "USERNAME": "NAME",
    "PROFESSION": "PROFESSION",
    "ROOM": "LOCATION", "DEPARTMENT": "LOCATION", "HOSPITAL": "LOCATION",
    "ORGANIZATION": "LOCATION", "STREET": "LOCATION", "CITY": "LOCATION",
    "STATE": "LOCATION", "COUNTRY": "LOCATION", "ZIP": "LOCATION",
    "LOCATION-OTHER": "LOCATION",
    "AGE": "AGE", "DATE": "DATE",
    "PHONE": "CONTACT", "FAX": "CONTACT", "EMAIL": "CONTACT", "URL": "CONTACT",
    "IPADDR": "CONTACT",
    "SSN": "ID", "MEDICALRECORD": "ID", "HEALTHPLAN": "ID", "ACCOUNT": "ID",
    "LICENSE": "ID", "VEHICLE": "ID", "DEVICE": "ID", "BIOID": "ID", "IDNUM": "ID",
}
MARK = re.compile(r"\{\{([A-Z-]+):(.+?)\}\}", re.S)
NAME_OK = re.compile(r"^\d{3,4}-\d{2}$")


def convert(src: str) -> tuple[str, list]:
    text, tags, last = [], [], 0
    out_len = 0
    for m in MARK.finditer(src):
        kind, value = m.group(1), m.group(2)
        if kind not in GROUP:
            raise ValueError(f"unknown category {kind!r} (allowed: {sorted(GROUP)})")
        if not value.strip() or value != value.strip():
            raise ValueError(f"{kind}: value has stray spaces or is empty: {value!r}")
        before = src[last:m.start()]
        if "{{" in before or "}}" in before:
            raise ValueError("a marker is malformed near: " + before[-60:])
        text.append(before)
        out_len += len(before)
        start = out_len
        text.append(value)
        out_len += len(value)
        tags.append((GROUP[kind], kind, start, out_len, value))
        last = m.end()
    tail = src[last:]
    if "{{" in tail or "}}" in tail:
        raise ValueError("a marker is malformed near: " + tail[:60])
    text.append(tail)
    return "".join(text), tags


def write(path_in: Path, out_dir: Path) -> int:
    if not NAME_OK.match(path_in.stem):
        raise ValueError(f"{path_in.name}: name it <patient>-<record>.txt, e.g. 104-03.txt")
    plain, tags = convert(path_in.read_text(encoding="utf-8"))
    for _g, kind, s, e, v in tags:                     # the check that matters
        if plain[s:e] != v:
            raise ValueError(f"{path_in.name}: span {s}-{e} is {plain[s:e]!r}, not {v!r}")
    rows = "\n".join(
        f'<{g} id="P{i}" start="{s}" end="{e}" text={quoteattr(v)} TYPE="{k}" comment="" />'
        for i, (g, k, s, e, v) in enumerate(tags))
    if "]]>" in plain:
        raise ValueError(f"{path_in.name}: the text may not contain ]]>")
    xml = ('<?xml version="1.0" encoding="UTF-8" ?>\n<deIdi2b2>\n'
           f"<TEXT><![CDATA[{plain}]]></TEXT>\n<TAGS>\n{rows}\n</TAGS>\n</deIdi2b2>\n")
    (out_dir / f"{path_in.stem}.xml").write_text(xml, encoding="utf-8")
    return len(tags)


def main(argv):
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, out = Path(argv[1]), Path(argv[2])
    out.mkdir(parents=True, exist_ok=True)
    total, files, bad = 0, 0, 0
    for p in sorted(src.glob("*.txt")):
        try:
            total += write(p, out)
            files += 1
        except ValueError as e:
            bad += 1
            print("REFUSED", e)
    print(f"{files} records written, {total} identifiers labelled, {bad} refused")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
