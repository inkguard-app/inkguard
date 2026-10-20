"""
OPERATION LOG — version the operations, never the text.

WHY NOT STORE EACH VERSION OF THE NOTE
--------------------------------------
Intermediate versions contain PHI. The raw note is PHI; the note after
the first stage is partially-redacted PHI. Keeping every version would
build a persistent PHI archive at every stage of the pipeline — the
opposite of a log that holds no text.

So this records what each stage DID: offsets, entity types, which layer
and section, the evidence scores (garnet), each detection's fate from the
Ledger, and a keyed hash of each resulting state. No note text, no
section text, and no patient key (only a keyed hash of it).

WHAT THAT BUYS
--------------
  traceability   every redaction attributed to a layer, a section
                 and the evidence behind it
  verifiability  replaying the log against the original must reproduce
                 every stage hash exactly; any mismatch is reported
  reconstruction only for whoever still holds the original: the
                 log alone rebuilds nothing, because it holds no text
  improvability  run a corpus through two software versions and diff the
                 logs — which spans were added or dropped, by which
                 layer, in which section

THIS LOG IS PRIVATE. It must not be published with a corpus. Span
offsets reveal the length of each redacted name, and script family —
recorded here so detection equity can be measured per writing system —
is a proxy for national origin. The shareable record is the aggregate
audit trail; this is the lab notebook behind it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

OPLOG_VERSION = 1


def _software_version() -> str:
    try:
        from inkguard import __version__
        return __version__
    except Exception:
        return "unknown"


def _key():
    from inkguard.security import KeyStore
    ks = KeyStore()
    return ks.derive("oplog-v1"), not getattr(ks, "ephemeral", False)


def _h(key: bytes, s: str) -> str:
    return hmac.new(key, s.encode(), hashlib.sha256).hexdigest()[:32]


def build(*, original, sanitized, redactions, assignments, trace, config,
          clean_after_redaction, final, sh_changes, shift_report,
          patient_key, mode, exit_report) -> dict:
    from inkguard import talc
    from inkguard.phi_scanner import LEXICON_VERSION
    from inkguard.onyx import script_family

    key, persistent = _key()
    ops = []
    for h in sorted(redactions, key=lambda x: x.start):
        op = {
            "start": h.start, "end": h.end,
            "entity": h.entity_type, "canonical": talc.canonical(h.entity_type),
            "layer": h.layer, "section": getattr(h, "section", ""),
            "score": round(float(h.score), 3),
            "evidence": list(getattr(h, "evidence", ()) or ()),
            "replacement": ("uncertain" if h.layer == "uncertain" else
                            "shift" if (shift_report is not None and
                                        talc.canonical(h.entity_type) == "DATE_TIME")
                            else mode),
        }
        if h.entity_type == "PERSON":
            op["script"] = script_family(h.text)          # private: equity QA
            a = (assignments or {}).get((h.start, h.end)) if h.layer != "uncertain" else None
            if a is not None:
                op["code"], op["role"], op["ambiguous"] = a.code, a.role, a.ambiguous
        ops.append(op)

    ledger = trace.ledger
    return {
        "oplog_version": OPLOG_VERSION,
        "software_version": _software_version(),
        "lexicon_version": LEXICON_VERSION,
        "doc_id": _h(key, "doc\x1f" + original),
        "patient": _h(key, "patient\x1f" + patient_key) if patient_key else None,
        "key_persistent": persistent,
        "profile": config.profile.value,
        "redaction_mode": mode,
        "note_profile": trace.profile,          # sections (quartz)
        "on_demand": trace.expressed,          # which on-demand detectors ran (phi_scanner)
        "raw_counts": trace.raw_counts,
        "ledger": [e.to_dict() for e in ledger.entries.values()] if ledger else [],
        "ledger_summary": ledger.summary() if ledger else None,
        "held": trace.held,
        "propagation": {"seed_names": trace.seed_count,
                     "adjacent_tokens": trace.adjacent_tokens,
                     "propagated": trace.propagated,
                     "from_patient_names": trace.from_memory},
        "operations": ops,
        "safe_harbor_changes": sh_changes,
        "dates_shifted": shift_report.shifted if shift_report else 0,
        "date_shift_mode": shift_report is not None,
        "stages": {
            "sanitized": _h(key, sanitized),
            "redacted": _h(key, clean_after_redaction),
            "final": _h(key, final),
        },
        "exit": exit_report.to_dict() if exit_report else None,
    }


# =====================================================================
# Replay and verify — requires the original
# =====================================================================

def replay(original: str, log: dict, patient_key: str | None = None) -> dict:
    """Rebuild every stage from the ORIGINAL plus the log.

    Detection is not re-run: the recorded operations are applied, so the
    replay reproduces exactly what that software version did, even after
    the software has changed.
    """
    from inkguard import basalt, deident_profile, date_shift
    from inkguard.phi_scanner import PHIHit
    from inkguard.policy import _apply_redactions, _apply_safe_harbor_rules
    from inkguard.onyx import Assignment

    sanitized = basalt.diagnose(original).sanitized_text
    hits, assign = [], {}
    for op in log["operations"]:
        s, e = op["start"], op["end"]
        hits.append(PHIHit(op["entity"], s, e, sanitized[s:e], op["score"],
                           op["layer"], op.get("section", "")))
        if op.get("code"):
            assign[(s, e)] = Assignment(f"[{op['role']}-{op['code']}]", op["code"],
                                        op["role"], op.get("ambiguous", False),
                                        op.get("script", ""))
    shifting = bool(log.get("date_shift_mode"))
    redacted = _apply_redactions(sanitized, hits, assign or None, keep_dates=shifting,
                                 mode=log.get("redaction_mode") or "mask") \
        if hits else sanitized

    final = redacted
    cfg = deident_profile.get_profile(log["profile"])
    if cfg.apply_safe_harbor_generalization:
        final, _ = _apply_safe_harbor_rules(final)
    note = None
    if shifting:
        from inkguard.policy import _shift_dates
        key, _ = _key()
        if not patient_key:
            note = "dates were shifted; supply patient_key to replay that stage"
        elif log.get("patient") != _h(key, "patient\x1f" + patient_key):
            note = "supplied patient_key does not match the one used"
        else:
            final, _ = _shift_dates(final, hits, patient_key)
    return {"sanitized": sanitized, "redacted": redacted, "final": final, "note": note}


def verify(original: str, log: dict, patient_key: str | None = None) -> dict:
    key, persistent = _key()
    if not log.get("key_persistent") or not persistent:
        return {"verified": False,
                "reason": "log or current key is ephemeral; hashes cannot be compared"}
    if log["doc_id"] != _h(key, "doc\x1f" + original):
        return {"verified": False, "reason": "this original is not the document the log describes"}
    stages = replay(original, log, patient_key)
    results = {}
    for name in ("sanitized", "redacted", "final"):
        results[name] = _h(key, stages[name]) == log["stages"][name]
    ok = all(results.values())
    return {"verified": ok, "stages": results,
            "note": stages["note"],
            "reason": None if ok else
            f"first mismatch at stage '{next(k for k, v in results.items() if not v)}'"}


# =====================================================================
# Diff — how did a software version change the outcome?
# =====================================================================

def diff(logs_a: list[dict], logs_b: list[dict]) -> dict:
    """Compare two runs over the same corpus, matched by doc_id."""
    a = {l["doc_id"]: l for l in logs_a}
    b = {l["doc_id"]: l for l in logs_b}
    added, removed = {}, {}
    grades = {"improved": 0, "worsened": 0, "same": 0}
    order = "ABCF"

    def bump(d, op):
        k = f"{op['canonical']} via {op['layer']} in {op.get('section') or '?'}"
        d[k] = d.get(k, 0) + 1

    for doc, la in a.items():
        lb = b.get(doc)
        if lb is None:
            continue
        sa = {(o["start"], o["end"], o["canonical"]): o for o in la["operations"]}
        sb = {(o["start"], o["end"], o["canonical"]): o for o in lb["operations"]}
        for k in sb.keys() - sa.keys():
            bump(added, sb[k])
        for k in sa.keys() - sb.keys():
            bump(removed, sa[k])
        ga = (la.get("exit") or {}).get("grade", "A")
        gb = (lb.get("exit") or {}).get("grade", "A")
        if order.index(gb) < order.index(ga):
            grades["improved"] += 1
        elif order.index(gb) > order.index(ga):
            grades["worsened"] += 1
        else:
            grades["same"] += 1
    return {
        "documents_compared": len(set(a) & set(b)),
        "versions": [next(iter(a.values()), {}).get("software_version"),
                     next(iter(b.values()), {}).get("software_version")],
        "spans_added": dict(sorted(added.items(), key=lambda kv: -kv[1])),
        "spans_removed": dict(sorted(removed.items(), key=lambda kv: -kv[1])),
        "exit_grades": grades,
    }


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m inkguard.beryl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("diff", help="compare two runs' operation logs")
    d.add_argument("a", type=Path)
    d.add_argument("b", type=Path)
    v = sub.add_parser("verify", help="replay one document and check every stage")
    v.add_argument("original", type=Path)
    v.add_argument("oplog", type=Path)
    v.add_argument("--patient-key")
    args = ap.parse_args(argv)
    if args.cmd == "diff":
        print(json.dumps(diff(_load(args.a), _load(args.b)), indent=2))
        return 0
    text = args.original.read_text()
    key, _ = _key()
    target = _h(key, "doc\x1f" + text)
    log = next((l for l in _load(args.oplog) if l["doc_id"] == target), None)
    if log is None:
        print(json.dumps({"verified": False, "reason": "no log entry for this document"}))
        return 1
    res = verify(text, log, args.patient_key)
    print(json.dumps(res, indent=2))
    return 0 if res["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
