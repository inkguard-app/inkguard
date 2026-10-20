"""
WELCOME, FINGERPRINT, METHODS TEXT AND SELF-TEST.

Four small things that let the people around a run check it for themselves:

- about()            the welcome page: what the tool is for, what it does,
                     what it does not do, and what is left to the person.
- code_fingerprint() a SHA-256 of the exact code that ran, written into
                     every report, so anyone can confirm later which code
                     made a batch (and that it was not altered).
- methods_text()     a paragraph a researcher can paste into a paper's
                     Methods or an ethics-board protocol, with the version,
                     profile, fingerprint and the measured accuracy. A draft
                     for them to complete: the review they did is theirs to
                     describe.
- self_test()        a one-minute installation check: the engine runs
                     on invented notes with network connections refused,
                     and the known identifiers in them are removed.

Every sentence here follows the same rule as the rest of the tool: say what
is done and how it is checked, never promise more.
"""
from __future__ import annotations

import hashlib
# Imported only to switch connections OFF during the self-test; this file
# never opens one (tests/test_no_network.py checks exactly that).
import socket
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
REPO = "https://github.com/inkguard-app/inkguard"
SITE = "https://inkguard.org"
EMAIL = "inkguard.project@gmail.com"
# The Zenodo DOI printed in every guide, citation file and Methods paragraph.
DOI = "10.5281/zenodo.23464777"
# The standing notice. One wording, shown wherever the tool shows its work
# (tests/test_fluorite.py checks every surface carries it, word for word).
VERIFY_NOTICE = ("Inkguard is an automated tool. It can make mistakes, including misreading "
                 "context. A person must check every output before it is used.")

# The held-out figure for this version (benchmark/RESULTS.md); a test keeps
# it equal to the accuracy table of the desktop app, which is in development.
HELD_OUT = "90.5%"


def code_fingerprint() -> str:
    """SHA-256 over every file of the engine and command line (path and
    contents, in a fixed order). Compiled caches are left out, so the same
    code gives the same fingerprint on every computer."""
    h = hashlib.sha256()
    for f in sorted(p for p in APP_DIR.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"):
        rel = f.relative_to(APP_DIR).as_posix()
        h.update(rel.encode("utf-8") + b"\0")
        h.update(f.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _version() -> str:
    from inkguard import __version__
    return __version__


def about() -> str:
    v = _version()
    return f"""
  Inkguard {v}

  {VERIFY_NOTICE}

  Offline redaction of patient identifiers from typed clinical documents.

  WHAT IT DOES
    - Redacts copies of your files on this computer and sorts every file into
      one of three folders: REDACTED-NO-FLAGS, REDACTED-FLAGGED (with the words
      to check and their lines) and NOT-PROCESSED-MAY-CONTAIN-PHI.
    - Reads each patient's notes together; looks for the identifiers the HIPAA
      Safe Harbor method lists, including in document properties, comments,
      tracked changes, hidden sheets and file names.
    - Writes a guide, an audit trail, citations and a feedback report.

  WHAT IT DOES NOT DO
    - It finds most identifiers, not all: {HELD_OUT} of them on the held-out half
      of its messy-notes benchmark (invented notes), before human review. Scores
      differ on other test sets (70.3% on general, non-clinical sentences; see
      benchmark/RESULTS.md). Not yet validated on real records.
    - It does not read photos or scans; those are copied, unread.
    - It is not legal advice, not a certification, not a medical device.

  START
    inkguard-window                                   the app
    inkguard YOUR_FOLDER -o RESULTS_FOLDER --dry-run   look first
    inkguard YOUR_FOLDER -o RESULTS_FOLDER             then run
    inkguard --self-test                               one-minute check

  Code fingerprint  {code_fingerprint()[:16]}…
  Feedback          {EMAIL}  (never send patient information)
  Licence           Apache License 2.0. LICENSE, NOTICE, THIRD_PARTY_NOTICES.md and
                    TRADEMARKS.md come with the software. Source: {REPO}
"""


def methods_text(*, version: str, profile: str, date_shifted: bool, fingerprint: str,
                 run_id: str = "") -> str:
    """A draft Methods paragraph. The bracketed parts are for the research
    team to complete; the software cannot know how they reviewed, and it
    never claims to have de-identified anything on its own."""
    if profile == "limited_data_set":
        return (
            f"Clinical documents were processed locally on the study team's computers with Inkguard "
            f"version {version} (Wajahat, 2026) to prepare a limited data set (45 CFR 164.514(e)), which "
            f"keeps dates and towns; the data remain protected health information and were disclosed only "
            f"under [data use agreement: parties and date]. The software redacted the other identifiers it "
            f"detected and listed the words it was unsure about for human review (code fingerprint, "
            f"SHA-256, {fingerprint}{'; run ' + run_id if run_id else ''}). All outputs were then reviewed "
            f"by [number and role of reviewers]. [IRB or ethics board, and protocol number.]"
        )
    elif date_shifted:
        method = ("its Safe Harbor profile with per-patient date shifting; because shifted dates keep a "
                  "day and month, the output does not meet the Safe Harbor method as written [state the "
                  "basis your institution used]")
    else:
        method = ("its Safe Harbor profile, which targets the identifier categories listed in 45 CFR "
                  "164.514(b)(2)(i)")
    run = f"; run {run_id}" if run_id else ""
    return (
        f"Clinical documents were processed locally on the study team's computers with Inkguard "
        f"version {version} (Wajahat, 2026) to support de-identification, using {method}. The software "
        f"read each patient's documents together, redacted the identifiers it detected, including those "
        f"in document metadata, comments and tracked changes, listed the words it was unsure about for "
        f"human review, and copied files it could not read (e.g., scans) unprocessed (code fingerprint, "
        f"SHA-256, {fingerprint}{run}). In developer-reported testing before human review, this version "
        f"removed {HELD_OUT} of Safe Harbor identifiers on a held-out set of synthetic notes; it has not "
        f"been validated on real clinical records. All outputs were then reviewed by [number and role of "
        f"reviewers], and unprocessed files were [handled how]. [Who made the final de-identification "
        f"determination, including that no actual knowledge of residual identifiability remained, and how "
        f"disagreements were resolved.] [IRB or ethics board, and protocol number.]"
    )


class _NoNetwork:
    """Every attempt to open a connection fails while this is active."""

    attempts = 0

    def __enter__(self):
        self._saved = (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
                       socket.getaddrinfo)

        def refuse(*a, **k):
            self.attempts += 1
            raise OSError("network refused by the Inkguard self-test")
        socket.socket.connect = refuse
        socket.socket.connect_ex = refuse
        socket.create_connection = refuse
        socket.getaddrinfo = refuse
        return self

    def __exit__(self, *exc):
        (socket.socket.connect, socket.socket.connect_ex, socket.create_connection,
         socket.getaddrinfo) = self._saved
        return False


_SAMPLE = ("Patient: Rosalind Ferraro   MRN 0071-4482   DOB 11/02/1979\n"
           "Seen by Dr. Kwame Osei. Phone (218) 555-0176. Email r.ferraro@example.org.\n"
           "Lives at 77 Tamarind Court, Duluth MN 55805. SSN 123-45-6789.\n"
           "A1c 7.4%, BP 132/84. Continue metformin 1000 mg BID.\n")
_MUST_GO = ("Rosalind", "Ferraro", "0071-4482", "11/02/1979", "Kwame", "Osei", "555-0176",
            "r.ferraro@example.org", "77 Tamarind", "55805", "123-45-6789")
_MUST_STAY = ("A1c 7.4%", "BP 132/84", "metformin 1000 mg BID")


def self_test() -> tuple[bool, list[str]]:
    """Returns (passed, lines to print). Uses only invented text."""
    lines: list[str] = []
    ok = True

    def check(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        lines.append(f"  {'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))

    lines.append(f"Inkguard {_version()} self-test (invented notes only; nothing is written)")
    fp = code_fingerprint()
    check("code fingerprint computed", len(fp) == 64, fp[:16] + "…")
    try:
        with _NoNetwork() as guard:
            from inkguard import contract
            r = contract.deidentify(_SAMPLE, mode="mask")
        check("engine ran with every network connection refused, and tried none",
              guard.attempts == 0, f"{guard.attempts} attempts")
    except Exception as exc:  # noqa: BLE001
        check("engine ran with every network connection refused", False, type(exc).__name__)
        return False, lines
    left = [w for w in _MUST_GO if w in r.clean_text]
    check("known identifiers removed", not left, f"{len(_MUST_GO) - len(left)} of {len(_MUST_GO)}")
    kept = [w for w in _MUST_STAY if w in r.clean_text]
    check("clinical content kept", len(kept) == len(_MUST_STAY), f"{len(kept)} of {len(_MUST_STAY)}")
    lines.append("")
    lines.append("RESULT: PASS. This computer runs the engine offline as designed." if ok else
                 f"RESULT: FAIL. Email {EMAIL} with these lines (they hold no patient information).")
    lines.append("This checks the installation, not accuracy on your notes.")
    lines.append(VERIFY_NOTICE)
    return ok, lines
