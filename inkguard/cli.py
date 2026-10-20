"""
THE COMMAND LINE: batch redaction of a folder of notes, or of a CSV.

WHY THIS EXISTS
---------------
For a researcher holding a folder of notes, or a CSV with a
`transcription` column: the redaction engine, run over files, on their own
computer, offline.

THE AUDIT TRAIL IS PART OF THE OUTPUT
-------------------------------------
The output is redacted text PLUS an account of what was done to it,
because an IRB or a data use agreement may ask exactly that. The audit
trail states which method was followed, what was removed, what was
deliberately retained and under which rule, and which files a person should
look at again. It documents the work; it does not certify the result.

EVERY FILE ACCOUNTED FOR
------------------------
Every file in the input folder is listed, by patient folder, and given
exactly one outcome: redacted with no flags, redacted and flagged, not
processed, or a system file (also copied to the not-processed folder). Every
file received comes out in exactly one of three tagged folders (see
cli_report.py); after the run the list is checked against the folder and the
disk again.

FAIL-CLOSED, PER FILE
---------------------
A file that cannot be cleaned is written to neither redacted folder. An
exact copy goes to the not-processed folder, it is recorded, and the run
continues. The
alternative — skipping quietly, or worse, copying the original through —
would mean a corpus that silently contains raw PHI in exactly the files
the scanner found hardest.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".text", ".md"}


# =====================================================================
# RUN ACCOUNTING
# =====================================================================

@dataclass
class RunStats:
    profile: str = "safe_harbor"
    started: float = field(default_factory=time.time)
    total: int = 0
    processed: int = 0
    failed: int = 0
    skipped_existing: int = 0
    entity_counts: dict[str, int] = field(default_factory=dict)
    linkage_review: int = 0
    linkage_caution: int = 0
    contaminated: int = 0
    cross_system: int = 0
    dates_shifted: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    redaction_mode: str = "mask"
    exit_grades: dict[str, int] = field(default_factory=dict)
    quarantined: int = 0
    ledger_lost: int = 0
    uncertain_masked: int = 0
    patients: int = 0
    memory_names: int = 0
    needs_review: int = 0
    review_reasons: dict[str, int] = field(default_factory=dict)
    file_mode: bool = True
    files_read: int = 0
    review_tool: int = 0        # flagged words that are the tool's gap
    review_writing: int = 0     # flagged words that are unclear writing
    review_either: int = 0      # abbreviations: the tool's gap or local shorthand
    lds_towns_removed: int = 0      # LDS: towns removed (no place evidence, or a name)
    lds_overlaps_removed: int = 0   # LDS: a person/ID read inside a kept date or town
    files: list = field(default_factory=list)          # FileEntry per input file
    doc_summaries: list = field(default_factory=list)  # Word, Excel, PDF, CSV: counts only
    accounting: dict = field(default_factory=dict)
    renamed_outputs: int = 0
    local_dir: str = ""             # the not-processed pile
    originals_unchanged: bool | None = None   # fingerprints re-checked after the run
    run_id: str = ""                # ties every file to its manifest and report
    stamp: bool = True              # write the provenance line (off with --no-header)
    stamped: int = 0
    guard_refused: int = 0          # files the intake guard would not open
    guard_notes: dict = field(default_factory=dict)   # active content noted and removed, by code
    guard_log: list = field(default_factory=list)     # PHI-free (id, verdict, code, reason)
    out_dir: str = ""               # the results folder (for fingerprints in the audit trail)
    run_record: dict = field(default_factory=dict)   # who ran it, for whom, where, when (cli_attest)
    locations: dict = field(default_factory=dict)   # input and results folders, for the audit trail
    started_over: bool = False      # an unfinished earlier run was deleted first
    scramble: bool = False          # --scramble-names

    def add_entities(self, hits) -> None:
        for h in hits:
            t = getattr(h, "entity_type", "UNKNOWN")
            self.entity_counts[t] = self.entity_counts.get(t, 0) + 1


# The report, the guide, the manifest and the problem report: cli_report.py.
from inkguard import topaz
from inkguard.cli_report import (  # noqa: F401 — the report lives there; re-exported
    LDS_REVIEW_GUIDANCE,
    NOT_REVIEWED,
    NOTICE_FILE,
    UNFINISHED_TEXT,
    MANIFEST_FILE,
    PROBLEM_FILE,
    TAG_DONE, TAG_FLAGGED, TAG_NOT, PILE_DONE, PILE_FLAGGED, PILE_NOT, PILES, PILE_TAG, TAG_SEP,
    GUIDE_FILE, EVIDENCE_FILE, TECH_DIR, REPORT_JSON, SUPPORT_LOG, GUARD_LOG, SETTINGS_FILE,
    WORDS_FILE, KEY_FILE, PILE_NOTE, SITE, ISSUES, cite_text, CITE_FILE, AUDIT_FILE, _piles,
    REPORT_EMAIL,
    _computer,
    _run_codes,
    problem_report_text,
    check_feedback,
    CLOSING_LINES,
    WEBSITE,
    FEEDBACK_FILE,
    write_output_manifest,
    NOT_OURS, STATUS_DONE, STATUS_FLAGGED,
    _manifest_status,
    _run_outputs,
    _opaque,
    _clean,
    _verify,
    _fingerprint,
    _methods,
    _lapis_incomplete,
    _version,
    _provenance,
    _stamp,
    guide_text,
    build_report,
    _files_section,
    _documents_section,
    write_audit,
    write_feedback,
    feedback_record,
)



# =====================================================================
# PROCESSING
# =====================================================================

def _count_lds(stats: RunStats, result) -> None:
    for h in result.hits or ():
        if getattr(h, "layer", "") != "lds_conflict":
            continue
        if "kept_type_overlap" in (getattr(h, "evidence", ()) or ()):
            stats.lds_overlaps_removed += 1
        else:
            stats.lds_towns_removed += 1


def _record(stats: RunStats, result) -> None:
    _count_lds(stats, result)
    stats.add_entities(result.hits)
    ex = getattr(result, "exit_report", None)
    if ex is not None:
        stats.exit_grades[ex.grade] = stats.exit_grades.get(ex.grade, 0) + 1
        stats.uncertain_masked += ex.uncertain_masked
    lk = getattr(result, "linkage", None)
    if lk is not None:
        if lk.verdict == "review":
            stats.linkage_review += 1
        elif lk.verdict == "caution":
            stats.linkage_caution += 1
    cont = getattr(result, "contamination", None)
    if cont is not None:
        if cont.contaminated:
            stats.contaminated += 1
        if cont.cross_system:
            stats.cross_system += 1
    ds = getattr(result, "date_shift_report", None)
    if ds is not None:
        stats.dates_shifted += ds.shifted


def _reason_text(result) -> str:
    comp = getattr(result, "comprehension", None)
    if comp is None:
        return "review"
    c = comp.summary()["reasons"]
    return ", ".join(f"{k} {v}" for k, v in sorted(c.items()))


# How flagged words are presented to the reviewer: cli_review.py.
from inkguard.cli_review import (  # noqa: F401 — re-exported
    REVIEW_REASONS,
    review_side,
    review_list,
)



def _count_reasons(stats: RunStats, result) -> None:
    comp = getattr(result, "comprehension", None)
    for n in (comp.blocking if comp else []):
        stats.review_reasons[n.reason] = stats.review_reasons.get(n.reason, 0) + 1
        side = review_side(n.reason)
        if side == "tool":
            stats.review_tool += 1
        elif side == "writing":
            stats.review_writing += 1
        else:
            stats.review_either += 1


def _file_context(texts, patient_key, profile, stats: RunStats):
    """PASS 1 of the patient-file mode, through the contract."""
    from inkguard import contract
    if not stats.file_mode:
        return None, patient_memory(texts)
    ctx = contract.learn(list(texts), patient_key=patient_key, profile=profile)
    stats.files_read += 1
    return ctx, set(ctx.names)


def _evaluate(engine, text, ctx, *, patient_key=None, profile_name=None, memory=None,
              redaction_mode="mask", want_oplog=False):
    """Run the engine over one note through the contract (never the engine
    directly). Returns the engine's own rich result, which the batch tool's
    reporting reads; `engine` is the binding the shell was handed by
    contract.engine()."""
    from inkguard import contract
    return contract.deidentify(
        text, profile=profile_name or "safe_harbor", mode=redaction_mode,
        patient_key=patient_key, memory=memory, knowledge=ctx,
        want_oplog=want_oplog, engine=engine).raw


def _write_review_list(out_dir: Path, patient_key: str, held_rows: list[dict]) -> None:
    if not held_rows:
        return
    d = out_dir / PILE_FLAGGED / patient_key
    d.mkdir(parents=True, exist_ok=True)
    (d / WORDS_FILE).write_text(review_list(patient_key, held_rows), encoding="utf-8")


def _tagged(pile: str, rel: Path) -> Path:
    """Where a file goes in a pile: its folders kept, its tag at the front."""
    rel = Path(rel)
    return Path(pile, *rel.parts[:-1], PILE_TAG[pile] + TAG_SEP + rel.name)


def done_path(out_dir: Path, rel) -> Path:
    """Where a cleaned, unflagged copy of `rel` is written."""
    return Path(out_dir) / _tagged(PILE_DONE, Path(rel))


def flagged_path(out_dir: Path, rel) -> Path:
    """Where a flagged copy of `rel` is written."""
    return Path(out_dir) / _tagged(PILE_FLAGGED, Path(rel))


def not_processed_path(out_dir: Path, rel) -> Path:
    """Where the copy of a file not processed is written."""
    return Path(out_dir) / _tagged(PILE_NOT, Path(rel))


def _base(rel: Path) -> Path:
    """The neutral name of an output, without its pile and tag."""
    parts = list(Path(rel).parts)
    if parts and parts[0] in PILES:
        tag = PILE_TAG[parts.pop(0)] + TAG_SEP
        if parts and parts[-1].startswith(tag):
            parts[-1] = parts[-1][len(tag):]
    return Path(*parts) if parts else Path(rel)


def _add_lines(words: list[dict], text: str) -> list[dict]:
    """Give each word to check the lines of the flagged copy it is on, so a
    reviewer can go straight to it."""
    lines = text.splitlines()
    kept = []
    for w in words:
        key = ((w.get("word") or "").strip().split("\n")[0])[:60].strip()
        if not key:
            continue
        hits = [i + 1 for i, ln in enumerate(lines) if key in ln] or \
               [i + 1 for i, ln in enumerate(lines) if key.lower() in ln.lower()]
        if not hits and text:
            continue        # flagged on an earlier read, removed by a later one: nothing to check
        w["lines"] = hits[:5]
        if len(hits) > 5:
            w["more_lines"] = len(hits) - 5
        kept.append(w)
    return kept


def _text_of(path: Path) -> str:
    """The text of a written copy, to find line numbers in. A flattened PDF's
    text is in the .txt beside it."""
    try:
        side = path.with_name(path.name + ".txt")
        if side.exists():
            return side.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() in TEXT_SUFFIXES:
            return path.read_text(encoding="utf-8", errors="replace")
        from inkguard import calcite
        return calcite.read_text(path)
    except Exception:  # noqa: BLE001 — line numbers are a help, never a stop
        return ""


def _words_to_check(text: str, result) -> list[dict]:
    """The words a reviewer must look at, read from the note by offset.
    This list stays in the flagged pile on this computer, like the draft."""
    from inkguard import contract
    comp = getattr(result, "comprehension", None)
    if comp is None:
        return []
    st = contract.sanitize(text)
    blocking = set(map(id, comp.blocking))
    out = [{"word": st[n.start:n.end], "reason": n.reason, "start": n.start, "end": n.end,
            "holds_the_note": id(n) in blocking}
           for n in comp.not_understood]
    # A newsworthy event: the whole sentence is the thing to check, and the
    # suggestion is to remove it.
    for kind, s, e in contract.newsworthy_events(st):
        out.append({"word": st[s:e], "reason": "newsworthy_event", "start": s, "end": e,
                    "holds_the_note": False,
                    "suggestion": f"{kind}: the kind of event may stay; consider removing what "
                                  "pins it down"})
    for what, s, e in contract.event_details(st):
        out.append({"word": st[s:e], "reason": "newsworthy_event", "start": s, "end": e,
                    "holds_the_note": False,
                    "suggestion": f"the event's {'name' if what == 'name' else 'numbers'}: "
                                  "suggested for removal"})
    return out


def _hold_for_review(out_dir: Path, rel: Path, text: str, result, stats: RunStats) -> None:
    """A redacted draft with flagged words goes to the flagged folder,
    with the words a person must check and the line each is on."""
    dst = out_dir / _tagged(PILE_FLAGGED, rel)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(_clean(result), encoding="utf-8")
    _stamp(dst, stats, held=True)
    stats.needs_review += 1
    _count_reasons(stats, result)
    _count_lds(stats, result)
    return {"file": dst.name, "words_to_check": _add_lines(_words_to_check(text, result), _text_of(dst))}


# Plain-English reasons the SHELL itself composes for a stopped file. Each
# describes the FILE — it would not open, it is a scan, its pages would not
# count the same way twice — never the detection engine, so each can be
# put in a report that may be shared, and useful to whoever helps with the
# problem. The list is a WHITELIST on purpose: anything not on
# it is treated as engine-internal and collapsed to a generic line, so a new
# internal message can never leak by being forgotten here (fail closed).
_OPERATIONAL_SIGNATURES = (
    "read error", "could not be read", "clean copy did not open",
    "clean copy does not read as expected", "could not be normalised in place",
    "could not be placed on the file exactly", "pdf looks scanned",
    "pages could not be counted", "flattened copy still carries text",
    "page is rotated or cropped", "runs sideways or at an angle",
    "set one character at a time", "drawn as shapes rather than text",
    "page check (ocr) is not available", "workbook could not be read",
    "table could not be read", "pdf could not be read",
    "review list not written", "could not be copied aside",
)
_GENERIC_FAILURE = "the tool stopped on this file as a precaution"


def _public_failure(err: str) -> str:
    """Translate a raw failure string into one that can be shared.

    The raw string is read only to CLASSIFY a failure into a count (see
    _fail); it is never stored, because an engine stop carries internal
    stage names that mean nothing to a reader. We keep the shell's own plain operational
    reason when there is one, keep a bare exception TYPE when an unexpected
    error crashed our file handling (never the message), and return one generic line for everything else."""
    low = err.lower()
    if any(sig in low for sig in _OPERATIONAL_SIGNATURES):
        return err
    head = err.split(":", 1)[0].strip()
    if (head and head.replace("_", "").isalnum() and head[:1].isupper()
            and head.endswith(("Error", "Exception", "Warning"))):
        return f"the tool stopped unexpectedly ({head})"
    return _GENERIC_FAILURE


def _fail(stats: RunStats, where: str, err: str) -> None:
    """Record a failed document, classified by which safeguard stopped it.
    Failures are never passed through: no redacted copy of the file is
    written.

    ``err`` is the raw reason and may name an internal engine stage; it is
    used here only to pick the right count, and is passed through
    _public_failure before it is stored, so the shareable report and manifest
    never carry engine-internal vocabulary."""
    stats.failed += 1
    if err.startswith("exit_inspection") or "(exit_inspection" in err:
        stats.quarantined += 1
        stats.exit_grades["F"] = stats.exit_grades.get("F", 0) + 1
    elif err.startswith("ledger") or "(ledger:" in err:
        stats.ledger_lost += 1
    stats.failures.append((where, _public_failure(err)))


def patient_memory(texts) -> set[str]:
    """SEED NAMES across one patient's notes (shale).

    Only the structural layers (labels, titles, age frames) — regular
    expressions, no model — so this costs almost nothing. They are the
    only layers trusted to supply seed names. The result lives in RAM for
    this one patient and is discarded before the next patient begins.
    """
    from inkguard import contract
    return contract.seed_names(texts)


# ---- every file accounted for: every file is counted for every patient and
# checked, so no whole file is missed. Images are not read: they are copied
# to the not-processed folder, so the person knows they are separate,
# sensitive and not to be exported.

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif",
                  ".webp", ".jp2", ".dcm", ".dicom", ".svg"}
OLD_OFFICE = {".doc", ".xls", ".ppt", ".pptx", ".odt", ".ods", ".pages", ".numbers", ".wpd", ".rtf"}
SYSTEM_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "ehthumbs.db"}
# Copies of the files not processed, and the key that names the originals,
# are written to 3_NOT-PROCESSED-MAY-CONTAIN-PHI inside the results folder.
# That folder must never be shared or copied off the computer; the guide
# says so.


def local_only_dir(out_dir: Path) -> Path:
    """The not-processed pile, which holds what must stay on this computer:
    copies of the files not redacted, and the key to the original names.
    Only the three folders hold input material, all in the one results
    folder."""
    return out_dir / PILE_NOT


@dataclass
class FileEntry:
    """One input file and what became of it."""
    id: str                 # P<patient>-F<file>: used in the report instead of the path
    patient: int
    source: str             # relative path (the local manifest only)
    sha256: str
    size: int
    kind: str               # text | document | image | other | system | link
    outcome: str = ""       # deidentified | held | set_aside | system | already_present
    output: str = ""
    reason: str = ""
    code: str = ""          # the intake guard's support code (ZC-…), if any


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _classify(p: Path) -> tuple[str, str]:
    from inkguard import calcite
    name, low, suf = p.name, p.name.lower(), p.suffix.lower()
    if p.is_symlink():
        return "link", "a link to another file (links are not followed)"
    if low in SYSTEM_NAMES or name.startswith("._"):
        return "system", "a system file"
    if name.startswith("~$"):
        return "system", "an Office lock file"
    if suf in TEXT_SUFFIXES:
        return "text", ""
    if suf in calcite.DOC_SUFFIXES:
        return "document", ""
    if suf in IMAGE_SUFFIXES:
        return "image", "an image or scan: the tool reads typed text only"
    if suf in OLD_OFFICE:
        return "other", f"a {suf} file: save it as .docx, .xlsx or .pdf to have it read"
    if name.startswith("."):
        return "other", "a hidden file"
    return "other", f"a file type the tool does not read ({suf or 'no extension'})"


# Names the tool writes for itself in the output folder. An input file or
# folder with one of these names is renamed on the way out, never written
# over the tool's own file.
_OURS_TOP = {GUIDE_FILE.lower(), EVIDENCE_FILE.lower(), FEEDBACK_FILE.lower(), CITE_FILE.lower()}
_OURS_DIRS = {x.lower() for x in PILES + (TECH_DIR,)}


def _not_ours(path: Path) -> Path:
    parts = list(path.parts)
    if not parts:
        return path
    if len(parts) > 1 and parts[0].lower() in _OURS_DIRS:
        parts[0] = parts[0] + "-files"
    if len(parts) == 1 and parts[0].lower() in _OURS_TOP:
        p = Path(parts[0])
        parts[0] = f"{p.stem}-input{p.suffix}"
    if parts[-1].lower() in (WORDS_FILE.lower(), KEY_FILE.lower(), PILE_NOTE.lower()):
        p = Path(parts[-1])
        parts[-1] = f"{p.stem}-input{p.suffix}"
    return Path(*parts)


class _Names:
    """Output names that carry no patient information. A folder or file
    name the engine finds an identifier in (a name, a date, a record
    number) is replaced by a neutral one; the local manifest keeps the map."""

    def __init__(self, engine=None):
        self.engine = engine                 # the engine binding, passed to the contract
        self.cache: dict[str, bool] = {}
        self.renamed = 0
        self.n_files = 0
        self.n_dirs = 0
        self.dirs: dict[str, str] = {}
        self.used: set[str] = set()

    @staticmethod
    def _key(p: Path) -> str:
        # Mac and Windows folders ignore case, and a name can be spelled
        # with composed or decomposed accents.
        import unicodedata
        return unicodedata.normalize("NFC", str(p)).casefold()

    def claim(self, path: Path, companions: tuple[str, ...] = ()) -> Path:
        """A path no other output uses, with its companion files (".txt"
        beside a flattened PDF). "visit.docx" and "visit.docm" both clean
        to "visit.docx"; the second becomes "visit-2.docx"."""
        path = _not_ours(path)
        stem, suf = path.stem, path.suffix
        k = 1
        cand = path
        while any(self._key(cand.with_name(cand.name + c)) in self.used for c in ("",) + companions):
            k += 1
            cand = path.with_name(f"{stem}-{k}{suf}")
        for c in ("",) + companions:
            self.used.add(self._key(cand.with_name(cand.name + c)))
        return cand

    def flagged(self, name: str) -> bool:
        """Does a folder or file name carry an identifier? A name is read
        several ways, because files are named in all of them: as written
        ("2021-03-02"), with separators as spaces ("lindqvist_hubert"), and
        split at case changes so a run-together name is seen
        ("LindqvistHubert" -> "Lindqvist Hubert", "hlindqvist" is checked
        token by token). Renaming a file costs nothing; a surname left in a file
        name identifies a patient."""
        import re
        # Names Windows cannot hold (CON, AUX, COM1 ...; a trailing dot or
        # space): the folder must copy to a Windows computer or a USB stick.
        if (re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?", name)
                or name.endswith((".", " "))):
            return True
        # Underscores become spaces but hyphens and dots stay, so a date
        # ("2021-03-02") is still read as a date.
        spaced = re.sub(r"_+", " ", name).strip()
        # Split CamelCase and letter/digit runs: "OkonkwoAdaeze2021" ->
        # "Okonkwo Adaeze 2021"; "ZhouWei-labs" -> "Zhou Wei labs".
        decamel = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])",
                         " ", name)
        decamel = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", decamel)
        decamel = re.sub(r"[_\-.]+", " ", decamel).strip()
        from inkguard import contract
        variants = {name.strip(), spaced, decamel} - {""}
        for t in variants:
            if t not in self.cache:
                try:
                    r = contract.deidentify(t, mode="mask", engine=self.engine)
                    self.cache[t] = r.status == "stop" or bool(r.edits)
                except Exception:  # noqa: BLE001
                    self.cache[t] = True
        if any(self.cache[t] for t in variants):
            return True
        # A token on the name lists ("smith_a", "jones.docx", "hlindqvist")
        # is treated as a name. Each variant is split, so a run-together or
        # initial-plus-surname spelling is caught. The name lists are the
        # engine's, reached through the contract.
        from inkguard import contract
        toks = set()
        for t in variants:
            toks.update(re.split(r"[^A-Za-z'’]+", t))
            # an initial glued to a surname: "hlindqvist" -> "lindqvist"
            for w in re.split(r"[^A-Za-z'’]+", t):
                if len(w) >= 5 and contract.is_known_name(w[1:]):
                    toks.add(w[1:])
        return any(len(tok) >= 3 and contract.is_known_name(tok) for tok in toks)

    scramble = False          # --scramble-names: every name becomes a random code

    def _code(self, prefix: str) -> str:
        import secrets
        alphabet = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
        while True:
            c = f"{prefix}-" + "".join(secrets.choice(alphabet) for _ in range(5))
            if c not in self.codes:
                self.codes.add(c)
                return c

    def rel(self, rel: Path, patient_index: int, suffix: str | None = None) -> Path:
        parts = list(rel.parts)
        out = []
        if not hasattr(self, "codes"):
            self.codes = set()
        for i, part in enumerate(parts[:-1]):
            key = "/".join(parts[:i + 1])
            if key not in self.dirs:
                if self.scramble:
                    self.dirs[key] = self._code("patient" if i == 0 else "folder")
                    self.renamed += 1
                elif self.flagged(part):
                    self.n_dirs += 1
                    self.dirs[key] = (f"patient-{patient_index:04d}" if i == 0
                                      else f"folder-{self.n_dirs:04d}")
                    self.renamed += 1
                else:
                    self.dirs[key] = part
            out.append(self.dirs[key])
        stem, suf = Path(parts[-1]).stem, suffix if suffix is not None else Path(parts[-1]).suffix
        if self.scramble:
            self.renamed += 1
            stem = self._code("file")
        elif self.flagged(stem):
            self.n_files += 1
            self.renamed += 1
            stem = f"file-{self.n_files:04d}"
        out.append(stem + suf)
        return Path(*out)


def _patient_of(rel: Path) -> str:
    """One folder per patient (the top-level folder); a loose file at the
    top is its own patient."""
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def _set_aside(src: Path, in_dir: Path, local_dir: Path, entry: FileEntry, reason: str,
               outcome: str = "set_aside") -> None:
    """A file that was not redacted is copied, tagged, to the
    not-processed pile, keeping its folders and original name. It never
    fails: a file that has vanished or cannot be copied is still recorded,
    and the accounting check reports the missing copy."""
    import shutil
    entry.outcome, entry.reason, entry.output = outcome, reason, ""
    base = local_dir
    dst = None
    try:
        base.mkdir(parents=True, exist_ok=True)
        rel = _tagged(PILE_NOT, src.relative_to(in_dir))
        dst = base.parent / rel
        if src.is_symlink():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        entry.output = rel.as_posix()
    except Exception as exc:  # noqa: BLE001
        # A half-written copy (a full disk) would look like the file; remove
        # it, so the accounting check reports the copy as missing.
        try:
            if dst is not None and dst.exists() and not entry.output:
                dst.unlink()
        except Exception:  # noqa: BLE001
            pass
        entry.reason = f"{reason} (the file could not be copied aside: {type(exc).__name__})"


def _discard(paths) -> None:
    for p in paths:
        try:
            Path(p).unlink()
        except OSError:
            pass


def _guard_ok(src: Path, e: FileEntry, in_dir: Path, local_dir: Path, stats: RunStats) -> bool:
    """Run the intake guard before the engine opens a file. A refused file is
    set aside with its support code; active content the guard notes (and that
    cleaning removes) is counted. Returns True when the file may be read."""
    from inkguard import zircon
    try:
        v = zircon.inspect(src)
    except Exception as exc:  # noqa: BLE001  (the guard must never stop the run)
        v = zircon.Verdict(zircon.VERDICT_REFUSE, "ZC-PARSE",
                           f"this file could not be checked safely ({type(exc).__name__}); it was not opened")
    e.code = v.code
    for n in v.notes:
        stats.guard_notes[n] = stats.guard_notes.get(n, 0) + 1
    if v.ok:
        return True
    stats.guard_refused += 1
    stats.guard_log.append({"id": e.id, "verdict": v.status, "code": v.code, "reason": v.reason})
    _set_aside(src, in_dir, local_dir, e, v.reason)
    return False


def _outside(p: Path, excluded: tuple[Path, ...]) -> bool:
    try:
        r = p.resolve()
    except OSError:
        return True
    return not any(r == x or x in r.parents for x in excluded)


IN_PROGRESS = "RUN_IN_PROGRESS"       # in TECH_DIR while a run is going
SNAPSHOT_DIR = "snapshot"              # in TECH_DIR: the copies that are processed


def _ours_in(out_dir: Path) -> list[Path]:
    """Everything a run of Inkguard writes into a results folder, and nothing
    else: the only paths a start-over may delete."""
    names = set(PILES) | {TECH_DIR, GUIDE_FILE, AUDIT_FILE, CITE_FILE, FEEDBACK_FILE}
    return [p for p in out_dir.iterdir() if p.name in names or p.name.startswith(".work-")] \
        if out_dir.is_dir() else []


def unfinished(out_dir: Path) -> bool:
    """Did a run into this folder start and not finish?"""
    g = out_dir / GUIDE_FILE
    try:
        if g.exists() and g.read_text(encoding="utf-8") == UNFINISHED_TEXT:
            return True
    except OSError:
        return True
    return (out_dir / TECH_DIR / IN_PROGRESS).exists()


def start_over(out_dir: Path) -> int:
    """Delete what an unfinished run wrote: the copies are lost, never the
    originals, and the run starts again. Only paths
    Inkguard writes are removed; anything else in the folder is left."""
    import shutil
    n = 0
    for p in _ours_in(out_dir):
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        else:
            p.unlink()
        n += 1
    return n


def _hide(path: Path) -> None:
    """Mark Inkguard's working folder hidden on Windows too (a leading dot
    hides it on macOS and Linux)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)   # FILE_ATTRIBUTE_HIDDEN
    except Exception:  # noqa: BLE001 — cosmetic; the run goes on
        pass


def _snapshot(in_dir: Path, out_dir: Path) -> Path:
    """Copy the input into the hidden working folder and compare each copy
    with its original by SHA-256, copying once more if they differ. The
    copies are what is processed, so a file edited or synced mid-run cannot
    change the run; the originals are only read. A copy that cannot be made,
    or still differs, is not handled here: the checks after the run report
    it. The folder is deleted at the end of a run that finishes; a run
    stopped part way leaves it, and the next run into the folder deletes it."""
    import shutil
    snap = out_dir / TECH_DIR / SNAPSHOT_DIR
    if snap.exists():
        shutil.rmtree(snap)
    snap.mkdir(parents=True)
    _hide(out_dir / TECH_DIR)
    for f in _inventory(in_dir, (out_dir.resolve(),)):
        dst = snap / f.relative_to(in_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(2):
            try:
                shutil.copy2(f, dst, follow_symlinks=False)
            except OSError:
                break
            if f.is_symlink() or _sha256(dst) == _sha256(f):
                break
    return snap


def _inventory(in_dir: Path, excluded: tuple[Path, ...]) -> list[Path]:
    return sorted(p for p in in_dir.rglob("*")
                  if (p.is_file() or p.is_symlink()) and _outside(p, excluded))


def process_directory(evaluate, in_dir: Path, out_dir: Path, stats: RunStats,
                      profile: str | None, resume: bool, quiet: bool,
                      mode: str = "pseudonym", oplog_fh=None, file_mode: bool = True) -> None:
    import shutil
    import tempfile
    from inkguard import calcite
    from inkguard import topaz
    stats.file_mode = file_mode
    ind, outd = in_dir.resolve(), out_dir.resolve()
    if ind == outd or ind in outd.parents or outd in ind.parents:
        raise SystemExit("[RUN-FOLDER-OVERLAP] " + topaz.message("RUN-FOLDER-OVERLAP"))
    local_dir = local_only_dir(out_dir)
    stats.local_dir = local_dir.name
    stats.out_dir = str(out_dir)
    # An earlier run into this folder that did not finish: delete what it
    # wrote and start again from the originals.
    if unfinished(out_dir) and not resume:
        start_over(out_dir)
        stats.started_over = True
        if not quiet:
            print("  The last run into this folder did not finish. What it wrote was deleted; "
                  "starting again from your originals.", file=sys.stderr)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / TECH_DIR).mkdir(parents=True, exist_ok=True)
    (out_dir / TECH_DIR / IN_PROGRESS).write_text(stats.run_id or "running", encoding="utf-8")
    original_in = in_dir
    in_dir = _snapshot(original_in, out_dir)
    everything = _inventory(in_dir, ())
    names = _Names(evaluate)
    names.scramble = bool(getattr(stats, "scramble", False))
    out_dir.mkdir(parents=True, exist_ok=True)
    # The folder is labelled before anything is written into it, so a run
    # that is stopped part way (power, memory, a full disk) never leaves
    # unlabelled copies; the full guide replaces this at the end.
    (out_dir / NOTICE_FILE).write_text(UNFINISHED_TEXT, encoding="utf-8")

    # Re-running into a folder that already holds output. Neutral names are
    # assigned in encounter order, so if the input set changed between runs
    # the same number could land on a different patient. To keep a file's
    # output stable across runs, the previous run's manifest (source path ->
    # output path) is reused, and every file already on disk is marked used
    # so a fresh name never lands on one.
    # A resumed run keeps earlier outputs only if they were made the same
    # way: a different profile, name mode or date shifting would leave files
    # labelled one way and cleaned another.
    settings = {"profile": profile or "safe_harbor", "names": mode,
                "date_shift": bool(os.environ.get("PHI_DATE_SHIFT")),
                "opaque": _opaque(), "scramble": bool(getattr(stats, "scramble", False))}
    settings_file = out_dir / TECH_DIR / SETTINGS_FILE
    if resume and settings_file.exists():
        try:
            before = json.loads(settings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            before = None
        if before != settings:
            raise SystemExit("[RUN-RESUME-SETTINGS] " + topaz.message("RUN-RESUME-SETTINGS"))
    prior = _prior_outputs(local_dir) if resume else {}
    local_dir.mkdir(parents=True, exist_ok=True)
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(json.dumps(settings), encoding="utf-8")
    for p in out_dir.rglob("*"):
        if p.is_file():
            names.used.add(_Names._key(_base(p.relative_to(out_dir))))

    # EVERY FILE, BY PATIENT. Each one gets exactly one outcome below, and
    # the manifest is checked against the folder again at the end.
    by_patient: dict[str, list[tuple[Path, FileEntry]]] = {}
    for f in everything:
        rel = f.relative_to(in_dir)
        by_patient.setdefault(_patient_of(rel), []).append((f, None))
    stats.patients = len(by_patient)
    entries: list[FileEntry] = []
    for pi, (key, group) in enumerate(by_patient.items(), 1):
        for fi, (f, _) in enumerate(group, 1):
            kind, why = _classify(f)
            try:
                size = f.lstat().st_size
                sha = "" if kind == "link" else _sha256(f)
            except OSError as exc:
                size, sha = 0, ""
                kind, why = "other", f"could not be read ({type(exc).__name__})"
            e = FileEntry(id=f"P{pi}-F{fi}", patient=pi, source=str(f.relative_to(in_dir)),
                          sha256=sha, size=size, kind=kind, reason=why)
            entries.append(e)
            group[fi - 1] = (f, e)
    stats.files = entries
    stats.total = sum(1 for e in entries if e.kind in ("text", "document"))
    stats.doc_summaries = []

    def stop(src: Path, e: FileEntry, why: str, detail: str = "") -> None:
        _fail(stats, e.id, detail or why)
        _set_aside(src, in_dir, local_dir, e, why)

    done = 0
    for pi, (patient_key, group) in enumerate(by_patient.items(), 1):
        texts: dict[Path, str] = {}
        docs: list[tuple[Path, FileEntry]] = []
        for src, e in group:
            if e.kind == "system":
                _set_aside(src, in_dir, local_dir, e, e.reason, outcome="system")
                continue
            if e.kind in ("image", "other", "link"):
                _set_aside(src, in_dir, local_dir, e, e.reason)
                continue
            # The intake guard sees every file the engine would open, first.
            if not _guard_ok(src, e, in_dir, local_dir, stats):
                continue
            if e.kind == "text":
                try:
                    from inkguard import lapis
                    texts[src] = lapis.strip(calcite.decode_text(src.read_bytes()))
                except Exception as exc:  # noqa: BLE001
                    stop(src, e, "could not be read", f"read error: {type(exc).__name__}")
            else:
                docs.append((src, e))
        by_src = {src: e for src, e in group}
        # A very long run with no spaces is not a note (lost spaces, pasted
        # code, encoded data) and is too costly to analyse: it is set aside.
        from inkguard import granite as _granite
        doc_text: dict[Path, str] = {}
        for src, e in list(docs):
            doc_text[src] = calcite.read_text(src)
        for src in [s_ for s_, t in texts.items() if _granite.longest_run(t) > _granite.MAX_UNBROKEN_RUN]:
            stop(src, by_src[src], "this file holds a very long run of characters with no spaces")
            del texts[src]
        for src, e in [d for d in docs if _granite.longest_run(doc_text[d[0]]) > _granite.MAX_UNBROKEN_RUN]:
            stop(src, e, "this file holds a very long run of characters with no spaces")
            docs.remove((src, e))

        # READ THE WHOLE FILE FIRST. The structured first note is where a
        # name appears WITH evidence; the follow-ups are where it appears
        # without. Pass 1 learns names, workplaces and clinical words from
        # every note and document; pass 2 below redacts each one with that
        # knowledge. If pass 1 itself fails, nothing of this patient is
        # redacted without it: every file is set aside.
        try:
            learn = list(texts.values()) + [doc_text[src] for src, _ in docs]
            ctx, memory = _file_context(learn, patient_key, profile, stats)
        except Exception as exc:  # noqa: BLE001
            for src in texts:
                stop(src, by_src[src], "the tool stopped on this patient's files", f"{type(exc).__name__}")
            for src, e in docs:
                stop(src, e, "the tool stopped on this patient's files", f"{type(exc).__name__}")
            done += len(texts) + len(docs)
            continue
        stats.memory_names += len(memory)
        held_rows: list[dict] = []

        for src, text in texts.items():
            e = by_src[src]
            done += 1
            written: list[Path] = []
            try:
                rel = src.relative_to(in_dir)
                # Reused only if the source is the same file, byte for byte:
                # a note edited since the last run is cleaned again.
                reuse, was = prior.get(str(rel), ("", ""))
                if resume and reuse and was == e.sha256 and (out_dir / reuse).exists():
                    stats.skipped_existing += 1
                    names.used.add(_Names._key(_base(Path(reuse))))
                    e.outcome, e.output = "already_present", reuse
                    continue
                safe = names.claim(names.rel(rel, pi))
                dst = out_dir / _tagged(PILE_DONE, safe)
                try:
                    result = _evaluate(evaluate, text, ctx, patient_key=patient_key,
                                       profile_name=profile, memory=memory, redaction_mode=mode,
                                       want_oplog=oplog_fh is not None)
                except Exception as exc:  # noqa: BLE001
                    stop(src, e, "the tool stopped on this file", f"{type(exc).__name__}: {exc}")
                    continue
                if oplog_fh is not None and getattr(result, "oplog", None):
                    oplog_fh.write(json.dumps(result.oplog) + "\n")
                if result.action.value == "blocked":
                    # The set-aside reason (shareable) is generic; the raw
                    # engine error is passed only as the classification detail
                    # and is never stored (see _fail / _public_failure).
                    stop(src, e, _GENERIC_FAILURE, result.error or "blocked by policy")
                    continue
                if result.action.value == "review":
                    written.append(out_dir / _tagged(PILE_FLAGGED, safe))
                    held_rows.append(_hold_for_review(out_dir, safe, text, result, stats))
                    e.outcome, e.output = "held", _tagged(PILE_FLAGGED, safe).as_posix()
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                written.append(dst)
                dst.write_text(_clean(result), encoding="utf-8")
                _stamp(dst, stats, held=False)
                e.outcome, e.output = "deidentified", _tagged(PILE_DONE, safe).as_posix()
                stats.processed += 1
                _record(stats, result)
            except Exception as exc:  # noqa: BLE001
                # One bad file never stops the run.
                _discard(written)
                stop(src, e, "the tool stopped on this file", f"{type(exc).__name__}: {exc}")
            if not quiet:
                print(f"  {done}/{stats.total} …", end="\r", file=sys.stderr)

        for src, e in docs:
            done += 1
            if not quiet:
                print(f"  {done}/{stats.total} …", end="\r", file=sys.stderr)
            moved: list[Path] = []
            try:
                rel = src.relative_to(in_dir)
                # Resumed run: a document already cleaned keeps its output,
                # as text files do; otherwise it would be cleaned again under
                # a new name and the first copy reported as a leftover.
                # Reused only if the source is the same file, byte for byte:
                # a note edited since the last run is cleaned again.
                reuse, was = prior.get(str(rel), ("", ""))
                if resume and reuse and was == e.sha256 and (out_dir / reuse).exists():
                    stats.skipped_existing += 1
                    names.used.add(_Names._key(_base(Path(reuse))))
                    e.outcome, e.output = "already_present", reuse
                    continue
                eng = calcite.Engine(evaluate, profile_name=profile or "safe_harbor",
                                     patient_key=patient_key, memory=set(memory), ctx=ctx)
                with tempfile.TemporaryDirectory(dir=out_dir, prefix=".work-") as tmp:
                    res = calcite.clean_file(src, Path(tmp) / rel.name, eng)
                    stats.doc_summaries.append(res.summary())
                    if res.status == "failed" or not res.outputs:
                        stop(src, e, res.reason or "this file could not be cleaned safely")
                        continue
                    main = res.outputs[0]
                    extras = tuple(o.name[len(main.name):] for o in res.outputs[1:])
                    safe = names.claim(names.rel(rel, pi, suffix=main.suffix), extras)
                    held = res.status == "held"
                    target = out_dir / _tagged(PILE_FLAGGED if held else PILE_DONE, safe)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    for o in res.outputs:
                        dest = target.parent / (target.name + o.name[len(main.name):])
                        moved.append(dest)
                        shutil.move(str(o), str(dest))
                        _stamp(dest, stats, held=held)
                    row = {"file": target.name, "reason": res.reason,
                           "words_to_check": (_add_lines([dict(w) for w in res.words_to_check],
                                                         _text_of(target)) if held else [])}
                if held:
                    e.outcome, e.reason = "held", res.reason
                    stats.needs_review += 1
                    for r in res.results:
                        _count_reasons(stats, r)
                    held_rows.append(row)
                else:
                    e.outcome = "deidentified"
                    stats.processed += 1
                    for r in res.results:
                        _record(stats, r)
                e.output = target.relative_to(out_dir).as_posix()
            except Exception as exc:  # noqa: BLE001
                _discard(moved)
                stop(src, e, "the tool stopped on this file", f"{type(exc).__name__}: {exc}")
        try:
            patient_dir = names.dirs.get(patient_key) or (
                names._code("patient") if names.scramble else
                f"patient-{pi:04d}" if names.flagged(patient_key) else patient_key)
            _write_review_list(out_dir, patient_dir, held_rows)
        except Exception as exc:  # noqa: BLE001
            stats.failures.append((f"P{pi}", f"review list not written: {type(exc).__name__}"))
        memory.clear()          # nothing carries over to the next patient
        ctx = None
        # Written after every patient, so an interrupted run can be resumed
        # from where it stopped.
        try:
            _write_manifest(local_dir, stats)
        except OSError:
            pass
    stats.renamed_outputs = names.renamed
    _verify_accounting(original_in, out_dir, stats)
    shutil.rmtree(in_dir, ignore_errors=True)          # the snapshot: copies only
    _write_manifest(local_dir, stats)
    _write_guard_log(out_dir, stats)
    _record_support(out_dir, stats)
    if not quiet:
        print(file=sys.stderr)


def _record_support(out_dir: Path, stats: RunStats) -> None:
    """A support log with no patient information, which can be shared: the
    shape of this run — counts only, never a file name or note text — and a
    code for any run-level problem (inkguard/topaz.py), so someone helping
    with a problem can work from the log alone."""
    from inkguard import topaz
    (out_dir / TECH_DIR).mkdir(parents=True, exist_ok=True)
    log = out_dir / TECH_DIR / SUPPORT_LOG
    acc = stats.accounting or {}
    topaz.record("RUN-SUMMARY", log, files=acc.get("files", 0), deidentified=stats.processed,
                 held=stats.needs_review, set_aside=acc.get("set_aside", 0),
                 failed=stats.failed, guard_refused=stats.guard_refused)
    for prob in acc.get("problems", []):
        code = ("RUN-INPUT-CHANGED" if ("changed during the run" in prob
                                         or "changed while the tool was running" in prob)
                else "RUN-UNACCOUNTED")
        topaz.record(code, log)


def _write_guard_log(out_dir: Path, stats: RunStats) -> None:
    """A record of what the intake guard refused, and what active content it
    noted — by file id and support code only, never a path or any patient
    detail, so it can be shared for technical support. Written in the
    output folder, since it carries no patient information."""
    if not stats.guard_log and not stats.guard_notes:
        return
    lines = ["# Intake guard log", "",
             "What the guard checked before the tool opened each file. File ids match",
             f"{KEY_FILE} (in {PILE_NOT}). No file names or patient details are here, so",
             "this log can be shared with technical support as it is.", ""]
    if stats.guard_log:
        lines.append("## Files not opened")
        lines.append("")
        lines.append("| File | Code | Reason |")
        lines.append("|---|---|---|")
        for r in stats.guard_log:
            lines.append(f"| {r['id']} | {r['code']} | {r['reason']} |")
        lines.append("")
    if stats.guard_notes:
        lines.append("## Active content found and removed while cleaning")
        lines.append("")
        for note, n in sorted(stats.guard_notes.items()):
            lines.append(f"- {note}: {n:,}")
        lines.append("")
    try:
        (out_dir / TECH_DIR).mkdir(parents=True, exist_ok=True)
        (out_dir / TECH_DIR / GUARD_LOG).write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        pass


def _verify_accounting(in_dir: Path, out_dir: Path, stats: RunStats) -> None:
    """Every input file has exactly one outcome, its output is on disk and
    no two share a name, a set-aside copy matches its source byte for byte,
    and the folder did not change while the run went on."""
    excluded = (out_dir.resolve(),)
    problems = []
    now = {}
    for p in _inventory(in_dir, excluded):
        try:
            now[str(p.relative_to(in_dir))] = p.lstat().st_size
        except OSError:
            pass
    seen = {e.source: e.size for e in stats.files}
    if set(now) != set(seen):
        problems.append(f"the input folder changed during the run ({len(set(now) ^ set(seen))} file(s) "
                        "added or removed)")
    elif any(now[k] != seen[k] for k in now):
        problems.append("a file in the input folder changed size during the run")
    # The originals are only ever read. Check it: every file's fingerprint
    # (SHA-256) taken when it was read must match its fingerprint now.
    changed = 0
    for e in stats.files:
        if not e.sha256 or e.kind == "link":
            continue
        try:
            if _sha256(in_dir / e.source) != e.sha256:
                changed += 1
        except OSError:
            changed += 1
    if changed:
        problems.append(f"{changed} original file(s) changed while the tool was running")
    stats.originals_unchanged = not changed and set(now) == set(seen)
    outputs: dict[str, str] = {}
    for e in stats.files:
        if e.outcome not in ("deidentified", "held", "set_aside", "system", "already_present"):
            problems.append(f"{e.id}: no outcome recorded")
            continue
        if e.outcome in ("deidentified", "held", "already_present"):
            if not (out_dir / e.output).exists():
                problems.append(f"{e.id}: its output is missing")
            k = _Names._key(Path(e.output))
            if k in outputs:
                problems.append(f"{e.id}: its output name is also used by {outputs[k]}")
            outputs[k] = e.id
        if e.outcome in ("set_aside", "system") and e.kind != "link":
            copy = out_dir / e.output if e.output else None
            if copy is None or not copy.exists() or _sha256(copy) != e.sha256:
                problems.append(f"{e.id}: its set-aside copy is missing or differs")
    per: dict[int, dict] = {}
    for e in stats.files:
        d = per.setdefault(e.patient, {"patient": e.patient, "files": 0, "deidentified": 0, "held": 0,
                                       "set_aside": 0, "system": 0, "already_present": 0})
        d["files"] += 1
        if e.outcome in d:
            d[e.outcome] += 1
    total = {k: sum(d[k] for d in per.values())
             for k in ("files", "deidentified", "held", "set_aside", "system", "already_present")}
    # Files from an earlier run that this run did not produce (a patient
    # removed from the input, e.g. consent withdrawn). They are reported, not
    # deleted, so a person removes them deliberately.
    mine = {_Names._key(Path(e.output)) for e in stats.files
            if e.outcome in ("deidentified", "held", "already_present") and e.output}
    orphans = []
    for p in out_dir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out_dir)
        # Only the two piles of cleaned copies can hold a leftover cleaned
        # copy; the not-processed pile is written fresh from the input.
        if rel.parts[0] not in (PILE_DONE, PILE_FLAGGED) or rel.name == WORDS_FILE:
            continue
        # A companion of an output file is owned by it: "note.pdf.txt" beside
        # "note.pdf", and "note.txt.check.json" beside a flagged "note.txt".
        key = _Names._key(rel)
        name = rel.name
        base = rel
        if name.endswith(".check.json"):
            base = rel.with_name(name[:-len(".check.json")])
        elif name.count(".") >= 2 and name.endswith(".txt"):
            base = rel.with_name(name[:-len(".txt")])
        if key not in mine and _Names._key(base) not in mine:
            orphans.append(str(rel))
    if orphans:
        problems.append(f"{len(orphans)} file(s) in the output folder are left over from an earlier "
                        "run and match no input file now; review and remove them: "
                        + ", ".join(sorted(orphans)[:10]) + (" …" if len(orphans) > 10 else ""))
    # The three folders: every file received is in exactly one, and the audit
    # trail states the counts.
    piles = {"received": len(stats.files), "done": 0, "flagged": 0, "not_processed": 0}
    for e in stats.files:
        if e.outcome in ("set_aside", "system"):
            piles["not_processed"] += 1
        elif e.outcome == "held" or (e.outcome == "already_present" and e.output.startswith(PILE_FLAGGED)):
            piles["flagged"] += 1
        elif e.outcome in ("deidentified", "already_present"):
            piles["done"] += 1
    if piles["done"] + piles["flagged"] + piles["not_processed"] != piles["received"]:
        problems.append("the three folders do not add up to the files received")
    stats.accounting = {"verified": not problems, "problems": problems, **total, "piles": piles,
                        "per_patient": [per[k] for k in sorted(per)], "orphans": orphans[:500]}


#: The outcome as written in the names key, in the words the results use.
_KEY_OUTCOME = {"deidentified": "redacted", "held": "flagged"}


def _prior_outputs(local_dir: Path) -> dict[str, tuple[str, str]]:
    """The source-path -> (output-path, source sha256) map from a previous
    run's manifest, so a resumed run reuses the output of a file it already
    did, and only if that file has not changed since."""
    m = local_dir / KEY_FILE
    out: dict[str, tuple[str, str]] = {}
    if not m.exists():
        return out
    try:
        with open(m, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("outcome") in ("redacted", "flagged", "deidentified", "held",
                                          "already_present") and row.get("output_path"):
                    out[row["source_path"]] = (row["output_path"], row.get("sha256", ""))
    except (OSError, csv.Error):
        pass
    return out


def _write_manifest(local_dir: Path, stats: RunStats) -> None:
    """The key: every file received, what became of it, and its new name. It
    names the originals, so it is written to the not-processed pile."""
    if not stats.files:
        return
    local_dir.mkdir(parents=True, exist_ok=True)
    with open(local_dir / KEY_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "patient", "source_path", "sha256", "bytes", "kind", "outcome",
                    "output_folder", "output_path", "guard_code", "reason"])
        for e in stats.files:
            folder = e.output.split("/", 1)[0] if e.output else ""
            w.writerow([e.id, e.patient, e.source, e.sha256, e.size, e.kind,
                        _KEY_OUTCOME.get(e.outcome, e.outcome), folder, e.output, e.code, e.reason])


def process_csv(evaluate, src: Path, out_csv: Path, text_col: str,
                id_col: str | None, stats: RunStats, profile: str | None,
                quiet: bool, mode: str = "pseudonym", oplog_fh=None,
                file_mode: bool = True) -> None:
    # Rows form a patient file only when an ID column groups them.
    stats.file_mode = bool(file_mode and id_col)
    from inkguard import calcite as _calcite
    # Read the way it was written, byte-order mark included: Excel's "CSV
    # UTF-8" starts with one, and it would otherwise be glued to the first
    # column's name.
    rows = list(csv.DictReader(io.StringIO(_calcite.decode_text(src.read_bytes()), newline="")))
    if not rows:
        raise SystemExit(f"{src}: no rows")
    if text_col not in rows[0]:
        raise SystemExit(
            f"column {text_col!r} not found. Available: {list(rows[0])}")
    # A row with more cells than the header has cells with no column name.
    # Nothing can say what they hold, so nothing is written.
    ragged = [i + 2 for i, r in enumerate(rows) if None in r]
    if ragged:
        raise SystemExit(
            f"{len(ragged)} row(s) have more cells than the header (first: line {ragged[0]}). "
            "Nothing was written. Fix the file so every row has one cell per column.")

    stats.total = len(rows)
    out_field = f"{text_col}_deidentified"
    review_rows: list[dict] = []

    # A stable per-row key when no ID column is given keeps date offsets
    # deterministic without inventing a patient grouping the data does not
    # assert. With an ID column, rows are grouped and share confirmed names.
    keys = [(r.get(id_col) if id_col else None) or f"row-{i}"
            for i, r in enumerate(rows, 1)]
    groups: dict[str, list[int]] = {}
    for i, k in enumerate(keys):
        groups.setdefault(str(k), []).append(i)
    stats.patients = len(groups)

    done = 0
    for patient_key, idxs in groups.items():
        ctx, memory = _file_context([rows[i].get(text_col) or "" for i in idxs],
                                    patient_key, profile, stats)
        stats.memory_names += len(memory)
        for i in idxs:
            done += 1
            row = rows[i]
            text = row.get(text_col) or ""
            if not text.strip():
                row[out_field] = ""
                continue
            try:
                result = _evaluate(evaluate, text, ctx, patient_key=patient_key,
                                   profile_name=profile, memory=memory, redaction_mode=mode,
                                   want_oplog=oplog_fh is not None)
            except Exception as exc:
                _fail(stats, f"row {i + 1}", f"{type(exc).__name__}: {exc}")
                row[out_field] = ""          # fail closed: never the original
                continue
            if oplog_fh is not None and getattr(result, "oplog", None):
                oplog_fh.write(json.dumps(result.oplog) + "\n")
            if result.action.value == "blocked":
                _fail(stats, f"row {i + 1}", result.error or "blocked by policy")
                row[out_field] = ""
                continue
            if result.action.value == "review":
                # Flagged: the output column stays empty for this row and
                # the draft goes to a separate file, so a shared CSV can never
                # carry one.
                row[out_field] = ""
                row[f"{text_col}_review"] = "flagged: " + _reason_text(result)
                review_rows.append({"row": i + 1, "draft": _clean(result),
                                    "check": _words_to_check(text, result)})
                stats.needs_review += 1
                _count_reasons(stats, result)
                continue
            row[out_field] = _clean(result)
            stats.processed += 1
            _record(stats, result)
            if not quiet:
                print(f"  {done}/{stats.total} …", end="\r", file=sys.stderr)
        memory.clear()
    if not quiet:
        print(file=sys.stderr)

    # EVERY OTHER COLUMN. A name, date of birth or record number in its own
    # column is patient information as surely as one inside the note: each
    # row is read as "Column: value" lines, so the column name works as a
    # label. The ID column identifies the patient by definition.
    from inkguard import calcite
    cols = list(rows[0].keys())
    skip = {j for j, c in enumerate(cols) if c in (text_col, out_field, f"{text_col}_review", id_col)}
    try:
        res = calcite.clean_table_rows(cols, [[r.get(c) or "" for c in cols] for r in rows],
                                       calcite.Engine(evaluate, profile_name=profile or "safe_harbor"),
                                       skip=skip)
        for r, new in zip(rows, res["rows"]):
            for j, c in enumerate(cols):
                if j not in skip:
                    r[c] = new[j]
        for p in res["passes"]:
            stats.add_entities(p.result.hits)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"the other columns could not be cleaned ({type(exc).__name__}); "
                         "nothing was written")
    if id_col:
        for r in rows:
            if (r.get(id_col) or "").strip():
                r[id_col] = "[ID]"
    # The cleaned note goes back into the note's own column, and the original
    # is never written out. A flagged row's cell stays empty; its draft is in
    # the flagged pile.
    for r in rows:
        r[text_col] = r.pop(out_field, "") or ""
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys())
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    if review_rows:
        # Flagged drafts live apart from the output CSV.
        rdir = out_csv.parent / PILE_FLAGGED
        rdir.mkdir(parents=True, exist_ok=True)
        with open(rdir / f"{TAG_FLAGGED}{TAG_SEP}{out_csv.stem}-rows.jsonl", "w", encoding="utf-8") as f:
            for r in review_rows:
                f.write(json.dumps(r) + "\n")


# What a reader calls each kind of identifier. The detectors have many
# finer labels (a date written "March 3rd" and one written "3/3" are found
# by different patterns); the report counts what was removed, not how.
_PLAIN_NAMES = {
    "PERSON": "Names", "DATE_TIME": "Dates", "AGE": "Ages over 89", "FAX": "Phone and fax numbers",
    "LOCATION": "Places and addresses", "PHONE_NUMBER": "Phone and fax numbers",
    "EMAIL_ADDRESS": "Email addresses", "US_SSN": "Social Security numbers",
    "MRN": "Medical record numbers", "HEALTH_PLAN_ID": "Health plan numbers",
    "ACCOUNT_NUMBER": "Account numbers", "ACCOUNT": "Account numbers", "LICENSE_NUMBER": "Licence and certificate numbers",
    "VEHICLE_ID": "Vehicle identifiers", "DEVICE_ID": "Device identifiers",
    "URL": "Web addresses", "IP_ADDRESS": "IP addresses", "UNIQUE_ID": "Other ID numbers and usernames",
    "EMPLOYER": "Employers", "FACILITY": "Places of care",
}


def _plain_categories(counts: dict) -> dict:
    from inkguard import contract
    out: dict[str, int] = {}
    for kind, n in counts.items():
        canon = contract.canonical_type(kind)
        label = _PLAIN_NAMES.get(canon) or _PLAIN_NAMES["UNIQUE_ID"]
        out[label] = out.get(label, 0) + n
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


APP_DATA_NAME = "inkguard"     # the app's data folder on this computer


def _code_table_info():
    from inkguard import contract
    return contract.pseudonym_table_info()


def default_code_table() -> Path:
    """Where the pseudonym code table lives when the practice has not chosen:
    the user's own app-data folder for this app, on each operating system.
    Never inside an input or output folder, so it is never copied or exported
    with the data it codes."""
    import sys
    home = Path.home()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / APP_DATA_NAME / "code_table.json"


def _safe_location(path: Path, evaluate) -> str:
    """A folder's path for the audit trail. A part of the path that reads as
    a person's name, a date or a number (a folder named after a patient) is
    withheld, so the audit trail stays free of patient information."""
    import re
    from inkguard import contract
    try:
        parts = Path(path).resolve().parts
    except OSError:
        parts = Path(path).parts
    shown = []
    for i, part in enumerate(parts):
        if i == 0:
            shown.append(part)
            continue
        risky = False
        for text in {part, re.sub(r"_+", " ", part).strip()}:   # dates keep their hyphens
            try:
                r = contract.deidentify(f"Folder: {text}", mode="mask", engine=evaluate)
                risky = risky or r.status == "stop" or bool(r.edits)
            except Exception:  # noqa: BLE001 — when unsure, withhold
                risky = True
        shown.append("[withheld]" if risky else part)
    # Only the folder's own name: the rest of a path can name a person or a
    # clinic (the user's account, a department) and adds nothing to the record.
    return shown[-1] if shown else ""


def check_feedback_file(path: Path) -> int:
    """`--check-feedback FILE`: 0 when the check finds no line to change, 1
    when a line should be changed first (each is shown, on this computer
    only)."""
    from inkguard import cli_docs
    try:
        text = cli_docs.document_text(Path(path))
    except Exception as exc:  # noqa: BLE001 — an unreadable file is reported, not raised
        print(f"Could not read {path}: {getattr(exc, 'strerror', None) or exc}")
        return 1
    flagged = check_feedback(text)
    if not flagged:
        print("The check found nothing that looks like patient information. "
              "Read it once yourself before sending.")
        print(f"Email it to {REPORT_EMAIL}. Thank you.")
        return 0
    print("Change these lines before sending. Describe the kind of problem, not the content:")
    for n, line in flagged:
        print(f"  line {n}: {line.strip()[:100]}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="inkguard",
        description="Redact patient identifiers from a folder of clinical notes or a CSV column.",
        epilog=(
            "Examples:\n"
            "  python -m inkguard.cli notes/ -o clean/\n"
            "  python -m inkguard.cli notes/ -o clean/ --profile lds --date-shift\n"
            "  python -m inkguard.cli data.csv -o clean.csv --text-column transcription\n"
            "\n"
            "Exit status: 0 every file processed; 3 finished, but some files were set aside\n"
            "(scans, photos, other kinds); 1 a file failed, was refused or is unaccounted\n"
            "for. Whatever the status, no output is ready to share until a person has\n"
            "read every note.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input", type=Path, nargs="?",
                   help="a folder of documents (text, Word, Excel, typed PDF, CSV), or one .csv")
    p.add_argument("-o", "--output", type=Path,
                   help="output directory (or output .csv for CSV input)")
    p.add_argument("--about", action="store_true",
                   help="what this tool is for, what it does and does not do, and how to start")
    p.add_argument("--self-test", action="store_true",
                   help="one-minute installation check: runs the engine on invented notes with "
                        "network connections refused. Writes nothing.")
    p.add_argument("--check-feedback", type=Path, metavar="FILE",
                   help="check a feedback note before you email it: lists any line you added "
                        "that could hold patient information. Writes nothing.")
    p.add_argument("--profile", choices=["safe_harbor", "lds"],
                   default="safe_harbor",
                   help="safe_harbor (default: the Safe Harbor profile; every output still "
                        "needs human review) or lds (keeps dates and towns; output REMAINS PHI)")
    p.add_argument("--date-shift", action="store_true",
                   help="shift dates per patient, preserving intervals. Shifted dates keep a "
                        "day and month, so this output is NOT Safe Harbor")
    p.add_argument("--max-offset-days", type=int, default=365)
    p.add_argument("--text-column", help="CSV input: column holding the note")
    p.add_argument("--id-column",
                   help="CSV input: column identifying the PATIENT, used to "
                        "keep date offsets consistent across their rows")
    p.add_argument("--report", type=Path,
                   help="write the audit trail here (a .pdf, with its machine record as .json "
                        "beside it); defaults to "
                        f"<output>/{EVIDENCE_FILE} and <output>/{TECH_DIR}/{REPORT_JSON}")
    p.add_argument("--names", choices=["mask", "pseudonym"], default="mask",
                   help="mask (default): every name becomes [NAME]. "
                        "pseudonym (Advanced): each person becomes a random code that "
                        "is consistent across all of that patient's notes; needs a "
                        "persistent PHI_ROOT_KEY and a code table (PHI_CODE_TABLE, "
                        "default: your app-data folder) — back the two up together.")
    p.add_argument("--new-code-table", action="store_true",
                   help="pseudonym mode, first time only: create the code table if none "
                        "exists (an existing table is never replaced)")
    p.add_argument("--accept-restored-code-table", action="store_true",
                   help="pseudonym mode: knowingly accept a code table restored from an older "
                        "backup (people coded after that backup get new codes)")
    p.add_argument("--oplog", type=Path,
                   help="write the operation log (JSONL) here, for your own records. "
                        "Do not publish it with the redacted files.")
    p.add_argument("--resume", action="store_true",
                   help="advanced: after an interruption, keep the files already written instead of "
                        "starting over (the default is to delete them and start again)")
    p.add_argument("--note-by-note", action="store_true",
                   help="redact each note on its own instead of reading the patient's whole "
                        "file first (faster; misses names confirmed only in other notes)")
    p.add_argument("--no-header", action="store_true",
                   help="do not write the one-line provenance header at the top of text "
                        "and Word outputs (for text that must stay exactly as cleaned)")
    p.add_argument("--scramble-names", action="store_true",
                   help="give every patient folder and file a random code instead of its name "
                        "(files stay grouped by patient; the key in the third folder links back)")
    p.add_argument("--dry-run", action="store_true",
                   help="look first: list what would be read, set aside or ignored, "
                        "and where the output would go. Writes nothing and reads no "
                        "file's contents.")
    p.add_argument("-q", "--quiet", action="store_true")
    from inkguard import cli_attest
    cli_attest.add_arguments(p)
    return p


def dry_run(args) -> int:
    """Look before doing anything: what would be read, what would be set
    aside and why, and where everything would go. Nothing is written and
    no file is opened."""
    src, out = args.input, args.output
    if not src.exists():
        raise SystemExit(f"[RUN-INPUT-MISSING] {topaz.message('RUN-INPUT-MISSING')} ({src})")
    print("DRY RUN. Nothing will be written and no file will be opened.")
    if src.is_file():
        print(f"  input      one file: {src.name} ({src.stat().st_size:,} bytes)")
    else:
        ind, outd = src.resolve(), out.resolve()
        if ind == outd or ind in outd.parents or outd in ind.parents:
            print("  STOP       the output folder is inside the input folder, or the other way "
                  "round. Choose a folder outside it.")
            return 2
        files = _inventory(src, (outd,))
        kinds: dict[str, int] = {}
        reasons: dict[str, int] = {}
        size = 0
        for f in files:
            k, why = _classify(f)
            kinds[k] = kinds.get(k, 0) + 1
            if why and k not in ("text", "document", "system"):
                reasons[why] = reasons.get(why, 0) + 1
            try:
                size += f.lstat().st_size
            except OSError:
                pass
        tops = {f.relative_to(src).parts[0] for f in files if len(f.relative_to(src).parts) > 1}
        read = kinds.get("text", 0) + kinds.get("document", 0)
        aside = sum(v for k, v in kinds.items() if k not in ("text", "document", "system"))
        print(f"  found      {len(files):,} files, {size / 1e6:,.1f} MB, in {len(tops):,} patient "
              "folder(s)")
        print(f"  would read {read:,} (typed text, Word, Excel, typed PDF, CSV)")
        print(f"  not read   {aside:,}, copied to {PILE_NOT}, not redacted:")
        for why, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"               {n:,} × {why}")
        print(f"  system     {kinds.get('system', 0):,} system files, copied to {PILE_NOT}")
        print(f"  results    {out}  (three folders: {PILE_DONE}, {PILE_FLAGGED},")
        print(f"             {PILE_NOT}; nothing is ready to share until a person reviews it)")
        if out.exists() and any(out.iterdir()):
            print("  NOTE       the results folder already has files in it; if they are from an "
                  "unfinished run, they are deleted and the run starts over. Choose an empty "
                  "folder to keep them.")
        print("  originals  only read, never changed; fingerprints are re-checked after the run")
    print("To run it for real, repeat the same command without --dry-run.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """A disk that fills, a folder the account may not write to, an output
    path that is a file: each ends with a short coded message that can be
    looked up, never a Python traceback."""
    import errno
    import warnings
    from inkguard import topaz
    warnings.filterwarnings("ignore", category=DeprecationWarning)   # library notices are not for the user
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print(f"\n[RUN-CANCELLED] {topaz.message('RUN-CANCELLED')}", file=sys.stderr)
        return 130
    except OSError as exc:
        code = ("RUN-DISK-FULL" if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1))
                else "RUN-PERMISSION" if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS)
                else "RUN-OUTPUT-WRITE")
        raise SystemExit(f"[{code}] " + topaz.message(code)
                         + "\n  If the run had started, it stopped part way and the output folder "
                           "is labelled THIS RUN DID NOT FINISH. Fix the cause, then run again: "
                           "it starts over.")


def _preflight(inp: Path, out: Path) -> None:
    """Before the engine loads: the output can be written, and there is room."""
    import shutil
    from inkguard import topaz
    if out.exists() and not out.is_dir():
        raise SystemExit("[RUN-OUTPUT-WRITE] " + topaz.message("RUN-OUTPUT-WRITE")
                         + "\n  (the output path is a file, not a folder)")
    probe_dir = out if out.exists() else next((p for p in out.parents if p.exists()), Path("."))
    if not os.access(probe_dir, os.W_OK):
        raise SystemExit("[RUN-PERMISSION] " + topaz.message("RUN-PERMISSION"))
    need = 0
    for f in inp.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                need += f.stat().st_size
        except OSError:
            pass
    # The snapshot, clean copies, copies not processed and the work folder:
    # about four times the input, plus room for the reports.
    if shutil.disk_usage(probe_dir).free < 4 * need + 50_000_000:
        raise SystemExit("[RUN-DISK-FULL] " + topaz.message("RUN-DISK-FULL")
                         + f"\n  (needs about {(4 * need + 50_000_000) / 1e6:,.0f} MB free)")


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    from inkguard import fluorite
    if args.about:
        print(fluorite.about())
        return 0
    if args.self_test:
        passed, lines = fluorite.self_test()
        print("\n".join(lines))
        return 0 if passed else 1
    if args.check_feedback is not None:
        return check_feedback_file(args.check_feedback)
    if args.input is None or args.output is None:
        print(fluorite.about())
        parser.error("give a folder (or a .csv) and -o OUTPUT; see --help")
    if args.dry_run:
        return dry_run(args)
    # The optional local audit trail details (cli_attest.py).
    from inkguard import cli_attest
    entered = cli_attest.gather(args)

    if args.date_shift:
        os.environ["PHI_DATE_SHIFT"] = "true"
        os.environ["PHI_DATE_SHIFT_MAX_DAYS"] = str(args.max_offset_days)

    # Imported after the env is set so module-level config reads it. The
    # engine binding comes from the contract, so the shell never imports the
    # engine itself.
    from inkguard import contract
    evaluate = contract.engine()

    profile_name = "limited_data_set" if args.profile == "lds" else "safe_harbor"
    cfg = contract.profile_info(profile_name)
    stats = RunStats(profile=cfg["profile"], redaction_mode=args.names)
    from inkguard import lapis
    stats.run_id = lapis.new_run_id()
    stats.stamp = not args.no_header
    stats.scramble = bool(args.scramble_names)
    stats.locations = {"input": _safe_location(args.input, evaluate),
                       "results": _safe_location(args.output, evaluate)}
    stats.run_record = {"entered": entered, "computer": cli_attest.footprint(stats.started),
                        "note": cli_attest.NOT_VERIFIED}

    if not args.input.exists():
        raise SystemExit(f"[RUN-INPUT-MISSING] {topaz.message('RUN-INPUT-MISSING')} ({args.input})")
    if args.input.is_dir():
        _preflight(args.input, args.output)

    # Codes that change between runs do not carry forward. Check once, up
    # front, rather than quarantining every document one by one.
    if args.names == "pseudonym" or args.date_shift:
        from inkguard.security import KeyStore
        if getattr(KeyStore(), "ephemeral", False):
            raise SystemExit(
                "A persistent PHI_ROOT_KEY is required for pseudonym codes and "
                "date shifting — without it, codes and offsets change every run "
                "and nothing carries forward.\n"
                "  export PHI_ROOT_KEY=$(openssl rand -hex 32)\n"
                "Keep it safe, and back it up together with the code table.\n"
                "Or run with --names mask.")
    if args.names == "pseudonym":
        # The code table lives in the user's own app-data folder — never in the
        # input or output — unless the practice has chosen a location.
        os.environ.setdefault("PHI_CODE_TABLE", str(default_code_table()))
        if args.accept_restored_code_table:
            info = contract.pseudonym_accept_restored()
            print(f"  Accepted the restored code table ({info['entries']} entries). People coded "
                  "after that backup will receive new codes from now on.")
        if args.new_code_table and not Path(os.environ["PHI_CODE_TABLE"]).expanduser().exists():
            info = contract.pseudonym_setup()
            print(f"  Created a new code table ({info['id']}). Back it up together with the "
                  "root key: neither alone can carry codes forward.")
        problem = contract.pseudonym_ready()
        if problem:
            raise SystemExit("[RUN-PSEUDONYM-SETUP] " + topaz.message("RUN-PSEUDONYM-SETUP")
                             + f"\n  ({problem})\n  Or run with --names mask.")

    oplog_fh = None
    if args.oplog:
        out_root = (args.output if not args.input.is_file() else args.output.parent).resolve()
        if str(args.oplog.resolve()).startswith(str(out_root)):
            print("  WARNING: the operation log is inside the output directory. It "
                  "is for your own records and must not be shared with the redacted files.",
                  file=sys.stderr)
        args.oplog.parent.mkdir(parents=True, exist_ok=True)
        oplog_fh = open(args.oplog, "w", encoding="utf-8")

    if not args.quiet:
        from inkguard import __version__
        print(f"Inkguard {__version__} — profile: {cfg['profile']}", file=sys.stderr)
        if cfg["is_still_phi"]:
            print("  WARNING: LDS output REMAINS PHI and requires a Data Use "
                  "Agreement.", file=sys.stderr)
            print("  NOTE: dates, ZIP codes and towns stay beside what is removed; "
                  "plan for closer manual review than full removal (see the audit trail).",
                  file=sys.stderr)

    is_csv = args.input.is_file() and args.input.suffix.lower() in {".csv", ".tsv"}
    # The original is only ever read: an output that IS the input would be
    # written over it.
    if is_csv and args.output.resolve() == args.input.resolve():
        raise SystemExit("[RUN-FOLDER-OVERLAP] " + topaz.message("RUN-FOLDER-OVERLAP"))
    if is_csv and not args.text_column:
        # No note column named: every cell of the table is read.
        from inkguard import calcite
        res = calcite.clean_csv(args.input, args.output,
                                calcite.Engine(evaluate, profile_name=profile_name))
        stats.total = 1
        stats.doc_summaries = [res.summary()]
        for r in res.results:
            stats.add_entities(r.hits)
        if res.status == "failed" or not res.outputs:
            _fail(stats, "input", res.reason)
        elif res.status == "held":
            held = args.output.parent / _tagged(PILE_FLAGGED, Path(args.output.name))
            held.parent.mkdir(parents=True, exist_ok=True)
            res.outputs[0].replace(held)
            words = _add_lines([dict(w) for w in res.words_to_check], _text_of(held))
            (held.parent / WORDS_FILE).write_text(review_list(
                args.output.stem, [{"file": held.name, "words_to_check": words}]), encoding="utf-8")
            stats.needs_review += 1
        else:
            stats.processed = 1
        report_dir = args.output.parent
    elif is_csv:
        process_csv(evaluate, args.input, args.output, args.text_column,
                    args.id_column, stats, profile_name, args.quiet,
                    mode=args.names, oplog_fh=oplog_fh, file_mode=not args.note_by_note)
        report_dir = args.output.parent
    else:
        if not args.input.is_dir():
            raise SystemExit(f"{args.input} is neither a directory nor a CSV")
        args.output.mkdir(parents=True, exist_ok=True)
        process_directory(evaluate, args.input, args.output, stats,
                          profile_name, args.resume, args.quiet,
                          mode=args.names, oplog_fh=oplog_fh, file_mode=not args.note_by_note)
        report_dir = args.output

    if oplog_fh is not None:
        oplog_fh.close()

    cli_attest.finish(stats.run_record)
    report = build_report(stats, cfg, args.date_shift)
    if args.report:
        base = args.report.with_suffix("")
        audit_path, json_path = base.with_suffix(".pdf"), base.with_suffix(".json")
    else:
        audit_path = Path(report_dir) / EVIDENCE_FILE
        json_path = Path(report_dir) / TECH_DIR / REPORT_JSON
    for q in (audit_path, json_path):
        q.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_audit(report, audit_path)
    if report_dir == args.output and Path(report_dir).is_dir():   # folder runs only
        (Path(report_dir) / NOTICE_FILE).write_text(
            guide_text(stats, Path(report_dir).name), encoding="utf-8")
        write_feedback(stats, Path(report_dir) / FEEDBACK_FILE)
        (Path(report_dir) / CITE_FILE).write_text(cite_text(report), encoding="utf-8")
        write_output_manifest(Path(report_dir), stats.run_id, _run_outputs(stats))
        # Finished: every document is written. Only now is the run marked done.
        (Path(report_dir) / TECH_DIR / IN_PROGRESS).unlink(missing_ok=True)

    if not args.quiet:
        print(f"\nredacted, no flags {stats.processed:,}  redacted and flagged "
              f"{stats.needs_review:,}  stopped {stats.failed:,}", file=sys.stderr)
        print(f"linkage flagged for review: {stats.linkage_review:,}", file=sys.stderr)
        print(f"audit trail: {audit_path}", file=sys.stderr)
        print("\nINCOMPLETE REVIEW. NOT READY TO SHARE.", file=sys.stderr)
        print("  " + _verify(), file=sys.stderr)
        pl = _piles(stats)
        print(f"  files received       {pl['received']:,}, each in exactly one folder:", file=sys.stderr)
        print(f"  {PILE_DONE:<34} {pl['done']:>6,}  redacted by the software; pending human "
              "review", file=sys.stderr)
        print(f"  {PILE_FLAGGED:<34} {pl['flagged']:>6,}  the software was unsure; a person must "
              f"decide ({WORDS_FILE})", file=sys.stderr)
        print(f"  {PILE_NOT:<34} {pl['not_processed']:>6,}  not read: photos, scans, other kinds, "
              "stopped files. Not redacted; keep on this computer.", file=sys.stderr)
        print("  ready to share       0. Nothing is ready until a person has read every note, "
              f"including those in {PILE_DONE}.", file=sys.stderr)
        if stats.originals_unchanged:
            print("  your originals       only read; every fingerprint re-checked after the run "
                  "and unchanged", file=sys.stderr)
        print("  network              Inkguard's own code opens no network connections "
              "(checked by its tests)",
              file=sys.stderr)
        if report_dir == args.output:
            print("", file=sys.stderr)
            for line in CLOSING_LINES:
                print(line, file=sys.stderr)

    acc = stats.accounting or {}
    if not args.quiet and acc:
        if not acc.get("verified", True):
            print("accounting problems: " + "; ".join(acc["problems"][:5]), file=sys.stderr)
    # Non-zero when anything failed, a file is unaccounted for, or the intake
    # guard would not open a file the user meant to process: a batch job that
    # silently drops or refuses documents should not look successful.
    if stats.failed or stats.guard_refused or (acc and not acc.get("verified", True)):
        return 1
    # Finished, but some files were set aside (scans, photos, other kinds):
    # a script must be able to tell that from "every file processed".
    return 3 if acc.get("set_aside", 0) else 0


if __name__ == "__main__":
    sys.exit(main())
