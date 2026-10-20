"""
INTERVAL-PRESERVING DATE SHIFTING.

THE RISK THIS REDUCES
---------------------
Re-identification does not need a name. An admission date is widely
knowable — a neighbour saw the ambulance, a post said "in hospital
today", an employer holds the leave record, an obituary gives a date of
death. Given a dataset containing an admission on 14 March 2024 for a
62-year-old woman in Boston, anyone holding one of those facts may find
the record.

A date is a quasi-identifier: harmless alone, identifying in
combination. A Limited Data Set under 45 CFR 164.514(e) retains full
dates AND full ZIP by design, so this is exactly the category where the
exposure is largest.

THE MECHANISM
-------------
Each patient receives a secret offset drawn from their own key, and
EVERY date in their record moves by that same offset.

    admitted    14 Mar 2024  ->  29 Oct 2023
    discharged  19 Mar 2024  ->   3 Nov 2023
    interval         5 days  ->      5 days

Absolute dates are destroyed; every interval survives exactly. Length of
stay, time to readmission, treatment gaps and age at encounter are all
preserved, because each is a difference between two dates that moved
together.

WHY PER-PATIENT, AND WHY IT IS THE WHOLE SECURITY PROPERTY
----------------------------------------------------------
A single global offset is worthless. Recover it from one patient whose
real admission date is known, and the entire corpus unshifts. With a
distinct offset per patient, breaking one patient reveals nothing about
any other — the compromise does not propagate.

The offset is derived by HMAC from the root key and the patient
identifier, which gives three properties at once: it is deterministic,
so the same patient shifts identically across every note and every run
and intervals hold ACROSS documents, not merely within one; it is
unpredictable without the key; and it is stored nowhere, so there is no
offset table to leak. Re-deriving it requires the root key, which is
what makes the shift reversible by the data owner and by nobody else.

WHAT IT COSTS — STATED PLAINLY
------------------------------
Seasonality is destroyed: influenza no longer falls in winter.
Cross-patient temporal alignment is destroyed: an outbreak cluster
cannot be reconstructed, because two patients admitted the same day
receive different offsets. For most clinical research that is an
acceptable price; for epidemiology it is disqualifying. Hence a switch,
never a default.

WHAT MUST NOT MOVE
------------------
Relative expressions ("3 days ago", "post-op day 2") are already
non-identifying and remain correct only if left alone — shifting them
would be meaningless, and rewriting them would break the note. Vitals
and ratios ("BP 120/80", "I/O 1200/800") are shaped like dates and are
not dates; moving one is a patient-safety defect, not a privacy gain.
Both are excluded explicitly rather than by hoping the date regex is
tight enough.

SAFE HARBOR
-----------
Safe Harbor keeps no date element finer than the year. A shifted date is
still a full date, and it is derived from the real one, so shifted output
is for the Limited Data Set profile or an Expert Determination, not for
Safe Harbor. The code does not yet enforce this: with PHI_DATE_SHIFT=true
a Safe Harbor run keeps full shifted dates.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from inkguard import clinical_context, hipaa
from inkguard.security import KeyStore

# Default window. +/- 365 days is the common convention in research
# de-identification: wide enough that the true date is not recoverable
# by guessing, narrow enough that a patient's care episode stays in a
# plausible era for chart review.
DEFAULT_MAX_OFFSET_DAYS = 365

_MONTHS = ["January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December"]
_MONTH_INDEX = {m[:3].lower(): i for i, m in enumerate(_MONTHS, start=1)}


# =====================================================================
# DATE PATTERNS — each carries the knowledge of how to rebuild itself
# so the output keeps the shape the clinician wrote.
# =====================================================================

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("iso",        re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")),
    ("us_slash",   re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")),
    ("us_dash",    re.compile(r"\b(\d{1,2})-(\d{1,2})-(\d{2,4})\b")),
    ("us_dot",     re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b")),
    ("month_dmy",  re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})\b")),
    ("month_mdy",  re.compile(
        r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(,?)\s+(\d{4})\b")),
]


@dataclass
class ShiftReport:
    shifted: int = 0
    skipped_protected: int = 0
    unparseable: int = 0
    offset_applied: bool = False
    factors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "dates_shifted": self.shifted,
            "protected_spans_skipped": self.skipped_protected,
            "unparseable_dates": self.unparseable,
            "offset_applied": self.offset_applied,
            # The offset itself is NEVER reported. Publishing it would
            # hand back exactly the secret the shift depends on.
            "note": ("Intervals between dates are preserved exactly. "
                     "Absolute dates are not recoverable without the "
                     "root key."),
        }


# =====================================================================
# OFFSET DERIVATION
# =====================================================================

_shared_keystore: KeyStore | None = None


class EphemeralKeyError(RuntimeError):
    """Raised when date shifting is attempted without a persistent root key."""


def _get_keystore() -> KeyStore:
    """One KeyStore per process, and it must NOT be ephemeral.

    Two requirements, both needed for intervals to hold across files.

    First, one KeyStore for the process: constructing KeyStore() per call
    would draw a new random root key on every call when PHI_ROOT_KEY is
    unset, so every date in a set of files would get its own offset.
    Intervals would hold inside a single date pair and break everywhere
    else, invisibly in any one file.

    Second, a persistent key: even one keystore per process is not
    enough. An ephemeral key changes on restart, so files processed in two
    sessions would receive two different offset families. Every
    cross-batch interval would then be wrong, and nothing in the output
    would reveal it — the dates still look like plausible dates.

    Interval integrity is the entire promise of date shifting, so an
    ephemeral key is refused rather than warned about.
    """
    global _shared_keystore
    if _shared_keystore is None:
        ks = KeyStore()
        if getattr(ks, "ephemeral", False):
            raise EphemeralKeyError(
                "Date shifting requires a persistent PHI_ROOT_KEY.\n"
                "Without one, offsets change every run and intervals "
                "between dates processed in different batches become "
                "silently wrong.\n\n"
                "Set PHI_ROOT_KEY to 64 random hexadecimal characters (for "
                "example, the output of: python -c \"import secrets; "
                "print(secrets.token_hex(32))\") and keep it safe — it is "
                "the only thing that can reverse the shift."
            )
        _shared_keystore = ks
    return _shared_keystore


def derive_offset(patient_key: str,
                  max_offset_days: int = DEFAULT_MAX_OFFSET_DAYS,
                  keystore: KeyStore | None = None) -> int:
    """Deterministic, key-dependent offset in [-max, +max], excluding 0.

    Zero is excluded because an unshifted record is indistinguishable
    from one that was never processed, and "did this patient get
    shifted?" must never depend on luck.
    """
    ks = keystore or _get_keystore()
    mac = hmac.new(ks.derive("date-shift-v1"),
                   patient_key.encode(), hashlib.sha256).digest()
    span = max_offset_days * 2                      # [-max, +max)
    raw = int.from_bytes(mac[:8], "big") % span
    offset = raw - max_offset_days
    if offset == 0:
        offset = max_offset_days                    # never a no-op
    return offset


# =====================================================================
# PARSING AND REBUILDING
# =====================================================================

def _expand_year(y: int) -> int:
    if y >= 100:
        return y
    return 2000 + y if y <= 25 else 1900 + y


def _parse(kind: str, m: re.Match) -> tuple[date, dict] | None:
    """Return (date, metadata-for-rebuild) or None if not a real date."""
    try:
        if kind == "iso":
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return date(y, mo, d), {}
        if kind in ("us_slash", "us_dash", "us_dot"):
            mo, d, yy = int(m.group(1)), int(m.group(2)), m.group(3)
            return date(_expand_year(int(yy)), mo, d), {"year_digits": len(yy)}
        if kind == "month_dmy":
            mon = _MONTH_INDEX.get(m.group(2)[:3].lower())
            if mon is None:
                return None
            return date(int(m.group(3)), mon, int(m.group(1))), \
                {"abbrev": len(m.group(2)) <= 3}
        if kind == "month_mdy":
            mon = _MONTH_INDEX.get(m.group(1)[:3].lower())
            if mon is None:
                return None
            return date(int(m.group(4)), mon, int(m.group(2))), \
                {"abbrev": len(m.group(1)) <= 3, "comma": m.group(3) == ","}
    except ValueError:
        # 02/30/2024, 13/45/1999 — shaped like a date, is not one.
        return None
    return None


def _rebuild(kind: str, d: date, meta: dict) -> str:
    if kind == "iso":
        return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
    if kind in ("us_slash", "us_dash", "us_dot"):
        sep = {"us_slash": "/", "us_dash": "-", "us_dot": "."}[kind]
        year = f"{d.year % 100:02d}" if meta.get("year_digits") == 2 else f"{d.year:04d}"
        return f"{d.month:02d}{sep}{d.day:02d}{sep}{year}"
    name = _MONTHS[d.month - 1]
    if meta.get("abbrev"):
        name = name[:3]
    if kind == "month_dmy":
        return f"{d.day} {name} {d.year}"
    comma = "," if meta.get("comma") else ""
    return f"{name} {d.day}{comma} {d.year}"


# =====================================================================
# PROTECTED SPANS — things shaped like dates that are not dates
# =====================================================================

def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []

    # Vitals, labs and ratios: "BP 120/80", "I/O 1200/800". Shifting one
    # of these is a clinical-safety defect, not a privacy gain.
    for pattern in (clinical_context.VITALS_PATTERN,
                    clinical_context.LAB_PATTERN,
                    clinical_context.VALUE_PATTERN,
                    clinical_context.SIZING_PATTERN):
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end()))

    # Relative time ("3 days ago", "post-op day 2") is already
    # non-identifying and stays correct only if untouched.
    spans.extend(hipaa.protected_time_spans(text))
    return spans


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < e and s < end for s, e in spans)


# =====================================================================
# PUBLIC API
# =====================================================================

def shift_text(text: str, patient_key: str,
               max_offset_days: int = DEFAULT_MAX_OFFSET_DAYS,
               keystore: KeyStore | None = None) -> tuple[str, ShiftReport]:
    """Shift every real date in `text` by this patient's offset.

    `patient_key` must identify the PATIENT, not the document, or dates
    in two notes about the same person would move by different amounts
    and every cross-encounter interval would be destroyed. The caller
    supplies it because only the caller knows which records belong to
    whom; it never leaves this process and is not stored.
    """
    report = ShiftReport()
    if not text or not patient_key:
        return text, report

    offset = timedelta(days=derive_offset(patient_key, max_offset_days, keystore))
    protected = _protected_spans(text)

    # Collect first, rewrite right-to-left, so earlier offsets stay valid.
    edits: list[tuple[int, int, str]] = []
    claimed: list[tuple[int, int]] = []

    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if _overlaps(m.start(), m.end(), protected):
                report.skipped_protected += 1
                continue
            # An earlier (more specific) pattern already took this span.
            if _overlaps(m.start(), m.end(), claimed):
                continue
            parsed = _parse(kind, m)
            if parsed is None:
                report.unparseable += 1
                continue
            d, meta = parsed
            try:
                shifted = d + offset
            except OverflowError:
                report.unparseable += 1
                continue
            edits.append((m.start(), m.end(), _rebuild(kind, shifted, meta)))
            claimed.append((m.start(), m.end()))

    for start, end, replacement in sorted(edits, key=lambda e: -e[0]):
        text = text[:start] + replacement + text[end:]
        report.shifted += 1

    report.offset_applied = report.shifted > 0
    return text, report


def enabled() -> bool:
    """OFF by default.

    Shifting destroys seasonality and cross-patient temporal alignment,
    which is the correct trade for most clinical research and
    disqualifying for epidemiology. A transform that silently changes
    what the data can answer must be chosen, never inherited.
    """
    return os.getenv("PHI_DATE_SHIFT", "false").lower() == "true"


def max_offset() -> int:
    try:
        return max(1, int(os.getenv("PHI_DATE_SHIFT_MAX_DAYS",
                                    str(DEFAULT_MAX_OFFSET_DAYS))))
    except ValueError:
        return DEFAULT_MAX_OFFSET_DAYS
