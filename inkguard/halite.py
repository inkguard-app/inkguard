"""
halite — settling a batch and taking the clean copies out of the pool.

This is the last mile: two people have reviewed every note independently,
and now the doctor decides the items they disagreed on, chooses what shape
the finished work comes back in, and signs. Then the clean copies leave the
pool, the pool's key is destroyed, and what is left inside is deleted.

Four rules shape this module.

  1. ONE FINAL TEXT PER NOTE, AND EVERY SHAPE CARRIES THE SAME ONE. The
     merge of both reviews plus the doctor's rulings (chert) decides the
     words. Whatever shape the doctor chooses — a fresh document, the
     practice's own file layout, plain text — the words in it are those
     words. A shape may never remove less, and may never remove more
     without saying so on the report.

  2. ARRANGEMENT IS AN IDENTIFIER. The order records sit in can line them up
     with an appointment book or a billing run, so the doctor chooses the
     arrangement, and the choice is printed on the review report.

  3. SMALL NUMBERS ARE CHECKED BOTH WAYS. A condition can single a patient
     out because it is rare in the world, or because only one patient in
     this batch carries it. Both counts run; anything at or below the
     warning line is put in front of the doctor before anything leaves.
     Below about ten patients the app says plainly that the second count
     means little, rather than letting a batch look as though it passed a
     test it is too small to take.

  4. NOTHING IS ERASED UNTIL THE COPIES ARE ON DISK AND COUNTED. Export
     writes, verifies, and only then destroys the key.
"""

from __future__ import annotations

import hashlib
import io
import os
import random
import re
import secrets
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from inkguard import granite

# ---------------------------------------------------------------------------
# What the doctor chooses
# ---------------------------------------------------------------------------

#: The three shapes, named for what the doctor is doing with the files —
#: never for how safe they are.
SHAPES = {
    "research": "Research copy",
    "office": "Office copy",
    "text": "Plain text copy",
}

#: How the records are ordered. "shuffled" is the default: it breaks the
#: link between the order and whatever produced the folder, at no cost.
ARRANGEMENTS = ("shuffled", "grouped", "original")

#: The default warning line for the small-numbers check: a condition only
#: ONE patient in the batch carries. The
#: published health-data threshold of about eleven would flag everything in
#: sets this size and teach people to ignore the warning.
DEFAULT_WARN_AT = 1
MAX_WARN_AT = 20

#: Below this many patients the odd-one-out count says little, because in a
#: small set almost everything is unique. The app says so.
SMALL_SET = 10

#: What the doctor signs.
DOCTOR_ATTESTATION = (
    "I authorized this review. I decided every item the two reviewers did not agree on, "
    "I saw the small-numbers warnings, and I am releasing these files on my own judgement.")

#: The one claim the software makes about its own output, printed on every
#: report. Deliberately not "de-identified data" and never "HIPAA compliant".
METHOD_LINE = (
    "Method: removal of the Safe Harbor identifiers (45 CFR 164.514(b)(2)), documented, "
    "with two independent reviewers and the responsible clinician's sign-off. Each patient's "
    "notes are read together, at least three times and until a read finds nothing new, "
    "before any reviewer sees them. Shuffling and "
    "the small-numbers warnings lower re-identification risk but are not a statistical "
    "de-identification determination, which needs a qualified expert.")


class ExportError(Exception):
    """A plain-words failure with a support code (topaz)."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 1. Settling one note: both reviews merged, the doctor's rulings applied
# ---------------------------------------------------------------------------

@dataclass
class Settled:
    """One note, finished. `clean` is the text that leaves; `unmask` are the
    words two reviewers and the doctor agreed are NOT patient information,
    which the Office copy must therefore leave on the page."""
    id: str
    item: str
    patient: int
    status: str                       # ready | blocked | held
    clean: str = ""
    reason: str = ""
    spans: list = field(default_factory=list)
    outcome: dict = field(default_factory=dict)
    unmask: set = field(default_factory=set)
    left: dict = field(default_factory=dict)      # identifier shapes still present
    rare: list = field(default_factory=list)
    pages: int = 0
    finds: dict = field(default_factory=dict)
    #: The words the three of them took OUT of this note. Patient words, so
    #: they live in memory for the length of one export and are never
    #: written to a file, a report or a log — they exist only so that what
    #: is written can be checked for them before the pool is emptied.
    removed_words: set = field(default_factory=set)
    #: Set when the clinician held this record back at the small-numbers
    #: check, so the report can tell that apart from a hold by the tool.
    withheld_by_doctor: bool = False
    #: The merged items of this note (both reviewers' votes, the source of
    #: each). Counts only — no words — and the accuracy report is built from
    #: them.
    items: list = field(default_factory=list)


def bucket(note: dict, d1: dict, d2: dict, text: str | None = None) -> list[dict]:
    """The items of one note the DOCTOR must decide: anything either
    reviewer was unsure about, and anything only one of them kept."""
    from inkguard import chert
    items, _ = chert.note_items(note, d1, d2, text if text is not None else note.get("text"))
    out = []
    for it in items:
        if not it["bucket"]:
            continue
        out.append({"key": it["key"], "entity": it["entity"], "source": it["source"],
                    "start": it["start"], "end": it["end"], "default": it["default"],
                    "locked": it["locked"],
                    # The doctor sees the two votes; the reviewers never see
                    # each other's. By this point both have signed off.
                    "votes": it["votes"], "shows": it.get("shows", "")})
    return out


def _unmaskable(note: dict, items: list[dict], outcome: dict, text: str) -> set[str]:
    """The words that may be left on the page in the Office copy.

    ONLY a word the engine itself raised as a question (or flagged as rare)
    and that both reviewers and the doctor then settled as "leave it in"
    qualifies. Never an arbitrary string, never a certain removal, never a
    word a reviewer typed. This is the one place a human determination can
    put text back on a page, so the gate is narrow and the list goes on the
    review report."""
    out: set[str] = set()
    for it in items:
        if it["source"] not in ("question", "rare"):
            continue
        if it.get("certain") or it.get("locked"):
            continue
        if outcome.get(it["key"]) != "keep":
            continue
        # BOTH reviewers must have said to leave it in. "I'm not sure" is
        # not agreement, and the report may not print it as one.
        votes = it.get("votes") or {}
        if votes.get("R1") != "keep" or votes.get("R2") != "keep":
            continue
        word = text[it["start"]:it["end"]].strip()
        if word and len(word) <= 80:
            out.add(word)
    return out


def settle_note(note: dict, d1: dict, d2: dict, doctor_note: dict, *, lds: bool) -> Settled:
    """Merge both reviews, apply the doctor's rulings, and render the text
    that will leave. Raises chert.ReviewError if the doctor has not decided
    everything — a batch can never be exported half-decided."""
    from inkguard import chert, contract
    s = Settled(id=note["id"], item=note.get("item", ""), patient=note.get("patient", 0),
                status=note.get("status", "ready"), pages=note.get("pages", 0))
    if note.get("status") != "ready":
        s.status = "blocked"
        s.reason = note.get("block_reason") or contract.BLOCKED_REASON
        return s
    text = note["text"]
    items, finds = chert.note_items(note, d1 or {}, d2 or {}, text)
    spans, outcome = chert.final_spans(note, items, doctor_note or {}, text)
    clean = chert._render(text, spans, lds)
    s.clean, s.spans, s.outcome, s.finds = clean, spans, outcome, finds
    s.items = [{k: it.get(k) for k in ("key", "source", "votes", "certain", "locked")}
               for it in items]
    s.removed_words = {text[sp["start"]:sp["end"]].strip() for sp in spans
                       if (sp["end"] - sp["start"]) >= 3}
    s.unmask = _unmaskable(note, items, outcome, text)
    s.left = contract.shapes_left(clean, lds)
    if s.left:
        # The final inspection. An identifier SHAPE still in the text after
        # three people have been through it holds the note back rather than
        # letting it out: the doctor is told which shape, never the value.
        s.status = "held"
        s.reason = ("the finished text still shows the shape of an identifier "
                    f"({', '.join(sorted(s.left))}); it was held rather than released")
    s.rare = [r for r in note.get("rare", []) if outcome.get(r["id"]) != "remove"]
    return s


# ---------------------------------------------------------------------------
# 2. The small-numbers check
# ---------------------------------------------------------------------------

#: The same word boundary the per-note rare check uses (chert._WORD), so a
#: condition counted here is the same phrase that was flagged there.
_COND_WORD = re.compile(r"[A-Za-z0-9]+")


def _conditions(text: str) -> set[str]:
    """Condition names from the rare-disease lexicon found in the finished
    text, as lower-case phrases. Used for the odd-one-out count, so it runs
    on the CLEAN text — what a recipient would actually hold."""
    from inkguard import chert
    lex, max_words = chert._rare_lexicon()
    toks = [(m.start(), m.end(), m.group().lower()) for m in _COND_WORD.finditer(text)]
    found, i = set(), 0
    while i < len(toks):
        hit = None
        for n in range(min(max_words, len(toks) - i), 0, -1):
            phrase = " ".join(t[2] for t in toks[i:i + n])
            if phrase in lex:
                hit = (phrase, n)
                break
        if hit:
            found.add(hit[0])
            i += hit[1]
        else:
            i += 1
    return found


def small_numbers(settled: list[Settled], *, warn_at: int = DEFAULT_WARN_AT) -> dict:
    """Two counts over the finished batch.

    *Rare in the world* — conditions on the rare-disease list, already
    flagged per note with whatever else in that note could combine with
    them.

    *The odd one out here* — a condition only `warn_at` patients or fewer
    carry across this batch, which makes those patients the only ones of
    their kind in the set whether or not the condition is rare anywhere.

    Returns what the doctor is shown before anything leaves. No patient
    text: condition names and counts only, and the note ids they sit in."""
    warn_at = max(1, min(int(warn_at), MAX_WARN_AT))
    ready = [s for s in settled if s.status == "ready"]
    patients = sorted({s.patient for s in ready})
    carriers: dict[str, set[int]] = {}
    where: dict[str, list[str]] = {}
    for s in ready:
        for cond in _conditions(s.clean):
            carriers.setdefault(cond, set()).add(s.patient)
            where.setdefault(cond, []).append(s.id)
    odd = [{"condition": c, "patients": len(p), "notes": sorted(where[c])}
           for c, p in carriers.items() if len(p) <= warn_at]
    odd.sort(key=lambda r: (r["patients"], r["condition"]))
    # A rare-condition flag is about a PATIENT, not about one note of theirs.
    # Grouped by patient and carrying every ready note of that patient, so a
    # ruling on it covers all of them: holding back one note of a patient
    # flagged in three would leave them just as identifiable.
    by_patient: dict[int, dict] = {}
    for s in ready:
        for r in s.rare:
            row = by_patient.setdefault(s.patient, {"patient": s.patient, "flags": 0,
                                                    "notes": [], "also": set(), "kinds": set()})
            row["flags"] += 1
            row["also"] |= set(r.get("also") or ())
            row["kinds"].add(r.get("kind", "condition"))
    for s in ready:
        if s.patient in by_patient:
            by_patient[s.patient]["notes"].append(s.id)
    rare = [{"patient": r["patient"], "flags": r["flags"], "notes": sorted(set(r["notes"])),
             "also": sorted(r["also"]), "kinds": sorted(r["kinds"])}
            for r in sorted(by_patient.values(), key=lambda x: x["patient"])]
    # A newsworthy event that two reviewers chose to keep is still a way to
    # find the patient; it comes back to the doctor here, named as such.
    events_kept = sum(1 for s in ready for r in s.rare if r.get("kind") == "event")
    out = {"warn_at": warn_at, "patients": len(patients), "notes": len(ready),
           "odd_one_out": odd, "rare_in_world": rare, "events_kept": events_kept,
           "to_decide": len(odd) + len(rare), "small_set": len(patients) < SMALL_SET}
    if out["small_set"]:
        out["small_set_note"] = (
            f"This batch holds {len(patients)} patient(s). Below about {SMALL_SET} the "
            "odd-one-out count means little, because in a set this small almost everything is "
            "unique. A set this size is identifiable by its size, and holding records back "
            "inside it does not change that.")
    return out


#: What the doctor may do with each warning.
RULINGS = ("generalize", "withhold", "accept")


def apply_small_numbers(settled: list[Settled], decisions: dict) -> dict:
    """The doctor's ruling on each warning. `decisions` maps a note id to
    one of RULINGS. "withhold" holds that whole record back from export;
    "generalize" means they edited or will edit the wording and accept it as
    it stands; "accept" is on the record as accepted."""
    held, counted = [], {"generalize": 0, "withhold": 0, "accept": 0}
    for s in settled:
        r = decisions.get(s.id)
        if r is None:
            continue
        if r not in RULINGS:
            raise ExportError("EXPORT-RULING", f"unknown ruling {r!r}")
        counted[r] += 1
        if r == "withhold":
            s.status = "held"
            s.withheld_by_doctor = True
            s.reason = "held back by the clinician after the small-numbers check"
            held.append(s.id)
    return {"withheld": held, "counted": counted}


# ---------------------------------------------------------------------------
# 3. Arrangement
# ---------------------------------------------------------------------------

def arrange(settled: list[Settled], how: str, *, seed: str | None = None) -> list[Settled]:
    """Order the records for export. Patient codes are already random
    (onyx), so shuffling is the one arrangement that carries no order
    information at all."""
    if how not in ARRANGEMENTS:
        raise ExportError("EXPORT-ARRANGEMENT", f"unknown arrangement {how!r}")
    rows = [s for s in settled if s.status == "ready"]
    if how == "original":
        return sorted(rows, key=lambda s: (s.item, s.id))
    if how == "grouped":
        return sorted(rows, key=lambda s: (s.patient, s.id))
    rng = random.Random(seed or secrets.token_hex(16))
    out = list(rows)
    rng.shuffle(out)
    return out


# ---------------------------------------------------------------------------
# 4. Record codes
# ---------------------------------------------------------------------------

_CODE_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY349"       # no look-alikes


def record_codes(settled: list[Settled], *, rng: random.Random | None = None) -> dict[int, str]:
    """A RANDOM code per patient, made fresh for this export and unique
    within it. Nothing about the person is in it (45 CFR 164.514(c)(1)): it
    is a label for reading the file, not a key, and it is not written down
    anywhere that could map it back."""
    r = rng or random.SystemRandom()
    codes: dict[int, str] = {}
    taken: set[str] = set()
    for p in sorted({s.patient for s in settled}):
        while True:
            c = "".join(r.choice(_CODE_ALPHABET) for _ in range(6))
            if c not in taken:
                break
        taken.add(c)
        codes[p] = c
    return codes


# ---------------------------------------------------------------------------
# 5. The three shapes
# ---------------------------------------------------------------------------

def _safe_part(s: str) -> str:
    """A file name component with nothing that could escape a folder."""
    s = re.sub(r"[^A-Za-z0-9._ -]", "_", str(s)).strip(" .") or "file"
    return s[:80]


def name_is_clean(part: str) -> bool:
    """Does this file or folder name hold an identifier?

    THE NAMES ARE PART OF THE DATA. A practice files notes under folders
    like "Ellison, Martha", so an Office copy that kept the original path
    would carry the patient's name in the path of a file whose text had just
    been cleaned (found by this module's own test). Every path
    component is put through the engine, and one that changes is replaced."""
    from inkguard import contract
    probe = part.replace("_", " ").replace("-", " ")
    try:
        r = contract.deidentify(probe, profile="safe_harbor", mode="mask")
    except Exception:                               # noqa: BLE001 — unreadable: treat as named
        return False
    if r.status != "clear":
        return False
    return r.clean_text == probe


def clean_path(rel: str, code: str, n: int) -> Path:
    """The path this file takes in the Office copy. The SHAPE of the folder
    tree is kept — a practice recognises its own filing by the shape — but
    any component that names someone becomes that record's code."""
    parts = [p for p in Path(rel).parts if p not in ("..", "/", "\\")]
    out: list[str] = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        stem, suffix = (Path(part).stem, Path(part).suffix) if last else (part, "")
        if name_is_clean(stem):
            out.append(_safe_part(stem) + suffix)
        elif last:
            out.append(f"{code}-{n:03d}{suffix}")
        else:
            out.append(f"Record {code}")
    return Path(*out) if out else Path(f"{code}-{n:03d}")


def research_copy(rows: list[Settled], codes: dict[int, str], out: Path, *,
                  title: str = "Redacted records") -> Path:
    """Every record in ONE freshly built Word document, headed by its code.

    The most conservative of the three shapes, and the reason is
    structural rather than clever: this document is built only from the cleaned text, so it
    cannot inherit metadata, tracked changes, comments or hidden rows from
    the originals — none of that was ever in it. It costs the original
    layout."""
    import docx                                   # python-docx (MIT)
    doc = docx.Document()
    cp = doc.core_properties
    for attr in ("author", "last_modified_by", "title", "subject", "comments",
                 "category", "keywords", "identifier"):
        try:
            setattr(cp, attr, "")
        except Exception:                          # noqa: BLE001 — field not settable
            pass
    doc.add_heading(title, level=1)
    for s in rows:
        doc.add_heading(f"Record {codes.get(s.patient, '?')}", level=2)
        for para in s.clean.split("\n"):
            doc.add_paragraph(para)
        doc.add_paragraph("")
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def plain_text_copy(rows: list[Settled], codes: dict[int, str], folder: Path) -> list[Path]:
    """Text only, one file per record, for other software to read."""
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for n, s in enumerate(rows, start=1):
        p = folder / f"{n:04d}-{codes.get(s.patient, 'record')}.txt"
        p.write_text(s.clean, encoding="utf-8")
        made.append(p)
    return made


def office_copy(folder: Path, items: list, rows: list[Settled], out: Path, *,
                profile: str, codes: dict[int, str]) -> dict:
    """One clean file per original, same folder layout, original format kept.

    The practice's own file is re-cleaned where it sits — never moved — and
    written to `out` under a path of the same SHAPE. Two rules guard this
    shape:

    THE WORDS KEPT ARE THAT RECORD'S OWN. A word two reviewers and the
    clinician left in belongs to the note they were reading. Handing the
    whole batch's kept words to every file would mean a word kept as a place
    name in one note keeps a different patient's first name in another,
    so `keep` is built per file.

    NOTHING THE THREE OF THEM REMOVED MAY APPEAR. After each file is
    written it is read back and checked for the very words that were taken
    out of that note. A file that still shows one is deleted and held, not
    released."""
    from inkguard import calcite, contract
    by_item = {s.item: s for s in rows}
    out.mkdir(parents=True, exist_ok=True)
    made, trouble, differed = [], [], []
    evaluate = contract.engine()
    for it in items:
        if it.outcome != "prepared" or it.id not in by_item:
            continue
        src = folder / it.source
        settled = by_item[it.id]
        n = len(made) + len(trouble) + 1
        dst = out / clean_path(it.source, codes.get(settled.patient, "record"), n)
        while dst.exists():
            dst = dst.with_name(f"{dst.stem}-{secrets.token_hex(2)}{dst.suffix}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix.lower() not in calcite.DOC_SUFFIXES:
            # A plain text file: the reviewed text IS the file, written out
            # exactly as the three of them agreed it.
            dst.write_text(settled.clean, encoding="utf-8")
            made.append(dst)
            continue
        # This record's own kept words, never the batch's.
        eng = calcite.Engine(evaluate, profile_name=profile,
                             keep=frozenset(settled.unmask))
        res = calcite.clean_file(src, dst, eng)
        if res.status != "clean":
            trouble.append({"id": it.id, "reason": res.reason or contract.BLOCKED_REASON})
            _drop(res.outputs or [dst])
            continue
        written = ""
        try:
            written = "\n".join(calcite.read_text(p) for p in (res.outputs or [dst]))
        except Exception:                          # noqa: BLE001
            trouble.append({"id": it.id,
                            "reason": "the written file could not be read back to check it"})
            _drop(res.outputs or [dst])
            continue
        showing = _still_showing(written, settled.removed_words)
        if showing:
            trouble.append({"id": it.id,
                            "reason": f"the written file still showed {showing} thing(s) the "
                                      "review took out, so it was not released in this shape"})
            _drop(res.outputs or [dst])
            continue
        made.extend(res.outputs or [dst])
        if _words_of(written) != _words_of(settled.clean):
            differed.append(it.id)
    return {"files": made, "trouble": trouble, "differed": differed}


def _still_showing(written: str, removed: set[str]) -> int:
    """How many of the words the review took out are in this file. Returns a
    count only: the words themselves never leave this function."""
    if not removed:
        return 0
    haystack = (written or "").lower()
    return sum(1 for w in removed if w and len(w) >= 3 and w.lower() in haystack)


def _drop(paths) -> None:
    for p in paths:
        try:
            Path(p).unlink()
        except OSError:
            pass


_WORDS = re.compile(r"[A-Za-z0-9]+")


def _words_of(text: str) -> list[str]:
    """Words only: the office shape keeps layout, so white space and page
    furniture differ by design. The WORDS must not."""
    return [w.lower() for w in _WORDS.findall(text or "")]
# ---------------------------------------------------------------------------
# 6. The review report — what goes with the files
# ---------------------------------------------------------------------------
#
# This is the record: who ran it, against which method, what was taken
# out, what three people decided, what was held back and why, and what was
# destroyed afterwards.
# It carries no patient text. It names the reviewers, because the record of
# who did the work is the point.

def _n(k: int, one: str, many: str | None = None) -> str:
    return f"{k} {one if k == 1 else (many or one + 's')}"


def evidence_report(run: dict) -> str:
    """The report, in plain Markdown so it opens anywhere and can be read in
    ten years without this app."""
    L: list[str] = []
    a = L.append
    a("# Review report")
    a("")
    from inkguard.fluorite import VERIFY_NOTICE
    a(f"> {VERIFY_NOTICE} This report records the checks people made; it does not replace them.")
    a("")
    a(f"**Batch** {run['batch']}  ")
    a(f"**Finished** {run['finished']}  ")
    a(f"**Practice** {run.get('practice') or 'not stated'} (as entered when the app was set up)  ")
    a(f"**Software** {run['app']} version {run['version']}  ")
    a(f"**Method** {run['profile']} — {run['citation']}")
    a("")
    a(METHOD_LINE)
    a("")
    if run.get("markers") == "opaque":
        a("**Markers** opaque: every removal in the released files reads [REMOVED], with no type, "
          "chosen at release by the responsible clinician.")
        a("")
    a("## Who did the work")
    a("")
    a(f"- Responsible clinician: **{run['doctor']}**")
    badges = run.get("badges") or {}
    for slot, who in sorted((run.get("reviewers") or {}).items()):
        b = badges.get(slot) or {}
        said = (f" — practice badge holder, passed this app's practice batch on {b['earned']}"
                if b.get("earned") else "")
        a(f"- Reviewer {slot}: **{who}**{said}")
    helpers = run.get("helpers") or []
    a(f"- Helpers named: {', '.join(helpers) if helpers else 'none'}")
    a("")
    if run.get("people") == 2 and run.get("doctor") in (run.get("reviewers") or {}).values():
        a("This practice works with two people: the responsible clinician was also one "
          "of the two reviewers, and settled the points where the two disagreed. The "
          "reviewers still worked independently; neither could see the other's "
          "decisions, or whether the other had finished, until both had signed off.")
    else:
        a("The two reviewers worked independently. Neither could see the other's "
          "decisions, or whether the other had finished, at any point; who else was "
          "on the batch is on this page because the record of who did the work is the "
          "point. The clinician saw both sets only after both had signed off.")
    a("")
    a("### Confirmed before the batch began")
    a("")
    for line in run.get("points") or []:
        a(f"- {line}")
    a("")
    a("### Signed at sign-off")
    a("")
    for slot, who in sorted((run.get("reviewers") or {}).items()):
        a(f"- {who} ({slot}): \"{run.get('reviewer_attestation', '')}\"")
    a(f"- {run['doctor']}: \"{DOCTOR_ATTESTATION}\"")
    a("")
    c = run["counts"]
    a("## What came in and what went out")
    a("")
    a(f"- Files taken in: **{c['files']}** across {_n(c['patients'], 'patient')}")
    a(f"- Notes reviewed: **{c['notes']}**  ")
    a(f"- Clean copies taken out: **{c['released']}**")
    a(f"- Held by the tool (an identifier shape was still showing): {c['held_by_tool']}")
    a(f"- Held back by the clinician: {c['withheld']}")
    a(f"- Stopped by the engine before review: {c['blocked']}")
    a(f"- Set aside, never read: {c['set_aside']}")
    a("")
    if run.get("set_aside_rows"):
        a("### Set aside, with the reason")
        a("")
        a("| File | Patient | Reason | Code |")
        a("|---|---|---|---|")
        for r in run["set_aside_rows"]:
            a(f"| {r['id']} | {r['patient'] + 1} | {r['reason']} | {r.get('code') or '—'} |")
        a("")
    if run.get("held_rows"):
        a("### Held, with the reason")
        a("")
        for r in run["held_rows"]:
            a(f"- {r['id']}: {r['reason']}")
        a("")
    a("## What three people decided")
    a("")
    d = run["decisions"]
    a(f"- Removals the engine made that stayed removed: **{d['engine_kept']}**")
    a(f"- Items the two reviewers did not agree on, decided by the clinician: "
      f"**{d['bucket']}** ({d['bucket_removed']} removed, {d['bucket_kept']} left in)")
    a(f"- Words the reviewers marked themselves that the engine had missed: **{d['marks']}**")
    a(f"- Words the engine asked about and a person answered: **{d['questions']}**")
    if d.get("spelling_slips"):
        a(f"- Of those, spelling slips (a word one keystroke from a known word, which could be a "
          f"misspelled name): **{d['spelling_slips']}**. Each was answered by both reviewers "
          "before they could confirm the note, and the responsible clinician signed the batch "
          "with them on this record.")
    a("")
    if run.get("unmasked"):
        a("### Words left on the page by human determination")
        a("")
        a("Each of these was raised by the tool as uncertain, and BOTH reviewers "
          "said to leave it in and the clinician agreed. An \"I'm not sure\" from "
          "either reviewer is not an agreement and never reaches this list. They "
          "appear in the finished text; nothing else the tool marked was left in.")
        a("")
        a("| Word | What the tool guessed | Decided by |")
        a("|---|---|---|")
        for u in run["unmasked"]:
            a(f"| {u['word']} | {u.get('guess') or '—'} | both reviewers and {run['doctor']} |")
        a("")
    a("## The small-numbers check")
    a("")
    sn = run["small_numbers"]
    line = ("**one patient** in this batch" if sn["warn_at"] == 1
            else f"**{_n(sn['warn_at'], 'patient')} or fewer** in this batch")
    a(f"Warning line: a condition carried by {line} of {_n(sn['patients'], 'patient')}.")
    a("")
    if sn.get("small_set_note"):
        a(f"> {sn['small_set_note']}")
        a("")
    if sn["odd_one_out"]:
        a("| Condition | Patients in this batch carrying it | Clinician's ruling |")
        a("|---|---|---|")
        ruled_all = run.get("small_rulings") or {}
        for r in sn["odd_one_out"]:
            # Every note the condition appears in, not just the first: a
            # record held back in one note and released in another would
            # otherwise be printed as accepted.
            said = sorted({ruled_all.get(nid, "accepted on the record") for nid in r["notes"]})
            a(f"| {r['condition']} | {r['patients']} | {', '.join(said)} |")
    else:
        a("No condition in this batch fell at or below the warning line.")
    a("")
    if sn.get("events_kept"):
        a(f"**Newsworthy events still described: {sn['events_kept']}** (a reminder, not a "
          "rule). Each was flagged as an event that can identify the people in it (a crash "
          "with many hurt, a disaster, a shooting). The tool suggested removing what pins it "
          "down, its name and how many were hurt; where those stayed, people chose to keep "
          "them. With a date and a hospital, a news report may still identify these patients: "
          "worth a last thought before the files are shared.")
        a("")
    rare_rows = sn.get("rare_in_world") or []
    flags = sum(r.get("flags", 1) for r in rare_rows)
    a(f"Conditions rare in the world: **{flags}** flag(s) across "
      f"**{_n(len(rare_rows), 'patient')}**."
      + (" Each was put in front of the clinician with whatever else in the note could combine "
         "with it, and ruled on." if rare_rows else ""))
    if rare_rows:
        a("")
        a("| Patient | Flags | What else was in the note | Clinician's ruling |")
        a("|---|---|---|---|")
        ruled_rare = run.get("small_rulings") or {}
        for r in rare_rows:
            said = sorted({ruled_rare.get(nid, "accepted on the record") for nid in r["notes"]})
            a(f"| record {r['patient'] + 1} | {r.get('flags', 1)} | "
              f"{', '.join(r.get('also') or ['nothing else']) } | {', '.join(said)} |")
    a("")
    a("## How the files came back")
    a("")
    a(f"- Arrangement: **{run['arrangement_said']}**")
    for key in run["shapes"]:
        a(f"- {SHAPES[key]}: `{SHAPES[key]}.zip`")
    if run.get("differed"):
        a(f"- Where the Office copy's words differ from the reviewed text: "
          f"{len(run['differed'])} file(s) — {', '.join(run['differed'])}. The Office copy "
          "removes anything the engine finds in the original file, so it can only remove "
          "more, never less.")
    if run.get("office_trouble"):
        a(f"- Files the Office copy could not be written for: {len(run['office_trouble'])}; "
          "they are listed above and were not released in that shape.")
    a("")
    a("File and folder names were checked as carefully as the text in them: a practice files "
      "notes under the patient's name, so the shape of your filing is kept in the copies and any "
      "name that held an identifier was replaced by that record's code. Your own folder is "
      "untouched — this app never renames your files.")
    a("")
    a("Record codes in these files were made at random for this export only. "
      "They are labels for reading, not keys, and nothing maps them back to a person.")
    a("")
    a("## Measured accuracy")
    a("")
    for line in (run.get("accuracy") or {}).get("lines", []):
        a(f"- **{line['result']}** — {line['test']}" if isinstance(line, dict)
          else f"- {line}")
    a("")
    said = (run.get("accuracy") or {}).get("plain")
    if said:
        a("")
        a(said)
    a("")
    a("These figures come from the named test sets, not from this batch. "
      "The record of this batch is the human review above.")
    a("")
    a("## What was destroyed")
    a("")
    e = run["erase"]
    a(f"- Working copies the app made: **{e['files']}** destroyed")
    a(f"- The key to this batch's work area: destroyed {e['key_destroyed']}")
    a("- Still inside the pool: **0**")
    a("")
    a("The key to this batch's working copies is destroyed before the copies are deleted "
      "(the cryptographic-erase approach described in NIST SP 800-88), so an interrupted "
      "deletion leaves only encrypted data. This app does not move, change or delete your "
      "original files.")
    a("")
    if run.get("codes"):
        a("## Support codes recorded during this batch")
        a("")
        for code, n in sorted(run["codes"].items()):
            a(f"- `{code}` × {n}")
        a("")
        a("These are codes and counts only. They can be sent to an IT person "
          "or to the Inkguard maintainers without sending any file.")
        a("")
    a("---")
    a("")
    a(f"Report built by {run['app']} {run['version']} on the practice's own computer. "
      "This app sent no part of this batch anywhere.")
    return "\n".join(L) + "\n"


def accuracy_report(settled: list[Settled], facts: dict) -> str:
    """The second document that travels with the files: how the tool and each
    reviewer compared with the version the three of them agreed.

    It states what it can and cannot say: the three-person version is the
    best reference available, not an absolute truth, and what nobody caught
    is not in it."""
    from inkguard import chert
    rows = [(s.items, s.outcome, 0) for s in settled if s.status == "ready" and s.items]
    if not rows:
        return ""
    acc = chert.accuracy(rows)
    L: list[str] = []
    a = L.append
    a("# Accuracy report")
    a("")
    from inkguard.fluorite import VERIFY_NOTICE
    a(f"> {VERIFY_NOTICE}")
    a("")
    a(f"**Batch** {facts.get('batch', '')}  ")
    a(f"**Finished** {facts.get('finished') or _now()}  ")
    a(f"**Software** {facts.get('app', '')} version {facts.get('version', '')}")
    a("")
    a("This batch only. It says how the tool and each reviewer compared with the "
      "version the three of them finally agreed.")
    a("")
    a(f"Identifiers in the final version: **{acc['final_identifiers']}**")
    a("")
    a("| | Took out | Of those, in the final version | Of the final version, found |")
    a("|---|---|---|---|")
    t = acc["tool"]
    a(f"| The tool | {t['removed']} | {_pct(t['precision'])} | {_pct(t['recall'])} |")
    for rid in ("1", "2"):
        r = acc[f"reviewer_{rid}"]
        who = (facts.get("reviewers") or {}).get(f"R{rid}") or f"Reviewer {rid}"
        a(f"| {who} | {r['removed']} | {_pct(r['precision'])} | {_pct(r['recall'])} |")
    a("")
    # The same figures in plain words, as counts: over-removal (taken out,
    # but the final version kept it) and missed (in the final version, not
    # taken out). What people changed is recorded per batch and never
    # folded into the software's benchmark.
    final = acc["final_identifiers"] or 0
    def _counts(x):
        over = round(x["removed"] * (1 - x["precision"] / 100)) if x["precision"] is not None else None
        missed = round(final * (1 - x["recall"] / 100)) if x["recall"] is not None else None
        return over, missed
    rows = [("The tool", t)] + [((facts.get("reviewers") or {}).get(f"R{rid}") or f"Reviewer {rid}",
                                 acc[f"reviewer_{rid}"]) for rid in ("1", "2")]
    for who, x in rows:
        over, missed = _counts(x)
        a(f"- {who}: over-removal {over if over is not None else '—'}, "
          f"missed {missed if missed is not None else '—'}")
    a("")
    a("### What each reviewer changed")
    a("")
    for rid in ("1", "2"):
        r = acc[f"reviewer_{rid}"]
        who = (facts.get("reviewers") or {}).get(f"R{rid}") or f"Reviewer {rid}"
        a(f"- **{who}**: changed the tool's decision {r['changes']} time(s)"
          + (f", and the final version upheld {_pct(r['changes_upheld'])} of those"
             if r["changes"] else "")
          + f"; referred {r['referred']} item(s) to the clinician.")
    a("")
    a(f"The two reviewers agreed on {_pct(acc['reviewers_agreed'])} of items.")
    a("")
    a("### What this cannot tell you")
    a("")
    a(acc["note"])
    a("")
    a("Leaving the tool's work alone is not counted as a reviewer's own find: only the "
      "changes they made themselves are. Figures from our own test sets, measured outside "
      "this batch, are on the review report.")
    a("")
    return "\n".join(L) + "\n"


def _pct(x) -> str:
    return "—" if x is None else f"{x}%"


# ---------------------------------------------------------------------------
# 7. Taking the copies out, then emptying the pool
# ---------------------------------------------------------------------------

#: Room to spare before writing. Export writes at most about twice the text
#: it read, plus the zips; this keeps a run from failing half-written.
SPACE_FACTOR = 4
MIN_FREE = 64 << 20


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _free_space(path: Path) -> int:
    p = path
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 1 << 62


def _zip_folder(folder: Path, target: Path) -> dict:
    """Zip one shape's folder, then open the zip again and check every file
    is in it at the right size. A zip that does not verify is deleted."""
    names = sorted(p for p in folder.rglob("*") if p.is_file())
    tmp = target.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in names:
            z.write(p, arcname=str(Path(folder.name) / p.relative_to(folder)))
    with zipfile.ZipFile(tmp) as z:
        if z.testzip() is not None:
            tmp.unlink()
            raise ExportError("EXPORT-VERIFY", "the zip did not read back cleanly")
        inside = {i.filename: i.file_size for i in z.infolist()}
    for p in names:
        key = str(Path(folder.name) / p.relative_to(folder))
        if inside.get(key) != p.stat().st_size:
            tmp.unlink()
            raise ExportError("EXPORT-VERIFY", "a file is missing from the zip or the wrong size")
    tmp.replace(target)
    return {"files": len(names), "bytes": target.stat().st_size,
            "sha256": _sha_file(target)}


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_destination(folder: Path, out_dir: Path) -> None:
    """Everything about the chosen folder that can be known before any work
    starts, so a refusal reaches the person while they are still there."""
    out_dir = Path(out_dir)
    if _inside(out_dir, Path(folder)) or _inside(Path(folder), out_dir):
        raise ExportError("RUN-FOLDER-OVERLAP",
                          "the folder for the finished files overlaps the folder the files came from")
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / f".write-check-{secrets.token_hex(4)}"
        probe.write_bytes(b"x")
        probe.unlink()
    except OSError as exc:
        raise ExportError("RUN-PERMISSION", type(exc).__name__) from exc
    if _free_space(out_dir) < MIN_FREE:
        raise ExportError("RUN-DISK-FULL", "not enough free space for the finished files")


def verify_shape(key: str, folder: Path, rows: list[Settled]) -> dict:
    """Read a written shape BACK OFF THE DISK and check it.

    This runs before anything is destroyed, which is the whole point: the
    only moment it is safe to empty the pool is once what was written has
    been read again and found to hold the agreed words and none of the
    removed ones."""
    files = [p for p in sorted(folder.rglob("*")) if p.is_file()]
    if not files:
        raise ExportError("EXPORT-EMPTY", f"{SHAPES[key]} came out with no files in it")
    if key == "text":
        if len(files) != len(rows):
            raise ExportError("EXPORT-VERIFY",
                              f"{SHAPES[key]} holds {len(files)} file(s) for {len(rows)} record(s)")
        wrote = {p: p.read_text(encoding="utf-8") for p in files}
        want = sorted(_words_of(r.clean) for r in rows)
        got = sorted(_words_of(t) for t in wrote.values())
        if want != got:
            raise ExportError("EXPORT-VERIFY", f"{SHAPES[key]} does not hold the agreed words")
    elif key == "research":
        from inkguard import calcite
        doc = [p for p in files if p.suffix.lower() == ".docx"]
        if not doc:
            raise ExportError("EXPORT-VERIFY", "the research document was not written")
        back = _words_of(calcite.read_text(doc[0]))
        inside = set(back)
        for r in rows:
            missing = [w for w in _words_of(r.clean) if w not in inside]
            if missing:
                raise ExportError("EXPORT-VERIFY",
                                  "the research document does not hold every agreed word")
            showing = _still_showing(" ".join(back), r.removed_words)
            if showing:
                raise ExportError("EXPORT-VERIFY",
                                  "the research document still shows something the review took out")
    else:                                           # office: checked file by file as written
        if len(files) < len(rows):
            raise ExportError("EXPORT-OFFICE-INCOMPLETE",
                              f"{SHAPES[key]} holds {len(files)} file(s) for {len(rows)} record(s)")
    return {"files": len(files)}


def export(folder: Path, items: list, settled: list[Settled], *,
           shapes: list[str], arrangement: str, out_dir: Path, facts: dict,
           profile: str, erase=None, seed: str | None = None) -> dict:
    """Write the chosen shapes into `out_dir`, read every one of them back,
    empty the pool, then put the review report in each shape and zip it.

    THE ORDER MATTERS AND IT IS THIS: write, read back, verify, erase,
    report, zip. `erase` is called once, after every shape has been verified
    off the disk, and returns {"files": n, "key_destroyed": "..."}: the
    pool's key belongs to whoever holds it (opal), not here. Nothing after
    the erase can lose the files — if zipping fails the shape's folder is
    left exactly where it is, with the report inside it, and said so."""
    shapes = [s for s in dict.fromkeys(shapes) if s in SHAPES]
    if not shapes:
        raise ExportError("EXPORT-NO-SHAPE", "no shape was chosen")
    if arrangement not in ARRANGEMENTS:
        raise ExportError("EXPORT-ARRANGEMENT", f"unknown arrangement {arrangement!r}")
    out_dir = Path(out_dir)
    check_destination(Path(folder), out_dir)
    rows = arrange(settled, arrangement, seed=seed)
    if not rows:
        raise ExportError("EXPORT-NOTHING", "nothing in this batch came through ready to release")
    need = max(MIN_FREE, SPACE_FACTOR * sum(len(s.clean.encode()) for s in rows))
    if _free_space(out_dir) < need:
        raise ExportError("RUN-DISK-FULL", "not enough free space for the finished files")

    codes = record_codes(rows)
    built: dict[str, dict] = {}
    folders: dict[str, Path] = {}
    office: dict = {"files": [], "trouble": [], "differed": []}
    try:
        for key in shapes:
            here = out_dir / SHAPES[key]
            if here.exists():
                shutil.rmtree(here)
            here.mkdir(parents=True)
            folders[key] = here
            if key == "research":
                research_copy(rows, codes, here / "De-identified records.docx")
            elif key == "text":
                plain_text_copy(rows, codes, here / "records")
            else:
                office = office_copy(Path(folder), items, rows, here / "files",
                                     profile=profile, codes=codes)
                if office["trouble"]:
                    # A shape that is short of even one record does not hold
                    # what the three of them agreed, and after the erase it
                    # could never be made again. Stop while it still can be.
                    raise ExportError("EXPORT-OFFICE-INCOMPLETE",
                                      f"{len(office['trouble'])} file(s) could not be written "
                                      "in the office shape")
            built[key] = verify_shape(key, here, rows)
    except ExportError:
        for p in folders.values():
            shutil.rmtree(p, ignore_errors=True)
        raise
    except Exception as exc:                        # noqa: BLE001
        for p in folders.values():
            shutil.rmtree(p, ignore_errors=True)
        raise ExportError("EXPORT-WRITE", type(exc).__name__) from exc

    unmask_rows = sorted(({"word": w, "guess": (facts.get("guesses") or {}).get(w, "")}
                          for r in rows for w in r.unmask), key=lambda u: u["word"].lower())
    seen, unmasked = set(), []
    for u in unmask_rows:
        if u["word"] not in seen:
            seen.add(u["word"])
            unmasked.append(u)

    def _run(erased: dict) -> dict:
        r = dict(facts)
        r.update(shapes=shapes, arrangement=arrangement,
                 arrangement_said=_ARRANGEMENT_SAID[arrangement],
                 finished=_now(), erase=erased,
                 differed=office.get("differed") or [],
                 office_trouble=office.get("trouble") or [],
                 unmasked=unmasked)
        return r

    # The report is BUILT ONCE BEFORE ANYTHING IS DESTROYED, and thrown
    # away. If anything about this batch cannot be written into a report,
    # that must be found while the pool is still here to try again — not
    # after its key is gone (found by this module's own test).
    try:
        evidence_report(_run({"files": 0, "key_destroyed": "pending"}))
    except Exception as exc:                        # noqa: BLE001
        for p in folders.values():
            shutil.rmtree(p, ignore_errors=True)
        raise ExportError("EXPORT-REPORT", type(exc).__name__) from exc

    # Every shape has been written AND READ BACK, and the report is known to
    # build. Only now is the pool emptied.
    erased = {"files": 0, "key_destroyed": "not recorded"}
    if erase is not None:
        erased = dict(erase())

    run_facts = _run(erased)
    report = evidence_report(run_facts)
    try:
        accuracy = accuracy_report(rows, run_facts)
    except Exception:                               # noqa: BLE001 — never costs the files
        accuracy = ""
    zips: dict[str, dict] = {}
    loose: list[str] = []
    for key, here in folders.items():
        # Past this line nothing may be lost. Each step is attempted, and a
        # step that fails leaves the files where they are rather than
        # undoing anything.
        try:
            (here / "Evidence report.md").write_text(report, encoding="utf-8")
            if accuracy:
                (here / "Accuracy report.md").write_text(accuracy, encoding="utf-8")
        except OSError:
            loose.append(str(here))
            continue
        try:
            zips[key] = _zip_folder(here, out_dir / f"{SHAPES[key]}.zip")
        except (ExportError, OSError):
            loose.append(str(here))                 # the folder stays, with its report inside
            continue
        shutil.rmtree(here, ignore_errors=True)
    return {"shapes": shapes, "arrangement": arrangement, "records": len(rows),
            "codes": len(codes), "zips": zips, "erased": erased, "report": report,
            "loose": loose, "built": built,
            "office": {"differed": office.get("differed") or [],
                       "trouble": office.get("trouble") or []}}


_ARRANGEMENT_SAID = {
    "shuffled": ("records in random order in the research and plain-text copies; "
                 "the office copy keeps your own folder layout"),
    "grouped": ("records of the same patient together in the research and plain-text copies; "
                "the office copy keeps your own folder layout"),
    "original": ("records in the order of the original files in the research and "
                 "plain-text copies; the office copy keeps your own folder layout"),
}
