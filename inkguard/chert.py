"""
THREE-PERSON RELEASE. In development, not part of the 0.25.0 release.

The software reads and redacts first. Then two people the clinic has
authorized each go through EVERY note on their own, without seeing each
other's work, and look at each note twice:

  look 1  SEE-THROUGH   every removal is highlighted but readable;
  look 2  BLACKED OUT   the note exactly as it will leave.

Reviewers mark whole words (a click or a drag snaps to word boundaries, so
two reviewers marking the same name produce the same span), keep words the
tool removed wrongly, answer the tool's questions, and send anything they
are unsure of to the UNSURE BUCKET. The authorizing doctor then sees
everything — both looks, both reviewers' work, the bucket — decides the
bucket, may remove more, and signs as the third reviewer.

The rules:
  - two reviewers removing something is final;
  - two reviewers keeping something stands unless the doctor removes it;
  - anything else the doctor decides, and it stays removed until then;
  - an identifier the tool is CERTAIN of (found by its label or its exact
    format) can never be kept by anyone; the doctor may withhold the note.

Commands (all local; nothing is sent anywhere):

  prepare   python -m inkguard.chert prepare NOTES_DIR --out BATCHES_DIR \\
                --authorized-by "Dr. Owner" --reviewer "Name One" --reviewer "Name Two"
  doctor    python -m inkguard.chert doctor BATCH_DIR [BATCH_DIR ...] \\
                --decisions R1.json R2.json [...] --out doctor.html
  release   python -m inkguard.chert release BATCH_DIR [BATCH_DIR ...] \\
                --decisions R1.json R2.json [...] --doctor DOCTOR.json --out CLEAN_DIR

THE PAGES, batch.json's source paths and release_map.csv contain or point
to patient information. They stay on the clinic's computer.
"""

from __future__ import annotations

import argparse
import bisect
import datetime
import hashlib
import hmac
import json
import math
import os
import secrets
import shutil
import re
import sys
from pathlib import Path

BATCH_KIND = "phi-review-batch"
DECISIONS_KIND = "phi-review-decisions"
BATCH_VERSION = 1
DEFAULT_BATCH_PATIENTS = 200
TEXT_SUFFIXES = {".txt", ".md", ".note", ".text"}
PAGE_TEMPLATE = Path(__file__).with_name("chert_page.html")

# What a reviewer can add, and the type it is removed as.
ADD_TYPES = {"name": "PERSON", "date": "DATE_TIME", "place": "LOCATION",
             "contact": "PHONE_NUMBER", "id": "UNIQUE_ID", "other": "OTHER_IDENTIFIER",
             # "Not sure": removed until the doctor decides.
             "unsure": "OTHER_IDENTIFIER"}

# Identifiers the tool is CERTAIN of: found by a field label ("MRN:",
# "Patient:"), a title or relation frame ("Dr. X", "wife X"), or an exact
# format (an SSN, an email address). Nobody can keep one of these.
_CERTAIN_LAYERS = frozenset({"anchor", "context"})
_CERTAIN_TYPES = frozenset({"US_SSN", "MRN", "PHONE_NUMBER", "FAX_NUMBER", "EMAIL_ADDRESS", "URL",
                            "IP_ADDRESS", "HEALTH_PLAN_ID", "ACCOUNT_NUMBER", "LICENSE_NUMBER",
                            "VEHICLE_ID", "DEVICE_ID", "BIOMETRIC_ID",
                            # numbers that pass a checksum
                            "UK_NHS", "CREDIT_CARD", "IBAN_CODE", "CRYPTO", "US_ITIN"})


def certain(layer: str, entity: str) -> bool:
    return layer != "uncertain" and (layer in _CERTAIN_LAYERS or entity in _CERTAIN_TYPES)

# A question the tool asks (a word it left in but could not name, or a
# candidate it held in doubt): if a reviewer says "remove", this is the type.
_QUESTION_TYPE = {
    "name_left_in": "PERSON", "possible_person": "PERSON", "lowercase_name": "PERSON",
    "initial_by_name": "PERSON", "date_left_in": "DATE_TIME", "place_left_in": "LOCATION",
    "unknown_code": "UNIQUE_ID", "long_number": "UNIQUE_ID",
    "spelling_slip": "OTHER_IDENTIFIER", "name_also_clinical": "PERSON",
}

ATTESTATION = ("I reviewed every note in this batch myself, in both looks, and to the best "
               "of my knowledge the blacked-out text contains nothing that identifies a patient.")


class ReviewError(Exception):
    """A batch cannot be prepared, resolved or released as asked."""


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitized(text: str) -> str:
    from inkguard import contract
    return contract.sanitize(text)


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


# =====================================================================
# rare diseases
# =====================================================================

_WORD = re.compile(r"[A-Za-z0-9]+")
_AGE_CUE = re.compile(r"\b\d{1,3}[ -]?(?:y/?o|yo|year[- ]old|years? old|yr[- ]old|M|F)\b", re.I)
_SEX_CUE = re.compile(r"\b(?:male|female|man|woman|boy|girl|gentleman|lady)\b", re.I)
_JOB_CUE = re.compile(
    r"\b(?:(?:school)?teacher|nurse|farmer|rancher|pastor|priest|rabbi|imam|firefighter|police(?:\s+officer)?|"
    r"sheriff|deputy|mayor|councilman|councilwoman|senator|judge|coach|professor|principal|"
    r"lawyer|attorney|pilot|athlete|quarterback|player|soldier|veteran|marine|officer|"
    r"surgeon|physician|dentist|pharmacist|miner|fisherman|logger|trucker|chef|owner of|"
    r"works? (?:as|at|for)|employed (?:as|at|by)|student at|attends)\b", re.I)
_SUPERLATIVE = re.compile(r"\b(?:only|first|sole|one of (?:two|three|few))\b[^.\n]{0,40}"
                          r"\b(?:case|patient|person|family|in (?:the )?(?:county|town|state|country))\b", re.I)


def _rare_lexicon():
    from inkguard import contract
    return contract.rare_diseases()


def rare_flags(text: str, removed: list[tuple[int, int]]) -> list[dict]:
    """Rare diseases named in the note (Orphanet-rare classes in MONDO; see
    tools/build_rare_diseases.py), each with the other details in the same
    note that could combine with it."""
    lex, max_words = _rare_lexicon()
    toks = [(m.start(), m.end(), m.group().lower()) for m in _WORD.finditer(text)]
    out, i = [], 0
    while i < len(toks):
        hit = None
        for n in range(min(max_words, len(toks) - i), 0, -1):
            s, e = toks[i][0], toks[i + n - 1][1]
            # A phrase must not run across a line break or a sentence end.
            if n > 1 and re.search(r"[\n.;:]", text[s:e]):
                continue
            if " ".join(t[2] for t in toks[i:i + n]) in lex:
                hit = (s, e, n)
                break
        if hit and not any(a < hit[1] and hit[0] < b for a, b in removed):
            also = []
            if _AGE_CUE.search(text):
                also.append("an age")
            if _SEX_CUE.search(text):
                also.append("sex")
            if _JOB_CUE.search(text):
                also.append("a job or school")
            if _SUPERLATIVE.search(text):
                also.append('"only" or "first" wording')
            out.append({"start": hit[0], "end": hit[1], "also": also})
            i += hit[2]
        else:
            i += 1
    return out


# =====================================================================
# 1. PREPARE
# =====================================================================

def _not_included(notes_dir: Path) -> dict[str, list[dict]]:
    """Every file this release does not read, by patient, with the reason.
    Every file is counted; images are set aside and flagged, never silently
    skipped."""
    from inkguard.cli import _classify
    out: dict[str, list[dict]] = {}
    for p in sorted(notes_dir.rglob("*")):
        if not (p.is_file() or p.is_symlink()):
            continue
        if (p.suffix.lower() in TEXT_SUFFIXES and not p.name.startswith(".")
                and not p.is_symlink()):
            continue
        kind, why = _classify(p)
        if kind == "document":
            why = ("a Word, Excel, PDF or CSV file: the three-person release reads text notes "
                   "for now; the batch tool cleans these")
        elif kind == "text":
            why = f"a {p.suffix} file: the three-person release reads .txt and .md notes"
        rel = p.relative_to(notes_dir)
        key = rel.parts[0] if len(rel.parts) > 1 else rel.stem
        out.setdefault(key, []).append({"source": str(rel), "reason": why,
                                        "system": kind == "system"})
    return out


def _patients(notes_dir: Path) -> list[tuple[str, list[Path]]]:
    """One folder per patient. A loose file at the top is its own patient."""
    files = sorted(p for p in notes_dir.rglob("*")
                   if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES
                   and not p.name.startswith("."))
    groups: dict[str, list[Path]] = {}
    for f in files:
        rel = f.relative_to(notes_dir)
        key = rel.parts[0] if len(rel.parts) > 1 else rel.stem
        groups.setdefault(key, []).append(f)
    return sorted(groups.items())


def note_payload(text: str, res, profile_is_lds: bool) -> tuple[dict, str]:
    """What the page shows for one note, and the sanitized text. Composed from
    contract primitives, so the review shell does not import the engine."""
    from inkguard import contract
    sanitized = _sanitized(text)
    blocked = res.action.value == "blocked"
    note: dict = {"sha256": _sha(sanitized), "status": "blocked" if blocked else "ready",
                  "block_reason": contract.BLOCKED_REASON if blocked else "",
                  "removals": [], "generalized": [], "kept": [], "questions": [], "rare": []}
    if blocked:
        return note, sanitized
    taken: list[tuple[int, int]] = []
    # A value under a field label ("Patient:", "MRN:", "Address:") is
    # certain whichever layer's span won the merge.
    labelled = contract.anchored_fields(sanitized)
    for k, h in enumerate(sorted(res.hits or [], key=lambda h: h.start)):
        ent = contract.canonical_type(h.entity_type)
        under_label = h.layer != "uncertain" and any(a <= h.start and h.end <= b for a, b in labelled)
        note["removals"].append({
            "id": f"r{k}", "start": h.start, "end": h.end,
            "entity": ent, "layer": h.layer,
            "unsure": h.layer == "uncertain", "certain": certain(h.layer, ent) or under_label,
            # What the blacked-out look shows: exactly what will leave.
            "shows": contract.replacement(h)})
        taken.append((h.start, h.end))
    if not profile_is_lds:
        for s, e, shows in contract.zip_generalizations(sanitized):
            if not any(s < b and a < e for a, b in taken):
                note["generalized"].append({"start": s, "end": e, "kind": "ZIP", "shows": shows})
        for s, e, shows in contract.age_generalizations(sanitized):
            if not any(s < y and x < e for x, y in taken):
                note["generalized"].append({"start": s, "end": e, "kind": "AGE", "shows": shows})
    for h in (getattr(res, "kept", None) or []):
        note["kept"].append({"start": h.start, "end": h.end,
                             "entity": contract.canonical_type(h.entity_type)})
    busy = taken + [(g["start"], g["end"]) for g in note["generalized"]]
    q = 0
    comp = getattr(res, "comprehension", None)
    for n in (comp.not_understood if comp is not None else []):
        if n.reason == "unsure_removal" or any(n.start < b and a < n.end for a, b in busy):
            continue
        note["questions"].append({"id": f"q{q}", "start": n.start, "end": n.end,
                                  "kind": "not_understood", "reason": n.reason,
                                  "entity": _QUESTION_TYPE.get(n.reason, "OTHER_IDENTIFIER")})
        busy.append((n.start, n.end))
        q += 1
    # Candidates the engine held in doubt and left in (not the ones it
    # settled on clinical grounds, which are decisions, not doubts).
    trace = getattr(res, "trace", None)
    for r in sorted(getattr(trace, "held", None) or [], key=lambda r: -r["score"]):
        if any(str(e).startswith(("clinical:", "document_clinical_use")) for e in r.get("evidence", [])):
            continue
        if r["score"] < -0.3 or any(r["start"] < b and a < r["end"] for a, b in busy):
            continue
        note["questions"].append({"id": f"q{q}", "start": r["start"], "end": r["end"],
                                  "kind": "tool_unsure", "reason": "held",
                                  "entity": contract.canonical_type(r["entity"])})
        busy.append((r["start"], r["end"]))
        q += 1
    note["questions"].sort(key=lambda x: x["start"])
    for f, fl in enumerate(rare_flags(sanitized, taken)):
        note["rare"].append({"id": f"f{f}", "kind": "condition", **fl})
    # A NEWSWORTHY EVENT is a quasi-identifier like a rare condition: the
    # reviewers decide, and removing the whole sentence is the suggestion.
    # It goes to the same "rare" decision as a rare
    # disease, so it is ruled on per patient and recorded.
    # The sentence is shown as a reminder (it may stay); the details that pin
    # the event down (its name, how many were hurt) are suggested for
    # removal and go unless both reviewers keep them.
    events = contract.newsworthy_events(sanitized)
    for kind, s, e in events:
        if any(a < e and s < b for a, b in taken):
            pass
        note["rare"].append({"id": f"f{len(note['rare'])}", "kind": "event", "event": kind,
                             "start": s, "end": e,
                             "also": ["a newsworthy event: the kind of event may stay; consider "
                                      "removing what pins it down"]})
    for what, s, e in contract.event_details(sanitized):
        if any(a < e and s < b for a, b in taken):
            continue
        note["rare"].append({"id": f"f{len(note['rare'])}", "kind": "event_detail", "event": what,
                             "start": s, "end": e,
                             "also": [f"the event's {'name' if what == 'name' else 'numbers'}: "
                                      "suggested for removal"]})
    return note, sanitized


# placeholder_for and merge_spans now live in inkguard/contract.py — the one
# place that maps "engine decision -> positions". Imported here so chert's
# own release rendering and the tests that reach them through chert keep
# working.
from inkguard.contract import merge_spans, placeholder_for  # noqa: E402,F401


def _render(sanitized: str, spans: list[dict], profile_is_lds: bool) -> str:
    """The released text: removals applied, then the Safe Harbor
    generalizations (ZIP, age 90+) unless the profile is LDS."""
    from inkguard import contract
    out, last = [], 0
    for sp in sorted(spans, key=lambda x: x["start"]):
        out.append(sanitized[last:sp["start"]])
        out.append(placeholder_for(sp, sanitized[sp["start"]:sp["end"]]))
        last = sp["end"]
    out.append(sanitized[last:])
    clean = "".join(out)
    if not profile_is_lds:
        clean = contract.apply_safe_harbor(clean)
    return clean


def prepare(notes_dir: Path, out_dir: Path, *, authorized_by: str, reviewers: list[str],
            batch_patients: int = DEFAULT_BATCH_PATIENTS, profile: str | None = None,
            quiet: bool = True) -> list[Path]:
    """Run the engine over every patient file and write the batches."""
    from inkguard import contract, __version__
    if len(reviewers) != 2 or _norm_name(reviewers[0]) == _norm_name(reviewers[1]):
        raise ReviewError("exactly two different reviewers are required")
    if not authorized_by.strip():
        raise ReviewError("the person authorizing the review must be named")
    if batch_patients < 1:
        raise ReviewError("a batch needs at least one patient")
    profile_name = "limited_data_set" if (profile or "").lower() in ("lds", "limited_data_set") \
        else "safe_harbor"
    lds = contract.profile_info(profile_name)["is_still_phi"]
    groups = _patients(notes_dir)
    aside = _not_included(notes_dir)
    if not groups:
        n = sum(len(v) for v in aside.values())
        raise ReviewError(f"no text notes found in {notes_dir}"
                          + (f" ({n} other file(s) are not read by this release)" if n else ""))
    in_batches = {key for key, _ in groups}
    chunks = [groups[i:i + batch_patients] for i in range(0, len(groups), batch_patients)]
    written: list[Path] = []
    for b, chunk in enumerate(chunks, 1):
        patients, page_notes, notes = [], [], []
        for p_index, (key, paths) in enumerate(chunk):
            texts = [_decode(p) for p in paths]
            results, _ctx = contract.process_patient(texts, patient_key=key,
                                                     profile=profile_name, mode="mask")
            note_ids = []
            for n_index, (path, text, res) in enumerate(zip(paths, texts, results)):
                nid = f"n{len(notes) + 1:05d}"
                payload, sanitized = note_payload(text, res, lds)
                # The release renders from these spans; it must reproduce
                # the engine's own output exactly, or the note is not ready.
                if payload["status"] == "ready":
                    if _render(sanitized, payload["removals"], lds) != res.clean_text:
                        payload.update(status="blocked",
                                       block_reason="engine output could not be reproduced",
                                       removals=[], generalized=[], kept=[], questions=[], rare=[])
                meta = {"id": nid, "patient": p_index, "position": n_index + 1,
                        "source": str(path.relative_to(notes_dir)), **payload}
                notes.append(meta)
                page_notes.append({**meta, "text": sanitized})
                note_ids.append(nid)
            patients.append({"index": p_index, "label": f"Patient {p_index + 1}",
                             "source_key": key, "notes": note_ids})
            if not quiet:
                print(f"\rbatch {b}/{len(chunks)}: patient {p_index + 1}/{len(chunk)}",
                      end="", file=sys.stderr)
        import secrets
        batch = {"kind": BATCH_KIND, "batch_version": BATCH_VERSION, "software_version": __version__,
                 "nonce": secrets.token_hex(8), "number": b, "of": len(chunks), "created_utc": _now(),
                 "profile": profile_name, "source_dir": str(notes_dir.resolve()),
                 "authorized_by": authorized_by.strip(),
                 "reviewers": [{"id": "R1", "name": reviewers[0].strip()},
                               {"id": "R2", "name": reviewers[1].strip()}],
                 "patients": patients,
                 # Notes WITHOUT text: offsets and types only.
                 "notes": notes,
                 # Files this release does not read, so none goes missing
                 # unnoticed: they are listed, set aside and not released.
                 "not_included": [x for key, _ in chunk for x in aside.get(key, [])]
                 + ([x for key, v in sorted(aside.items()) if key not in in_batches for x in v]
                    if b == 1 else [])}
        # The id covers EVERYTHING a reviewer decides on — every removal,
        # question and flag, the profile, the software and the people — plus
        # a fresh random part, and is keyed with the clinic's root key when
        # one is set. Removal ids are positions ("r2"): a decision file
        # from any other preparation, or a batch.json edited afterwards,
        # must never be read against them.
        batch_id, sealed = _batch_id(batch)
        batch = {"batch": batch_id, "sealed": sealed, **batch}
        batch_id = batch["batch"]
        bdir = out_dir / f"batch-{b:03d}"
        if bdir.exists() and any(bdir.iterdir()):
            raise ReviewError(f"{bdir} already holds a batch; prepare into an empty folder")
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "batch.json").write_text(json.dumps(batch, indent=1), encoding="utf-8")
        page_patients = [{"index": p["index"], "label": p["label"], "notes": p["notes"]}
                         for p in patients]
        for r in batch["reviewers"]:
            key = page_key(batch_id, r["id"]) if sealed else None
            page = _page({"mode": "review", "batch": batch_id, "number": b, "of": len(chunks),
                          "profile": profile_name, "authorized_by": batch["authorized_by"],
                          "reviewer": r, "patients": page_patients, "notes": page_notes,
                          "attestation": ATTESTATION, "seal": key.hex() if key else None})
            (bdir / f"review-reviewer-{r['id'][1]}.html").write_text(page, encoding="utf-8")
        written.append(bdir)
        if not quiet:
            print(file=sys.stderr)
    return written


# =====================================================================
# the page
# =====================================================================

# ---- offsets. Python counts characters; the page's JavaScript counts
# UTF-16 code units, where an emoji or other character beyond the Basic
# Multilingual Plane takes two. Offsets are converted at the boundary: to
# the page's count when a page is written, back when a page's file is read.
# Without this, a mark placed after an emoji would land on the wrong word.

def _astral(text: str) -> list[int]:
    return [i for i, ch in enumerate(text) if ord(ch) > 0xFFFF]


def to_page_offset(text: str, i: int) -> int:
    a = _astral(text)
    return i + bisect.bisect_left(a, i) if a else i


def from_page_offset(text: str | None, j: int) -> int:
    """A page offset back to a character offset. An offset between the two
    halves of a character moves past the character."""
    if text is None:
        return j
    a = _astral(text)
    if not a:
        return j
    ends = [p + k + 2 for k, p in enumerate(a)]     # page offset just after each such character
    return j - bisect.bisect_right(ends, j)


def _page_offsets(data: dict) -> dict:
    data = json.loads(json.dumps(data))           # a copy: the batch keeps character offsets
    for n in data.get("notes") or []:
        a = _astral(n.get("text") or "")
        if not a:
            continue
        for key in ("removals", "generalized", "kept", "questions", "rare", "items"):
            for x in n.get(key) or []:
                x["start"] += bisect.bisect_left(a, x["start"])
                x["end"] += bisect.bisect_left(a, x["end"])
    return data


def _page(data: dict) -> str:
    from inkguard import contract, __version__
    data = _page_offsets(data)
    data = {**data, "software_version": __version__,
            "placeholders": contract.placeholders(), "add_types": ADD_TYPES}
    # Every "<" inside the JSON is escaped, so no note text can close the
    # script element or open a comment inside it.
    blob = json.dumps(data, separators=(",", ":")).replace("<", "\\u003c")
    template = PAGE_TEMPLATE.read_text(encoding="utf-8")
    title = ("Doctor's review" if data["mode"] == "doctor" else "Review") + f" · batch {data['number']}"
    # The notes go in LAST, so no note text can be mistaken for a slot.
    return template.replace("__TITLE__", title).replace("__DATA__", blob)


# =====================================================================
# 2. DECISIONS — reading and checking what the pages exported
# =====================================================================

def _batch_basis(batch: dict) -> str:
    keys = ("kind", "batch_version", "software_version", "nonce", "number", "of", "created_utc",
            "profile", "source_dir", "authorized_by", "reviewers", "patients", "notes",
            "not_included")
    return json.dumps({k: batch.get(k) for k in keys}, sort_keys=True, separators=(",", ":"))


def _seal_key() -> bytes | None:
    """The clinic's root key (PHI_ROOT_KEY), derived for this purpose. None
    when no key is configured: an ephemeral key would not survive until the
    release, so the batch is then fingerprinted but not sealed."""
    from inkguard.security import KeyStore
    ks = KeyStore()
    return None if ks.ephemeral else ks.derive("two-reviewer-batch-v1")


def page_key(batch_id: str, who: str) -> bytes | None:
    """The key ONE page seals its exported file with (reviewer 1, reviewer 2
    or the doctor, for this batch only), derived from the clinic's root key.
    The page holds only this derived key, never the root key. A file edited
    outside its page no longer matches its seal. This makes editing
    detectable; it is not proof against someone who takes the key out of
    the page on purpose."""
    from inkguard.security import KeyStore
    ks = KeyStore()
    if ks.ephemeral:
        return None
    return hmac.new(ks.derive("review-page-file-v1"), f"{batch_id}:{who}".encode(),
                    hashlib.sha256).digest()


def seal_file(obj: dict, key: bytes) -> dict:
    """What a page exports: the file as one JSON string, and its HMAC."""
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return {"kind": obj.get("kind"), "sealed_payload": payload,
            "seal": hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()}


def _seal_ok(d: dict, key: bytes | None) -> bool:
    payload, seal = d.get("_payload"), d.get("_seal")
    if key is None or not isinstance(payload, str) or not isinstance(seal, str) \
            or not re.fullmatch(r"[0-9a-f]{64}", seal):
        return False
    try:
        mac = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(mac, seal)


def _batch_id(batch: dict) -> tuple[str, bool]:
    key = _seal_key()
    basis = _batch_basis(batch).encode()
    if key is None:
        return hashlib.sha256(basis).hexdigest()[:16], False
    return hmac.new(key, basis, hashlib.sha256).hexdigest()[:16], True


def load_batch(bdir: Path) -> dict:
    p = bdir / "batch.json"
    if not p.exists():
        raise ReviewError(f"{bdir} is not a review batch (no batch.json)")
    try:
        batch = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ReviewError("batch.json cannot be read") from None
    if not isinstance(batch, dict) or batch.get("kind") != BATCH_KIND:
        raise ReviewError("batch.json is not a review batch")
    # Whether a batch is sealed is decided by the clinic's key, never by the
    # file: a "sealed": false written into batch.json must not switch the
    # seal checks off.
    keyed = _seal_key() is not None
    if batch.get("sealed") and not keyed:
        raise ReviewError("this batch is sealed with the clinic's key; set PHI_ROOT_KEY to use it")
    if keyed and batch.get("sealed") is not True:
        raise ReviewError("this batch was prepared without the clinic's key, but a key is set now; "
                          "prepare it again so that it is sealed")
    expected = _batch_id(batch)[0]
    if not isinstance(batch.get("batch"), str) or not hmac.compare_digest(expected, batch["batch"]):
        raise ReviewError("batch.json was changed after it was prepared (or the key differs); "
                          "prepare the batch again")
    batch["sealed"] = keyed
    return batch


def _bad(p, why: str):
    raise ReviewError(f"{Path(p).name} is malformed: {why}")


_ANSWERS = {"remove", "fine", "ok", "unsure"}

# Feedback for the Inkguard maintainers: optional,
# short, from both reviewers and the doctor. The doctor edits what the
# reviewers wrote and decides what is shared; the release writes it as a
# file the clinic sends itself, after words from the notes are taken out.
FEEDBACK_ISSUES = {
    "over": "removed too much",
    "under": "missed something",
    "click": "something did not respond to a click",
    "keys": "keys did not do what was expected",
    "reading": "hard to read",
    "slow": "slow",
    "unclear": "instructions unclear",
}
NOTE_FLAGS = {"over": "removed too much here", "under": "missed something here",
              "page": "page problem here"}
FEEDBACK_TEXT_MAX = 4000


def _check_feedback(p, fb, where: str = "feedback") -> None:
    if fb is None:
        return
    if not isinstance(fb, dict):
        _bad(p, f"{where} is not a mapping")
    r = fb.get("rating")
    if r is not None and not (isinstance(r, int) and not isinstance(r, bool) and 1 <= r <= 5):
        _bad(p, f"{where}.rating is not 1 to 5")
    iss = fb.get("issues", [])
    if not isinstance(iss, list) or not all(isinstance(x, str) and x in FEEDBACK_ISSUES for x in iss):
        _bad(p, f"{where}.issues has an unknown entry")
    txt = fb.get("text", "")
    if not isinstance(txt, str) or len(txt) > FEEDBACK_TEXT_MAX:
        _bad(p, f"{where}.text is not text or is too long")


def _check_note_flags(p, nid: str, st: dict) -> None:
    f = st.get("fb")
    if f is None:
        return
    if not isinstance(f, dict) or not all(k in NOTE_FLAGS and isinstance(v, bool) for k, v in f.items()):
        _bad(p, f"{nid}.fb is malformed")


def _check_adds(p, nid: str, adds, types) -> None:
    if not isinstance(adds, list):
        _bad(p, f"{nid}.add has the wrong type")
    for a in adds:
        if not (isinstance(a, dict) and isinstance(a.get("start"), int)
                and isinstance(a.get("end"), int) and not isinstance(a.get("start"), bool)
                and not isinstance(a.get("end"), bool)
                and 0 <= a["start"] < a["end"] <= 10_000_000):
            _bad(p, f"{nid} has a mark with bad offsets")
        if a.get("type") not in types:
            _bad(p, f"{nid} has a mark of unknown type")


def _check_signature(p, d: dict) -> None:
    so = d.get("signed_off")
    if so is not None and not (isinstance(so, dict) and isinstance(so.get("at", ""), str)):
        _bad(p, "signed_off is not a signature")


def _check_seconds(p, nid: str, st: dict) -> None:
    sec = st.get("seconds", 0)
    if not isinstance(sec, (int, float)) or isinstance(sec, bool) or not math.isfinite(sec) or sec < 0:
        _bad(p, f"{nid}.seconds is not a time")


def _validate(p, d: dict) -> None:
    """Every field release reads from a REVIEWER's file, checked for type
    and range BEFORE anything is merged or written. A hand-edited or damaged
    file is refused whole."""
    _check_signature(p, d)
    _check_feedback(p, d.get("feedback"))
    notes = d.get("notes")
    if not isinstance(notes, dict):
        _bad(p, "notes is not a mapping")
    for nid, st in notes.items():
        if not isinstance(st, dict):
            _bad(p, f"{nid} is not a mapping")
        _check_note_flags(p, nid, st)
        for key, typ in (("keep", list), ("unsure", list), ("answers", dict), ("looks", dict)):
            if key in st and not isinstance(st[key], typ):
                _bad(p, f"{nid}.{key} has the wrong type")
        for key in ("keep", "unsure"):
            if not all(isinstance(k, str) for k in st.get(key, [])):
                _bad(p, f"{nid}.{key} holds something other than removal ids")
        if set(st.get("keep", [])) & set(st.get("unsure", [])):
            _bad(p, f"{nid} both keeps and doubts the same removal")
        _check_adds(p, nid, st.get("add", []), ADD_TYPES)
        if not all(isinstance(k, str) and v in _ANSWERS for k, v in st.get("answers", {}).items()):
            _bad(p, f"{nid} has an unknown answer")
        _check_seconds(p, nid, st)
        if st.get("confirmed_at") is not None and not isinstance(st.get("confirmed_at"), str):
            _bad(p, f"{nid}.confirmed_at is not a time stamp")


def _read_json(p) -> dict:
    """A page's exported file. Always UTF-8, which is what browsers write.
    The seal is taken ONLY from the page's wrapper; a plain file cannot
    carry one of its own."""
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewError(f"{Path(p).name} cannot be read: {exc}") from None
    if not isinstance(d, dict):
        _bad(p, "not a decisions object")
    sealed = None
    if "sealed_payload" in d:
        payload, seal = d.get("sealed_payload"), d.get("seal")
        if not isinstance(payload, str) or not isinstance(seal, str) or \
                set(d) - {"kind", "sealed_payload", "seal"}:
            _bad(p, "the seal is damaged")
        try:
            d = json.loads(payload)
        except ValueError:
            _bad(p, "the sealed part cannot be read")
        if not isinstance(d, dict):
            _bad(p, "not a decisions object")
        sealed = (payload, seal)
    if any(str(k).startswith("_") for k in d):
        _bad(p, "has fields no review page writes")
    if sealed:
        d["_payload"], d["_seal"] = sealed
    return d


def load_decisions(batch: dict, paths: list[Path]) -> dict[str, dict]:
    """This batch's two reviewer files, checked. Returns {"R1": ..., "R2": ...}.
    `paths` may hold files for other batches too; only this batch's are used."""
    by_id: dict[str, dict] = {}
    names = {r["id"]: r["name"] for r in batch["reviewers"]}
    for p in paths:
        d = _read_json(p)
        if d.get("kind") != DECISIONS_KIND or d.get("role") != "reviewer":
            continue
        if d.get("batch") != batch["batch"]:
            continue
        rid = d.get("reviewer")
        if rid not in names or _norm_name(d.get("reviewer_name")) != _norm_name(names[rid]):
            raise ReviewError(f"{Path(p).name}: reviewer is not one this batch authorized")
        if rid in by_id:
            raise ReviewError(f"two files from {names[rid]}; the other reviewer's file is needed")
        _validate(p, d)
        d["_sha256"] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        # A sealed batch's pages seal every file they export. A file whose
        # seal does not match was changed outside its page: an integrity
        # violation, shown to the doctor and never released.
        d["_seal_ok"] = (not batch.get("sealed")) or _seal_ok(d, page_key(batch["batch"], rid))
        by_id[rid] = d
    missing = [names[r] for r in ("R1", "R2") if r not in by_id]
    if missing:
        raise ReviewError(f"batch {batch['number']}: no decisions file from " + " or ".join(missing))
    return by_id


def _unfinished(batch: dict, d: dict) -> list[str]:
    """Why this reviewer's file cannot be used yet (empty = ready)."""
    probs = []
    so = d.get("signed_off") or {}
    if not so.get("at") or _norm_name(so.get("typed_name")) != _norm_name(d.get("reviewer_name")):
        probs.append("has not signed off the batch")
    elif any(isinstance(st, dict) and str(st.get("confirmed_at") or "") > str(so["at"])
             for st in (d.get("notes") or {}).values()):
        # ISO-8601 UTC stamps compare as text. A note confirmed after the
        # signature was changed after it: sign off again.
        probs.append("changed notes after signing off")
    missing = looks = unanswered = 0
    for n in batch["notes"]:
        if n["status"] != "ready":
            continue
        st = (d.get("notes") or {}).get(n["id"]) or {}
        if not st.get("confirmed_at"):
            missing += 1
            continue
        lk = st.get("looks") or {}
        if not (lk.get("see") and lk.get("blackout")):
            looks += 1
        ans = st.get("answers") or {}
        if any(x["id"] not in ans for x in n["questions"] + n["rare"]):
            unanswered += 1
    if missing:
        probs.append(f"has not confirmed {missing} note(s)")
    if looks:
        probs.append(f"skipped a look on {looks} note(s)")
    if unanswered:
        probs.append(f"left questions unanswered on {unanswered} note(s)")
    return probs


def _ready_reviews(batch: dict, dec: dict[str, dict]) -> None:
    names = {r["id"]: r["name"] for r in batch["reviewers"]}
    problems = {rid: _unfinished(batch, d) for rid, d in dec.items()}
    if any(problems.values()):
        msg = "; ".join(f"{names[r]} " + ", ".join(p) for r, p in problems.items() if p)
        raise ReviewError(f"batch {batch['number']} not ready: {msg}")


# =====================================================================
# 3. THE THREE-PERSON VOTE
# =====================================================================
#
# Every span anyone could remove is an ITEM with one vote per reviewer:
# "remove", "keep" or "unsure". A reviewer's vote is what their own final
# view of the note shows: a tool removal they did not keep is a remove
# vote; a word they did not mark is a keep vote.
#
#   both reviewers remove      -> removed; the doctor cannot keep it
#   both reviewers keep        -> kept, unless the doctor removes it
#   anything else              -> the doctor decides; removed until then
#
# The UNSURE BUCKET is every item a reviewer called unsure, and every tool
# removal exactly one reviewer wanted to keep. The doctor must decide each
# of them before signing. Removing is always open to the doctor.

_VOTE_ANSWER = {"remove": "remove", "fine": "keep", "ok": "keep", "unsure": "unsure"}

# A WORD for marking: letters and digits, joined by inner punctuation, so
# "415-555-0199", "o'brien" and "j.smith@x.org" are one word and a full stop
# after a name is not part of it. The page snaps every click and drag to
# these boundaries; the release snaps again, so two reviewers marking the
# same name always produce the same span.
_MARK_WORD = re.compile(r"[\w@]+(?:[\-'’./][\w@]+)*")


def snap(text: str, s: int, e: int) -> tuple[int, int] | None:
    """Widen [s, e) to whole words; None if it covers no word."""
    if text is None:
        return s, e
    s, e = max(0, min(s, len(text))), max(0, min(e, len(text)))
    # Words never contain white space, so the search starts and ends at the
    # white space around the span: a long joined token is never cut.
    lo_pos, hi_pos = s, e
    while lo_pos > 0 and not text[lo_pos - 1].isspace():
        lo_pos -= 1
    while hi_pos < len(text) and not text[hi_pos].isspace():
        hi_pos += 1
    lo = hi = None
    for m in _MARK_WORD.finditer(text, lo_pos, hi_pos):
        if m.end() <= s or m.start() >= e:
            continue
        lo = m.start() if lo is None else min(lo, m.start())
        hi = m.end() if hi is None else max(hi, m.end())
    return (lo, hi) if lo is not None else None


def _stretch(s: int, e: int, n: dict) -> tuple[int, int]:
    """A mark that cuts into a value Safe Harbor generalizes ("93-year-old",
    a ZIP) takes the whole value: a partial mark leaves digits the
    generalization can no longer recognise."""
    for g in n.get("generalized", []):
        if s < g["end"] and g["start"] < e:
            s, e = min(s, g["start"]), max(e, g["end"])
    return s, e


def note_items(n: dict, st1: dict, st2: dict, text: str | None = None) -> tuple[list[dict], dict]:
    """The items of one note with both reviewers' votes, and the counts the
    capture–recapture estimate uses. Deterministic: the doctor's page and
    the release build the same keys from the same files. `text` (the note)
    snaps reviewer marks to whole words."""
    st = {"R1": st1 or {}, "R2": st2 or {}}
    items: list[dict] = []

    def rules(it: dict) -> dict:
        v = it["votes"]
        it.setdefault("certain", False)
        it["locked"] = (v["R1"] == "remove" and v["R2"] == "remove") or it["certain"]
        it["default"] = ("keep" if (v["R1"] == "keep" and v["R2"] == "keep" and not it["certain"])
                         else "remove")
        it["bucket"] = ("unsure" in v.values()
                        or (it["source"] == "engine" and list(v.values()).count("keep") == 1))
        return it

    for r in n["removals"]:
        votes, tampered = {}, []
        for rid in ("R1", "R2"):
            s = st[rid]
            votes[rid] = ("keep" if r["id"] in (s.get("keep") or []) else
                          "unsure" if r["id"] in (s.get("unsure") or []) else "remove")
            if votes[rid] == "keep" and r.get("certain"):
                # The page never offers "keep" here: this file was changed
                # outside it. Counted as an integrity violation; the vote
                # counts as remove.
                tampered.append(rid)
                votes[rid] = "remove"
        items.append(rules({"key": r["id"], "start": r["start"], "end": r["end"],
                            "entity": r["entity"], "layer": r["layer"], "shows": r["shows"],
                            "source": "engine", "votes": votes, "certain": bool(r.get("certain")),
                            "tampered": tampered}))
    for q in n["questions"]:
        votes = {rid: _VOTE_ANSWER.get((st[rid].get("answers") or {}).get(q["id"]), "keep")
                 for rid in ("R1", "R2")}
        if votes["R1"] == "keep" and votes["R2"] == "keep":
            continue                                    # both said fine: nothing to remove
        s, e = _stretch(q["start"], q["end"], n)
        items.append(rules({"key": q["id"], "start": s, "end": e, "entity": q["entity"],
                            "layer": "review", "source": "question", "reason": q["reason"],
                            "votes": votes}))
    for f in n["rare"]:
        kind = f.get("kind", "condition")
        # The details that pin an event down go unless BOTH reviewers keep
        # them. The event sentence itself, like a rare
        # condition, is a reminder: it is removed only if a reviewer says so.
        detail = kind == "event_detail"
        votes = {rid: _VOTE_ANSWER.get((st[rid].get("answers") or {}).get(f["id"]),
                                       "remove" if detail else "keep")
                 for rid in ("R1", "R2")}
        if detail:
            if list(votes.values()) == ["keep", "keep"]:
                continue
        elif kind == "event":
            if list(votes.values()) == ["keep", "keep"]:
                continue
        elif "unsure" not in votes.values():
            continue                                    # checked by both: an advisory, not a removal
        items.append(rules({"key": f["id"], "start": f["start"], "end": f["end"],
                            "entity": "EVENT" if kind.startswith("event") else "OTHER_IDENTIFIER",
                            "layer": "review", "source": "rare", "votes": votes}))
    # Reviewers' own marks, clustered where they overlap (across reviewers).
    marks = []
    for rid in ("R1", "R2"):
        for i, a in enumerate(st[rid].get("add") or []):
            sn = snap(text, from_page_offset(text, int(a["start"])), from_page_offset(text, int(a["end"])))
            if sn is None:
                continue                                 # whitespace only: nothing to remove
            s, e = _stretch(sn[0], sn[1], n)
            marks.append({"rid": rid, "i": i, "start": s, "end": e, "type": a["type"],
                          "propagated": bool(a.get("propagated_from"))})
    marks.sort(key=lambda m: (m["start"], -m["end"], m["rid"], m["i"]))
    clusters: list[dict] = []
    for m in marks:
        if clusters and m["start"] < clusters[-1]["end"]:
            c = clusters[-1]
            c["end"] = max(c["end"], m["end"])
            c["marks"].append(m)
        else:
            clusters.append({"start": m["start"], "end": m["end"], "marks": [m]})
    finds = {"R1": 0, "R2": 0, "both": 0}
    for k, c in enumerate(clusters):
        votes, types = {}, set()
        for rid in ("R1", "R2"):
            mine = [m for m in c["marks"] if m["rid"] == rid]
            votes[rid] = ("keep" if not mine else
                          "unsure" if all(m["type"] == "unsure" for m in mine) else "remove")
            types |= {m["type"] for m in mine if m["type"] != "unsure"}
        ents = {ADD_TYPES[t] for t in types} or {"OTHER_IDENTIFIER"}
        entity = "PERSON" if "PERSON" in ents else (ents.pop() if len(ents) == 1 else "OTHER_IDENTIFIER")
        who = sorted({m["rid"] for m in c["marks"]})
        # Keyed by the span, not by position in the list: a decision can
        # never slide onto a different word.
        items.append(rules({"key": f"m{c['start']}-{c['end']}", "start": c["start"], "end": c["end"],
                            "entity": entity,
                            "layer": "review", "source": "mark", "who": who, "votes": votes,
                            "propagated": all(m["propagated"] for m in c["marks"])}))
        # Independent finds, for the estimate: marks a reviewer made in THIS
        # note (not copied here from another note of the patient).
        own = {rid for m in c["marks"] if not m["propagated"] for rid in [m["rid"]]}
        for rid in own:
            finds[rid] += 1
        if own == {"R1", "R2"}:
            finds["both"] += 1
    return items, finds


def final_spans(n: dict, items: list[dict], doctor_note: dict,
                text: str | None = None) -> tuple[list[dict], dict]:
    """Apply the doctor's rulings and marks. Returns (spans to remove, the
    outcome of each item key)."""
    rulings = (doctor_note or {}).get("rulings") or {}
    if set(rulings) - {it["key"] for it in items}:
        raise ReviewError(f"{n['id']}: the doctor's file has decisions for items these reviews do "
                          "not have; it was made against other files")
    outcome, spans = {}, []
    for it in items:
        r = rulings.get(it["key"])
        if r == "keep" and it["locked"]:
            raise ReviewError(f"{n['id']}: the doctor's file keeps something both reviewers removed "
                              "or the tool is certain of")
        if it["bucket"] and r not in ("keep", "remove"):
            raise ReviewError(f"{n['id']}: an item in the unsure bucket was not decided by the doctor")
        fate = r if r in ("keep", "remove") else it["default"]
        outcome[it["key"]] = fate
        if fate == "remove":
            spans.append({"start": it["start"], "end": it["end"], "entity": it["entity"],
                          "layer": it["layer"]})
    for a in (doctor_note or {}).get("add") or []:
        sn = snap(text, from_page_offset(text, int(a["start"])), from_page_offset(text, int(a["end"])))
        if sn is None:
            continue
        s, e = _stretch(sn[0], sn[1], n)
        spans.append({"start": s, "end": e, "entity": ADD_TYPES[a["type"]], "layer": "review"})
    return spans, outcome


def accuracy(rows: list[tuple[list[dict], dict, int]]) -> dict:
    """How the tool and each reviewer compare with the FINAL version the
    three people agreed. rows: (items, outcome, doctor marks) per note.
    Doctor marks count as identifiers everyone before them missed."""
    t = {"final": 0, "tool_removed": 0, "tool_confirmed": 0, "tool_found": 0, "tool_asked": 0,
         "agree": 0, "items": 0, "doctor_marks": 0,
         **{f"{r}_{k}": 0 for r in ("R1", "R2")
            for k in ("removed", "right", "changes", "upheld", "referred")}}
    for items, outcome, dmarks in rows:
        for it in items:
            fin = outcome[it["key"]] == "remove"
            t["items"] += 1
            t["agree"] += it["votes"]["R1"] == it["votes"]["R2"]
            if it["source"] == "engine":
                t["tool_removed"] += 1
                t["tool_confirmed"] += fin
            if fin:
                t["final"] += 1
                t["tool_found"] += it["source"] == "engine"
                t["tool_asked"] += it["source"] == "question"
            tool_action = "remove" if it["source"] == "engine" else "keep"
            for rid in ("R1", "R2"):
                v = it["votes"][rid]
                rem = v in ("remove", "unsure")
                t[f"{rid}_removed"] += rem
                t[f"{rid}_right"] += rem and fin
                # The reviewer's OWN changes to what the tool did, and how
                # many the final version upheld. Leaving the tool's work
                # alone is not a change, so it earns nothing here.
                if v == "unsure":
                    t[f"{rid}_referred"] += 1
                elif v != tool_action:
                    t[f"{rid}_changes"] += 1
                    t[f"{rid}_upheld"] += (v == "remove") == fin
        t["final"] += dmarks
        t["doctor_marks"] += dmarks

    def pct(a, b):
        return round(100 * a / b, 1) if b else None
    return {"final_identifiers": t["final"],
            "tool": {"removed": t["tool_removed"], "precision": pct(t["tool_confirmed"], t["tool_removed"]),
                     "recall": pct(t["tool_found"], t["final"]),
                     "asked_about_but_left_in": t["tool_asked"]},
            **{f"reviewer_{rid[1]}": {"removed": t[f"{rid}_removed"],
                                      "precision": pct(t[f"{rid}_right"], t[f"{rid}_removed"]),
                                      "recall": pct(t[f"{rid}_right"], t["final"]),
                                      "changes": t[f"{rid}_changes"],
                                      "changes_upheld": pct(t[f"{rid}_upheld"], t[f"{rid}_changes"]),
                                      "referred": t[f"{rid}_referred"]}
               for rid in ("R1", "R2")},
            "reviewers_agreed": pct(t["agree"], t["items"]),
            "doctor_marks": t["doctor_marks"],
            "note": ("Measured against the final version the three reviewers produced: the best "
                     "reference available, not an absolute truth. A reviewer's version includes "
                     "the tool's removals they left alone; their own changes are counted "
                     "separately. What none of them caught is estimated separately "
                     "(capture–recapture).")}


# =====================================================================
# 4. REVIEW QUALITY AND INTEGRITY
# =====================================================================
#
# Short, factual notes for the doctor (a note confirmed in less than the
# expected reading time, a word the tool read as a name that a reviewer
# kept), and three stops that no one can release past: a decisions file
# that keeps an identifier the tool marks as certain (the page never offers
# that), a file that does not match the file its page saved, and most notes
# confirmed in less than the expected reading time. A stop writes an
# integrity alert with reason codes and counts only: no names, no note
# text. Every message is worded as a fact about a file, never about a
# person.

READING_WORDS_PER_SECOND = 15      # 900 words a minute: faster than careful reading
TOO_FAST_SHARE = 0.25              # more than a quarter of a reviewer's notes
MIN_NOTES_FOR_SPEED_RULE = 4


def min_seconds(text: str) -> float:
    return max(3.0, len(_MARK_WORD.findall(text)) / READING_WORDS_PER_SECOND)


def review_quality(batch: dict, dec: dict[str, dict], texts: dict[str, str]) -> dict:
    per, alerts, violations, per_note = {}, [], [], {}
    ready = [n for n in batch["notes"] if n["status"] == "ready"]
    for rid, label in (("R1", "Reviewer 1"), ("R2", "Reviewer 2")):
        too_fast = zero = kept = kept_names = unsure = tampered = 0
        for n in ready:
            st = dec[rid]["notes"].get(n["id"]) or {}
            sec = float(st.get("seconds") or 0)
            if sec < min_seconds(texts[n["id"]]):
                too_fast += 1
                per_note.setdefault(n["id"], []).append(
                    f"{label}: {sec:.0f} s on this note (expected reading time about "
                    f"{min_seconds(texts[n['id']]):.0f} s).")
            zero += sec == 0
            keeps = set(st.get("keep") or [])
            by_id = {r["id"]: r for r in n["removals"]}
            for k in keeps:
                r = by_id.get(k)
                if r is None:
                    continue
                kept += 1
                if r.get("certain"):
                    tampered += 1
                elif r["entity"] == "PERSON":
                    kept_names += 1
                    per_note.setdefault(n["id"], []).append(
                        f"{label} kept a word the tool read as a name.")
            unsure += len(st.get("unsure") or []) + sum(
                1 for v in (st.get("answers") or {}).values() if v == "unsure") + sum(
                1 for a in (st.get("add") or []) if a.get("type") == "unsure")
        share = too_fast / len(ready) if ready else 0.0
        per[rid] = {"notes": len(ready), "too_fast": too_fast, "too_fast_share": round(share, 3),
                    "no_time": zero, "kept": kept, "kept_names": kept_names, "unsure": unsure,
                    "kept_certain": tampered}
        if not dec[rid].get("_seal_ok", True):
            violations.append({"reason": "file_mismatch", "reviewer": label, "count": 1})
        if tampered:
            violations.append({"reason": "kept_certain_identifier", "reviewer": label,
                               "count": tampered})
        if len(ready) >= MIN_NOTES_FOR_SPEED_RULE and share > TOO_FAST_SHARE:
            violations.append({"reason": "reading_time", "reviewer": label,
                               "count": too_fast, "share": round(share, 3)})
        elif too_fast:
            alerts.append(f"{label} confirmed {_n(too_fast, 'note')} in less than the expected "
                          "reading time.")
        if kept_names:
            alerts.append(f"{label} kept {_n(kept_names, 'word')} the tool read as a name.")
    return {"reviewers": per, "alerts": alerts, "violations": violations, "per_note": per_note}


_VIOLATION_TEXT = {
    "kept_certain_identifier": "file keeps an identifier the tool marks as certain",
    "reading_time": "most notes were confirmed in less than the expected reading time",
    "file_mismatch": "file does not match the file its review page saved",
}


def integrity_alert(batch: dict, quality: dict) -> dict:
    """What a hard stop leaves behind: no names, no note text."""
    from inkguard import __version__
    return {"kind": "phi-review-integrity-alert", "batch": batch["batch"],
            "batch_number": batch["number"], "software_version": __version__,
            "created_utc": _now(),
            "violations": [{"reason": v["reason"], "reviewer_role": v["reviewer"].lower(),
                            "count": v["count"], **({"share": v["share"]} if "share" in v else {})}
                           for v in quality["violations"]],
            "note": ("Automatic checks stopped this release before anything was written. "
                     "Reason codes and counts only; no names, no note text.")}


# =====================================================================
# 5. THE DOCTOR'S PAGE — full visibility, the unsure bucket, master control
# =====================================================================

DOCTOR_KIND = "phi-review-doctor"
RELEASED_MARKER = "released.json"
DOCTOR_ATTESTATION = ("I authorized this review. I decided every item in the unsure bucket "
                      "myself, and I stand behind the final version of these notes.")


def _source_text(batch: dict, n: dict) -> str:
    src = Path(batch["source_dir"])
    path = (src / n["source"]).resolve()
    if src.resolve() not in path.parents:
        raise ReviewError(f"batch.json points outside the notes folder ({n['id']})")
    text = _sanitized(_decode(path))
    if _sha(text) != n["sha256"]:
        raise ReviewError(f"a source note changed since the batch was prepared ({n['id']})")
    return text


def doctor(batch_dirs: list[Path], decision_paths: list[Path], out: Path) -> dict:
    """One page for the authorizing doctor across any number of batches.
    Both reviewers must have finished and signed each batch."""
    batches = [load_batch(Path(b)) for b in batch_dirs]
    if not batches:
        raise ReviewError("no batches given")
    auth = {_norm_name(b["authorized_by"]) for b in batches}
    if len(auth) != 1:
        raise ReviewError("these batches were authorized by different people; one doctor page "
                          "covers one person's batches")
    if len({b["batch"] for b in batches}) != len(batches):
        raise ReviewError("the same batch was given twice")
    page_batches, patients, notes = [], [], []
    open_items = 0
    cr_finds = {"R1": 0, "R2": 0, "both": 0}
    for b in batches:
        dec = load_decisions(b, decision_paths)
        _ready_reviews(b, dec)
        texts = {n["id"]: _source_text(b, n) for n in b["notes"] if n["status"] == "ready"}
        quality = review_quality(b, dec, texts)
        page_batches.append({"batch": b["batch"], "number": b["number"], "of": b["of"],
                             "profile": b["profile"], "reviewers": b["reviewers"],
                             "reviewer_files": {rid: dec[rid]["_sha256"] for rid in ("R1", "R2")},
                             "feedback": {rid: dec[rid].get("feedback") for rid in ("R1", "R2")},
                             "quality": {k: quality[k] for k in ("reviewers", "alerts", "violations")}})
        base = len(patients)
        for p in b["patients"]:
            patients.append({"index": base + p["index"], "label": f"Batch {b['number']} · {p['label']}",
                             "batch": b["batch"], "notes": [f"{b['batch']}:{nid}" for nid in p["notes"]]})
        for n in b["notes"]:
            entry = {"id": f"{b['batch']}:{n['id']}", "batch": b["batch"], "nid": n["id"],
                     "patient": base + n["patient"], "position": n["position"], "status": n["status"],
                     "block_reason": n.get("block_reason", ""), "generalized": n["generalized"],
                     "kept": n["kept"], "rare": n["rare"], "questions": n["questions"]}
            if n["status"] == "ready":
                text = texts[n["id"]]
                items, finds = note_items(n, dec["R1"]["notes"].get(n["id"]),
                                          dec["R2"]["notes"].get(n["id"]), text)
                for k in cr_finds:
                    cr_finds[k] += finds[k]
                open_items += sum(1 for it in items if it["bucket"])
                entry.update(text=text, items=items, alerts=quality["per_note"].get(n["id"], []),
                             fb={rid: (dec[rid]["notes"].get(n["id"]) or {}).get("fb")
                                 for rid in ("R1", "R2")},
                             seconds={rid: (dec[rid]["notes"].get(n["id"]) or {}).get("seconds", 0)
                                      for rid in ("R1", "R2")})
            else:
                entry.update(text="", items=[])
            notes.append(entry)
    b0 = batches[0]
    doc_key = page_key("+".join(b["batch"] for b in batches), "DOC")
    page = _page({"mode": "doctor", "batch": "+".join(b["batch"] for b in batches),
                  "number": b0["number"], "of": b0["of"], "profile": b0["profile"],
                  "authorized_by": b0["authorized_by"],
                  "reviewer": {"id": "DOC", "name": b0["authorized_by"]},
                  "batches": page_batches, "patients": patients, "notes": notes,
                  "estimate": capture_recapture(cr_finds["R1"], cr_finds["R2"], cr_finds["both"]),
                  "attestation": DOCTOR_ATTESTATION, "seal": doc_key.hex() if doc_key else None,
                  "not_included": sum(1 for b in batches for x in b.get("not_included") or []
                                      if not x.get("system"))})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return {"page": str(out), "batches": len(batches), "notes": len(notes),
            "unsure_bucket": open_items}


def load_doctor(path: Path, batch: dict, dec: dict[str, dict]) -> dict[str, dict]:
    """The doctor's decisions for ONE batch, checked: signed by the person
    who authorized it, made against exactly these two reviewer files, and
    no ruling changed after the signature."""
    d = _read_json(path)
    if d.get("kind") != DOCTOR_KIND or d.get("role") != "doctor":
        raise ReviewError(f"{Path(path).name} is not the doctor's decisions file")
    pages = d.get("pages")
    sec = (d.get("batches") or {}).get(batch["batch"])
    if not isinstance(sec, dict) or not (isinstance(pages, str) and batch["batch"] in pages.split("+")):
        raise ReviewError(f"the doctor's file does not cover batch {batch['number']}")
    if batch.get("sealed") and not _seal_ok(d, page_key(pages, "DOC")):
        raise ReviewError("the doctor's file does not match the file the doctor's page saved; "
                          "please save it again from the page")
    so = d.get("signed_off")
    if not isinstance(so, dict) or not isinstance(so.get("at"), str) or \
            _norm_name(so.get("typed_name")) != _norm_name(batch["authorized_by"]):
        raise ReviewError("the doctor's file is not signed by the person who authorized the review")
    files = sec.get("reviewer_files") or {}
    for rid in ("R1", "R2"):
        if files.get(rid) != dec[rid]["_sha256"]:
            raise ReviewError(f"the doctor reviewed a different version of reviewer {rid[1]}'s file")
    notes = sec.get("notes")
    if not isinstance(notes, dict):
        _bad(path, "notes is not a mapping")
    vendor = d.get("vendor")
    if vendor is not None:
        if not isinstance(vendor, dict) or not isinstance(vendor.get("share", False), bool):
            _bad(path, "vendor (the feedback for the maintainers) is malformed")
        _check_feedback(path, vendor, "vendor")
        rv = vendor.get("reviewers", {})
        if not isinstance(rv, dict) or not all(
                isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("include", True), bool)
                and isinstance(v.get("text", ""), str) and len(v.get("text", "")) <= FEEDBACK_TEXT_MAX
                for k, v in rv.items()):
            _bad(path, "vendor.reviewers is malformed")
    for nid, st in notes.items():
        if not isinstance(st, dict):
            _bad(path, f"{nid} is not a mapping")
        _check_note_flags(path, nid, st)
        rulings, when = st.get("rulings", {}), st.get("decided_at", {})
        if not isinstance(rulings, dict) or not isinstance(when, dict) or not all(
                isinstance(k, str) and v in ("keep", "remove") for k, v in rulings.items()):
            _bad(path, f"{nid} has a malformed ruling")
        if any(not isinstance(when.get(k), str) or when[k] > so["at"] for k in rulings):
            raise ReviewError("the doctor changed a decision after signing; sign again")
        _check_adds(path, nid, st.get("add", []), {k for k in ADD_TYPES if k != "unsure"})
        _check_seconds(path, nid, st)
        if not isinstance(st.get("withhold", False), bool):
            _bad(path, f"{nid}.withhold is not yes or no")
        if any(isinstance(a, dict) and isinstance(a.get("at"), str) and a["at"] > so["at"]
               for a in st.get("add", [])):
            raise ReviewError("the doctor marked something after signing; sign again")
    return {"notes": notes, "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "signed_at": so["at"], "vendor": vendor}


# =====================================================================
# 6. RELEASE
# =====================================================================

def capture_recapture(n1: int, n2: int, m: int) -> dict:
    """Chapman's estimator of the total number of findable items from two
    independent reviews: n1 and n2 found, m found by both. Returns the
    estimate of items BOTH reviewers missed, with an approximate 95%
    interval. Assumes every item was equally findable; items hard to see
    for everyone make the real number higher."""
    seen = n1 + n2 - m
    if n1 == 0 and n2 == 0:
        return {"estimable": False, "found_by_either": 0,
                "note": "Neither reviewer found anything the tool had missed, so there is "
                        "nothing to estimate from."}
    if m < 3:
        # With fewer than three shared finds the estimate swings wildly
        # (and reads falsely exact when the counts are tiny).
        return {"estimable": False, "found_by_reviewer_1": n1, "found_by_reviewer_2": n2,
                "found_by_both": m, "found_by_either": seen,
                "note": f"Too few items found by both reviewers ({m}) to estimate what both "
                        "missed; larger batches give a usable estimate."}
    total = (n1 + 1) * (n2 + 1) / (m + 1) - 1
    var = (n1 + 1) * (n2 + 1) * (n1 - m) * (n2 - m) / ((m + 1) ** 2 * (m + 2))
    sd = math.sqrt(max(var, 0.0))
    missed = max(0.0, total - seen)
    lo = max(0.0, total - 1.96 * sd - seen)
    hi = max(0.0, total + 1.96 * sd - seen)
    return {"estimable": True, "found_by_reviewer_1": n1, "found_by_reviewer_2": n2,
            "found_by_both": m, "found_by_either": seen,
            "estimated_missed_by_both": round(missed, 1),
            "interval_95": [round(lo, 1), round(hi, 1)],
            "note": ("Few shared finds make this rough." if m < 8 else "")}


def _shapes_left(clean: str, lds: bool) -> dict:
    """The final inspection at release — identifier shapes still present in
    the released text — through the contract."""
    from inkguard import contract
    return contract.shapes_left(clean, lds)


def _release_batch(batch: dict, dec: dict, doc: dict, texts: dict[str, str]) -> dict:
    """Everything for one batch, computed in memory. Nothing is written."""
    lds = batch["profile"] == "limited_data_set"
    outputs, rows = [], []
    acc_rows = []
    counts = {"notes": 0, "released": 0, "blocked_by_engine": 0, "held_after_review": 0,
              "engine_removals": 0, "kept_in_final": 0, "bucket_items": 0,
              "bucket_kept_by_doctor": 0, "bucket_removed_by_doctor": 0,
              "doctor_changed_default": 0, "doctor_marks": 0, "doctor_opened": 0,
              "questions": 0, "rare_flags": 0, "propagated_marks": 0,
              "unsure_R1": 0, "unsure_R2": 0, "unsure_both": 0, "withheld_by_doctor": 0,
              "set_aside_files": 0}
    finds = {"R1": 0, "R2": 0, "both": 0}
    seconds = {"R1": [], "R2": [], "DOC": []}
    learning = {"questions": {}, "tool_missed": {}, "tool_over_removed": {}}
    note_facts: dict[str, dict] = {}
    sensitive: set[str] = set()          # in memory only: every string anyone removed or flagged
    for n in batch["notes"]:
        counts["notes"] += 1
        rel_out = f"patient-{n['patient'] + 1:04d}/note-{n['position']:03d}.txt"
        if n["status"] != "ready":
            counts["blocked_by_engine"] += 1
            rows.append(f"{rel_out},{_csv(n['source'])},not released: blocked by the engine")
            continue
        text = texts[n["id"]]
        st1, st2 = dec["R1"]["notes"].get(n["id"]) or {}, dec["R2"]["notes"].get(n["id"]) or {}
        dn = doc["notes"].get(n["id"]) or {}
        items, f = note_items(n, st1, st2, text)
        for k in finds:
            finds[k] += f[k]
        spans, outcome = final_spans(n, items, dn, text)
        seconds["R1"].append(float(st1.get("seconds") or 0))
        seconds["R2"].append(float(st2.get("seconds") or 0))
        seconds["DOC"].append(float(dn.get("seconds") or 0))
        counts["doctor_opened"] += bool(dn.get("opened"))
        counts["questions"] += len(n["questions"])
        counts["rare_flags"] += len(n["rare"])
        dmarks = len(dn.get("add") or [])
        counts["doctor_marks"] += dmarks
        rulings = dn.get("rulings") or {}
        for it in items:
            v = it["votes"]
            fin = outcome[it["key"]]
            counts["engine_removals"] += it["source"] == "engine"
            counts["kept_in_final"] += fin == "keep" and it["source"] == "engine"
            counts["unsure_R1"] += v["R1"] == "unsure"
            counts["unsure_R2"] += v["R2"] == "unsure"
            counts["unsure_both"] += v["R1"] == "unsure" and v["R2"] == "unsure"
            counts["propagated_marks"] += bool(it.get("propagated"))
            if it["bucket"]:
                counts["bucket_items"] += 1
                counts["bucket_kept_by_doctor"] += fin == "keep"
                counts["bucket_removed_by_doctor"] += fin == "remove"
            elif it["key"] in rulings and rulings[it["key"]] != it["default"]:
                counts["doctor_changed_default"] += 1
            # LEARNING (no text): how each kind of item ended.
            if it["source"] == "question":
                q = learning["questions"].setdefault(it["reason"], {"asked": 0, "removed": 0,
                                                                     "reviewers_agreed": 0})
                q["asked"] += 1
                q["removed"] += fin == "remove"
                q["reviewers_agreed"] += v["R1"] == v["R2"]
            if it["source"] == "mark" and fin == "remove":
                learning["tool_missed"][it["entity"]] = learning["tool_missed"].get(it["entity"], 0) + 1
            if it["source"] == "engine" and fin == "keep":
                k = f"{it['entity']}/{it['layer']}"
                learning["tool_over_removed"][k] = learning["tool_over_removed"].get(k, 0) + 1
        for a in dn.get("add") or []:
            e = ADD_TYPES[a["type"]]
            learning["tool_missed"][e] = learning["tool_missed"].get(e, 0) + 1
        acc_rows.append((items, outcome, dmarks))
        facts = {"tool_over_removed": {}, "tool_missed": {}}
        for it in items:
            sensitive.add(text[it["start"]:it["end"]])
            fin = outcome[it["key"]]
            if it["source"] == "engine" and fin == "keep":
                k = f"{it['entity']}/{it['layer']}"
                facts["tool_over_removed"][k] = facts["tool_over_removed"].get(k, 0) + 1
            elif it["source"] != "engine" and fin == "remove":
                facts["tool_missed"][it["entity"]] = facts["tool_missed"].get(it["entity"], 0) + 1
        for a in dn.get("add") or []:
            sn = snap(text, from_page_offset(text, int(a["start"])), from_page_offset(text, int(a["end"])))
            if sn:
                sensitive.add(text[sn[0]:sn[1]])
            e = ADD_TYPES[a["type"]]
            facts["tool_missed"][e] = facts["tool_missed"].get(e, 0) + 1
        for r0 in n["removals"] + n["questions"] + n["rare"] + n["kept"]:
            sensitive.add(text[r0["start"]:r0["end"]])
        note_facts[n["id"]] = facts
        if dn.get("withhold"):
            counts["withheld_by_doctor"] += 1
            rows.append(f"{rel_out},{_csv(n['source'])},withheld by the doctor")
            continue
        clean = _render(text, merge_spans(spans), lds)
        left = _shapes_left(clean, lds)
        if left:
            counts["held_after_review"] += 1
            rows.append(f"{rel_out},{_csv(n['source'])},held: "
                        + " ".join(f"{k}x{v}" for k, v in sorted(left.items())))
            continue
        outputs.append((rel_out, clean))
        counts["released"] += 1
        rows.append(f"{rel_out},{_csv(n['source'])},released")
    for x in batch.get("not_included") or []:
        counts["set_aside_files"] += 0 if x.get("system") else 1
        rows.append(f"(not included),{_csv(x['source'])},"
                    + ("system file, ignored" if x.get("system") else
                       f"set aside, not de-identified: {_csv(x['reason'])}"))
    return {"outputs": outputs, "rows": rows, "counts": counts, "finds": finds,
            "seconds": seconds, "acc_rows": acc_rows, "learning": learning,
            "note_facts": note_facts, "sensitive": sensitive}


def release(batch_dirs, decision_paths: list[Path], out_dir: Path, *, doctor_file: Path) -> dict:
    """Release one or more batches. Every batch needs both reviewers' signed
    files and the doctor's signed file; everything is checked and rendered
    for EVERY batch before anything is written."""
    if isinstance(batch_dirs, (str, Path)):
        batch_dirs = [batch_dirs]
    prepared, seen = [], set()
    for bd in batch_dirs:
        batch = load_batch(Path(bd))
        if batch["batch"] in seen:
            raise ReviewError("the same batch was given twice")
        seen.add(batch["batch"])
        marker = Path(bd) / RELEASED_MARKER
        if marker.exists():
            raise ReviewError(f"batch {batch['number']} was released before (see {marker}); "
                              "prepare the notes again to release a new version")
        dec = load_decisions(batch, decision_paths)
        _ready_reviews(batch, dec)
        tag = f"{batch['number']:03d}-{batch['batch']}"
        texts = {n["id"]: _source_text(batch, n) for n in batch["notes"] if n["status"] == "ready"}
        quality = review_quality(batch, dec, texts)
        if quality["violations"]:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"integrity_alert-{tag}.json").write_text(
                json.dumps(integrity_alert(batch, quality), indent=1), encoding="utf-8")
            why = "; ".join(f"{v['reviewer']} {_VIOLATION_TEXT[v['reason']]}"
                            for v in quality["violations"])
            raise ReviewError(f"batch {batch['number']} needs a fresh review before release "
                              f"({why}). Nothing was released. Details: integrity_alert-{tag}.json")
        doc = load_doctor(doctor_file, batch, dec)
        if (out_dir / f"batch-{tag}").exists():
            raise ReviewError(f"{out_dir / f'batch-{tag}'} already exists: this batch was released before")
        r = _release_batch(batch, dec, doc, texts)
        r["quality"] = quality
        r["sealed"] = batch["sealed"]
        prepared.append((batch, dec, doc, tag, r))
    # Every check has passed for every batch. Everything is written into a
    # staging folder first and moved into place only when all of it was
    # written, so a failure part-way leaves nothing behind.
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = out_dir / f".staging-{secrets.token_hex(4)}"
    stage.mkdir()
    reports, all_acc, all_finds, learning = [], [], {"R1": 0, "R2": 0, "both": 0}, []
    try:
        for batch, dec, doc, tag, r in prepared:
            for rel_out, clean in r["outputs"]:
                dst = stage / f"batch-{tag}" / rel_out
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text(clean, encoding="utf-8")
            (stage / f"batch-{tag}").mkdir(exist_ok=True)
            cr = capture_recapture(r["finds"]["R1"], r["finds"]["R2"], r["finds"]["both"])
            acc = accuracy(r["acc_rows"])
            report = _report(batch, dec, doc, r, cr, acc, doctor_file)
            report["released_dir"] = str(out_dir / f"batch-{tag}")
            # The map back to the source files is patient-linked: it stays local.
            (stage / f"release_map-batch-{tag}.csv").write_text(
                "\n".join(["released_path,source_path,status"] + r["rows"]) + "\n", encoding="utf-8")
            (stage / f"batch_report-{tag}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
            (stage / f"batch_report-{tag}.md").write_text(_report_md(report), encoding="utf-8")
            (stage / f"evidence_summary-{tag}.md").write_text(_evidence_md(report), encoding="utf-8")
            reports.append(report)
            all_acc += r["acc_rows"]
            for k in all_finds:
                all_finds[k] += r["finds"][k]
            learning.append(r["learning"])
        combined = {"batches": [rp["number"] for rp in reports],
                    "accuracy": accuracy(all_acc),
                    "estimate": capture_recapture(all_finds["R1"], all_finds["R2"], all_finds["both"]),
                    "learning": _merge_learning(learning, [rp["counts"] for rp in reports])}
        nums = [rp["number"] for rp in reports]
        label = ("+".join(f"{x:03d}" for x in nums) if len(nums) <= 4
                 else f"{nums[0]:03d}-to-{nums[-1]:03d}")
        # Named by the batches' ids too, so a later preparation's batch 001
        # never overwrites these.
        stamp = f"{label}-{hashlib.sha256('+'.join(rp['batch'] for rp in reports).encode()).hexdigest()[:8]}"
        (stage / f"accuracy_report-{stamp}.md").write_text(_accuracy_md(combined, reports), encoding="utf-8")
        (stage / f"learning_report-{stamp}.json").write_text(json.dumps(combined["learning"], indent=1),
                                                             encoding="utf-8")
        vendor = prepared[0][2].get("vendor") or {}
        if vendor.get("share"):
            vr = maintainer_report(prepared, reports, combined, vendor)
            (stage / f"send_to_maintainers-{stamp}.json").write_text(json.dumps(vr, indent=1), encoding="utf-8")
            (stage / f"send_to_maintainers-{stamp}.md").write_text(_vendor_md(vr), encoding="utf-8")
            combined["maintainer_report"] = f"send_to_maintainers-{stamp}.md"
        for x in sorted(stage.iterdir()):
            if (out_dir / x.name).exists():
                raise ReviewError(f"{out_dir / x.name} already exists; nothing was released")
        for x in sorted(stage.iterdir()):
            os.replace(x, out_dir / x.name)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    for (batch, dec, doc, tag, r), bd in zip(prepared, batch_dirs):
        (Path(bd) / RELEASED_MARKER).write_text(json.dumps(
            {"batch": batch["batch"], "released_utc": _now(), "released_dir": str(out_dir / f"batch-{tag}")},
            indent=1), encoding="utf-8")
    return {"batches": reports, "combined": combined}


def _clean_comment(text: str, tokens: list[str], sensitive: set[str]) -> str | None:
    """A person's own words may leave only after three filters: every string
    anyone removed, marked or was asked about in these notes becomes "[the
    word]"; the engine redacts what is left as it would a note (and
    takes out any word it cannot name); anything still holding one of those
    strings is left out whole. None means: left out."""
    from inkguard import contract
    t = text[:FEEDBACK_TEXT_MAX]
    for tok in tokens:
        t = re.sub(r"(?i)(?<![\w])" + re.escape(tok) + r"(?![\w])", "[the word]", t)
    r = contract.deidentify(t, mode="mask").raw
    comp = getattr(r, "comprehension", None)
    if r.action.value == "review" and comp is not None and comp.not_understood:
        for x in sorted(comp.not_understood, key=lambda x: -x.start):
            t = t[:x.start] + "[the word]" + t[x.end:]
        r = contract.deidentify(t, mode="mask").raw
    if r.action.value == "blocked" or not r.clean_text:
        return None
    clean = r.clean_text.strip()
    low = clean.lower()
    if any(re.search(r"(?<![\w])" + re.escape(s) + r"(?![\w])", low) for s in sensitive):
        return None
    return clean


def maintainer_report(prepared: list, reports: list[dict], combined: dict, vendor: dict) -> dict:
    """The report the clinic may send to the Inkguard maintainers: numbers, the
    ratings and issues people ticked, notes they flagged (as facts about
    types, never words), and their comments after _clean_comment. Built only
    when the doctor chose to share it; the clinic sends the file itself."""
    from inkguard import __version__
    sensitive: set[str] = set().union(*(r["sensitive"] for *_, r in prepared))
    tokens = sorted({s.strip() for s in sensitive if len(s.strip()) >= 2}, key=len, reverse=True)
    sens = {s.strip().lower() for s in sensitive if len(s.strip()) >= 3}
    comments: list[dict] = []
    left_out = 0
    ratings = {"doctor": vendor.get("rating"), "reviewer_1": [], "reviewer_2": []}
    issues: dict[str, int] = {}
    edits = vendor.get("reviewers") or {}

    def comment(who: str, text) -> None:
        nonlocal left_out
        text = (text or "").strip()
        if not text:
            return
        c = _clean_comment(text, tokens, sens)
        if c is None:
            left_out += 1
        else:
            comments.append({"from": who, "text": c})

    def tick(codes) -> None:
        for c in codes or []:
            issues[FEEDBACK_ISSUES[c]] = issues.get(FEEDBACK_ISSUES[c], 0) + 1

    comment("doctor", vendor.get("text"))
    tick(vendor.get("issues"))
    flagged, k = [], 0
    for batch, dec, doc, tag, r in prepared:
        include = {}
        for rid, who in (("R1", "reviewer_1"), ("R2", "reviewer_2")):
            ed = edits.get(f"{batch['batch']}:{rid}") or {}
            include[rid] = ed.get("include", True)
            if not include[rid]:
                continue
            fb = dec[rid].get("feedback") or {}
            if fb.get("rating"):
                ratings[who].append(fb["rating"])
            tick(fb.get("issues"))
            comment(who, ed["text"] if "text" in ed else fb.get("text"))
        for n in batch["notes"]:
            if n["status"] != "ready":
                continue
            flags, by = set(), set()
            sources = [(rid, who, (dec[rid]["notes"].get(n["id"]) or {}).get("fb"))
                       for rid, who in (("R1", "reviewer_1"), ("R2", "reviewer_2")) if include[rid]]
            sources.append(("DOC", "doctor", (doc["notes"].get(n["id"]) or {}).get("fb")))
            for _, who, f in sources:
                on = {x for x, v in (f or {}).items() if v}
                if on:
                    flags |= on
                    by.add(who)
            if flags:
                k += 1
                flagged.append({"note": k, "flags": [NOTE_FLAGS[x] for x in sorted(flags)],
                                "flagged_by": sorted(by), **r["note_facts"].get(n["id"], {})})
    # Last check: the comments are searched for every sensitive string again.
    blob = json.dumps(comments, ensure_ascii=False).lower()
    if any(re.search(r"(?<![\w])" + re.escape(s) + r"(?![\w])", blob) for s in sens):
        left_out += len(comments)
        comments = []
    total = {key: sum(rp["counts"][key] for rp in reports)
             for key in ("notes", "released", "held_after_review", "blocked_by_engine",
                         "withheld_by_doctor", "bucket_items")}
    secs = {who: [s for *_, r in prepared for s in r["seconds"][rid]]
            for rid, who in (("R1", "reviewer_1"), ("R2", "reviewer_2"), ("DOC", "doctor"))}
    q = [r["quality"]["reviewers"] for *_, r in prepared]
    return {
        "kind": "phi-review-feedback-report", "report_version": 1, "software_version": __version__,
        "created_utc": _now(), "batches": len(reports),
        "profiles": sorted({rp["profile"] for rp in reports}),
        "notes": total,
        "ratings": ratings, "issues": issues, "flagged_notes": flagged,
        "comments": comments, "comments_left_out": left_out,
        "accuracy": combined["accuracy"], "estimate": combined["estimate"],
        "learning": combined["learning"],
        "median_seconds_per_note": {w: round(_median(v), 1) for w, v in secs.items()},
        "review_quality": {f"reviewer_{rid[1]}": {
            "notes_under_expected_reading_time": sum(x[rid]["too_fast"] for x in q),
            "tool_removals_kept": sum(x[rid]["kept"] for x in q),
            "sent_to_doctor": sum(x[rid]["unsure"] for x in q)} for rid in ("R1", "R2")},
        # A check that was run, not a promise.
        "patient_text_check": "comments were checked against every marked and removed string",
    }


def _vendor_md(v: dict) -> str:
    a, n = v["accuracy"], v["notes"]

    def rate(x):
        if isinstance(x, list):
            return " / ".join(f"{r}/5" for r in x) if x else "—"
        return f"{x}/5" if x else "—"
    L = ["# Report for the Inkguard maintainers", "",
         f"Software {v['software_version']} · {_n(v['batches'], 'batch', 'batches')} · "
         f"{v['created_utc'][:10]}", "",
         "Prepared by the clinic after its review. Numbers, ticks and the clinic's own comments "
         "only: no names and no note text. Read it through; send it if it looks right.", "",
         "## Ratings", "",
         f"- Doctor: {rate(v['ratings']['doctor'])} · Reviewer 1: {rate(v['ratings']['reviewer_1'])}"
         f" · Reviewer 2: {rate(v['ratings']['reviewer_2'])}", "",
         "## In their words", ""]
    who = {"doctor": "Doctor", "reviewer_1": "Reviewer 1", "reviewer_2": "Reviewer 2"}
    L += [f"- **{who[c['from']]}:** {c['text']}" for c in v["comments"]] or ["- No comments."]
    if v["comments_left_out"]:
        L.append(f"- _{_n(v['comments_left_out'], 'comment was', 'comments were')} left out because "
                 "it still held words from the notes._")
    L += ["", "## Ticked", ""]
    L += [f"- {k}: {c}" for k, c in sorted(v["issues"].items(), key=lambda x: -x[1])] or ["- Nothing."]
    L += ["", "## Notes flagged", ""]
    for f in v["flagged_notes"]:
        extra = []
        if f.get("tool_over_removed"):
            extra.append("kept in the end: " + ", ".join(f"{k} ×{c}" for k, c in f["tool_over_removed"].items()))
        if f.get("tool_missed"):
            extra.append("added in review: " + ", ".join(f"{k} ×{c}" for k, c in f["tool_missed"].items()))
        L.append(f"- Note {f['note']}: {', '.join(f['flags'])} ({', '.join(who[b] for b in f['flagged_by'])})"
                 + (" — " + "; ".join(extra) if extra else ""))
    if not v["flagged_notes"]:
        L.append("- None.")
    L += ["", "## How the software did", "",
          "| Their version | Removed | Right (precision) | Found (recall) |", "|---|---|---|---|",
          f"| The tool alone | {a['tool']['removed']} | {_pct(a['tool']['precision'])} | "
          f"{_pct(a['tool']['recall'])} |",
          f"| Reviewer 1 | {a['reviewer_1']['removed']} | {_pct(a['reviewer_1']['precision'])} | "
          f"{_pct(a['reviewer_1']['recall'])} |",
          f"| Reviewer 2 | {a['reviewer_2']['removed']} | {_pct(a['reviewer_2']['precision'])} | "
          f"{_pct(a['reviewer_2']['recall'])} |", "",
          f"- Identifiers in the final version: {a['final_identifiers']}; the doctor added "
          f"{a['doctor_marks']}; reviewers agreed on {_pct(a['reviewers_agreed'])} of items."]
    lr = v["learning"]
    if lr.get("tool_missed"):
        L.append("- Added in review, by type: " + ", ".join(f"{k} {c}" for k, c in sorted(lr["tool_missed"].items())))
    if lr.get("tool_over_removed"):
        L.append("- Kept in the end, by type: " + ", ".join(f"{k} {c}" for k, c in sorted(lr["tool_over_removed"].items())))
    t = v["median_seconds_per_note"]
    L += ["", "## Notes and time", "",
          f"- {n['notes']} notes: {n['released']} released, {n['held_after_review']} held by the final "
          f"check, {n['blocked_by_engine']} stopped by the software, {n['withheld_by_doctor']} "
          "withheld by the doctor",
          f"- Median time per note: reviewer 1 {t['reviewer_1']} s, reviewer 2 {t['reviewer_2']} s, "
          f"doctor {t['doctor']} s", ""]
    return "\n".join(L)


def _merge_learning(parts: list[dict], counts: list[dict]) -> dict:
    """What this release teaches the tool — counts and types only, never a
    word of any note."""
    out = {"kind": "phi-review-learning", "questions": {}, "tool_missed": {},
           "tool_over_removed": {}, "unsure": {"reviewer_1": 0, "reviewer_2": 0, "both": 0,
                                               "doctor_kept": 0, "doctor_removed": 0}}
    for p in parts:
        for reason, q in p["questions"].items():
            t = out["questions"].setdefault(reason, {"asked": 0, "removed": 0, "reviewers_agreed": 0})
            for k in t:
                t[k] += q[k]
        for key in ("tool_missed", "tool_over_removed"):
            for k, v in p[key].items():
                out[key][k] = out[key].get(k, 0) + v
    for c in counts:
        out["unsure"]["reviewer_1"] += c["unsure_R1"]
        out["unsure"]["reviewer_2"] += c["unsure_R2"]
        out["unsure"]["both"] += c["unsure_both"]
        out["unsure"]["doctor_kept"] += c["bucket_kept_by_doctor"]
        out["unsure"]["doctor_removed"] += c["bucket_removed_by_doctor"]
    return out


def _n(k: int, word: str, plural: str | None = None) -> str:
    return f"{k} {word if k == 1 else (plural or word + 's')}"


def _csv(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def _median(xs: list[float]) -> float:
    xs = sorted(x for x in xs if x > 0)
    if not xs:
        return 0.0
    k = len(xs) // 2
    return xs[k] if len(xs) % 2 else (xs[k - 1] + xs[k]) / 2


def _report(batch, dec, doc, r, cr, acc, doctor_file) -> dict:
    from inkguard import __version__
    names = {x["id"]: x["name"] for x in batch["reviewers"]}
    f = r["finds"]
    either = f["R1"] + f["R2"] - f["both"]
    return {
        "kind": "phi-review-batch-report", "batch": batch["batch"], "number": batch["number"],
        "of": batch["of"], "software_version_prepared": batch["software_version"],
        "software_version_released": __version__, "profile": batch["profile"],
        "prepared_utc": batch["created_utc"], "released_utc": _now(),
        "patients": len(batch["patients"]), "counts": r["counts"],
        "reviewer_finds": {**f, "either": either,
                           "agreement": round(f["both"] / either, 3) if either else None},
        "estimate": cr, "accuracy": acc,
        "review_quality": {"reviewers": r["quality"]["reviewers"], "alerts": r["quality"]["alerts"]},
        "sealed": r.get("sealed", False),
        "time": {k: {"total_minutes": round(sum(r["seconds"][k]) / 60, 1),
                     "median_seconds_per_note": round(_median(r["seconds"][k]), 1)}
                 for k in ("R1", "R2", "DOC")},
        # AUDIT (local): who authorized and who reviewed, and fingerprints
        # of exactly what each of the three submitted.
        "audit": {"authorized_by": batch["authorized_by"],
                  "reviewers": [{"id": rid, "name": names[rid],
                                 "signed_off_utc": (dec[rid].get("signed_off") or {}).get("at"),
                                 "decisions_sha256": dec[rid]["_sha256"]} for rid in ("R1", "R2")],
                  "doctor": {"name": batch["authorized_by"], "signed_off_utc": doc["signed_at"],
                             "decisions_sha256": doc["sha256"]}},
    }


def _pct(x) -> str:
    return "—" if x is None else f"{x:g}%"


def _changes_md(a: dict) -> list[str]:
    return ["| Their own changes | Made | Upheld by the final version | Sent to the doctor |",
            "|---|---|---|---|"] + [
        f"| Reviewer {k} | {a[f'reviewer_{k}']['changes']} | "
        f"{_pct(a[f'reviewer_{k}']['changes_upheld'])} | {a[f'reviewer_{k}']['referred']} |"
        for k in ("1", "2")] + [""]


def _report_md(r: dict) -> str:
    c, f, e, t, a = r["counts"], r["reviewer_finds"], r["estimate"], r["time"], r["accuracy"]
    au = r["audit"]
    L = [f"# Three-person release — batch {r['number']} of {r['of']}", "",
         f"Batch `{r['batch']}` · profile **{r['profile']}** · prepared {r['prepared_utc']} · "
         f"released {r['released_utc']} · software {r['software_version_released']}", "",
         "## Notes", "",
         f"- Patients: **{r['patients']}**; notes: **{c['notes']}**",
         f"- Released: **{c['released']}**",
         f"- Not released, blocked by the engine: **{c['blocked_by_engine']}**",
         f"- Held after review (an identifier shape was still present): **{c['held_after_review']}**",
         f"- Other files set aside unread (images, other formats; not de-identified, not released; "
         f"listed in the release map): **{c.get('set_aside_files', 0)}**",
         "", "## Accuracy against the final version", "",
         "| Their version | Removed | Right (precision) | Found (recall) |", "|---|---|---|---|",
         f"| The tool | {a['tool']['removed']} | {_pct(a['tool']['precision'])} | {_pct(a['tool']['recall'])} |",
         f"| Reviewer 1 | {a['reviewer_1']['removed']} | {_pct(a['reviewer_1']['precision'])} | "
         f"{_pct(a['reviewer_1']['recall'])} |",
         f"| Reviewer 2 | {a['reviewer_2']['removed']} | {_pct(a['reviewer_2']['precision'])} | "
         f"{_pct(a['reviewer_2']['recall'])} |", "", *_changes_md(a),
         f"- Identifiers in the final version: **{a['final_identifiers']}**; reviewers agreed on "
         f"{_pct(a['reviewers_agreed'])} of items.",
         f"- The tool asked about **{a['tool']['asked_about_but_left_in']}** identifiers it had "
         "left in, and a reviewer removed them.",
         f"- _{a['note']}_", "",
         "## The unsure bucket and the doctor", "",
         f"- Items in the unsure bucket: **{c['bucket_items']}** (the doctor kept "
         f"{c['bucket_kept_by_doctor']}, removed {c['bucket_removed_by_doctor']})",
         f"- Called unsure by reviewer 1: {c['unsure_R1']} · by reviewer 2: {c['unsure_R2']} · "
         f"by both: {c['unsure_both']}",
         f"- The doctor changed **{c['doctor_changed_default']}** other decisions and marked "
         f"**{c['doctor_marks']}** things everyone had missed",
         f"- The doctor opened **{c['doctor_opened']}** of "
         f"{c['notes'] - c['blocked_by_engine']} notes and withheld **{c['withheld_by_doctor']}**",
         "", "## What the reviewers found that the tool missed", "",
         f"- Reviewer 1: **{f['R1']}** · Reviewer 2: **{f['R2']}** · both: **{f['both']}** · "
         f"either: **{f['either']}**"
         + (f" · agreement {f['agreement']:.0%}" if f["agreement"] is not None else ""),
         f"- Copied by the page to the same patient's other notes: **{c['propagated_marks']}**"]
    if e.get("estimable"):
        L += [f"- **Estimated identifiers both reviewers missed: {e['estimated_missed_by_both']}** "
              f"(95% interval {e['interval_95'][0]}–{e['interval_95'][1]}), by capture–recapture. "
              + (e["note"] or "")]
    else:
        L += [f"- {e['note']}"]
    q = r["review_quality"]
    L += ["", "## Review quality", ""]
    for rid, label in (("R1", "Reviewer 1"), ("R2", "Reviewer 2")):
        x = q["reviewers"][rid]
        L.append(f"- {label}: {x['too_fast']} of {x['notes']} notes confirmed in less than the "
                 f"expected reading time; kept {x['kept']} tool removals ({x['kept_names']} read as names); "
                 f"sent {x['unsure']} items to the unsure bucket")
    L += [f"- _{a}_" for a in q["alerts"]] or ["- No alerts."]
    L += ["", "## Time", ""]
    for k, label in (("R1", "Reviewer 1"), ("R2", "Reviewer 2"), ("DOC", "Doctor")):
        L.append(f"- {label}: {t[k]['total_minutes']} min "
                 f"(median {t[k]['median_seconds_per_note']} s per note)")
    L += ["", "## Audit (keep with the clinic's records)", "",
          f"- Authorized by: {au['authorized_by']}"]
    for rv in au["reviewers"]:
        L.append(f"- {rv['id']}: {rv['name']}, signed off {rv['signed_off_utc']}, "
                 f"decisions file SHA-256 `{rv['decisions_sha256']}`")
    L.append(f"- Doctor: {au['doctor']['name']}, signed off {au['doctor']['signed_off_utc']}, "
             f"decisions file SHA-256 `{au['doctor']['decisions_sha256']}`")
    L += ["", "> This report names the reviewers and points to the source files through "
          "the release map. Both stay on this computer. Share evidence_summary instead."]
    return "\n".join(L) + "\n"


def _accuracy_md(combined: dict, reports: list[dict]) -> str:
    a, e = combined["accuracy"], combined["estimate"]
    L = ["# Accuracy report", "",
         f"Batches {', '.join(str(b) for b in combined['batches'])} · "
         f"{sum(r['counts']['released'] for r in reports)} notes released", "",
         "| Their version | Removed | Right (precision) | Found (recall) |", "|---|---|---|---|",
         f"| The tool alone | {a['tool']['removed']} | {_pct(a['tool']['precision'])} | "
         f"{_pct(a['tool']['recall'])} |",
         f"| Reviewer 1 | {a['reviewer_1']['removed']} | {_pct(a['reviewer_1']['precision'])} | "
         f"{_pct(a['reviewer_1']['recall'])} |",
         f"| Reviewer 2 | {a['reviewer_2']['removed']} | {_pct(a['reviewer_2']['precision'])} | "
         f"{_pct(a['reviewer_2']['recall'])} |", "", *_changes_md(a),
         f"- Identifiers in the final version: **{a['final_identifiers']}**, of which the doctor "
         f"added **{a['doctor_marks']}** that everyone else had missed.",
         f"- Reviewers agreed on {_pct(a['reviewers_agreed'])} of items.",
         f"- {a['note']}"]
    if e.get("estimable"):
        L.append(f"- **After review: an estimated {e['estimated_missed_by_both']} identifiers were "
                 f"missed by both reviewers** (95% interval {e['interval_95'][0]}–"
                 f"{e['interval_95'][1]}); the doctor's pass and the final shape check come after "
                 "this.")
    else:
        L.append(f"- {e['note']}")
    return "\n".join(L) + "\n"


def _evidence_md(r: dict) -> str:
    """The shareable summary: numbers and roles, no names, no text."""
    c, f, e, a = r["counts"], r["reviewer_finds"], r["estimate"], r["accuracy"]
    profile = ("the Limited Data Set profile (dates, ZIP codes and towns retained; the output "
               "remains PHI and requires a data use agreement)"
               if r["profile"] == "limited_data_set" else "the Safe Harbor profile")
    if e.get("estimable"):
        est = (f"A capture–recapture estimate of identifiers missed by both reviewers was "
               f"{e['estimated_missed_by_both']} (approximate 95% interval "
               f"{e['interval_95'][0]}–{e['interval_95'][1]}) across "
               f"{_n(c['released'], 'released note')}.")
    elif e.get("found_by_either"):
        est = ("Too few identifiers were found by both reviewers for a capture–recapture "
               "estimate of what both missed.")
    else:
        est = ("Neither reviewer found an identifier the software had missed, so no "
               "capture–recapture estimate could be made.")
    tool = (f"Against the final version, the software alone removed {_pct(a['tool']['recall'])} of "
            f"identifiers (recall) and {_pct(a['tool']['precision'])} of its removals were "
            "confirmed (precision).")
    return "\n".join([
        f"# Review summary — batch {r['number']} of {r['of']}", "",
        "## Methods statement", "",
        f"Clinical notes ({_n(c['notes'], 'note')} from {_n(r['patients'], 'patient')}) were "
        f"processed with Inkguard version {r['software_version_released']} under {profile}. "
        "Every note was then reviewed by two reviewers authorized by the data owner, each on a "
        "separate review page that does not show the other reviewer's marks. For every note, each "
        "reviewer's page recorded both views: the planned removals visible, and the removals "
        "applied. Reviewers could add removals, keep a removal, or refer an item to the "
        "authorizing clinician. A removal both reviewers made was final; a word both kept stayed "
        "unless the clinician removed it; every referred item, and every removal only one reviewer "
        "wanted to keep, was decided by the clinician; other disagreements were resolved toward "
        "removal. The clinician could also add removals and withhold notes, and signed the final "
        "version as the third reviewer. "
        + ("Each review file was sealed by its page and verified at release. "
           if r.get("sealed") else "")
        + "Automatic checks on the review files and on review time found nothing to stop the "
        "release. "
        f"The reviewers added {_n(f['either'], 'removal')} ({f['both']} found by both), the "
        f"clinician decided {_n(c['bucket_items'], 'referred item')} and added "
        f"{_n(c['doctor_marks'], 'removal')}. {tool} {est} "
        f"{_n(c['released'], 'note was', 'notes were')} released; "
        f"{_n(c['blocked_by_engine'] + c['held_after_review'] + c['withheld_by_doctor'], 'was', 'were')} "
        f"not released ({c['blocked_by_engine']} stopped by the software, {c['held_after_review']} "
        f"held by the final check, {c['withheld_by_doctor']} withheld by the clinician)."
        + (f" {_n(c.get('set_aside_files', 0), 'other file was', 'other files were')} set aside unread "
           "(images or formats this release does not read); they are not de-identified and not "
           "part of the release." if c.get("set_aside_files") else ""), "",
        "## Citation", "",
        "Wajahat A. Inkguard (version " + r["software_version_released"] + "). Zenodo. "
        "DOI: 10.5281/zenodo.23464777.", "",
        f"_Batch {r['batch']} · prepared {r['prepared_utc']} · released {r['released_utc']}_", "",
    ])


# =====================================================================
# command line
# =====================================================================

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m inkguard.chert",
                                 description="Three-person review of redacted notes. "
                                             "In development, not part of the 0.25.0 release.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="run the tool and write review batches")
    p.add_argument("notes", type=Path, help="folder with one sub-folder per patient")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--authorized-by", required=True, help="the doctor or owner authorizing review")
    p.add_argument("--reviewer", action="append", required=True,
                   help="a reviewer's full name (give exactly two)")
    p.add_argument("--batch-patients", type=int, default=DEFAULT_BATCH_PATIENTS)
    p.add_argument("--profile", choices=["safe_harbor", "lds"], default="safe_harbor")
    d = sub.add_parser("doctor", help="the doctor's page across one or more reviewed batches")
    d.add_argument("batches", type=Path, nargs="+")
    d.add_argument("--decisions", type=Path, nargs="+", required=True,
                   help="both reviewers' files for every batch given")
    d.add_argument("--out", type=Path, required=True, help="where to write the doctor's page")
    s = sub.add_parser("release", help="merge the three reviews and write the clean notes")
    s.add_argument("batches", type=Path, nargs="+")
    s.add_argument("--decisions", type=Path, nargs="+", required=True)
    s.add_argument("--doctor", type=Path, required=True, help="the doctor's signed file")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--opaque", action="store_true",
                   help="every removal in the released files reads [REMOVED], with no type "
                        "(final files only; the review pages always show what was removed)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "prepare":
            dirs = prepare(args.notes, args.out, authorized_by=args.authorized_by,
                           reviewers=args.reviewer, batch_patients=args.batch_patients,
                           profile=args.profile, quiet=False)
            for x in dirs:
                print(f"{x}: review-reviewer-1.html, review-reviewer-2.html")
            print("These pages contain patient notes. Keep them on this computer.")
        elif args.cmd == "doctor":
            info = doctor(args.batches, args.decisions, args.out)
            print(f"{info['page']}: {info['notes']} notes from {info['batches']} batch(es); "
                  f"{info['unsure_bucket']} item(s) in the unsure bucket")
            print("This page contains patient notes. Keep it on this computer.")
        else:
            from inkguard import contract
            with contract.opaque_scope(args.opaque):
                rep = release(args.batches, args.decisions, args.out, doctor_file=args.doctor)
            for b in rep["batches"]:
                c = b["counts"]
                print(f"batch {b['number']}: released {c['released']} of {c['notes']} notes; held "
                      f"after review {c['held_after_review']}; blocked by the engine "
                      f"{c['blocked_by_engine']} -> {b['released_dir']}")
            print(f"reports in {args.out} (batch_report and release_map stay local; "
                  "evidence_summary, accuracy_report and learning_report hold no names or note text)")
            if rep["combined"].get("maintainer_report"):
                print(f"report for the Inkguard maintainers: {args.out / rep['combined']['maintainer_report']} "
                      "(read it; send it if it looks right)")
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


def _decode(path: Path) -> str:
    """Older Windows notes are not UTF-8; reading them with replacement
    characters broke names apart and let fragments through."""
    from inkguard import calcite
    return calcite.decode_text(path.read_bytes())


if __name__ == "__main__":
    sys.exit(main())
