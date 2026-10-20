"""The local audit trail: who ran Inkguard, for whom, on which computer and
when.

Every detail is optional and recorded as entered: your name and role, the
principal investigator, the institution, and the IRB or ethics approval.
The software also records what it can see of the computer: its name, the
operating system and the time zone. The computer's name is recorded as the
operating system reports it, and it often contains the owner's name.
Network addresses and the account name are not recorded.

Names are recorded as entered; the software cannot confirm who typed them,
and the audit trail says so. None of this goes into the feedback report.
"""
from __future__ import annotations

import os
import platform
import re
import time

#: (key, command-line flag, environment variable, question, required)
FIELDS = (
    ("operator", "--operator", "INKGUARD_OPERATOR", "Your name", False),
    ("operator_role", "--operator-role", "INKGUARD_OPERATOR_ROLE", "Your role", False),
    ("pi", "--pi", "INKGUARD_PI", "Principal investigator", False),
    ("institution", "--institution", "INKGUARD_INSTITUTION", "Institution", False),
    ("protocol", "--protocol", "INKGUARD_PROTOCOL", "IRB or ethics approval", False),
)
#: What happens, in order. Not shown anywhere in 0.25.0: neither the window
#: nor `inkguard --about` uses this list, LIMITS or steps_text().
STEPS = (
    "Choose the folder with your files, and a new folder for the results.",
    "Optionally, record who is running Inkguard and for whom, in the local audit trail.",
    "Inkguard redacts copies of your files. Your originals are only read.",
    "Every file comes out in one of three folders: REDACTED-NO-FLAGS, REDACTED-FLAGGED or "
    "NOT-PROCESSED-MAY-CONTAIN-PHI.",
    "A person reviews every file before any of it is used or shared.",
)
#: One notice, not a contract: the Apache License 2.0
#: already carries the warranty and liability terms for everyone.
NOTICE = ("Inkguard does not replace review: a person must review every file before it is used or "
          "shared. Free, open-source software, provided as is under the Apache License 2.0, without "
          "warranty or liability.")
#: The limits, stated plainly. Not shown anywhere in 0.25.0 (see STEPS).
LIMITS = (
    "Typed text only. Photos, scans and other formats are copied, unread, to the third folder.",
    "A public preview, not yet validated on real clinical records.",
    "Not a certification, not an Expert Determination, and not legal advice.",
    "Not a medical device. It does not interpret clinical content.",
    "An independent, non-commercial project, not affiliated with any university, medical school or hospital.",
    "It sends nothing anywhere. Never send patient information to the project.",
)


def steps_text() -> str:
    return "\n".join(["HOW IT WORKS", *[f"  {i}. {t}" for i, t in enumerate(STEPS, 1)], "", f"  {NOTICE}"])


NOT_VERIFIED = ("Names are recorded as entered before the run. Inkguard records who was asked and "
                "what was typed, on which computer and when; it cannot confirm a person's identity.")


def add_arguments(p) -> None:
    g = p.add_argument_group("local audit trail (optional; recorded as entered)")
    for key, flag, env, question, required in FIELDS:
        g.add_argument(flag, dest=f"attest_{key}", metavar="TEXT",
                       help=f"{question}. Or set {env}.")


def _clean(v: str | None) -> str:
    v = re.sub(r"\s+", " ", (v or "")).strip()
    return v[:160]


def gather(args, ask=None) -> dict:
    """The local audit trail details, from flags or the environment. All
    optional: what is given is recorded, as entered."""
    out: dict = {}
    for key, flag, env, _question, _required in FIELDS:
        out[key] = _clean(getattr(args, f"attest_{key}", None) or os.environ.get(env))
    out["entered_utc"] = _utc()
    return out


def _utc(t: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t or time.time()))


def _local(t: float | None = None) -> str:
    return time.strftime("%H:%M:%S %Z local", time.localtime(t or time.time()))


def footprint(started: float) -> dict:
    """What the software can see of the computer it ran on."""
    system = platform.system()
    if system == "Darwin":
        system = "macOS " + (platform.mac_ver()[0] or "")
    elif system == "Windows":
        system = "Windows " + platform.release()
    else:
        system = f"{system} {platform.release()}"
    return {
        "computer_name": platform.node(),
        "operating_system": system.strip(),
        "python": platform.python_version(),
        "time_zone": time.strftime("%Z (UTC%z)"),
        "started_utc": _utc(started),
        "started_local": _local(started),
    }


def finish(record: dict) -> dict:
    now = time.time()
    record["finished_utc"], record["finished_local"] = _utc(now), _local(now)
    return record
