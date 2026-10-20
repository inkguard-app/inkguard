#!/usr/bin/env python3
"""
Score the engine on ASQ-PHI (Weatherhead, Golovko & McCaffrey, Data in
Brief 2026; licence not stated on the dataset page, so the data is not
distributed here): 1,051 fully synthetic clinical search queries,
832 with 2,973 labelled PHI values and 219 hard negatives with none.

  python tools/eval_asq_phi.py path/to/synthetic_clinical_queries.txt [--half=even|odd] [-v]

Even-numbered queries are the TUNING half; odd-numbered queries are the
HELD-OUT half. Any change made while looking at misses must be judged on
the half that was not looked at.

A labelled value counts as removed when it no longer appears in the
delivered output. Two policy differences are reported separately rather
than hidden:
  - ASQ-PHI labels hospital and clinic names as GEOGRAPHIC_LOCATION.
    Safe Harbor does not list facility names; this engine removes named
    places of care anyway ([FACILITY]), and they are scored separately.
  - A date whose output keeps only the year counts as removed: Safe
    Harbor permits the year.
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FACILITY = re.compile(r"\b(?:hospital|clinic|medical|center|centre|health|infirmary|"
                      r"memorial|sinai|mayo|kaiser|ucla|ucsf|nyu|va\b|st\.|saint|general|"
                      r"children|university|institute)\b", re.I)


def parse(path: Path):
    blocks = path.read_text().split("===QUERY===")
    for b in blocks:
        if not b.strip():
            continue
        q, _, tags = b.partition("===PHI_TAGS===")
        vals = [json.loads(l) for l in tags.strip().splitlines() if l.strip().startswith("{")]
        yield q.strip(), vals


def main(path: str, half: str = "all") -> None:
    from inkguard.policy import evaluate
    per = defaultdict(lambda: [0, 0])
    facility = [0, 0]
    misses = Counter()
    miss_examples = defaultdict(list)
    neg_total = neg_changed = 0
    neg_examples = []
    blocked = 0
    for i, (q, vals) in enumerate(parse(Path(path))):
        if half == "even" and i % 2:
            continue
        if half == "odd" and not i % 2:
            continue
        r = evaluate(q)
        if r.action.value == "blocked":
            blocked += 1
            continue
        out = r.clean_text
        if not vals:
            neg_total += 1
            if out != q:
                neg_changed += 1
                if len(neg_examples) < 25:
                    neg_examples.append((q, out))
            continue
        for v in vals:
            t, val = v["identifier_type"], v["value"]
            gone = val not in out
            if t == "GEOGRAPHIC_LOCATION" and FACILITY.search(val):
                facility[0] += gone; facility[1] += 1
                continue
            per[t][0] += gone; per[t][1] += 1
            if not gone:
                misses[t] += 1
                if len(miss_examples[t]) < 12:
                    miss_examples[t].append((val, out))
    tot = [sum(a for a, _ in per.values()), sum(b for _, b in per.values())]
    print(f"ASQ-PHI ({half})  blocked queries: {blocked}")
    for t, (a, b) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        print(f"  {t:32s} {a:>4d}/{b:<4d} {a / b:6.1%}")
    print(f"  {'ALL (Safe Harbor identifiers)':32s} {tot[0]:>4d}/{tot[1]:<4d} {tot[0] / tot[1]:6.1%}")
    print(f"  facility names (removed as [FACILITY]): {facility[0]}/{facility[1]} removed")
    print(f"  hard negatives changed: {neg_changed}/{neg_total}")
    if "-v" in sys.argv:
        for t, ex in miss_examples.items():
            for val, out in ex:
                print(f"MISS {t}: {val!r} | {out[:150]}")
        for q, out in neg_examples:
            print(f"NEG  {q[:110]}\n  -> {out[:110]}")


if __name__ == "__main__":
    half = next((a.split("=")[1] for a in sys.argv if a.startswith("--half=")), "all")
    main(sys.argv[1], half)
