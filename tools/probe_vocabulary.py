"""Probe: does each curated neurosurgical-care and care-setting phrase survive redaction
in ordinary clinical sentences, and does it cause the note to be flagged?

    python tools/probe_vocabulary.py            # summary and the phrases that fail
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PHI_ROOT_KEY", "ab" * 32)

from inkguard import contract                       # noqa: E402
from inkguard.lexicon_neuro import all_phrases      # noqa: E402

FRAMES = ("{p} was discussed with the team.",
          "Plan: {p} next week, then follow up in clinic.",
          "IMPRESSION: {P}.")


def run():
    lost, held, total = [], [], 0
    for group, phrases in all_phrases().items():
        for p in phrases:
            for f in FRAMES:
                total += 1
                text = f.format(p=p[0].upper() + p[1:], P=p.upper())
                want = p.upper() if "{P}" in f else p[0].upper() + p[1:]
                r = contract.deidentify(text, mode="mask")
                if want not in r.clean_text:
                    lost.append((group, text, r.clean_text))
                elif r.status != "clear":
                    held.append((group, text, [w["word"] for w in r.words_to_check]))
    return total, lost, held


if __name__ == "__main__":
    total, lost, held = run()
    print(f"{total} sentences: {len(lost)} lost a clinical phrase, {len(held)} flagged for review")
    for g, t, c in lost:
        print(f"LOST [{g}] {t!r} -> {c!r}")
    for g, t, w in held:
        print(f"FLAGGED [{g}] {t!r} words={w}")
