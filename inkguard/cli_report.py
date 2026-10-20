"""What a run writes besides the redacted files: the layout of the results
folder (three tagged folders), the guide, the audit trail, the citations
file and the feedback report, and the fingerprints of what was written.
Presentation only: what is said about a run, never how a note is
de-identified."""
from __future__ import annotations

import csv
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # annotations only
    from inkguard.cli import RunStats


def _cli():
    """The command-line module, looked up when called, so helpers that live
    there (and tests that replace them) are the ones used."""
    from inkguard import cli
    return cli


# =====================================================================
# THE REPORT
# =====================================================================

# Both the Limited Data Set and full removal are offered; the guidance says
# plainly that Limited Data Set output needs closer manual review.
LDS_REVIEW_GUIDANCE = (
    "Closer manual review is advised for Limited Data Set output. The profile keeps "
    "dates, ZIP codes and towns right beside the words it removes, so a single mistake "
    "in telling a town from a person (\"Jackson\") or a date from a record number "
    "leaves identifying text in, and the kept dates, ZIP and age together already point "
    "toward one person. The tool settles every such conflict toward removal: it keeps a "
    "town only where the note plainly uses it as a place (a state or ZIP after it, "
    "\"lives in\" before it) and removes the rest, so fewer towns survive than the "
    "profile allows. Read every file, flagged or not, and look closely "
    "wherever a kept town or date sits next to a person. Limited Data Set output remains "
    "protected health information and is shared only under a data use agreement.")


#: The review statement written into the machine record of every run
#: (.inkguard/audit_trail.json). The software decides nothing on its own: a
#: person must read every file, including those with no flags.
NOT_REVIEWED = (
    "INCOMPLETE REVIEW. NOT READY TO SHARE. No person has checked these files. The software alone "
    "produced them, and \"no flags\" means only that the software found nothing "
    "it was unsure of. Before any file leaves this computer, a person must read "
    "every file, including those with no flags, and check every removal. Arrange "
    "independent review and sign-off as your study protocol requires.")


# ---- The results folder: every file received comes out
# in exactly one of three piles, and every file in a pile carries the pile's
# tag at the front of its name, so a search for the tag finds them all. Only
# the three piles hold anything from the input. Beside them: one guide to
# read first, one audit trail to keep, one feedback form (the only file the
# user is asked to email to the project, by hand), and technical records with
# no input material.
#
#   0_READ_ME_FIRST_(Guide).txt
#   1_REDACTED-NO-FLAGS/                REDACTED-NO-FLAGS__<name>
#   2_REDACTED-FLAGGED/                 REDACTED-FLAGGED__<name>, _WORDS-TO-CHECK.md
#   3_NOT-PROCESSED-MAY-CONTAIN-PHI/    NOT-PROCESSED-MAY-CONTAIN-PHI__<original name>,
#                                       _ORIGINAL-NAMES-KEY.csv
#   4_Audit_trail.pdf                   facts only: who, when, every file by id
#   5_Citations.txt
#   6_PLEASE-RETURN-...Feedback-report.docx   made by the software; your feedback; you may email it
#   .inkguard/                          hidden: machine copies, for --resume and the window
TAG_DONE = "REDACTED-NO-FLAGS"
TAG_FLAGGED = "REDACTED-FLAGGED"
TAG_NOT = "NOT-PROCESSED-MAY-CONTAIN-PHI"
PILE_DONE = "1_" + TAG_DONE
PILE_FLAGGED = "2_" + TAG_FLAGGED
PILE_NOT = "3_" + TAG_NOT
PILES = (PILE_DONE, PILE_FLAGGED, PILE_NOT)
PILE_TAG = {PILE_DONE: TAG_DONE, PILE_FLAGGED: TAG_FLAGGED, PILE_NOT: TAG_NOT}
TAG_SEP = "__"
GUIDE_FILE = "0_READ_ME_FIRST_(Guide).txt"
NOTICE_FILE = GUIDE_FILE
EVIDENCE_FILE = "4_Audit_trail.pdf"
AUDIT_FILE = EVIDENCE_FILE
CITE_FILE = "5_Citations.txt"
TECH_DIR = ".inkguard"
REPORT_JSON = "audit_trail.json"              # in TECH_DIR
SUPPORT_LOG = "support_log.jsonl"             # in TECH_DIR
GUARD_LOG = "intake_guard_log.md"             # in TECH_DIR
SETTINGS_FILE = "run_settings.json"           # in TECH_DIR
WORDS_FILE = "_WORDS-TO-CHECK.md"             # in each patient folder of PILE_FLAGGED
KEY_FILE = "_ORIGINAL-NAMES-KEY.csv"          # in PILE_NOT: new names -> original names
PILE_NOTE = "_ABOUT-THIS-FOLDER.txt"          # in PILE_NOT

UNFINISHED_TEXT = """INCOMPLETE REVIEW. NOT READY TO SHARE.

THIS RUN DID NOT FINISH.

Inkguard started cleaning files into this folder and stopped before the
end. This text is replaced by the full guide when a run finishes, so if
you are reading it, the run was interrupted (the computer slept or shut
down, the window was closed, memory or disk space ran out).

Nothing in this folder has been checked by a person, and some files may be
missing or only partly written. Do not share anything from it.

To finish: run Inkguard again into this same folder. What this run wrote
is deleted and it starts over from your originals, which were only read.
If it stops again, email inkguard.project@gmail.com (never send patient
information).
"""
MANIFEST_FILE = "file_fingerprints.csv"       # in TECH_DIR
FEEDBACK_FILE = "6_PLEASE-RETURN-to-inkguard.project@gmail.com_Feedback-report.docx"
PROBLEM_FILE = FEEDBACK_FILE
REPORT_EMAIL = "inkguard.project@gmail.com"
WEBSITE = "https://inkguard.org"
SITE = "inkguard.org"
SOURCE = "https://github.com/inkguard-app/inkguard"

# The last thing every run says, on the screen and in the guide.
ISSUES = SOURCE + "/issues"
CLOSING_LINES = (
    f"Please email the feedback report (6_PLEASE-RETURN-...) to {REPORT_EMAIL}.",
    "  Its system record holds no patient information; check anything you add.",
    f"Did Inkguard help? Cite the version you used ({CITE_FILE}).",
)


#: Measured performance of this version, from benchmark/RESULTS.md. Every
#: figure was measured on invented or public test data, never on a user's files.
PERFORMANCE = (
    ("Messy-notes benchmark, held-out half (72 invented patients, 289 notes, never used for tuning)",
     "Identifiers Safe Harbor requires removing, found and removed",
     "90.5% (4,058 of 4,485; 95% CI 89.1-91.7%)"),
    ("Preservation set, held-out half",
     "Identifiers removed / clinical phrases kept whole",
     "97.9% (95% CI 96.2-99.4%) / 98.1% (95% CI 97.1-99.1%)"),
    ("ASQ-PHI, a published external benchmark", "Safe Harbor identifiers",
     "97.1% on the half never used for tuning"),
    ("Generated mixed set (400 notes, 30 name origins, new seed)", "Identifiers removed",
     "97.8% (95% CI 97.1-98.5%); 89.7% on the first seed, before fixes"),
    ("Microsoft presidio-research synthetic sentences (general text, many non-US formats)",
     "Identifiers removed", "70.3%"),
)
PERFORMANCE_WEAKEST = ("Usernames and handles (55.9% removed), device identifiers (58.1%), other ID "
                       "numbers (63.6%), ages 90 and over (75.4%), ZIP codes (77.8%) on the held-out half.")

#: Quoted from the Apache License 2.0 (LICENSE), sections 7 and 8.
LICENCE_WARRANTY = ("Licensor provides the Work (and each Contributor provides its Contributions) on an "
                    "\"AS IS\" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or "
                    "implied ... You are solely responsible for determining the appropriateness of using "
                    "or redistributing the Work and assume any risks associated with Your exercise of "
                    "permissions under this License.")
LICENCE_LIABILITY = ("In no event and under no legal theory ... shall any Contributor be liable to You for "
                     "damages, including any direct, indirect, special, incidental, or consequential "
                     "damages of any character arising as a result of this License or out of the use or "
                     "inability to use the Work ...")


def _computer() -> str:
    """The operating system and Python version: what support needs, and
    nothing that identifies the computer or its owner."""
    import platform
    system = platform.system()
    if system == "Darwin":
        system = "macOS " + (platform.mac_ver()[0] or "")
    elif system == "Windows":
        system = "Windows " + platform.release()
    return f"{system.strip()} · Python {platform.python_version()}"


def _run_codes(stats) -> dict[str, int]:
    """Support codes this run raised: the intake guard's, and any run-level
    problem. Codes only, never a file or a reason in words."""
    codes: dict[str, int] = {}
    for e in stats.files:
        if e.code:
            codes[e.code] = codes.get(e.code, 0) + 1
    for prob in (stats.accounting or {}).get("problems", []):
        c = ("RUN-INPUT-CHANGED" if ("changed during the run" in prob
                                     or "changed while the tool was running" in prob)
             else "RUN-UNACCOUNTED")
        codes[c] = codes.get(c, 0) + 1
    return dict(sorted(codes.items()))


FEEDBACK_NAME = "Feedback report"
# The fixed header and footer lines of a document, as the check reads them.
_META_RE = re.compile(r"^Run [0-9A-Za-z\-]+ · Version [0-9][0-9A-Za-z.\-]* · Written "
                      r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]+ UTC$")
_ABOUT_RE = re.compile(r"^About this document\. Written by Inkguard [0-9][0-9A-Za-z.\-]*, code "
                       r"fingerprint [0-9a-f]{16}, for run [0-9A-Za-z\-]+\..*$")
#: Section 2: the sender's own details, all optional. They are the sender's,
#: not a patient's, so the checker does not run the patient scan on them; it
#: checks only that each holds what its label says.
CONTACT_FIELDS = ("Name:", "Role:", "Organisation:", "Email:")
CONTACT_PERMISSION = "You may contact me about this feedback."


def _short_citation() -> str:
    from inkguard.fluorite import DOI
    return f"Wajahat A. Inkguard (Version {_version()}). " + (f"https://doi.org/{DOI}" if DOI else SOURCE)


def feedback_record(stats) -> list[str]:
    """The system record of the feedback report: plain lines, counts and
    codes only, nothing that could carry patient information (a test
    checks). Short for an ordinary run; longer when more went wrong."""
    from collections import Counter
    from inkguard import __version__, lapis
    piles = _piles(stats)
    acc = stats.accounting or {}
    files = stats.files or []
    codes = _run_codes(stats)
    kinds = Counter()
    ext = Counter()
    size = 0
    why = Counter()
    for e in files:
        suf = Path(e.source).suffix.lower() if e.source else ""
        ext[suf if suf and len(suf) <= 8 and suf[1:].isalnum() else "(other)"] += 1
        kinds[f"{e.kind}: {_OUTCOME_WORDS.get(e.outcome, e.outcome)}"] += 1
        size += e.size or 0
        if e.outcome in ("set_aside", "system"):
            r = (e.reason or "").split(" (")[0].split(":")[0]
            why[r if r and not any(ch.isdigit() for ch in r) and len(r) < 80 else "other reason"] += 1
    hidden = Counter()
    for d in stats.doc_summaries or []:
        hidden.update(d.get("hidden_content_found") or {})

    def row(label, value):
        return f"{label:<14}{value}"

    def join(c):
        return " · ".join(f"{k} {v:,}" for k, v in c) or "none"
    lines = [
        row("Software", f"Inkguard {__version__} · code {_cli()._fingerprint()[:16]}"),
        row("Run", f"{stats.run_id} · {_computer()} · {time.time() - stats.started:,.0f} s"),
        row("Method", f"{lapis._METHOD.get(stats.profile, stats.profile)} · names {stats.redaction_mode} · "
                      f"date shift {'on' if os.environ.get('PHI_DATE_SHIFT') == 'true' else 'off'} · "
                      f"whole file first {'yes' if stats.file_mode else 'no'}"),
        row("Files", f"{piles['received']:,} received ({size / 1e6:,.1f} MB, {stats.patients:,} folders) · "
                     f"no flags {piles['done']:,} · flagged {piles['flagged']:,} · "
                     f"not processed {piles['not_processed']:,}"),
        row("Checks", f"accounted for {'yes' if acc.get('verified') else 'no' if acc else '-'} · "
                      f"stopped {stats.failed:,} · refused at intake {stats.guard_refused:,}"),
        row("Codes", ", ".join(f"{c} x{n}" for c, n in codes.items()) or "none"),
        row("Types", join(sorted(ext.items()))),
        row("Outcomes", join(sorted(kinds.items()))),
        row("Flag reasons", join((k.replace("_", " "), v) for k, v in
                                 sorted(stats.review_reasons.items(), key=lambda kv: -kv[1]))),
        row("Flag kinds", f"tool still learning {stats.review_tool:,} · abbreviations {stats.review_either:,} · "
                          f"unclear writing {stats.review_writing:,}"),
        row("Removed", join(_cli()._plain_categories(stats.entity_counts).items())),
    ]
    if why:
        lines.append(row("Not processed", join(why.most_common())))
    if hidden:
        lines.append(row("Hidden", join(sorted(hidden.items()))))
    if stats.guard_notes:
        lines.append(row("Intake notes", join(sorted(stats.guard_notes.items()))))
    for prob in acc.get("problems", [])[:20]:
        lines.append(row("Problem", "RUN-UNACCOUNTED" if "changed" not in prob else "RUN-INPUT-CHANGED"))
    return lines


def write_feedback(stats, path: Path) -> None:
    """The feedback report, as a Word file (inkguard/cli_docs.py)."""
    from inkguard import cli_docs
    written = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    cli_docs.feedback_docx(feedback_record(stats), stats.run_id, written, path)


def problem_report_text(stats) -> str:
    """The feedback report as the lines its Word file holds, in order: what
    the check reads, and what tests read."""
    from inkguard import cli_docs
    lines, sha = cli_docs.system_lines(feedback_record(stats))
    return "\n".join([*problem_report_text_template_lines(), "Name: ", "Role: ", "Organisation: ", "Email: ",
                      cli_docs.SYSTEM_BEGIN, *lines, cli_docs.SYSTEM_END.format(sha=sha)])


def check_feedback(text: str) -> list[tuple[int, str]]:
    """The lines of a feedback report that may hold patient information.

    The system record is skipped when its fingerprint still matches (it was
    written from counts and codes); if it was edited, it is scanned like
    everything else. Fixed lines are accepted. Contact lines are checked for
    their own kind only. Every other line is run through the
    de-identification engine and a conservative net for numbers, emails and
    web addresses; any line where something would be removed or flagged is
    returned, with its line number."""
    import hashlib
    from inkguard import amber, cli_docs, contract
    lines = text.split("\n")
    template = set(problem_report_text_template_lines())
    skip: set[int] = set()
    if cli_docs.SYSTEM_BEGIN in lines:
        b = lines.index(cli_docs.SYSTEM_BEGIN)
        e = next((i for i in range(b + 1, len(lines)) if lines[i].startswith("--- end system record")), None)
        if e is not None:
            m = re.search(r"fingerprint ([0-9a-f]{16})", lines[e])
            body = "\n".join(lines[b + 1:e])
            if m and hashlib.sha256(body.encode("utf-8")).hexdigest()[:16] == m.group(1):
                skip = set(range(b, e + 1))
    flagged = []
    for i, line in enumerate(lines):
        t = line.strip()
        if (i in skip or not t or t in template or _META_RE.match(t) or _ABOUT_RE.match(t)
                or re.fullmatch(r"[☐☒xX\[\] ]{1,4}" + re.escape(CONTACT_PERMISSION) + r".*", t)):
            continue
        contact = _contact_line_ok(t)
        if contact is not None:
            if not contact:
                flagged.append((i + 1, line))
            continue
        try:
            r = contract.deidentify(t, mode="mask")
            risky = r.status == "stop" or bool(r.edits) or r.status == "review"
        except Exception:  # noqa: BLE001 — a checker that fails must say no
            risky = True
        if not risky:
            risky = any(p.search(t) for p in (amber._EMAIL, amber._URL, amber._PHONE, amber._LONGNUM))
        if risky:
            flagged.append((i + 1, line))
    return flagged


def problem_report_text_template_lines() -> list[str]:
    """Every fixed line of the feedback report (no run values)."""
    from inkguard import cli_docs
    return [x.strip() for x in (
        f"Inkguard · {FEEDBACK_NAME}", f"Please email this report to {REPORT_EMAIL}.",
        "The system record holds no patient information; check anything you add. Your section is optional. "
        "To check it before sending: inkguard --check-feedback <this file>",
        cli_docs.FEEDBACK_BOX, cli_docs.FEEDBACK_HINT, cli_docs.CONTACT_BOX, cli_docs.SYSTEM_HEAD,
        f"End of {FEEDBACK_NAME.lower()}.")]


_CONTACT_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}")


def _contact_line_ok(line: str) -> bool | None:
    """None when the line is not a contact field. Otherwise whether its value
    holds only what the label asks for: an email address on the email line,
    and no record numbers, addresses or links in the others."""
    for f in CONTACT_FIELDS:
        if line.startswith(f):
            v = line[len(f):].strip()
            if not v:
                return True
            if f == "Email:":
                return bool(_CONTACT_EMAIL.fullmatch(v))
            return (len(v) <= 120 and not re.search(r"\d{3}", v) and "@" not in v
                    and "://" not in v and "www." not in v.lower())
    return None


CITE_NAME = "Citations"


def cite_text(report: dict) -> str:
    """5_Citations.txt (inkguard/cli_docs.py)."""
    from inkguard import cli_docs
    return cli_docs.cite_txt(report)


def _piles(stats) -> dict:
    """How many files received went to each pile. Every file received is in
    exactly one: the sum is checked after the run (see cli._verify_accounting)."""
    acc = stats.accounting or {}
    p = acc.get("piles")
    if p:
        return p
    # A run with no file accounting (a CSV): count what the run itself counted.
    sa = acc.get("set_aside", stats.failed)
    return {"received": stats.processed + stats.needs_review + sa, "done": stats.processed,
            "flagged": stats.needs_review, "not_processed": sa}


def write_output_manifest(out_dir: Path, run_id: str = "", outputs: dict | None = None) -> int:
    """A fingerprint (SHA-256) of every file this run left in the results
    folder, so anyone can later confirm a file is exactly what the tool
    wrote. Written to the technical records. The not-processed pile is left
    out: those are the originals, and a fingerprint of a patient's record is
    a link back to that record."""
    from inkguard import __version__
    rows = []
    me = Path(TECH_DIR) / MANIFEST_FILE
    for f in sorted(out_dir.rglob("*")):
        if not f.is_file():
            continue
        relp = f.relative_to(out_dir)
        if relp == me or relp.parts[0].startswith(".work-") or relp.parts[0] == PILE_NOT \
                or relp == Path(TECH_DIR) / "RUN_IN_PROGRESS":
            continue
        rel = relp.as_posix()
        status = _manifest_status(rel, outputs)
        if status == NOT_OURS:
            # Never vouched for: a file the tool did not write gets no
            # fingerprint, version or run id.
            rows.append([rel, status, "", f.stat().st_size, "", ""])
            continue
        rows.append([rel, status, _cli()._sha256(f), f.stat().st_size, __version__, run_id])
    (out_dir / TECH_DIR).mkdir(parents=True, exist_ok=True)
    with (out_dir / me).open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "status", "sha256", "bytes", "inkguard_version", "run_id"])
        w.writerows(rows)
    return len(rows)


NOT_OURS = "not written by this run: check it, it may hold patient information"
STATUS_DONE = f"{TAG_DONE}: incomplete review"
STATUS_FLAGGED = f"{TAG_FLAGGED}: pending review"


def _manifest_status(rel: str, outputs: dict | None) -> str:
    """What a file in the results folder is. With the run's own list of what
    it wrote, a cleaned note is a cleaned note, and anything the run did not
    write is said to be so."""
    top = "/" not in rel
    name = rel.rsplit("/", 1)[-1]
    flagged = rel.startswith(PILE_FLAGGED + "/")
    if outputs is None:
        if flagged:
            return STATUS_FLAGGED
        return STATUS_DONE if rel.startswith(PILE_DONE + "/") else "report or guide"
    if ((top and name.lower() in _cli()._OURS_TOP) or rel.startswith(TECH_DIR + "/")
            or (flagged and name == WORDS_FILE)):
        return "report or guide"
    key = rel
    while True:
        if key in outputs:
            return outputs[key]
        cut = key.rfind(".")
        if cut <= 0:
            return NOT_OURS
        key = key[:cut]                     # note.pdf.txt beside note.pdf


def _run_outputs(stats) -> dict:
    out = {}
    for e in stats.files or []:
        if not e.output:
            continue
        rel = Path(e.output).as_posix()
        if e.outcome == "held" or (e.outcome == "already_present" and rel.startswith(PILE_FLAGGED + "/")):
            out[rel] = STATUS_FLAGGED
        elif e.outcome in ("deidentified", "already_present"):
            out[rel] = STATUS_DONE
    return out


def _opaque() -> bool:
    from inkguard import contract
    return contract.opaque_enabled()


def _clean(result) -> str:
    """The text that is written: the engine's output, with every placeholder
    made the same marker when --opaque is on."""
    from inkguard import contract
    return contract.opaque(result.clean_text) if contract.opaque_enabled() else result.clean_text


def _verify() -> str:
    from inkguard import fluorite
    return fluorite.VERIFY_NOTICE


def _fingerprint() -> str:
    from inkguard import fluorite
    if not hasattr(_fingerprint, "value"):
        _fingerprint.value = fluorite.code_fingerprint()
    return _fingerprint.value


def _methods(stats, shifted: bool) -> str:
    from inkguard import fluorite
    return fluorite.methods_text(version=_version(), profile=stats.profile, date_shifted=shifted,
                             fingerprint=_fingerprint(), run_id=stats.run_id)


def _lapis_incomplete() -> str:
    from inkguard import lapis
    return lapis.INCOMPLETE


def _version() -> str:
    from inkguard import __version__
    return __version__


def _provenance(stats, status: str) -> str:
    from inkguard import __version__, lapis
    profile = stats.profile
    if profile == "safe_harbor" and os.environ.get("PHI_DATE_SHIFT") == "true":
        profile = "date_shifted"               # shifted dates are not Safe Harbor
    from inkguard import contract
    return lapis.line(version=__version__, profile=profile, status=status,
                      run_id=stats.run_id, manifest=f"{TECH_DIR}/{MANIFEST_FILE}",
                      opaque=contract.opaque_enabled())


def _stamp(path: Path, stats, held: bool) -> None:
    if not stats.stamp:
        return
    from inkguard import lapis
    if lapis.stamp(path, _provenance(stats, lapis.PENDING if held else lapis.INCOMPLETE)):
        stats.stamped += 1


def download_guide() -> str:
    """GUIDE.txt in the download. The same text every run writes as
    0_READ_ME_FIRST_(Guide).txt: one generic guide, so the user reads at the start
    exactly what they are given at the end. The counts are in the audit trail."""
    return guide_text()


def guide_text(stats: "RunStats | None" = None, out_name: str = "") -> str:
    """The plain-language guide at the top of every results folder, written
    for the clinic and research staff who will open it, not for programmers:
    what happened to every file, what each thing is, what to do next, what to
    keep, what not to do, and a place to acknowledge it. Acknowledging
    approves nothing."""
    return "\n".join([
        "READ ME FIRST",
        "=" * 13,
        "",
        f"Inkguard {_version()}",
        "",
        _verify(),
        "",
        "INCOMPLETE REVIEW. NOT READY TO SHARE.",
        "Inkguard's output is produced by software alone. No person has checked it, and",
        "the software can make mistakes. A person must read every file before any of it",
        "leaves this computer.",
        "",
        "WHAT HAPPENS TO YOUR FILES",
        "  Inkguard writes everything into one results folder. Every file you give it",
        "  comes out in exactly one of three folders, and every file in a folder starts",
        f"  with that folder's tag. How many went to each is in {AUDIT_FILE}.",
        "",
        "  Search for a tag (in Finder, File Explorer, or any search) to find every file",
        "  in that group.",
        "",
        f"  {PILE_DONE}/",
        f"     Tag: {TAG_DONE}{TAG_SEP}",
        "     The software removed what it found and was not unsure of anything.",
        "     Not yet reviewed by a person.",
        "",
        f"  {PILE_FLAGGED}/",
        f"     Tag: {TAG_FLAGGED}{TAG_SEP}",
        "     The software removed what it found but was unsure about some words.",
        f"     Each patient folder has a {WORDS_FILE} that lists them, with the line",
        "     each is on and why the software was unsure.",
        "",
        f"  {PILE_NOT}/",
        f"     Tag: {TAG_NOT}{TAG_SEP}",
        "     Copies of the files the software did not or could not read: photos,",
        "     scans, file types it does not read, files it stopped on as a",
        "     precaution, and system files. NOT de-identified; original names kept.",
        f"     {KEY_FILE} links each neutral name in folders 1 and 2",
        "     back to the original file.",
        "",
        "  Neutral names: where the software finds an identifier in a file or folder name,",
        "  the copy has a neutral one instead (for example file-0001 or patient-0001). With",
        "  randomised names (--scramble-names, or the window's option), every name",
        "  becomes a random code.",
        "",
        "THE OTHER THINGS IN THIS FOLDER",
        f"  {GUIDE_FILE}",
        "     This guide. The same guide comes with the download.",
        f"  {AUDIT_FILE}",
        "     The audit trail: a machine-written log of the run. Who entered what before",
        "     it started, when it ran, the exact software, how many files went to each",
        "     folder, and every file by id with its folder and fingerprint. Facts only,",
        "     with no note text. It does show the computer's name and the name of the",
        "     input folder you chose.",
        f"  {CITE_FILE}",
        "     How to cite the version you used, with a Methods paragraph template.",
        f"  {FEEDBACK_FILE}",
        "     Made by the software for the people who maintain Inkguard: codes and",
        "     counts, and a box for anything you want to say. Please email it back; it is",
        "     the only thing we receive. Nothing is sent for you.",
        f"  {TECH_DIR}/",
        "     Hidden. The run's machine record. During a run it also holds copies of",
        "     your originals, deleted when the run ends; if a run is interrupted they",
        "     stay until you run again into this folder or delete it. Treat it like",
        f"     {PILE_NOT}.",
        *(["",
           "  WATERMARK: the first line of every text and Word file names the software, the",
           "  version, the review status and the run id, so it stays labelled wherever it",
           "  goes, unless the run was started with --no-header. Spreadsheets, CSV and PDF",
           "  files are identified by their file-name tag only."]),
        "",
        "YOUR ORIGINAL FILES",
        "  Only read, never changed, moved or deleted. After the run each original's",
        f"  fingerprint (SHA-256) is checked again; {AUDIT_FILE} says whether all matched.",
        "  Inkguard's own code makes no network connections.",
        "",
        "WHAT TO DO NEXT",
        f"  1. Read {AUDIT_FILE} to see what happened to every file.",
        f"  2. In {PILE_FLAGGED}, open each {WORDS_FILE} and decide every word.",
        "     The line numbers take you to it; Find (Ctrl+F or Cmd+F) works too.",
        f"  3. Read every file in {PILE_DONE} as well, and check each removal.",
        f"  4. Decide what to do with {PILE_NOT}: de-identify those files by",
        "     hand, or leave them out of the study.",
        "  5. Arrange independent review and sign-off as your study protocol requires.",
        "     Whether any file may be shared is for your study team and institution to decide.",
        "",
        "WHAT TO KEEP",
        f"  - {AUDIT_FILE}, with your study records. It is your only copy of the run's",
        "    audit trail: Inkguard sends nothing anywhere and keeps no copy.",
        f"  - {PILE_NOT} on this computer only, for as long as your data plan allows.",
        "",
        "WHAT NOT TO DO",
        "  - Do not email, upload, copy to a USB drive or share any file here until a person",
        "    has reviewed it.",
        f"  - Do not share this whole folder: it holds {PILE_NOT}. Copy out only files",
        "    a person has reviewed.",
        f"  - Never share {PILE_NOT} or {KEY_FILE}. They are not de-identified.",
        "",
        "PROBLEMS AND THANKS",
        *["  " + line for line in CLOSING_LINES],
        "  Never send patient information.",
        "",
        "CITING INKGUARD",
        f"  {_short_citation()}",
        f"  The full reference, BibTeX and a Methods paragraph template: {CITE_FILE}.",
        "",
        "IMPORTANT",
        "  Inkguard is provided as is, without warranty or liability, under the Apache",
        "  License 2.0. Its output is not a certification or a legal",
        "  determination. Reviewing the files, and deciding whether any may be used or",
        "  shared, is the responsibility of the study team and its institution.",
        "",
        "ACKNOWLEDGEMENT (optional, for your records)",
        "  Signing below records that you read this guide. It does NOT approve any file",
        "  and does NOT make anything ready to share.",
        "",
        "  I have read this guide and understand that these files are an incomplete review",
        "  and are not ready to share.",
        "",
        "  Name: ______________________________   Role: ____________________",
        "",
        "  Date: ______________   Signature: ______________________________",
        "",
    ])


def build_report(stats: RunStats, cfg, shifted: bool) -> dict:
    elapsed = time.time() - stats.started
    return {
        "report_version": 1,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": stats.run_id,
        "provenance_line": ({"files_stamped": stats.stamped,
                             "example": _provenance(stats, _lapis_incomplete())}
                            if stats.stamp else {"files_stamped": 0, "turned_off": True}),
        "human_review": {"done": False, "statement": NOT_REVIEWED},
        "run_record": stats.run_record or {},
        "scramble": bool(getattr(stats, "scramble", False)),
        "input_set_fingerprint": _input_set_fingerprint(stats),
        "file_log": _file_log(stats),
        "locations": dict(getattr(stats, "locations", {}) or {}),
        "by_folder": _by_folder(stats),
        "support_codes": _run_codes(stats),
        "verify_notice": _verify(),
        # Which exact code made this batch: anyone can recompute it from the
        # same release and compare (python -m inkguard.cli --about).
        "software": {"name": "Inkguard", "version": _version(),
                     "code_fingerprint_sha256": _fingerprint()},
        "methods_paragraph_draft": _methods(stats, shifted),
        # Where things stand, in the words the person using the tool needs:
        # what is done, what is waiting for them, what was never processed,
        # and that nothing is ready to share yet.
        # The three piles: every file received is in exactly one.
        "piles": {**{k: v for k, v in _piles(stats).items()},
                  "folders": {"done": PILE_DONE, "flagged": PILE_FLAGGED, "not_processed": PILE_NOT},
                  "every_file_in_exactly_one": (stats.accounting or {}).get("verified")},
        "human_reviewed": False,
        "status": {
            "cleaned_by_software_not_yet_reviewed": stats.processed,
            "held_for_a_person": stats.needs_review,
            "set_aside_not_processed": (stats.accounting or {}).get("set_aside", stats.failed),
            "of_which_stopped_as_a_precaution": stats.failed,
            "ready_to_share": 0,
            "originals_unchanged_checked_by_sha256": stats.originals_unchanged,
        },
        "deidentification": {
            "profile": cfg["profile"],
            "citation": cfg["citation"],
            "description": cfg["description"],
            "output_is_still_phi": cfg["is_still_phi"],
            "requires_data_use_agreement": cfg["requires_dua"],
            "obligations": cfg["obligations"],
            **({"review_guidance": LDS_REVIEW_GUIDANCE,
                "towns_removed_to_be_safe": stats.lds_towns_removed,
                "names_or_ids_removed_inside_kept_dates_or_towns": stats.lds_overlaps_removed}
               if cfg["is_still_phi"] else {}),
        },
        "placeholders": ("opaque: every removal shows [REMOVED], with no type"
                         if _opaque() else "typed: each removal shows its kind, e.g. [NAME], [DATE]"),
        "date_shifting": {
            "applied": shifted,
            "dates_shifted": stats.dates_shifted,
            "note": ("Per-patient offsets. Intervals between dates are "
                     "preserved exactly; absolute dates are not "
                     "recoverable without the root key."
                     if shifted else
                     "Not applied. Original dates retained subject to the "
                     "profile above."),
        },
        "corpus": {
            "documents_found": stats.total,
            "documents_processed": stats.processed,
            "documents_failed": stats.failed,
            "documents_skipped_existing": stats.skipped_existing,
            "elapsed_seconds": round(elapsed, 1),
        },
        "identifiers_removed": _cli()._plain_categories(stats.entity_counts),
        "residual_risk": {
            "records_flagged_for_review": stats.linkage_review,
            "records_flagged_caution": stats.linkage_caution,
            "note": (
                "Screening signal only. NOT an Expert Determination under "
                "45 CFR 164.514(b)(1) and no population estimate is "
                "reported. Safe Harbor lists the identifiers to remove; "
                "it does not measure whether the remaining content still "
                "describes one findable person. Whether any record may be "
                "shared is for the study team and its institution to decide."
            ),
        },
        "data_integrity": {
            "records_with_multiple_person_scoped_ids": stats.contaminated,
            "records_with_multi_system_history": stats.cross_system,
            "note": (
                "Multiple person-scoped identifiers (e.g. two SSNs) in one "
                "record suggest content carried forward from another "
                "patient. Multiple institution-scoped identifiers (e.g. two "
                "MRNs) normally indicate a patient seen at more than one "
                "system and are reported as history, not as a defect."
            ),
        },
        "names": {
            "redaction_mode": stats.redaction_mode,
            "note": (
                "Each person is replaced by a code, e.g. [PERSON-K7Q3F], that is "
                "identical in every note of that patient's record and different "
                "across patients. Codes are random: nothing about the person goes "
                "into a code. The table that gives a person the same code each time "
                "is kept only on this computer and is never shared or exported. "
                "Roles (PERSON / FAMILY / PROVIDER) are labels, not "
                "identity. A bare surname shared by two named people keeps its "
                "own code rather than being merged into either."
                if stats.redaction_mode == "pseudonym" else
                "Each person is replaced by a generic [NAME] placeholder."),
            # Which table coded this output, and how big it was: ties every
            # code to its table without revealing anything about anyone.
            "code_table": (_cli()._code_table_info() if stats.redaction_mode == "pseudonym" else None),
            "patients": stats.patients,
            "uncertain_tokens_masked": stats.uncertain_masked,
        },
        "exit_inspection": {
            "every_document_inspected_on_exit": True,
            "grades": dict(sorted(stats.exit_grades.items())),
            "quarantined": stats.quarantined,
            "detections_lost_between_stages": stats.ledger_lost,
            "note": (
                "Each redacted copy was searched again for confirmed names and "
                "structured identifiers. A file that failed this check was not "
                f"written as redacted; it was copied to {PILE_NOT}/. The grades "
                "describe this software check only, not a person's review. "
                "Grade A: nothing found by this check (not a promise that nothing "
                "remains). B: uncertain words masked as [NAME], or a linkage "
                "caution. C: linkage review. "
                f"F: an identifier found again; the file was stopped and copied to {PILE_NOT}/."),
        },
        "patient_file": {
            "read_whole_file_first": stats.file_mode,
            "patient_files_read": stats.files_read,
            "note": (
                "Every note of a patient was read before any was redacted: a name, "
                "employer or facility confirmed in one note was removed from all of "
                "them, and a word the file uses clinically was read that way in every "
                "note." if stats.file_mode else
                "Notes were redacted one at a time (--note-by-note)."),
        },
        "review_check": {
            "held_for_review": stats.needs_review,
            "review_list": {"words_the_tool_is_still_learning": stats.review_tool,
                            "abbreviations_not_found": stats.review_either,
                            "writing_the_tool_could_not_interpret": stats.review_writing},
            "reasons": dict(sorted(stats.review_reasons.items())),
            "note": (
                f"A file goes to {PILE_DONE}/ only if every word left in it is one the "
                "tool can name; it still needs a person's review. Files with words it "
                f"cannot name were redacted and flagged: they are in {PILE_FLAGGED}/, "
                "each patient folder with a list of the words to check. No file may "
                "leave this computer until a person has checked it."),
        },
        "failures": [{"file": f, "error": e} for f, e in stats.failures[:200]],
        "files": _files_section(stats),
        "documents": _documents_section(stats),
    }


_OUTCOME_WORDS = {"deidentified": "redacted, no flags", "held": "redacted, flagged",
                  "set_aside": "not processed", "system": "not processed (system file)",
                  "already_present": "kept from an earlier run"}


def _file_log(stats) -> list[dict]:
    """Every file received, by id: its outcome, and for what was redacted
    the neutral name written and its fingerprint. Original names (pile 3)
    are never listed."""
    out = []
    for e in stats.files or []:
        written, sha = "", ""
        if e.output and not e.output.startswith(PILE_NOT):
            written = e.output
            try:
                sha = _cli()._sha256(Path(stats.out_dir) / e.output) if stats.out_dir else ""
            except OSError:
                sha = ""
        out.append({"id": e.id, "outcome": _OUTCOME_WORDS.get(e.outcome, e.outcome),
                    "written": written, "sha256": sha})
    return out


def _by_folder(stats) -> list[dict]:
    """Files per patient folder, by outcome. A folder is named as it was
    written in the first two folders (neutral if its name held an
    identifier); a folder with nothing written there is named by its id."""
    rows: dict[int, dict] = {}
    for e in stats.files or []:
        r = rows.setdefault(e.patient, {"folder": "", "files": 0, "done": 0, "flagged": 0, "not_processed": 0})
        r["files"] += 1
        out = Path(e.output) if e.output else None
        if e.outcome in ("set_aside", "system"):
            r["not_processed"] += 1
        elif out and out.parts[0] == PILE_FLAGGED:
            r["flagged"] += 1
        else:
            r["done"] += 1
        if out and out.parts[0] in (PILE_DONE, PILE_FLAGGED) and not r["folder"]:
            r["folder"] = out.parts[1] if len(out.parts) > 2 else "(top level)"
    return [{**r, "folder": r["folder"] or f"P{k} (only files not processed)"} for k, r in sorted(rows.items())]


def _input_set_fingerprint(stats) -> str:
    """One SHA-256 over the sorted fingerprints of every file received: the
    same set of files gives the same value, and no name or path goes in."""
    import hashlib
    shas = sorted(e.sha256 for e in (stats.files or []) if e.sha256)
    return hashlib.sha256("\n".join(shas).encode()).hexdigest() if shas else ""


def _files_section(stats: RunStats) -> dict:
    a = stats.accounting or {}
    if not a:
        return {}
    return {
        "every_file_accounted_for": a.get("verified", False),
        "found": a.get("files", 0), "deidentified": a.get("deidentified", 0),
        "held_for_review": a.get("held", 0), "set_aside_not_deidentified": a.get("set_aside", 0),
        "system_files_ignored": a.get("system", 0), "already_present": a.get("already_present", 0),
        "outputs_renamed_to_neutral_names": stats.renamed_outputs,
        "intake_guard_refused": stats.guard_refused,
        "intake_guard_notes": dict(sorted(stats.guard_notes.items())),
        "problems": a.get("problems", []),
        "per_patient": a.get("per_patient", []),
        "set_aside": [{"file": e.id, "reason": e.reason} for e in stats.files if e.outcome == "set_aside"][:500],
        "note": ("Every file in the input folder was counted, given exactly one outcome and "
                 "checked on disk after the run. Files not processed (images, scans, files the "
                 f"tool could not open or clean, system files) are NOT de-identified: copies are in "
                 f"{PILE_NOT}/, must stay on this computer and must not be shared. "
                 f"{KEY_FILE}, in the same folder, maps each file id to its original path."),
    }


def _documents_section(stats: RunStats) -> dict:
    docs = stats.doc_summaries or []
    if not docs:
        return {}
    hidden: dict[str, int] = {}
    removed: dict[str, int] = {}
    sweep = {"parts_searched": 0, "strings_searched": 0, "found": 0}
    by_kind: dict[str, int] = {}
    for d in docs:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
        for k, v in d["hidden_content_found"].items():
            hidden[k] = hidden.get(k, 0) + v
        for k, v in d["removed_by_type"].items():
            removed[k] = removed.get(k, 0) + v
        for k in sweep:
            sweep[k] += (d.get("sweep") or {}).get(k, 0)
    return {"files": len(docs), "by_kind": by_kind,
            "hidden_content_found": dict(sorted(hidden.items())),
            "removed": dict(sorted(removed.items())), "sweep": sweep,
            "note": ("Where present in this run's files: every part of each document was read, "
                     "not only the visible text. Tracked "
                     "changes were accepted (deleted words are gone), and comments, hidden text, "
                     "images, embedded files, macros, links, properties and custom data were "
                     "removed. Hidden sheets, rows and columns were cleaned and shown. PDFs were "
                     "flattened to page images and re-checked, so removed text is not left in the file's text layer. Each clean "
                     "copy was then searched, part by part, for every string removed and for the "
                     "names in its hidden details.")}


def _when(utc: str | None) -> str:
    """One time format everywhere: 2026-10-19 04:26:34 UTC."""
    return (utc or "").replace("T", " ").replace("Z", " UTC")


def _profile_fact(report: dict) -> str:
    d = report["deidentification"]
    if d.get("output_is_still_phi"):
        return f"{d['profile']} (Limited Data Set: the output remains PHI; a data use agreement is required)"
    if report["date_shifting"].get("applied"):
        return (f"{d['profile']} with date shifting (shifted dates keep a day and month, so this output is "
                "**not** Safe Harbor de-identified, whoever reviews it)")
    return f"{d['profile']} ({d['citation']})"


AUDIT_NAME = "Audit trail"
AUDIT_STATEMENT = [
    "A machine-written record of one run. It records facts only, is designed to hold no patient",
    "information, and is not a certification.",
]


def write_audit(report: dict, path: Path) -> None:
    """4_Audit_trail.pdf (inkguard/cli_docs.py)."""
    from inkguard import cli_docs
    cli_docs.audit_pdf(report, path)
