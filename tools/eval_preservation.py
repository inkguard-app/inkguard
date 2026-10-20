#!/usr/bin/env python3
"""
Clinical-preservation benchmark: does the tool keep the clinical detail a
study needs while it removes the identifiers?

The notes are in benchmark/neuro/ (invented neurosurgical and surrounding
care, written independently of the tool's word lists; see its README).
Markup: {{TYPE|text}} is an identifier, [[text]] is clinical content that
must survive. Two figures, side by side, on the same notes:

  identifiers removed   every identifying word of the span was taken out
                        (a date may keep its year, a ZIP its first three
                        digits, as Safe Harbor allows)
  clinical kept         phrases: every character of the phrase reached the
                        reader unchanged; words: the share of the phrase's
                        words that did

A note the tool flags for review is scored too (its draft is what the
reviewer sees), and the share flagged is reported.

--baseline runs Microsoft Presidio's analyzer in its default configuration
(all its recognizers, the same spaCy English model) on the same notes and
replaces every finding, as a reference point for a widely used open-source
tool. It is not tuned in any way. Presidio is a general-purpose toolkit
meant to be configured; its defaults are not designed for clinical notes,
and Inkguard itself builds on it.

The held-out half is scored in aggregate only; nothing from it is printed.

    python tools/eval_preservation.py --half tuning -v
    python tools/eval_preservation.py --half held-out
    python tools/eval_preservation.py --half held-out --baseline
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
SET = ROOT / "benchmark" / "neuro"

_MARK = re.compile(r"\{\{([A-Z]+)\|(.*?)\}\}|\[\[(.*?)\]\]", re.S)
_WORD = re.compile(r"[^\W_]+", re.U)
_YEAR = re.compile(r"(?:19|20)\d\d")
from eval_corpus import GENERIC as _G  # noqa: E402  (the same rule as the main benchmark)
# The kind of a place or organisation stays by the tool's policy ("[FACILITY]
# Spine Institute"); only its name must go.
KIND = {"institute", "spine", "imaging", "rehabilitation", "rehab", "centre", "program", "llp",
        "llc", "insurance", "claims", "school", "elementary", "logistics", "nursing", "skilled",
        "facility", "senior", "living", "mri", "open", "diagnostic", "family", "medicine",
        "children's", "craniofacial", "hand", "nerve", "brain", "tumor", "tumour", "eye", "pain",
        "trauma", "level", "i", "stroke", "comprehensive", "neurovascular", "neuroscience",
        "movement", "disorders", "management", "agency", "aging", "area", "appeals", "unit",
        "advantage", "mutual", "workers'", "comp", "auto", "women's", "infants", "neuromodulation",
        "house", "urgent", "care", "acute", "spinal", "injuries", "suite", "ear", "skull", "base",
        "and", "for", "on", "neuro-ophthalmology", "orthopaedic", "orthopedic", "university",
        "community", "group", "associates", "services", "program", "medicare", "lane"}
GENERIC = set(_G)
# Not Safe Harbor identifiers in themselves: places of care (removed by
# the tool's own policy) and organisations. Scored, and shown apart.
NOT_SAFE_HARBOR = {"FACILITY", "ORG"}


def parse(raw: str):
    """Plain text, identifier spans (type, start, end) and keep spans (start, end)."""
    out, ids, keeps, pos = [], [], [], 0
    last = 0
    for m in _MARK.finditer(raw):
        out.append(raw[last:m.start()])
        pos += m.start() - last
        if m.group(1):
            val = m.group(2)
            ids.append((m.group(1), pos, pos + len(val)))
        else:
            val = m.group(3)
            keeps.append((pos, pos + len(val)))
        out.append(val)
        pos += len(val)
        last = m.end()
    out.append(raw[last:])
    return "".join(out), ids, keeps


def load(path: Path):
    notes = [n.strip("\n") + "\n" for n in path.read_text(encoding="utf-8").split("=====NOTE=====")]
    return [parse(n) for n in notes if n.strip()]


def kept_chars(text: str, clean: str) -> list[bool]:
    from eval_corpus import kept_chars as k
    return k(text, clean)


def required(typ: str, val: str) -> bool:
    if typ == "AGE":
        m = re.search(r"\d+", val)
        return bool(m) and int(m.group()) > 89
    return True


def caught(text: str, typ: str, s: int, e: int, kept: list[bool]) -> int:
    """0 caught, 1 partial, 2 missed."""
    words = []
    for m in _WORD.finditer(text[s:e]):
        w = m.group()
        if w.lower() in GENERIC or (typ in NOT_SAFE_HARBOR and w.lower() in KIND):
            continue
        if typ == "DATE" and _YEAR.fullmatch(w):
            continue
        a, b = s + m.start(), s + m.end()
        if typ == "ZIP" and w.isdigit() and len(w) == 5:
            a = a + 3                                       # first three digits may stay
        words.append((a, b))
    if not words:
        return 0
    left = [all(kept[a:b]) for a, b in words]
    return 0 if not any(left) else (2 if all(left) else 1)


def presidio_engine():
    import inkguard.phi_scanner  # noqa: F401  (makes tldextract offline, as in the tool)
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    nlp = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}]}).create_engine()
    return AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])


def presidio_clean(engine, text: str) -> str:
    res = engine.analyze(text=text, language="en")
    spans = sorted({(r.start, r.end, r.entity_type) for r in res})
    out, last = [], 0
    for s, e, t in spans:
        if s < last:
            continue
        out.append(text[last:s])
        out.append(f"<{t}>")
        last = e
    out.append(text[last:])
    return "".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--half", choices=("tuning", "held-out"), default="tuning")
    ap.add_argument("--baseline", action="store_true", help="score Presidio's defaults instead")
    ap.add_argument("-v", action="store_true", help="show misses and lost terms (tuning only)")
    ap.add_argument("--cap", type=int, default=40)
    a = ap.parse_args()
    show = a.v and a.half == "tuning"
    files = sorted((SET / a.half).glob("p*.txt"))
    t0 = time.time()
    per = defaultdict(lambda: [0, 0, 0])
    keep_phr = [0, 0]                                       # intact, total
    keep_words = [0, 0]                                     # kept, total
    held = notes = 0
    lost, leaks = [], []
    from collections import Counter
    hold_why = Counter()
    engine = presidio_engine() if a.baseline else None
    per_patient = []                                         # (ids caught, ids, phrases kept, phrases)
    if not a.baseline:
        from inkguard import contract
    for f in files:
        loaded = load(f)
        pp = [0, 0, 0, 0]
        if a.baseline:
            cleans = [(presidio_clean(engine, t), "clear") for t, _, _ in loaded]
        else:
            results, _ctx = contract.process_patient([t for t, _, _ in loaded],
                                                     patient_key=f.stem, mode="mask")
            cleans = []
            for r, (t, _i, _k) in zip(results, loaded):
                status = "blocked" if r.action.value == "blocked" else r.action.value
                cleans.append(("" if status == "blocked" else r.clean_text, status))
                comp = getattr(r, "comprehension", None)
                if show and status == "review" and comp is not None:
                    for n in comp.blocking:
                        hold_why[(n.reason, t[n.start:n.end])] += 1
        for (text, ids, keeps), (clean, status) in zip(loaded, cleans):
            notes += 1
            if status != "clear" and status not in ("redacted", "allow"):
                held += 1
            kept = kept_chars(text, clean) if clean else [False] * len(text)
            for typ, s, e in ids:
                if not required(typ, text[s:e]):
                    continue
                k = caught(text, typ, s, e, kept)
                per[typ][k] += 1
                if typ not in NOT_SAFE_HARBOR:
                    pp[0] += (k == 0)
                    pp[1] += 1
                if k and show and len(leaks) < a.cap:
                    leaks.append(f"{typ} {text[s:e]!r}")
            for s, e in keeps:
                keep_phr[1] += 1
                intact = all(kept[s:e])
                keep_phr[0] += intact
                pp[2] += intact
                pp[3] += 1
                ws = [(s + m.start(), s + m.end()) for m in _WORD.finditer(text[s:e])]
                keep_words[1] += len(ws)
                keep_words[0] += sum(all(kept[x:y]) for x, y in ws)
                if not intact and show and len(lost) < a.cap:
                    lost.append(f"{text[s:e]!r}")
            print(f"  {notes}", end="\r", file=sys.stderr)
        per_patient.append(pp)
    print(file=sys.stderr)
    tot = [sum(v[i] for t, v in per.items() if t not in NOT_SAFE_HARBOR) for i in range(3)]
    allt = [sum(v[i] for v in per.values()) for i in range(3)]
    n_id = sum(tot)
    who = "Presidio defaults (reference)" if a.baseline else "Inkguard"
    print(f"{who} · clinical-preservation set · {a.half} · {len(files)} patients, {notes} notes, "
          f"{time.time() - t0:.0f}s")
    print(f"  identifiers removed   {tot[0]:>5d}/{n_id:<5d} {tot[0] / n_id:6.1%}   "
          f"partial {tot[1]}  missed {tot[2]}   (Safe Harbor types)")
    print(f"  with places and orgs  {allt[0]:>5d}/{sum(allt):<5d} {allt[0] / sum(allt):6.1%}")
    print(f"  clinical phrases kept {keep_phr[0]:>5d}/{keep_phr[1]:<5d} {keep_phr[0] / keep_phr[1]:6.1%}")
    print(f"  clinical words kept   {keep_words[0]:>5d}/{keep_words[1]:<5d} "
          f"{keep_words[0] / keep_words[1]:6.1%}")
    # Patient bootstrap, as for the main benchmark: notes of one patient are
    # not independent, so whole patients are resampled (2,000 times).
    import random
    rng = random.Random(20261018)
    def ci(i, j):
        rates = []
        for _ in range(2000):
            pick = [rng.choice(per_patient) for _ in per_patient]
            tot = sum(x[j] for x in pick)
            rates.append(sum(x[i] for x in pick) / tot if tot else 0)
        rates.sort()
        return rates[50], rates[1949]
    lo, hi = ci(0, 1)
    plo, phi = ci(2, 3)
    print(f"  95% intervals (patient bootstrap): identifiers {lo:.1%}-{hi:.1%}; "
          f"clinical phrases {plo:.1%}-{phi:.1%}")
    if not a.baseline:
        print(f"  notes flagged or stopped for a person: {held} of {notes} ({held / notes:.1%})")
    print("  by type (removed / total):")
    for typ, (c, p, m) in sorted(per.items(), key=lambda kv: -sum(kv[1])):
        print(f"    {typ:10s} {c:>4d}/{c + p + m:<4d}")
    if show:
        print("\nWHAT FLAGGED NOTES (tuning half; reason, word: count)")
        for (r, w), n in hold_why.most_common(60):
            print(f"  {r:16s} {w!r}: {n}")
        print("\nLEAKS (tuning half)")
        print("\n".join(leaks))
        print("\nCLINICAL PHRASES NOT KEPT INTACT (tuning half)")
        print("\n".join(lost))


if __name__ == "__main__":
    main()
