#!/usr/bin/env python3
"""Make a folder of invented notes, as big as you like, to stress-test Inkguard.

    python tools/make_stress_set.py 1000 stress-1000
    python -m inkguard.cli stress-1000 -o stress-1000-clean

Every note comes from the synthetic benchmark (benchmark/records), so no real
person is in it. Notes are spread over patients (one folder each). In each
copy every digit is changed and a new invented name is added, so copies
differ; the names already in the note stay.

With --nasty, a few awkward files are added on purpose, each of which the tool
must handle without crashing and without releasing anything unchecked: an
empty file, a very long single-line file, a picture pretending to be a note,
a note in another language, invisible characters inside identifiers, a Word
file, a spreadsheet and a CSV.
"""
from __future__ import annotations

import argparse
import html
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDS = ROOT / "benchmark" / "records"

FIRST = ["Ava", "Mateo", "Noor", "Kwame", "Lena", "Ravi", "Sofia", "Tomas", "Yuki", "Amara",
         "Olu", "Ines", "Farid", "Greta", "Hugo", "Leila", "Marek", "Nia", "Priya", "Sven"]
LAST = ["Okafor", "Lindqvist", "Haddad", "Moreau", "Tanaka", "Castillo", "Novak", "Mensah",
        "Fischer", "Ibrahim", "Kowalski", "Nakamura", "Oyelaran", "Petrov", "Quinlan", "Rasku"]


def _notes() -> list[str]:
    out = []
    for p in sorted(RECORDS.glob("*.xml")):
        x = p.read_text(encoding="utf-8")
        m = re.search(r"<TEXT><!\[CDATA\[(.*?)\]\]></TEXT>", x, re.S) or \
            re.search(r"<TEXT>(.*?)</TEXT>", x, re.S)
        if m:
            out.append(html.unescape(m.group(1)).strip())
    if not out:
        sys.exit(f"No benchmark notes found in {RECORDS}.")
    return out


def _vary(text: str, rng: random.Random) -> str:
    """Change numbers and add a fresh invented name, so copies differ."""
    text = re.sub(r"\d", lambda m: str(rng.randint(0, 9)), text)
    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    return f"Patient: {name}\n{text}\nSeen with {name.split()[0]} today."


def _nasty(folder: Path, rng: random.Random) -> None:
    d = folder / "patient-awkward"
    d.mkdir(parents=True, exist_ok=True)
    (d / "empty.txt").write_text("", encoding="utf-8")
    (d / "one-long-line.txt").write_text(
        ("Patient Ava Okafor MRN 4481203 seen for follow up of hypertension. " * 40000),
        encoding="utf-8")
    (d / "picture-named-note.txt").write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(rng.randrange(256) for _ in range(4000)))
    (d / "other-language.txt").write_text(
        "Пациент Иван Петров, 54 года, поступил с болью в груди. Телефон +7 000 000 01 23. "
        * 30, encoding="utf-8")
    zw = "​"
    (d / "hidden-characters.txt").write_text(
        f"Patient Nia Men{zw}sah, SSN 123-45-{zw}6789, phone 555-01{zw}47. Asthma, stable.\n",
        encoding="utf-8")
    try:
        from docx import Document
        doc = Document()
        doc.add_paragraph("Clinic letter for Hugo Fischer, DOB 03/14/1961. Diabetes review.")
        doc.core_properties.author = "Dr. Leila Haddad"
        doc.save(d / "letter.docx")
    except Exception:
        pass
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(["Name", "MRN", "Note"])
        for _ in range(200):
            ws.append([f"{rng.choice(FIRST)} {rng.choice(LAST)}", str(rng.randint(10**6, 10**7)),
                       "Seen in clinic, BP 130/80, continue lisinopril."])
        hidden = wb.create_sheet("old list")
        hidden.append(["Greta Moreau", "555-0199"])
        hidden.sheet_state = "hidden"
        wb.save(d / "clinic-list.xlsx")
    except Exception:
        pass
    rows = ["patient,note"] + [f"{rng.choice(FIRST)} {rng.choice(LAST)},\"Follow up on {rng.randint(1,12)}/"
                               f"{rng.randint(1,28)}, call 555-01{rng.randint(10,99)}.\"" for _ in range(300)]
    (d / "notes.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("notes", type=int, help="how many notes to make, for example 100, 1000 or 10000")
    ap.add_argument("folder", help="where to put them (a new folder)")
    ap.add_argument("--per-patient", type=int, default=5, help="notes per patient (default 5)")
    ap.add_argument("--nasty", action="store_true", help="also add awkward files on purpose")
    ap.add_argument("--seed", type=int, default=7, help="change this for a different set")
    a = ap.parse_args()
    out = Path(a.folder)
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} already has files in it; choose a new folder name.")
    rng = random.Random(a.seed)
    notes = _notes()
    width = len(str(max(1, a.notes // max(1, a.per_patient))))
    for i in range(a.notes):
        patient = out / f"patient-{i // a.per_patient + 1:0{width}d}"
        patient.mkdir(parents=True, exist_ok=True)
        (patient / f"note-{i % a.per_patient + 1:02d}.txt").write_text(
            _vary(rng.choice(notes), rng) + "\n", encoding="utf-8")
    if a.nasty:
        _nasty(out, rng)
    print(f"made {a.notes:,} notes in {out}" + (" plus awkward files" if a.nasty else ""))
    print(f"now run:  python -m inkguard.cli {out} -o {out}-clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
