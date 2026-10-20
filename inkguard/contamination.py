"""
CROSS-PATIENT CONTAMINATION AND TEMPLATE-RESIDUE DETECTION.

WHY THIS LAYER EXISTS
---------------------
Defensive documentation does not produce sloppy notes; it produces
copy-forwarded ones. A clinician who fears under-documenting carries
the prior encounter's block into today's note, and a clinician working
from a cloned template sometimes carries another PATIENT's block. The
resulting note is meticulous, well-formed, fully templated — and
contains two people.

Checks on identifier SHAPES alone do not notice this: two MRNs in one
note are two well-formed MRNs. Both get redacted, both look correct, and
nothing asks whether they belong to the same person.

WHAT THIS LAYER DOES NOT CLAIM
------------------------------
This is NOT a privacy control. By the time it runs, both MRNs have
already been detected and will both be redacted, so no identifier
escapes because of contamination. The harm it addresses is different
and is not covered anywhere else in the pipeline:

  dataset integrity  A research corpus that silently merges two
                     patients into one record produces conclusions
                     drawn from a person who does not exist.
  clinical safety    Wrong-patient data in a note is a documented
                     source of harm in its own right.
  redaction review   Safe Harbor asks whether identifiers were
                     removed. It does not ask whether the note
                     described one person. A user publishing a
                     redacted corpus still has to answer that.

Because the privacy risk is already handled by redaction, the severity is
DEGRADE rather than BLOCK: the note is flagged, and the user decides.

NORMALIZATION IS THE WHOLE DIFFICULTY
-------------------------------------
Naive value comparison produces false alarms, and a contamination
detector that cries wolf gets switched off. "03/14/1962" and
"1962-03-14" are the same date. "MRN: 4471982" and "4471982" are the
same record. Everything is normalized to a canonical form before any
two values are called different, and identifier classes that
legitimately repeat (provider names, family history dates) are held to
a deliberately weaker rule than the singleton classes.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from inkguard.basalt import Finding, Severity


# =====================================================================
# SINGLETON IDENTIFIER CLASSES
#
# A clinical note describes exactly one patient, so these appear at most
# once per note with one value. Two distinct values means two people, or
# one person recorded under two identities — both of which are defects.
#
# Deliberately EXCLUDED, because multiplicity is normal for them:
#   PERSON        patient + attending + resident + next of kin
#   PHONE_NUMBER  patient, pharmacy, referring office
#   DATE_TIME     every clinical event carries one
#   LOCATION      home address, referring facility
#   NPI           one per provider, several providers per note
# =====================================================================
SINGLETON_ENTITIES: frozenset[str] = frozenset({
    "US_SSN",
})

# MRN IS NOT A SINGLETON, and assuming it was is a domain error worth
# recording because it looked obviously correct.
#
# An MRN is scoped to the issuing hospital system, not to the person. A
# patient who has been seen at three systems legitimately carries three
# MRNs, and a merged health system routinely holds two for the same
# patient. So multiple MRNs in one note is the ordinary signature of a
# patient with history, not evidence that two patients were merged.
# Treating it as contamination would fire constantly on exactly the
# long-history patients whose notes matter most, and a detector that
# cries wolf gets switched off.
#
# Redaction is UNAFFECTED: every MRN is removed regardless of age or
# issuing system. Safe Harbor §164.514(b)(2)(i)(H) lists "medical record
# numbers" with no exception for outdated ones, and an old MRN remains
# directly re-identifying to the system that issued it — that system
# still holds the mapping. Only the contamination FLAG changes here.
#
# What multiple MRNs do signal is quasi-identifier accumulation: a
# patient carrying MRNs from several systems sits in a smaller
# population than one carrying a single MRN, which matters when a
# user assembles a corpus. That is advisory information, not a
# defect in the note.
CROSS_SYSTEM_ENTITIES: frozenset[str] = frozenset({
    "MRN",
    "HEALTH_PLAN_ID",   # likewise payer-scoped: plan changes are normal
})

# DOB is handled separately. It is singleton-ish — one patient, one date
# of birth — but family history legitimately mentions other people's
# ages and years ("mother died 1998"), so it gets a weaker severity and
# a higher threshold than a true singleton.
DOB_ENTITIES: frozenset[str] = frozenset({"DATE_OF_BIRTH", "DOB"})


# =====================================================================
# NORMALIZATION
# =====================================================================

_LABEL_PREFIX = re.compile(
    r"^\s*(?:mrn|medical\s+record(?:\s+(?:number|no\.?))?|chart|record|"
    r"account|patient\s+id|encounter|ssn|social\s+security|"
    r"member\s+id|health\s+plan\s+id|policy(?:\s+(?:number|no\.?))?)"
    r"\s*[:#]?\s*",
    re.IGNORECASE,
)


def normalize_identifier(value: str) -> str:
    """Canonical form for comparing two identifier strings.

    Strips any label the recognizer captured along with the value, drops
    separators, and upper-cases. Without this, "MRN: 4471982" and a bare
    "4471982" compare unequal and the detector reports contamination on
    a note that mentions the same record twice — the exact false alarm
    that would get this layer disabled.
    """
    v = _LABEL_PREFIX.sub("", value or "")
    v = re.sub(r"[^A-Za-z0-9]", "", v)
    return v.upper()


_MONTHS = {
    m: i for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"], start=1)
}


def normalize_date(value: str) -> str | None:
    """Canonical YYYY-MM-DD, or None if the string is not a full date.

    Returning None for partial dates is deliberate: a bare year or a
    month/day with no year cannot be compared safely, and guessing a
    century is how you manufacture a contamination alert out of nothing.
    """
    v = (value or "").strip()

    # ISO: 1962-03-14
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _fmt(y, mo, d)

    # Numeric US: 03/14/1962, 3-14-62, 03.14.1962
    m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})", v)
    if m:
        mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return _fmt(_expand_year(y), mo, d)

    # Month name: March 14, 1962 / Mar 14 1962
    m = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{2,4})", v)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return _fmt(_expand_year(int(m.group(3))),
                    _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))

    # Day first: 14 Mar 1962
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{2,4})", v)
    if m and m.group(2)[:3].lower() in _MONTHS:
        return _fmt(_expand_year(int(m.group(3))),
                    _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))

    return None


def _expand_year(y: int) -> int:
    """Two-digit years: a DOB is in the past, so 62 is 1962, not 2062.
    The cutoff is fixed: 00-25 read as 2000-2025, 26-99 as 1926-1999."""
    if y >= 100:
        return y
    return 2000 + y if y <= 25 else 1900 + y


def _fmt(y: int, mo: int, d: int) -> str | None:
    if not (1 <= mo <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


# =====================================================================
# TEMPLATE RESIDUE
#
# Scaffolding left behind when a template was filled incompletely. On
# its own an unfilled placeholder carries no PHI — it is a blank. It
# matters for two reasons: it is direct evidence that the template
# machinery misfired on this note, which raises the prior that
# something ELSE was left behind too; and a half-deleted section is
# exactly where a prior patient's block survives.
# =====================================================================
TEMPLATE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("epic_wildcard",   re.compile(r"\*{3,}")),
    ("smartlink",       re.compile(r"@[A-Z][A-Z0-9_]{2,30}@")),
    ("square_token",    re.compile(r"\[(?:[A-Z][A-Z\s_/]{2,30})\]")),
    ("angle_token",     re.compile(r"<(?:[A-Za-z][A-Za-z\s_/]{2,30})>")),
    ("curly_token",     re.compile(r"\{(?:[A-Za-z][A-Za-z\s_:/|]{2,40})\}")),
    ("blank_fill",      re.compile(r"_{4,}")),
    ("xxx_placeholder", re.compile(r"\b[Xx]{3,}\b")),
    ("unexpanded_dot",  re.compile(r"(?<![\w.])\.[a-z][a-z0-9]{2,20}\b(?=\s|$)")),
]


def check_template_residue(text: str) -> list[Finding]:
    findings: list[Finding] = []
    total = 0
    samples: list[str] = []
    kinds: list[str] = []

    for kind, pattern in TEMPLATE_PATTERNS:
        matches = pattern.findall(text)
        if matches:
            total += len(matches)
            kinds.append(kind)
            if len(samples) < 3:
                samples.append(kind)   # the kind only: matched text could carry a name

    if total:
        findings.append(Finding(
            code="TEMPLATE_RESIDUE",
            severity=Severity.ADVISE,
            message=(
                f"{total} unfilled template placeholder(s) present "
                f"({', '.join(kinds)}). The note was not fully expanded "
                f"before the file was saved."
            ),
            fix=("Expand or strip template scaffolding before sharing. "
                 "Residue is also a signal that a prior encounter's "
                 "content may have survived in this note — check for "
                 "carried-forward sections."),
            count=total,
            sample=" | ".join(samples),
        ))
    return findings


# =====================================================================
# CONTAMINATION
# =====================================================================

@dataclass
class ContaminationReport:
    findings: list[Finding]
    conflicts: dict[str, list[str]]   # entity type -> distinct normalized values

    @property
    def contaminated(self) -> bool:
        """True only for evidence that the note describes TWO PEOPLE.

        Deliberately NOT "any conflicting identifier". Multiple MRNs are
        a patient with history at several systems, which is ordinary and
        must not raise a contamination alarm — see CROSS_SYSTEM_ENTITIES.
        Only person-scoped identifiers count here.
        """
        return any(k in SINGLETON_ENTITIES for k in self.conflicts)

    @property
    def cross_system(self) -> bool:
        """Patient carries identifiers from more than one system/payer.
        Not a defect; corpus-design metadata."""
        return any(k in CROSS_SYSTEM_ENTITIES for k in self.conflicts)

    def to_dict(self) -> dict:
        return {
            "contaminated": self.contaminated,
            "cross_system_history": self.cross_system,
            "conflicting_identifiers": {
                k: len(v) for k, v in self.conflicts.items()
            },
            "findings": [f.to_dict() for f in self.findings],
        }


def check_contamination(hits) -> ContaminationReport:
    """Detect multiple distinct values for identifier classes that a
    single-patient note can only have one of.

    Takes scanner hits rather than raw text on purpose: the scanner has
    already decided what IS an identifier, and re-deriving that here
    would mean two pieces of code disagreeing about the same span. This
    layer only asks whether the identifiers found are consistent with
    ONE person.
    """
    by_type: dict[str, set[str]] = defaultdict(set)
    raw_by_type: dict[str, dict[str, str]] = defaultdict(dict)

    for h in hits:
        etype = getattr(h, "entity_type", None)
        value = getattr(h, "text", "") or ""
        if etype in SINGLETON_ENTITIES or etype in CROSS_SYSTEM_ENTITIES:
            norm = normalize_identifier(value)
            # Very short normalized values are not trustworthy evidence
            # of identity — a 2-character remnant is noise, not an MRN.
            if len(norm) >= 4:
                by_type[etype].add(norm)
                raw_by_type[etype].setdefault(norm, value)
        elif etype in DOB_ENTITIES:
            norm = normalize_date(value)
            if norm:
                by_type[etype].add(norm)
                raw_by_type[etype].setdefault(norm, value)

    findings: list[Finding] = []
    conflicts: dict[str, list[str]] = {}
    severity = Severity.DEGRADE

    for etype, values in by_type.items():
        if len(values) < 2:
            continue
        conflicts[etype] = sorted(values)

        if etype in CROSS_SYSTEM_ENTITIES:
            # Normal for a patient with history. Reported as corpus
            # metadata, never as a defect.
            findings.append(Finding(
                code=f"CROSS_SYSTEM_{etype}",
                severity=Severity.ADVISE,
                message=(
                    f"{len(values)} distinct {etype} values. An {etype} is "
                    f"scoped to the issuing system, so this normally means "
                    f"the patient has been seen at more than one system "
                    f"(or the system has merged), not that the note "
                    f"describes two people."
                ),
                fix=("No action needed for privacy — every value is "
                     "redacted regardless of age or issuing system. Note "
                     "for corpus design: a patient carrying identifiers "
                     "from several systems sits in a smaller population "
                     "than one carrying a single identifier."),
                count=len(values),
                sample=f"{len(values)} distinct values",
            ))
            continue

        is_dob = etype in DOB_ENTITIES
        findings.append(Finding(
            code=f"CROSS_PATIENT_{etype}",
            severity=Severity.ADVISE if is_dob else severity,
            message=(
                f"{len(values)} distinct {etype} values in one note. "
                + ("An SSN is scoped to the PERSON, not to a hospital "
                   "system, so more than one is not explained by a "
                   "patient having history at several institutions. Two "
                   "SSNs means two people."
                   if not is_dob else
                   "This may be legitimate (family history) or may "
                   "indicate carried-forward content.")
            ),
            fix=("Review the note for copy-forwarded sections before "
                 "including it in a redacted corpus. Every value "
                 "has been redacted, so no identifier is exposed; the "
                 "risk here is that the record describes two people."),
            count=len(values),
            # Never echo the identifier itself — a diagnostic that
            # prints PHI back to the caller defeats the tool.
            sample=f"{len(values)} distinct values",
        ))

    return ContaminationReport(findings=findings, conflicts=conflicts)


def analyze(text: str, hits) -> ContaminationReport:
    """Full structural pass: contamination plus template residue."""
    report = check_contamination(hits)
    report.findings.extend(check_template_residue(text))
    return report
