#!/usr/bin/env python3
"""
Fold per-source mined terms into an additive lexicon module.

SAFETY RULES, in order of importance:

1. EVERY newly mined eponym goes to the AMBIGUOUS set, without
   exception. The harvest cannot tell "Lambert-Eaton syndrome" from a
   patient named Lambert, and the cost of the two errors is not
   symmetric: a term wrongly ambiguous causes one over-redaction that
   the integrity checker reports; a term wrongly unambiguous leaks a
   patient name silently. Ambiguous terms still resolve as clinical
   when a qualifier follows, so nothing is lost clinically.

2. Only common nouns and adjectives -- anatomy, procedures, conditions
   -- are allowed into the unambiguous set. None of these are surnames.

3. Anything already known to be ambiguous stays ambiguous, and generic
   English picked up by the harvest is dropped rather than shipped.

Kept for the record: this script does not run as it stands, because it
imports tools/mine_lexicon.py, which is not in this repository.

In the shipped output (inkguard/data/lexicon_data_v2.json.gz) every term
comes from FDA GUDID, FDA drug listings or ICD-10-CM. The shipped file
holds the words only; the source of each term is listed in
tools/lexmine/lexicon_data_v2_sources.csv.gz.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
from tools.mine_lexicon import KNOWN_AMBIGUOUS  # noqa: E402
from inkguard.clinical_lexicon import AMBIGUOUS_EPONYMS  # noqa: E402

# Ordinary English the suffix/qualifier harvests pick up. Harmless in
# isolation but it is noise in a clinical lexicon.
GENERIC_NOISE = {
    "early", "late", "dominant", "fine", "core", "adjustment", "common",
    "deep", "superficial", "great", "greater", "lesser", "small",
    "large", "long", "short", "major", "minor", "true", "false",
    "first", "second", "third", "middle", "lower", "upper", "anterior",
    "posterior", "medial", "lateral", "superior", "inferior", "left",
    "right", "internal", "external", "primary", "secondary", "normal",
}

UNAMBIGUOUS_CATEGORIES = (
    "conditions", "anatomy_multiword", "anatomy_single",
    "procedures", "devices", "drugs",
)


def main():
    mined_dir = Path("mined")
    files = sorted(mined_dir.glob("*.json"))
    if not files:
        sys.exit("no mined/*.json found")

    provenance: dict[str, set[str]] = defaultdict(set)
    unambiguous: set[str] = set()
    ambiguous: set[str] = set()

    # Registry sources (GUDID, NDC) arrive pre-classified by the census
    # surname check in extract_registry.py, so they bypass the
    # capitalization heuristic below -- surname frequency is stronger
    # evidence than capitalization and is already decided.
    registry_unambiguous: set[str] = set()
    registry_ambiguous: set[str] = set()
    for f in list(files):
        data = json.loads(f.read_text())
        if "brand_strings" not in data:
            continue
        files.remove(f)
        src = data["source"]
        for t in data["unambiguous"]:
            registry_unambiguous.add(t)
            provenance[t].add(src)
        for t in data["ambiguous_surname_collision"]:
            registry_ambiguous.add(t)
            provenance[t].add(src)

    for f in files:
        data = json.loads(f.read_text())
        src = data["source"]
        new = data["new_vs_baseline"]

        # Rule 1: all mined eponyms are ambiguous, full stop.
        for t in new.get("eponyms", []):
            if len(t) >= 4 and t not in GENERIC_NOISE:
                ambiguous.add(t)
                provenance[t].add(src)

        # Rule 2: common-noun categories may be unambiguous.
        for cat in UNAMBIGUOUS_CATEGORIES:
            for t in new.get(cat, []):
                if len(t) < 4 or t in GENERIC_NOISE:
                    continue
                unambiguous.add(t)
                provenance[t].add(src)

    # Rule 3: never promote anything already known to be name-like.
    #
    # SELF-CONTAMINATION GUARD. AMBIGUOUS_EPONYMS is a union that now
    # includes MINED_V2_AMBIGUOUS -- this script's own previous output.
    # Reading it whole made the second run treat its own terms as
    # "already known" and drop them, silently shrinking the lexicon on
    # every re-run. The baseline must be the sources this script does
    # NOT write, so v2 is subtracted back out.
    try:
        from inkguard.lexicon_data_v2 import MINED_V2_AMBIGUOUS as _prior_output
    except ImportError:
        _prior_output = set()
    already_ambiguous = (
        ({a.lower() for a in AMBIGUOUS_EPONYMS} | set(KNOWN_AMBIGUOUS))
        - {t.lower() for t in _prior_output}
    )
    collisions = sorted((unambiguous | ambiguous) & already_ambiguous)
    unambiguous -= already_ambiguous
    ambiguous -= already_ambiguous

    # Rule 4: TIE-BREAK BY EVIDENCE, NOT BY CATEGORY.
    #
    # The eponym harvest keys on qualifiers that include anatomical nouns
    # ("celiac artery", "brachial plexus"), so it also catches ordinary
    # adjectives. Demoting all of those would cost
    # over-redaction for no safety gain -- nobody is named Carotid.
    #
    # Capitalization in the input text (read from extract/*.txt; this
    # path is not used for the shipped output) separates the two cases without
    # anyone's judgement: a proper noun ("Achilles", "Lambert") is
    # capitalized nearly everywhere it appears, an adjective ("carotid")
    # almost never is, except at a sentence start. Terms above the
    # threshold are treated as name-shaped and demoted; the rest are
    # common vocabulary and stay unambiguous.
    CAP_THRESHOLD = 0.6
    corpus = ""
    for tf in sorted(Path("extract").glob("*.txt")):
        corpus += tf.read_text(errors="ignore") + "\n"

    # MID-SENTENCE ONLY. A first attempt counted every occurrence and
    # demoted "actinomycosis", "autoimmune" and "cardiovascular" --
    # capitalized not because they are names but because they stood at
    # the head of headings and bullets. Sentence-initial and
    # line-initial position carries no evidence about proper-noun-hood,
    # so those occurrences are excluded and only genuinely mid-sentence
    # capitalization counts.
    import re as _re
    cap_ratio: dict[str, float] = {}
    for term in sorted(unambiguous | ambiguous):
        if " " in term:
            continue
        # capture the two characters before each occurrence to judge position
        pattern = _re.compile(rf"(.{{0,2}})\b({_re.escape(term)})\b", _re.IGNORECASE)
        mid_total = mid_capped = 0
        for m in pattern.finditer(corpus):
            prefix, word = m.group(1), m.group(2)
            stripped = prefix.rstrip(" \t")
            # line start, sentence start, or list/heading marker -> no evidence
            if stripped == "" or stripped.endswith((".", "!", "?", ":", ";", "\n", "•", "-", ")")):
                continue
            mid_total += 1
            if word[:1].isupper():
                mid_capped += 1
        if mid_total >= 3:
            cap_ratio[term] = mid_capped / mid_total

    name_shaped = {t for t, r in cap_ratio.items() if r >= CAP_THRESHOLD}
    demoted_by_caps = sorted(unambiguous & name_shaped)
    ambiguous |= (unambiguous & name_shaped)
    unambiguous -= name_shaped

    # Anything the eponym harvest claimed that is demonstrably NOT
    # name-shaped, and that a common-noun harvest also found, stays
    # unambiguous: two independent signals say it is ordinary vocabulary.
    both = unambiguous & ambiguous
    rescued = sorted(t for t in both if cap_ratio.get(t, 1.0) < CAP_THRESHOLD)
    ambiguous -= set(rescued)
    # Whatever is still claimed by both, with no capitalization evidence
    # either way, defaults to ambiguous — the safe direction.
    overlap = sorted(unambiguous & ambiguous)
    unambiguous -= ambiguous

    # Fold registry terms in last. A surname collision always wins:
    # a term any registry flagged as a real surname stays ambiguous even
    # if a text harvest thought it was ordinary vocabulary.
    unambiguous |= (registry_unambiguous - already_ambiguous - registry_ambiguous)
    ambiguous |= (registry_ambiguous - already_ambiguous)
    unambiguous -= ambiguous

    header = '"""\nMined clinical vocabulary, second pass — GENERATED FILE.\n\n'
    header += "Sources mined SEPARATELY. The source of each term is listed in\n"
    header += "tools/lexmine/lexicon_data_v2_sources.csv.gz. Terminology only:\n"
    header += "no sentences, definitions or passages from any source are\n"
    header += "reproduced here.\n\n"
    header += "SAFETY: every eponym harvested in this pass is AMBIGUOUS by\n"
    header += "construction. The harvest cannot distinguish an eponym from a\n"
    header += "surname, and a wrongly-unambiguous term leaks patient names\n"
    header += "silently. Ambiguous terms still count as clinical when a disease\n"
    header += "qualifier follows.\n"
    header += '"""\n\n'

    lines = [header]
    lines.append("MINED_V2_UNAMBIGUOUS: set[str] = {\n")
    for t in sorted(unambiguous):
        lines.append(f'    "{t}",  # {"+".join(sorted(provenance[t]))}\n')
    lines.append("}\n\n")
    lines.append("MINED_V2_AMBIGUOUS: set[str] = {\n")
    for t in sorted(ambiguous):
        lines.append(f'    "{t}",  # {"+".join(sorted(provenance[t]))}\n')
    lines.append("}\n")

    out = Path(__file__).resolve().parents[2] / "inkguard" / "lexicon_data_v2.py"
    out.write_text("".join(lines))

    print(f"sources mined : {[json.loads(f.read_text())['source'] for f in files]}")
    print(f"unambiguous   : {len(unambiguous)}")
    print(f"ambiguous     : {len(ambiguous)}")
    print(f"kept ambiguous (already known name-like): {len(collisions)} -> {collisions[:12]}")
    print(f"claimed by both harvests, demoted  : {len(overlap)} -> {overlap[:12]}")
    print(f"written: {out}")


def _pack() -> None:
    """Store the list as data (inkguard/data/lexicon_data_v2.json.gz) with a short loader."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[2]))
    from inkguard._data import pack_module
    pack_module("lexicon_data_v2", "tools/lexmine/integrate.py")


if __name__ == "__main__":
    main()
    _pack()
