"""
amber: a voluntary feedback report for the desktop app, which is in
development. Not used by the 0.25.0 command line or window, except for the
four search patterns below (_EMAIL, _URL, _PHONE, _LONGNUM), which the
feedback check in cli_report uses.

After a run, a person may save a short, plain-language account of roughly
what went wrong and why, add their own note and a suggestion if they want to,
and choose to email the file to the project's maintainers themselves. The
maintainers use these files to find and fix bugs.

Two parts could carry patient information, and amber is designed to keep it
out of both. It is checked by software that can miss things, so the person
reads the report before emailing it:

  1. The account of "what went wrong" is built ONLY from the run's support
     log: the `topaz` codes, their counts, and the fixed codebook text. That
     log is designed to hold codes, counts and times, never a file name, path
     or note text, so the account carries no file names or note text either.

  2. The person's own free text is the one place a name or a record number
     could slip in ("Mr. Okafor's chart wouldn't open"). amber passes every
     free-text field through the de-identification engine and a conservative
     identifier net BEFORE it is placed in the report, then searches the
     finished report again and marks it not ready if anything is found.
     The person sees the exact scrubbed text in the saved file.

The person's OWN contact details are not patient information and are theirs
to share; amber keeps a contact field verbatim and never scrubs it, and the
field is filled only if the person chooses to be reachable.

Nothing is sent: amber builds the report and saves it as a file. The
software contains no network code (tests/test_no_network.py).

amber reaches the engine only through `contract`, like every other module
outside the engine, so the boundary test covers it too.
"""
from __future__ import annotations

import datetime
import json
import re
import uuid
from dataclasses import dataclass, field, asdict

from inkguard import contract, topaz

# What the person sees when a free-text field still looked like it held
# patient details after scrubbing.
_UNSAFE_FIELD = ("(this note was left out because it still looked like it held "
                 "patient details after scrubbing — please rephrase it without "
                 "names, dates or record numbers)")

# A conservative identifier net applied to free text AFTER the engine pass, so
# an identifier the engine does not catch in casual prose is less likely to
# stay in. It is deliberately over-eager: the person reviews the result, and
# over-masking a feedback note costs little while under-masking cannot be
# undone once the person has emailed it.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\d[\d\-.\s()]{5,}\d)(?!\d)")   # 7+ characters: digits, separators between
_LONGNUM = re.compile(r"(?<!\d)\d{5,}(?!\d)")                  # MRN / account / SSN-like

# A capitalised word is the shape a bare name takes ("Okafor's chart crashed")
# when it carries no title or cue for the engine's name model. Because the
# person may email this text, amber masks every capitalised word the core did not
# already remove UNLESS it is plainly ordinary — a short acronym (PDF, CSV,
# OCR), or a common word that opens a sentence or names a part of the app. This
# over-masks a rare ordinary word now and then; the person sees the result and
# can rephrase, and over-masking a note that may be emailed is the better way
# to be wrong. The list is for readability only — it is NOT a safety boundary.
# Matches a bracketed placeholder we (or the core) already inserted, OR a
# capitalised word. The placeholder alternative comes first so a mask like
# [NUMBER] or [NAME] is recognised and left alone, not re-read as a name.
_CAP_TOKEN = re.compile(r"\[[A-Z][A-Z-]*\]|[A-Z][A-Za-z]+")
_SAFE_CAP = frozenset(w.lower() for w in (
    # sentence openers, pronouns, determiners, conjunctions, prepositions
    "The", "This", "That", "These", "Those", "There", "Then", "Than", "Here",
    "It", "Its", "We", "Our", "Us", "You", "Your", "My", "Mine", "His", "Her",
    "Their", "They", "Them", "He", "She", "I", "A", "An", "As", "At", "In",
    "On", "Of", "Or", "And", "But", "So", "If", "No", "Not", "Nor", "Yes",
    "All", "Any", "Some", "Many", "Few", "More", "Most", "Less", "One", "Two",
    "Three", "Each", "Every", "Both", "Either", "Neither", "Also", "Too",
    "When", "Where", "What", "Which", "Why", "How", "Who", "Whom", "Whose",
    "After", "Before", "During", "While", "Since", "Until", "About", "With",
    "Without", "From", "Into", "Over", "Under", "Again", "Once", "Still",
    "Only", "Just", "Even", "Ever", "Never", "Always", "Often", "Sometimes",
    "Maybe", "Perhaps", "Please", "Thanks", "Thank", "Sorry", "Overall",
    # common verbs/auxiliaries that start sentences
    "Is", "Are", "Was", "Were", "Be", "Been", "Being", "Am", "Do", "Does",
    "Did", "Done", "Have", "Has", "Had", "Can", "Could", "Will", "Would",
    "Shall", "Should", "May", "Might", "Must", "Need", "Let", "Got", "Get",
    "Make", "Made", "Seems", "Looks", "Works", "Worked", "Keeps", "Kept",
    "Tried", "Trying", "Tends", "Happens", "Happened",
    # app / domain nouns a note is likely to use
    "App", "Tool", "File", "Files", "Folder", "Folders", "Document",
    "Documents", "Doc", "Docs", "Page", "Pages", "Report", "Reports", "Run",
    "Review", "Reviewer", "Reviewers", "Patient", "Patients", "Note", "Notes",
    "Record", "Records", "Clean", "Export", "Exports", "Error", "Errors",
    "Crash", "Button", "Screen", "Scan", "Scans", "Image", "Images",
    "Everything", "Nothing", "Something", "Anything", "Someone", "Nobody",
    # formats, platforms, components (acronyms are handled separately too)
    "Word", "Excel", "Pdf", "Pdfs", "Csv", "Csvs", "Windows", "Mac", "Macos",
    "Ocr", "Os", "Linux",
    # days and months (the core handles dates; these keep prose readable)
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
))


@dataclass
class FeedbackReport:
    """One feedback report, designed to hold no patient information. `ready`
    is False until every free-text field has passed the final search;
    `blocked_fields` names the ones that did not, so the person knows what to
    rephrase."""
    report_id: str
    created: str
    app_version: str
    os: str
    run_shape: dict = field(default_factory=dict)      # counts only, from the run
    what_went_wrong: list = field(default_factory=list)  # from the support log
    note: str = ""                                      # scrubbed
    suggestion: str = ""                                # scrubbed
    contact: dict = field(default_factory=dict)         # the person's own, verbatim
    scrub_counts: dict = field(default_factory=dict)    # items masked per field
    ready: bool = True
    blocked_fields: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------
# What went wrong: built only from the run's support log (codes and counts).
# ---------------------------------------------------------------------

def summarize_support_log(events: list[dict]) -> list[dict]:
    """Group a run's support events by code into a plain account of codes:
    for each code, how many times it happened, its category, and the codebook
    explanation an IT person reads. `RUN-SUMMARY` (the normal end-of-run line)
    is skipped — it is not a problem. Only the code and count are taken from
    each event; any caller-supplied context is ignored here, so nothing a
    caller may have attached can reach the report by this path."""
    counts: dict[str, int] = {}
    for e in events:
        code = str(e.get("code", "")).strip()
        if not code or code == "RUN-SUMMARY":
            continue
        counts[code] = counts.get(code, 0) + 1
    out: list[dict] = []
    for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        entry = topaz.CODEBOOK.get(code)
        category = entry[0] if entry else "Other"
        means = entry[2] if entry else "An unrecognised code; share it with support."
        out.append({"code": code, "count": n, "category": category, "means": means})
    return out


def load_support_events(log_path) -> list[dict]:
    """Read a support log of codes and counts (one JSON event per line). Missing or
    unreadable lines are skipped; a missing log yields no events."""
    from pathlib import Path
    p = Path(log_path)
    events: list[dict] = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return events


# ---------------------------------------------------------------------
# Scrubbing the person's own free text.
# ---------------------------------------------------------------------

def _nets(s: str) -> tuple[str, int]:
    """amber's own conservative nets, applied to text the engine has already
    cleaned: structured identifiers first, then every capitalised word that is
    not plainly ordinary (a bare name). Returns (text, items_masked)."""
    masked = 0

    def _sub(pattern: re.Pattern, tag: str, text: str) -> str:
        nonlocal masked
        def repl(_m):
            nonlocal masked
            masked += 1
            return tag
        return pattern.sub(repl, text)

    s = _sub(_EMAIL, "[EMAIL]", s)
    s = _sub(_URL, "[LINK]", s)
    s = _sub(_PHONE, "[PHONE]", s)
    s = _sub(_LONGNUM, "[NUMBER]", s)

    def _cap(m: re.Match) -> str:
        nonlocal masked
        tok = m.group(0)
        if tok.startswith("["):                    # an existing placeholder
            return tok
        if tok.isupper() and len(tok) <= 5:        # acronym: PDF, CSV, OCR, OS
            return tok
        if tok.lower() in _SAFE_CAP:               # ordinary word — readability
            return tok
        masked += 1
        return "[WORD]"

    s = _CAP_TOKEN.sub(_cap, s)
    return s, masked


def scrub(text: str) -> tuple[str, int]:
    """Scrub one free-text field. The de-identification engine runs first
    (names with cues, dates, the Safe Harbor identifiers); then amber's own
    nets mask structured identifiers and any bare, cue-less name the engine
    left. Returns the scrubbed text and the number of items masked. If the
    engine stops on the text, the whole field is replaced."""
    text = (text or "").strip()
    if not text:
        return "", 0
    try:
        r = contract.deidentify(text, mode="mask")
    except Exception:  # noqa: BLE001 — a scrub must never crash the report
        r = None
    if r is None or r.status == "stop":
        # The engine stopped on this text: do not try to salvage it.
        return _UNSAFE_FIELD, 1
    cleaned = r.clean_text if r.clean_text is not None else text
    netted, net_n = _nets(cleaned)
    return netted, len(r.edits or []) + net_n


def _residual_identifier(text: str) -> bool:
    """The final check for one finished free-text field: run it back through
    the engine and amber's nets, and report whether anything would still be
    masked. A finished field must come back with nothing masked."""
    if not text or text == _UNSAFE_FIELD:
        return False
    try:
        r = contract.deidentify(text, mode="mask")
    except Exception:  # noqa: BLE001
        return True
    if r is None or r.status == "stop" or (r.edits or []):
        return True
    _, net_n = _nets(r.clean_text if r.clean_text is not None else text)
    return net_n > 0


# ---------------------------------------------------------------------
# Building the report.
# ---------------------------------------------------------------------

def build(*, support_events: list[dict] | None = None, run_shape: dict | None = None,
          note: str = "", suggestion: str = "", contact: dict | None = None,
          os_name: str = "") -> FeedbackReport:
    """Assemble one feedback report. All free text is scrubbed; the account
    of what went wrong is built only from the support log; the person's own
    contact is kept verbatim and never scrubbed. The report is then searched
    again, and `ready` is set only if nothing more is found in any field."""
    from inkguard import __version__
    note_clean, note_n = scrub(note)
    sugg_clean, sugg_n = scrub(suggestion)

    fr = FeedbackReport(
        report_id=uuid.uuid4().hex,     # random — not derived from anything in the run
        created=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        app_version=__version__,
        os=str(os_name or ""),
        run_shape=_safe_shape(run_shape or {}),
        what_went_wrong=summarize_support_log(support_events or []),
        note=note_clean,
        suggestion=sugg_clean,
        contact=_safe_contact(contact or {}),
        scrub_counts={"note": note_n, "suggestion": sugg_n},
    )

    # Final check: a scrubbed field in which anything is still found is
    # blanked and named, and the report is marked not ready, so save() refuses it.
    for name_, value in (("note", fr.note), ("suggestion", fr.suggestion)):
        if _residual_identifier(value):
            setattr(fr, name_, _UNSAFE_FIELD)
            fr.blocked_fields.append(name_)
    fr.ready = not fr.blocked_fields
    return fr


# Only these keys, and only as plain numbers, are allowed through from a run's
# aggregate counts — so nothing richer (and possibly identifying) can ride in
# on `run_shape`.
_SHAPE_KEYS = ("found", "deidentified", "held", "set_aside", "failed",
               "guard_refused", "quarantined", "patients")


def _safe_shape(shape: dict) -> dict:
    out: dict[str, int] = {}
    for k in _SHAPE_KEYS:
        v = shape.get(k)
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            out[k] = v
        elif isinstance(v, float) and v.is_integer():
            out[k] = int(v)
    return out


def _safe_contact(contact: dict) -> dict:
    """The person's own contact, their choice to include. Kept verbatim (it is
    not patient information), but confined to known fields and trimmed, so the
    contact box cannot be used to smuggle a wall of text."""
    out: dict[str, str] = {}
    for k in ("name", "email", "phone", "practice"):
        v = contact.get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()[:200]
    return out


# ---------------------------------------------------------------------
# What the person reads before choosing to email it, and the saved JSON.
# ---------------------------------------------------------------------

def render_markdown(fr: FeedbackReport) -> str:
    """The short, plain account the person reads before choosing to email
    it. It shows everything the saved report holds."""
    L = ["# Feedback report", "",
         f"- Report id: `{fr.report_id}`",
         f"- Created: {fr.created}",
         f"- App version: {fr.app_version}" + (f" · {fr.os}" if fr.os else ""), ""]
    if fr.run_shape:
        L += ["## This run, in numbers", "",
              ", ".join(f"{k}: {v}" for k, v in fr.run_shape.items()), ""]
    L += ["## What the app reported", ""]
    if fr.what_went_wrong:
        for w in fr.what_went_wrong:
            L.append(f"- **{w['code']}** ×{w['count']} ({w['category']}) — {w['means']}")
    else:
        L.append("- Nothing was flagged during this run.")
    L.append("")
    if fr.note:
        L += ["## Your note", "", fr.note, ""]
    if fr.suggestion:
        L += ["## Your suggestion for support", "", fr.suggestion, ""]
    if fr.contact:
        L += ["## How to reach you", "",
              ", ".join(f"{k}: {v}" for k, v in fr.contact.items()), ""]
    if not fr.ready:
        L += ["---", "",
              "> This report is **not ready to send**. One or more of your notes still "
              "looked like it held patient details and was left out. Please rephrase "
              "without names, dates or record numbers.", ""]
    else:
        L += ["---", "",
              "> This report holds codes, counts and your own words, checked for patient "
              "details by software that can miss things. Read it before you email it. "
              "Inkguard sends nothing: the report is saved as a file, and you decide "
              "whether to email it.", ""]
    return "\n".join(L)


def to_json(fr: FeedbackReport) -> str:
    """The report as saved, in JSON. Indented so a person can read every
    field before choosing to email it."""
    return json.dumps(fr.to_dict(), indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------
# Saving the report: saved locally; the person decides whether to email it.
# ---------------------------------------------------------------------

class NotSendable(RuntimeError):
    """The report is not marked ready (a field still looked like it held
    patient details), so it is not saved."""


def save(fr: FeedbackReport, folder) -> dict:
    """Write the report beside the run (JSON for the maintainers, Markdown
    for the person to read). Works offline; the person may attach the file
    to an email themselves. Refuses a report that is not ready."""
    from pathlib import Path
    if not fr.ready:
        raise NotSendable("this report is not ready to send")
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"feedback_report_{fr.created[:10]}_{fr.report_id[:8]}"
    j, m = d / f"{stem}.json", d / f"{stem}.md"
    j.write_text(to_json(fr), encoding="utf-8")
    m.write_text(render_markdown(fr), encoding="utf-8")
    return {"json": str(j), "markdown": str(m)}


