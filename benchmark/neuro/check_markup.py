#!/usr/bin/env python3
"""Validate the neuro benchmark answer-key markup and print counts.

Usage:  python3 check_markup.py [benchmark_dir]

Checks every pNNN.txt under tuning/ and held-out/:
  * file names and patient number ranges (tuning p001-p050, held-out p051-p070)
  * 2 to 4 notes per file, separated by a line containing exactly =====NOTE=====
  * identifier markup {{TYPE|text}} with a valid TYPE and non-empty text
  * clinical markup [[text]] with non-empty text
  * balanced markup, no nesting, no overlap, no stray delimiters
  * 3 to 10 identifiers per note (error), 15 to 40 clinical terms per note (warning)
  * every 4-digit year in the text lies between 2060 and 2080 (warning)

Error messages for held-out files never include note text, only the file,
note number and kind of problem, so the held-out content is not leaked.
Exit status is 0 when there are no errors, 1 otherwise. Standard library only.
"""
import os
import re
import sys
from collections import Counter

TYPES = ("NAME", "DATE", "AGE", "MRN", "PHONE", "EMAIL", "ADDRESS", "ZIP",
         "FACILITY", "ORG", "ID", "URL")
SEPARATOR = "=====NOTE====="
SPLITS = {"tuning": (1, 50), "held-out": (51, 70)}
NOTES_MIN, NOTES_MAX = 2, 4
ID_MIN, ID_MAX = 3, 10
CLIN_MIN, CLIN_MAX = 15, 40
YEAR_MIN, YEAR_MAX = 2060, 2080
DELIMS = ("{{", "}}", "[[", "]]")


def parse_note(text):
    """Return (identifiers, clinical_terms, errors) for one note."""
    ids, clin, errors = [], [], []
    i, n = 0, len(text)
    while i < n:
        nxt = [(text.find(d, i), d) for d in DELIMS]
        nxt = [(p, d) for p, d in nxt if p != -1]
        if not nxt:
            break
        pos, d = min(nxt)
        if d in ("}}", "]]"):
            errors.append("stray closing %s at offset %d" % (d, pos))
            i = pos + 2
            continue
        close = "}}" if d == "{{" else "]]"
        end = text.find(close, pos + 2)
        if end == -1:
            errors.append("unclosed %s at offset %d" % (d, pos))
            break
        inner = text[pos + 2:end]
        for other in DELIMS:
            if other in inner:
                errors.append("nested or overlapping markup (%s inside %s...%s) at offset %d"
                              % (other, d, close, pos))
        if "\n" in inner:
            errors.append("markup spans a line break at offset %d" % pos)
        if d == "{{":
            if "|" not in inner:
                errors.append("identifier without TYPE| at offset %d" % pos)
            else:
                typ, val = inner.split("|", 1)
                if typ not in TYPES:
                    errors.append("invalid TYPE %r at offset %d" % (typ, pos))
                elif not val.strip():
                    errors.append("empty identifier at offset %d" % pos)
                else:
                    ids.append((typ, val))
        else:
            if not inner.strip():
                errors.append("empty clinical term at offset %d" % pos)
            else:
                clin.append(inner)
        i = end + 2
    return ids, clin, errors


def check(base):
    errors, warnings = [], []
    stats = {}
    for split, (lo, hi) in SPLITS.items():
        folder = os.path.join(base, split)
        st = {"patients": 0, "notes": 0, "ids": Counter(), "clin": 0,
              "clin_unique": set(), "notes_per_patient": Counter()}
        stats[split] = st
        if not os.path.isdir(folder):
            errors.append("%s: folder missing" % split)
            continue
        names = sorted(os.listdir(folder))
        expected = ["p%03d.txt" % k for k in range(lo, hi + 1)]
        for e in expected:
            if e not in names:
                errors.append("%s: missing %s" % (split, e))
        for name in names:
            if name not in expected:
                errors.append("%s: unexpected file %s" % (split, name))
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as fh:
                raw = fh.read()
            lines = raw.split("\n")
            for ln in lines:
                if ln.strip() == SEPARATOR and ln != SEPARATOR:
                    errors.append("%s/%s: separator line has extra whitespace" % (split, name))
                elif SEPARATOR in ln and ln.strip() != SEPARATOR:
                    errors.append("%s/%s: separator text inside another line" % (split, name))
            notes, cur = [], []
            for ln in lines:
                if ln == SEPARATOR:
                    notes.append("\n".join(cur))
                    cur = []
                else:
                    cur.append(ln)
            notes.append("\n".join(cur))
            st["patients"] += 1
            if not NOTES_MIN <= len(notes) <= NOTES_MAX:
                errors.append("%s/%s: %d notes (expected %d-%d)"
                              % (split, name, len(notes), NOTES_MIN, NOTES_MAX))
            st["notes_per_patient"][len(notes)] += 1
            for k, note in enumerate(notes, 1):
                where = "%s/%s note %d" % (split, name, k)
                if not note.strip():
                    errors.append("%s: empty note" % where)
                    continue
                st["notes"] += 1
                ids, clin, errs = parse_note(note)
                for e in errs:
                    if split == "tuning":
                        m = re.search(r"offset (\d+)", e)
                        if m:
                            o = int(m.group(1))
                            e += " near %r" % note[max(0, o - 25):o + 40]
                    errors.append("%s: %s" % (where, e))
                if len(ids) < ID_MIN:
                    errors.append("%s: %d identifiers (expected %d-%d)"
                                  % (where, len(ids), ID_MIN, ID_MAX))
                elif len(ids) > ID_MAX:
                    warnings.append("%s: %d identifiers (aim %d-%d)"
                                  % (where, len(ids), ID_MIN, ID_MAX))
                if not CLIN_MIN <= len(clin) <= CLIN_MAX:
                    warnings.append("%s: %d clinical terms (aim %d-%d)"
                                    % (where, len(clin), CLIN_MIN, CLIN_MAX))
                for y in re.findall(r"(?<!\d)((?:19|20)\d\d)(?!\d)", note):
                    if not YEAR_MIN <= int(y) <= YEAR_MAX:
                        warnings.append("%s: year-like number %s outside %d-%d"
                                        % (where, y, YEAR_MIN, YEAR_MAX))
                for typ, _ in ids:
                    st["ids"][typ] += 1
                st["clin"] += len(clin)
                st["clin_unique"].update(c.lower() for c in clin)
    return stats, errors, warnings


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    stats, errors, warnings = check(base)
    for split, st in stats.items():
        total_ids = sum(st["ids"].values())
        print("== %s ==" % split)
        print("  patients: %d" % st["patients"])
        print("  notes: %d  (patients with 2/3/4 notes: %d/%d/%d)"
              % (st["notes"], st["notes_per_patient"][2], st["notes_per_patient"][3],
                 st["notes_per_patient"][4]))
        print("  identifiers: %d" % total_ids)
        for t in TYPES:
            print("    %-9s %d" % (t, st["ids"][t]))
        print("  clinical terms: %d (unique, case-folded: %d)"
              % (st["clin"], len(st["clin_unique"])))
    if warnings:
        print("\nWARNINGS (%d):" % len(warnings))
        for w in warnings:
            print("  " + w)
    if errors:
        print("\nERRORS (%d):" % len(errors))
        for e in errors:
            print("  " + e)
        sys.exit(1)
    print("\nOK: markup valid in all files.")


if __name__ == "__main__":
    main()
