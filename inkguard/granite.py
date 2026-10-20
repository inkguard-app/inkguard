"""
granite — batches: the work in hand, kept encrypted and erasable.

A batch is one set of a practice's files taken through the tool together.
Two rules shape everything here:

  1. THE PRACTICE'S FILES ARE READ WHERE THEY ARE. granite never moves,
     changes, renames or deletes anything in the folder the doctor chose.
     It opens each file, reads it, and leaves it exactly as it was.

  2. WHAT THE APP MAKES IS ENCRYPTED AND ERASABLE. The text pulled out of
     each file, the engine's marks, the reviewers' decisions — everything
     that could carry patient information — lives only in this batch's own
     work area, encrypted under a key that belongs to that batch alone and
     is held in the operating system's protected store. Finishing the batch
     (or its time running out) destroys that key first, so everything left
     becomes unreadable at once, and then the files are deleted.

Because of rule 2, a batch's folder paths and file names are kept HERE and
never in the app's own records: a folder called after a patient is patient
information, so it must be erasable with the rest of the batch. The app's
records keep only counts, dates and stages, so they can be backed up.

Every file the doctor drops in is accounted for. A file that is not read —
an image, an old-format document, a system file, or one the intake guard
(zircon) refuses — is listed with the reason, never silently skipped, and
is never exported.

granite is a shell module: it reaches the engine only through the contract
(tests/test_boundary.py enforces this), and every failure it can show
someone is a topaz support code with plain words.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import secrets
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

WORK_MAGIC = b"GRAN1"
CHARS_PER_PAGE = 1800                 # a typed page, for "about N pages"
MAX_FILES = 20_000                    # one batch; beyond this, split the work
MAX_BYTES = 20 * 1024 ** 3            # 20 GB of input in one batch
MAX_NOTE_CHARS = 4_000_000           # one file's text; longer is set aside
# Human writing has spaces. A very long unbroken run is an encoded image, a
# base64 blob or a damaged export — not prose — and the engine slows sharply
# on one (measured: a 9,000-character run takes about six times as long as
# the same amount of ordinary text, and it worsens with length). Such a file
# is set aside with a plain reason rather than left to hold up a batch.
MAX_UNBROKEN_RUN = 5_000

# Stages a batch moves through. The review stages are driven by the review
# screens; granite only records where a batch is.
STAGES = ("preparing", "processing", "ready_for_review", "in_review",
          "ready_for_doctor", "signed_off", "failed")


class BatchError(Exception):
    """Something a person can be told about, as a topaz support code."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pages(chars: int) -> int:
    """About how many typed pages that much text is."""
    return max(1, round(chars / CHARS_PER_PAGE)) if chars else 0


# ---------------------------------------------------------------------------
# The work area — one folder per batch, every file encrypted under that
# batch's own key
# ---------------------------------------------------------------------------

class WorkArea:
    """Named pieces of a batch's work, encrypted at rest (AES-GCM). The key
    is held by the caller (opal keeps it in the operating system's protected
    store) and is never written here, so destroying it makes every piece
    unreadable at once."""

    def __init__(self, path, key: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.path = Path(path)
        self._aead = AESGCM(key)
        self._lock = threading.RLock()

    def _file(self, name: str) -> Path:
        if not name or not all(c.isalnum() or c in "-_" for c in name) or len(name) > 80:
            raise BatchError("APP-BAD-REQUEST", "not a work-area name")
        return self.path / (name + ".bin")

    def put(self, name: str, value) -> None:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
        nonce = secrets.token_bytes(12)
        blob = WORK_MAGIC + nonce + self._aead.encrypt(nonce, data, name.encode())
        p = self._file(name)
        with self._lock:
            p.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".w.", dir=str(p.parent))
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(blob)
                    f.flush()
                    os.fsync(f.fileno())
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
                os.replace(tmp, p)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass

    def get(self, name: str, default=None):
        p = self._file(name)
        with self._lock:
            if not p.exists():
                return default
            try:
                raw = p.read_bytes()
                if not raw.startswith(WORK_MAGIC):
                    raise ValueError
                plain = self._aead.decrypt(raw[len(WORK_MAGIC):len(WORK_MAGIC) + 12],
                                           raw[len(WORK_MAGIC) + 12:], name.encode())
                return json.loads(plain)
            except Exception:  # noqa: BLE001 — changed, wrong key, truncated: one answer
                raise BatchError("BATCH-UNREADABLE",
                                 "this batch's work could not be opened") from None

    def has(self, name: str) -> bool:
        return self._file(name).exists()

    # -- the ledger: what went into this pool, what came out, what is left ----
    #
    # The work area is an encrypted pool. Everything the app makes goes in; the
    # clean copies and the report come out; whatever is left is destroyed.
    # The ledger is the running account of that, kept inside the pool itself,
    # so the screen can show it live and the review report can close it:
    # "in 42 · out 42 · left 0".

    def note(self, event: str, n: int = 1, detail: dict | None = None) -> dict:
        with self._lock:
            led = self.get("ledger") or {"opened": _now(), "events": [], "counts": {}}
            led["counts"][event] = led["counts"].get(event, 0) + n
            led["events"].append({"at": _now(), "event": event, "n": n, **(detail or {})})
            del led["events"][:-500]                  # the running tail, not a transcript
            self.put("ledger", led)
            return led

    def ledger(self) -> dict:
        led = self.get("ledger") or {"opened": None, "events": [], "counts": {}}
        c = led.get("counts", {})
        led["in_pool"] = max(0, c.get("note_in", 0) - c.get("note_out", 0) - c.get("note_erased", 0))
        return led

    def erase(self) -> int:
        """Delete every piece. The key is destroyed by the caller FIRST, so
        anything this cannot delete is already unreadable."""
        removed = 0
        with self._lock:
            if self.path.exists():
                for f in sorted(self.path.rglob("*"), reverse=True):
                    try:
                        f.unlink() if f.is_file() else f.rmdir()
                        removed += 1
                    except OSError:
                        pass
                try:
                    self.path.rmdir()
                except OSError:
                    pass
        return removed


# ---------------------------------------------------------------------------
# Taking stock of the folder: every file, by patient, with its outcome
# ---------------------------------------------------------------------------

@dataclass
class Item:
    """One file the doctor dropped in, and what became of it. `source` is
    the path inside the chosen folder — patient information, so it lives
    only in the encrypted work area."""
    id: str                     # P<patient>-F<file>, used anywhere a path cannot go
    patient: int
    source: str
    sha256: str
    size: int
    kind: str                   # text | document | image | other | system | link
    outcome: str = "pending"    # pending | prepared | set_aside | system | failed
    reason: str = ""
    code: str = ""              # the intake guard's support code (ZC-…), if any
    notes: list = field(default_factory=list)   # guard notes (what cleaning removes)
    chars: int = 0
    note_id: str = ""           # the note this file became, once prepared


def _hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def first_look(folder: Path) -> None:
    """A quick, bounded look before a batch is started, so a wrong folder is
    refused at once. It stops as soon as it finds something readable rather
    than walking a whole drive."""
    folder = Path(folder)
    if not folder.is_absolute() or not folder.is_dir():
        raise BatchError("BATCH-FOLDER", "that is not a folder on this computer")
    from inkguard.cli import _classify
    seen = 0
    try:
        for p in folder.rglob("*"):
            if not (p.is_file() or p.is_symlink()):
                continue
            seen += 1
            if _classify(p)[0] in ("text", "document"):
                return
            if seen >= 2000:
                return                                 # too many to judge from here; the batch will say
    except OSError:
        raise BatchError("BATCH-FOLDER", "that folder could not be read") from None
    raise BatchError("BATCH-EMPTY" if not seen else "BATCH-NOTHING-READ", "nothing to read")


def patient_fingerprint(keys) -> str:
    """A short mark of which patients a folder held, and in what order. A
    part is planned against one of these; if the folder has changed when the
    part is read, the plan no longer means what it said and is refused
    rather than quietly covering different people."""
    return hashlib.sha256("\x1f".join(keys).encode("utf-8")).hexdigest()[:16]


def take_stock(folder: Path, part: dict | None = None) -> tuple[list[Item], dict]:
    """Walk the chosen folder and classify every file, by patient (one folder
    per patient; a loose file at the top is its own patient). Nothing is
    opened by the engine here and no text is read."""
    from inkguard.cli import _classify, _patient_of
    folder = Path(folder)
    if not folder.is_absolute() or not folder.is_dir():
        raise BatchError("BATCH-FOLDER", "that is not a folder on this computer")
    found = []
    total_bytes = 0
    for p in sorted(folder.rglob("*")):
        if not (p.is_file() or p.is_symlink()):
            continue
        found.append(p)
        if len(found) > MAX_FILES:
            raise BatchError("BATCH-TOO-BIG", f"more than {MAX_FILES} files")
        try:
            total_bytes += p.lstat().st_size
        except OSError:
            pass
        if total_bytes > MAX_BYTES:
            raise BatchError("BATCH-TOO-BIG", "more than 20 GB of files")
    if not found:
        raise BatchError("BATCH-EMPTY", "that folder has no files in it")

    by_patient: dict[str, list[Path]] = {}
    for p in found:
        by_patient.setdefault(_patient_of(p.relative_to(folder)), []).append(p)

    # A part covers a run of whole patients, in the same order assess() used.
    order = sorted(by_patient.items())
    first = 1
    if part:
        want = part.get("fingerprint")
        if want and want != patient_fingerprint([k for k, _g in order]):
            raise BatchError("BATCH-FOLDER-CHANGED",
                             "the folder has changed since this batch was planned")
        lo, hi = int(part.get("from", 1)), int(part.get("to", len(order)))
        order = order[max(0, lo - 1):hi]
        first = max(1, lo)
        if not order:
            raise BatchError("BATCH-EMPTY", "that part has no patients in it")
    items: list[Item] = []
    # Numbered by their place in the WHOLE folder, so P4-F1 means the same
    # patient whichever part it is in.
    for pi, (_key, group) in enumerate(order, first):
        for fi, p in enumerate(group, 1):
            kind, why = _classify(p)
            try:
                size = p.lstat().st_size
                sha = "" if kind == "link" else _hash(p)
            except OSError as exc:
                size, sha, kind = 0, "", "other"
                why = f"could not be read ({type(exc).__name__})"
            items.append(Item(id=f"P{pi}-F{fi}", patient=pi, source=str(p.relative_to(folder)),
                              sha256=sha, size=size, kind=kind, reason=why))
    summary = {"files": len(items), "patients": len(order), "bytes": total_bytes,
               "to_read": sum(1 for i in items if i.kind in ("text", "document"))}
    return items, summary


def guard(folder: Path, item: Item) -> bool:
    """The intake guard, before anything opens the file. False means set
    aside with a reason the doctor can read and a code the IT person can
    look up."""
    from inkguard import zircon
    try:
        v = zircon.inspect(folder / item.source)
    except Exception as exc:  # noqa: BLE001 — the guard must never stop a batch
        v = zircon.Verdict(zircon.VERDICT_REFUSE, "ZC-PARSE",
                           f"this file could not be checked safely ({type(exc).__name__}); "
                           "it was not opened")
    item.code, item.notes = v.code, list(v.notes)
    if v.ok:
        return True
    item.outcome, item.reason = "set_aside", v.reason
    return False


# ---------------------------------------------------------------------------
# Reading and redacting one patient's files
# ---------------------------------------------------------------------------

def read_one(folder: Path, item: Item) -> str:
    """The text of one file. A document is read by calcite; a text note is
    read as text. The file itself is never changed."""
    from inkguard import calcite
    src = folder / item.source
    text = (calcite.read_text(src) if item.kind == "document"
            else calcite.decode_text(src.read_bytes()))
    if len(text) > MAX_NOTE_CHARS:
        raise BatchError("BATCH-FILE-TOO-LONG", "this file holds more text than one note should")
    if longest_run(text) > MAX_UNBROKEN_RUN:
        raise BatchError("BATCH-FILE-ENCODED", "this file holds a very long run of characters "
                                               "with no spaces")
    return text


def longest_run(text: str) -> int:
    """The longest stretch with no space, tab or line break in it."""
    longest = run = 0
    for ch in text:
        run = 0 if ch.isspace() else run + 1
        if run > longest:
            longest = run
    return longest


def unchanged(folder: Path, item: Item) -> bool:
    """The file is still the one that was taken stock of. A batch can be
    picked up days later, so what is redacted must be what the batch
    recorded — otherwise the report would name a file that has moved on."""
    if not item.sha256:
        return True                                   # a link: never read anyway
    try:
        return _hash(folder / item.source) == item.sha256
    except OSError:
        return False


def prepare_patient(folder: Path, items: list[Item], *, patient_key: str, profile: str,
                    first_note: int, should_stop=None) -> tuple[list[dict], dict]:
    """Read one patient's files, run the engine over them together (the
    whole-patient read: names learned from every file are used on all of
    them), and return one review payload per file.

    If the engine stops on this patient, nothing of theirs is half-done:
    every one of their files is set aside with the same plain reason."""
    from inkguard import chert, contract
    lds = contract.profile_info(profile)["is_still_phi"]
    texts: list[str] = []
    readable: list[Item] = []
    for it in items:
        if should_stop is not None and should_stop():
            return [], {"chars": 0, "stopped": True}
        if not unchanged(folder, it):
            it.outcome = "set_aside"
            it.reason = ("this file changed on disk after the batch was started; it was not read, "
                         "so nothing in the report can be out of step with your file")
            continue
        try:
            text = read_one(folder, it)
        except BatchError as e:
            it.outcome = "set_aside"
            it.code = e.code
            it.reason = ("this file holds a very long run of characters with no spaces — usually "
                         "an embedded image or encoded data rather than a note; it was not read"
                         if e.code == "BATCH-FILE-ENCODED"
                         else "this file holds more text than one note should")
            continue
        except Exception as exc:  # noqa: BLE001
            it.outcome, it.reason = "set_aside", f"could not be read ({type(exc).__name__})"
            continue
        it.chars = len(text)
        texts.append(text)
        readable.append(it)
    if not readable:
        return [], {"chars": 0}

    try:
        results, _ctx = contract.process_patient(texts, patient_key=patient_key,
                                                 profile=profile, mode="mask",
                                                 should_stop=should_stop)
    except contract.Stopped:
        return [], {"chars": 0, "stopped": True}
    except Exception:  # noqa: BLE001 — the engine's own words never leave the oplog
        for it in readable:
            it.outcome, it.reason = "failed", contract.BLOCKED_REASON
        return [], {"chars": sum(len(t) for t in texts)}

    notes = []
    for n, (it, text, res) in enumerate(zip(readable, texts, results)):
        nid = f"n{first_note + n:05d}"
        payload, sanitized = chert.note_payload(text, res, lds)
        if payload["status"] == "ready" and chert._render(sanitized, payload["removals"], lds) != res.clean_text:
            # The review screens render from these marks; if that cannot
            # reproduce the engine's own output exactly, the note is held.
            payload.update(status="blocked", block_reason=contract.BLOCKED_REASON,
                           removals=[], generalized=[], kept=[], questions=[], rare=[])
        it.note_id, it.outcome = nid, "prepared"
        # The note's own words, as the reviewers will read them. Patient
        # text, so it lives in the pool and nowhere else.
        notes.append({"id": nid, "item": it.id, "patient": it.patient, "text": sanitized,
                      "chars": len(text), "pages": pages(len(text)), **payload})
    return notes, {"chars": sum(len(t) for t in texts), "reads": _ctx.reads}


# ---------------------------------------------------------------------------
# A batch's own record, in its work area
# ---------------------------------------------------------------------------

def new_id() -> str:
    return "b" + secrets.token_hex(6)


def save_items(work: WorkArea, items: list[Item]) -> None:
    work.put("items", [asdict(i) for i in items])


def load_items(work: WorkArea) -> list[Item]:
    return [Item(**d) for d in (work.get("items") or [])]


def counts(items: list[Item]) -> dict:
    """What the screens show about a batch, with no paths or names."""
    out = {"files": len(items), "patients": len({i.patient for i in items}),
           "prepared": 0, "set_aside": 0, "system": 0, "failed": 0, "pending": 0,
           "chars": 0}
    for i in items:
        out[i.outcome] = out.get(i.outcome, 0) + 1
        out["chars"] += i.chars
    out["pages"] = pages(out["chars"])
    return out


def set_aside_rows(items: list[Item]) -> list[dict]:
    """Every file that was not read, with the reason — shown to the doctor
    and listed in the review report. These can never be exported."""
    return [{"id": i.id, "patient": i.patient, "kind": i.kind, "reason": i.reason,
             "code": i.code, "outcome": i.outcome}
            for i in items if i.outcome in ("set_aside", "failed", "system")]


# ---------------------------------------------------------------------------
# Review: one reviewer's decisions on one note, kept in the pool
# ---------------------------------------------------------------------------
#
# Two people review every note independently, and neither sees the other's
# answers until the doctor settles the batch. Their decisions live in the
# pool beside the notes, one piece per note per reviewer, so a long batch is
# saved as it goes and nothing is held in a screen.

REVIEWER_SLOTS = ("R1", "R2")
MAX_ADDS = 500                        # marks a reviewer may add to one note
MAX_WORDS = 200                       # characters in a reviewer's own word for an item


def note_key(nid: str) -> str:
    return "note-" + nid


def decision_key(slot: str, nid: str) -> str:
    if slot not in REVIEWER_SLOTS:
        raise BatchError("APP-BAD-REQUEST", "not a reviewer slot")
    return f"dec-{slot}-{nid}"


def load_note(work: WorkArea, nid: str) -> dict:
    n = work.get(note_key(nid))
    if not n:
        raise BatchError("REVIEW-NOTE-GONE", "that note is not in this batch")
    return n


def load_decision(work: WorkArea, slot: str, nid: str) -> dict:
    return work.get(decision_key(slot, nid)) or {}


def save_decision(work: WorkArea, slot: str, nid: str, st: dict) -> None:
    work.put(decision_key(slot, nid), st)


def check_decision(note: dict, given, add_types: dict, answers: set) -> dict:
    """What a screen sends back for one note, checked field by field before
    anything is kept. Anything unexpected is refused whole rather than
    half-trusted: these answers decide what leaves the building."""
    if not isinstance(given, dict):
        raise BatchError("APP-BAD-REQUEST", "not a decision")
    ids = {r["id"] for r in note.get("removals", [])}
    certain = {r["id"] for r in note.get("removals", []) if r.get("certain")}
    qids = {q["id"] for q in note.get("questions", [])}
    out: dict = {}
    for key in ("keep", "unsure"):
        v = given.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise BatchError("APP-BAD-REQUEST", f"{key} is malformed")
        unknown = set(v) - ids
        if unknown:
            raise BatchError("APP-BAD-REQUEST", f"{key} names something that is not in this note")
        out[key] = sorted(set(v))
    if set(out["keep"]) & set(out["unsure"]):
        raise BatchError("APP-BAD-REQUEST", "the same mark is both kept and doubted")
    # Some identifiers are never a judgement call — a record number, an email
    # address, a word under a "Patient:" label. The screens do not offer
    # "keep" for those, and the service does not accept it either.
    if set(out["keep"]) & certain:
        raise BatchError("REVIEW-CERTAIN", "that one cannot be kept")

    adds = given.get("add", [])
    if not isinstance(adds, list) or len(adds) > MAX_ADDS:
        raise BatchError("APP-BAD-REQUEST", "too many marks added")
    length = len(note.get("text", ""))
    clean_adds = []
    for a in adds:
        if not (isinstance(a, dict) and isinstance(a.get("start"), int)
                and isinstance(a.get("end"), int) and not isinstance(a.get("start"), bool)
                and not isinstance(a.get("end"), bool)
                and 0 <= a["start"] < a["end"] <= length and a.get("type") in add_types):
            raise BatchError("APP-BAD-REQUEST", "a mark the reviewer added is malformed")
        clean_adds.append({"start": a["start"], "end": a["end"], "type": a["type"]})
    out["add"] = clean_adds

    # The unsure pile: for each word the tool could not name, the reviewer
    # says whether it goes, and may say what they think it is — ticked from
    # the list, or typed in their own words. Both are kept: the answer
    # decides this note, and what they call it tells us where the tool is weak.
    ans, kinds, words = {}, {}, {}
    for qid, v in (given.get("answers") or {}).items():
        if qid not in qids or v not in answers:
            raise BatchError("APP-BAD-REQUEST", "an answer is for something not asked")
        ans[qid] = v
    for qid, v in (given.get("kinds") or {}).items():
        if qid not in qids or v not in add_types:
            raise BatchError("APP-BAD-REQUEST", "a kind is not one of the kinds offered")
        kinds[qid] = v
    for qid, v in (given.get("words") or {}).items():
        if qid not in qids or not isinstance(v, str) or len(v) > MAX_WORDS:
            raise BatchError("APP-BAD-REQUEST", "a reviewer's own words are too long")
        if v.strip():
            words[qid] = v.strip()
    out["answers"], out["kinds"], out["words"] = ans, kinds, words

    flags = given.get("fb") or {}
    if not isinstance(flags, dict) or not all(isinstance(v, bool) for v in flags.values()):
        raise BatchError("APP-BAD-REQUEST", "a note flag is malformed")
    out["fb"] = {k: v for k, v in flags.items() if v}
    return out


def unanswered(note: dict, st: dict) -> list[str]:
    """Which of this note's questions the reviewer has not answered. Every
    one must be answered before they can sign the batch off: a word the tool
    could not name is exactly where a person is needed."""
    answered = set((st.get("answers") or {}).keys())
    return [q["id"] for q in note.get("questions", []) if q["id"] not in answered]


# ---------------------------------------------------------------------------
# Taking the measure of a folder before anything is read
# ---------------------------------------------------------------------------
#
# Before a batch is started, the doctor is told what they are taking on:
# how many patients, how many pages, and roughly how long each reviewer
# will be at it. A folder bigger than one sitting is split into parts on
# patient boundaries — never through the middle of a patient, because the
# whole-patient read is what lets a name learned in one note catch it in
# another. Chunking protects the HUMAN review, which is where accuracy
# really falls off; the engine reads one patient at a time either way.

# How much text a file of a given kind holds, as a share of its size on
# disk. A rough guide for planning only — the real count comes from the
# text once it is read.
TEXT_SHARE = {".txt": 1.0, ".text": 1.0, ".md": 1.0, ".csv": 0.9, ".tsv": 0.9,
              ".docx": 0.09, ".docm": 0.09, ".xlsx": 0.07, ".xlsm": 0.07, ".pdf": 0.02}
PAGES_PER_SITTING = 120               # about two hours for one reviewer
MIN_PART_PAGES = 20                   # never make a part trivially small
SECONDS_PER_PAGE_FLOOR = 45           # the quick end of a careful read, for a "at least" estimate


def estimate_pages(path: Path, size: int) -> int:
    share = TEXT_SHARE.get(path.suffix.lower())
    return pages(int(size * share)) if share else 0


def assess(folder: Path, *, per_part: int = PAGES_PER_SITTING) -> dict:
    """A quick look at a folder: what is in it, how big the reading is, and
    how it should be split. Nothing is opened by the engine and no text is
    read — only names, sizes and types."""
    from inkguard.cli import _classify, _patient_of
    folder = Path(folder)
    if not folder.is_absolute() or not folder.is_dir():
        raise BatchError("BATCH-FOLDER", "that is not a folder on this computer")
    groups: dict[str, dict] = {}
    files = unreadable = total_bytes = 0
    for p in sorted(folder.rglob("*")):
        if not (p.is_file() or p.is_symlink()):
            continue
        files += 1
        if files > MAX_FILES:
            raise BatchError("BATCH-TOO-BIG", f"more than {MAX_FILES} files")
        try:
            size = p.lstat().st_size
        except OSError:
            size = 0
        total_bytes += size
        key = _patient_of(p.relative_to(folder))
        g = groups.setdefault(key, {"files": 0, "pages": 0, "read": 0})
        g["files"] += 1
        if _classify(p)[0] in ("text", "document"):
            g["read"] += 1
            g["pages"] += estimate_pages(p, size)
        else:
            unreadable += 1
    if not files:
        raise BatchError("BATCH-EMPTY", "that folder has no files in it")
    if total_bytes > MAX_BYTES:
        raise BatchError("BATCH-TOO-BIG", "more than 20 GB of files")
    order = sorted(groups.items())
    if not any(g["read"] for _k, g in order):
        raise BatchError("BATCH-NOTHING-READ", "nothing in that folder can be read")

    # Parts are runs of whole patients, each about one sitting's reading.
    per_part = max(MIN_PART_PAGES, int(per_part))
    parts, start, pages_in, files_in = [], 1, 0, 0
    for i, (_key, g) in enumerate(order, 1):
        pages_in += g["pages"]
        files_in += g["files"]
        last = i == len(order)
        if (pages_in >= per_part and not last) or last:
            parts.append({"from": start, "to": i, "patients": i - start + 1,
                          "pages": pages_in, "files": files_in})
            start, pages_in, files_in = i + 1, 0, 0
    total_pages = sum(p["pages"] for p in parts)
    mark = patient_fingerprint([k for k, _g in order])
    for p in parts:
        p["fingerprint"] = mark
    return {"files": files, "patients": len(order), "pages": total_pages,
            "unreadable": unreadable, "bytes": total_bytes, "fingerprint": mark,
            "parts": parts, "per_part": per_part}


def at_least_minutes(pages_count: int, seconds_per_page: float | None = None) -> int:
    """How long a careful read takes at the quick end — said as a floor, so
    nobody is told a job is shorter than it is."""
    rate = min(float(seconds_per_page or SECONDS_PER_PAGE_FLOOR), SECONDS_PER_PAGE_FLOOR)
    return max(1, int(pages_count * rate / 60))


# What the tool measured on its own test sets, in the words the review
# report and the honest-claims rule allow: a figure is never shown without
# the test set it came from. Kept here so the screens, the assessment and
# the report all quote the same numbers from one place.
ACCURACY = {
    "as_of": "v0.25.0",
    "lines": [
        # The hardest set first. Notes written to break de-identification
        # tools (capitals, nicknames, word-names, pasted fax pages, family
        # members by relation, numbers read aloud) score lower than clean
        # benchmarks, and that is the figure a practice should plan around.
        {"test": "Messy notes: the held-out half of an invented set (72 patients, 289 notes) "
                 "written to break de-identification tools, never used for tuning",
         "result": "90.5% of the identifiers Safe Harbor requires, removed by the tool alone"},
        # A 1,000-surname figure was withdrawn: its test inputs were not
        # shipped, so nobody could reproduce it.
        {"test": "ASQ-PHI, a published external benchmark (synthetic queries)",
         "result": "97.1% on the half never used for tuning; 96.8% on the half used for tuning"},
        # The synthetic best case (12 languages, 200 documents) is deliberately
        # NOT shown here: a perfect score on a screen teaches a reviewer the
        # tool catches everything, which is the opposite of what a review is
        # for.
    ],
    "plain": ("On a published external test set the tool removes about 97 of every 100 "
              "identifiers; on deliberately messy notes, about 90. The rest is what your review is "
              "for: anything it is unsure about is put in front of a person, and in this review "
              "nothing leaves until two reviewers and the doctor have signed."),
}
