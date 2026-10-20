"""
quartz — map a note's sections before any detector runs.

WHY THIS EXISTS
---------------
A clinical note is not one uniform text. It is several typed sections
with different content, and identifiers are not evenly distributed among
them:

  IDENTITY        demographics block — names and identifiers are dense
  NARRATIVE       HPI, social and family history — names in prose,
                  including relatives
  SIGNATURE       attribution — provider names are dense
  CLINICAL_LIST   medications, allergies, diagnoses — drug and
                  condition vocabulary is dense, names are rare
  PROCEDURE       operative description — device, suture and technique
                  vocabulary is dense, names are rare
  MEASUREMENT     vitals, exam, labs — values; names very rare
  ASSESSMENT_PLAN clinical reasoning — names rare, and usually titled

Running every detector at the same intensity across all of these
removed words such as "Toradol", "Foley", "Monocryl" and "Roux": a name
detector strict enough to catch "Mcintyre" in a demographics line is
strict enough to remove a drug name in a medication list. The fix is
not a weaker detector. It is running the same detector at different
intensities in different sections.

WHAT THIS MODULE DOES NOT DO
----------------------------
It never removes or suppresses detection. It only produces a MAP. Every
decision it influences is made downstream and recorded with its reason.
In particular, nothing here is allowed to switch off the structural
name layers (labels, titles, frames) in any section: a titled name
can appear anywhere — "assisted by Dr. Ramanathan" sits in a procedure
section — and those layers must run everywhere, on every note.

COST
----
A few regular-expression passes and a sort. Negligible against a single
NER call, which is the point: the profile has to be cheap enough to run
on everything, because its job is to decide what else is worth running.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

IDENTITY = "IDENTITY"
NARRATIVE = "NARRATIVE"
SIGNATURE = "SIGNATURE"
CLINICAL_LIST = "CLINICAL_LIST"
PROCEDURE = "PROCEDURE"
MEASUREMENT = "MEASUREMENT"
ASSESSMENT_PLAN = "ASSESSMENT_PLAN"
UNKNOWN = "UNKNOWN"

# Sections where people's names are expected. UNKNOWN is here on
# purpose: when the map is missing, the safe assumption is that names may
# be present. Failing to recognise a section must never make detection
# weaker.
NAME_BEARING = frozenset({IDENTITY, NARRATIVE, SIGNATURE, UNKNOWN})
NAME_SPARSE = frozenset({CLINICAL_LIST, PROCEDURE, MEASUREMENT, ASSESSMENT_PLAN})


# =====================================================================
# HEADER RECOGNITION — the labels that open a section
# =====================================================================

# ALL-CAPS header followed by a colon, at the start of the text, a line,
# or after the ",", "." or ";" that exported datasets substitute for
# newlines (MTSamples writes "PROCEDURE:,  Laparoscopic ...").
_CAPS_HEADER = re.compile(
    r"(?:^|(?<=[\n,.;]))[ \t]*"
    r"(?P<label>[A-Z][A-Z0-9 /&()'\-]{1,48}[A-Z)])"
    r"[ \t]*:"
)
# Title-case header at the start of a line: "Medications:", "Assessment
# and Plan:". Line-start only — mid-sentence "Note: ..." is prose.
_TITLE_HEADER = re.compile(
    r"(?m)^[ \t]*(?P<label>[A-Z][a-z]+(?:[ /&][A-Za-z][a-z]*){0,4})[ \t]*:"
)

# Title-case header INLINE, after sentence punctuation: "...no pain.
# Medications: Toradol 30 mg". This is what a note looks like when a
# clinician pastes the whole structured note into one free-text field.
# Only accepted if the label names a KNOWN section kind — an inline
# "Note:" or "Plan B:" is prose, and guessing otherwise would invent
# structure that is not there.
_INLINE_TITLE_HEADER = re.compile(
    r"(?<=[.;!?])[ \t]+(?P<label>[A-Z][a-z]+(?:[ /&][A-Za-z][a-z]*){0,4})[ \t]*:"
)

# Lazy SOAP: single-letter headers. Individually useless — "P: 72" is a
# pulse, "A: yes" is an answer — so they are accepted only by QUORUM:
# at least three of S, O, A, P present, in that order, in one document.
# A lone letter proves nothing; the set together is unmistakable.
# "(?![ \t]*\d)": a letter-colon followed by a NUMBER is a measurement,
# not a header. "P: 72" is a pulse; without this guard the quorum logic
# accepted it as the Plan header and then skipped the real "A:" and "P:"
# that followed, because they no longer fitted the S-O-A-P order.
_SOAP_LETTER = re.compile(
    r"(?:^|(?<=[\s.,;]))(?P<label>[SOAP])[ \t]*:(?![ \t]*[\d.])")
_SOAP_KIND = {"S": None, "O": None, "A": None, "P": None}  # replaced below

# Keyword -> section kind. Longest matching keyword wins, so "past medical
# history" is a CLINICAL_LIST even though it contains "history", and
# "family history" is NARRATIVE even though it contains "history".
_KEYWORDS: dict[str, str] = {
    # identity
    "patient": IDENTITY, "patient name": IDENTITY, "pt name": IDENTITY,
    "name": IDENTITY, "dob": IDENTITY, "date of birth": IDENTITY,
    "mrn": IDENTITY, "medical record": IDENTITY, "ssn": IDENTITY,
    "social security": IDENTITY, "address": IDENTITY, "phone": IDENTITY,
    "telephone": IDENTITY, "email": IDENTITY, "e-mail": IDENTITY,
    "sex": IDENTITY, "gender": IDENTITY, "account": IDENTITY,
    "member id": IDENTITY, "insurance": IDENTITY, "policy": IDENTITY,
    "emergency contact": IDENTITY, "next of kin": IDENTITY,
    "guardian": IDENTITY, "parent": IDENTITY, "demographics": IDENTITY,
    # "Identifying data:" was once read as "data" and treated as a lab
    # section, where names are rare.
    "identifying data": IDENTITY, "identifying information": IDENTITY,
    "identification": IDENTITY, "id data": IDENTITY, "employee": IDENTITY,
    "employer": IDENTITY, "guarantor": IDENTITY, "subscriber": IDENTITY,
    "caregiver": IDENTITY, "point of contact": IDENTITY, "contact": IDENTITY,
    # narrative
    "hpi": NARRATIVE, "history of present illness": NARRATIVE,
    "present illness": NARRATIVE, "chief complaint": NARRATIVE,
    "cc": NARRATIVE, "subjective": NARRATIVE, "interval history": NARRATIVE,
    "hospital course": NARRATIVE, "social history": NARRATIVE,
    "family history": NARRATIVE, "reason for visit": NARRATIVE,
    "reason for consultation": NARRATIVE, "reason for referral": NARRATIVE,
    "history": NARRATIVE, "narrative": NARRATIVE, "clinical history": NARRATIVE,
    # Where family, work and home are described for context — dense with
    # relatives' names, employers and addresses.
    "social": NARRATIVE, "social hx": NARRATIVE, "sh": NARRATIVE,
    "living situation": NARRATIVE, "support system": NARRATIVE,
    "occupation": NARRATIVE, "family": NARRATIVE, "fh": NARRATIVE,
    # clinical lists
    "medication": CLINICAL_LIST, "medications": CLINICAL_LIST,
    "meds": CLINICAL_LIST, "current medications": CLINICAL_LIST,
    "allergies": CLINICAL_LIST, "allergy": CLINICAL_LIST,
    "past medical history": CLINICAL_LIST, "pmh": CLINICAL_LIST,
    "past surgical history": CLINICAL_LIST, "psh": CLINICAL_LIST,
    "problem list": CLINICAL_LIST, "problems": CLINICAL_LIST,
    "diagnosis": CLINICAL_LIST, "diagnoses": CLINICAL_LIST,
    "preoperative diagnosis": CLINICAL_LIST,
    "postoperative diagnosis": CLINICAL_LIST,
    "pre-op diagnosis": CLINICAL_LIST, "post-op diagnosis": CLINICAL_LIST,
    "indication": CLINICAL_LIST, "indications": CLINICAL_LIST,
    "immunizations": CLINICAL_LIST, "discharge medications": CLINICAL_LIST,
    # procedure
    "procedure": PROCEDURE, "procedures": PROCEDURE,
    "operation": PROCEDURE, "operations": PROCEDURE,
    "description of procedure": PROCEDURE, "procedure in detail": PROCEDURE,
    "technique": PROCEDURE, "findings": PROCEDURE,
    "operative findings": PROCEDURE, "anesthesia": PROCEDURE,
    "estimated blood loss": PROCEDURE, "ebl": PROCEDURE,
    "complications": PROCEDURE, "specimen": PROCEDURE,
    "specimens": PROCEDURE, "drains": PROCEDURE, "implants": PROCEDURE,
    "fluids": PROCEDURE, "tourniquet": PROCEDURE,
    "instrumentation": PROCEDURE, "approach": PROCEDURE,
    "operative note": PROCEDURE, "procedure performed": PROCEDURE,
    # measurement
    "vitals": MEASUREMENT, "vital signs": MEASUREMENT,
    "physical exam": MEASUREMENT, "physical examination": MEASUREMENT,
    "exam": MEASUREMENT, "examination": MEASUREMENT, "pe": MEASUREMENT,
    "labs": MEASUREMENT, "laboratory": MEASUREMENT,
    "laboratory data": MEASUREMENT, "data": MEASUREMENT,
    "results": MEASUREMENT, "imaging": MEASUREMENT,
    "radiology": MEASUREMENT, "ros": MEASUREMENT,
    "review of systems": MEASUREMENT, "heent": MEASUREMENT,
    "neck": MEASUREMENT, "lungs": MEASUREMENT, "heart": MEASUREMENT,
    "abdomen": MEASUREMENT, "extremities": MEASUREMENT,
    "neurologic": MEASUREMENT, "neuro": MEASUREMENT, "skin": MEASUREMENT,
    "general": MEASUREMENT, "cardiovascular": MEASUREMENT,
    "respiratory": MEASUREMENT, "gastrointestinal": MEASUREMENT,
    "genitourinary": MEASUREMENT, "musculoskeletal": MEASUREMENT,
    "psychiatric": MEASUREMENT, "ekg": MEASUREMENT, "ecg": MEASUREMENT,
    # assessment / plan
    "assessment": ASSESSMENT_PLAN, "impression": ASSESSMENT_PLAN,
    "plan": ASSESSMENT_PLAN, "assessment and plan": ASSESSMENT_PLAN,
    "a/p": ASSESSMENT_PLAN, "recommendation": ASSESSMENT_PLAN,
    "recommendations": ASSESSMENT_PLAN, "disposition": ASSESSMENT_PLAN,
    "discussion": ASSESSMENT_PLAN, "follow-up": ASSESSMENT_PLAN,
    "follow up": ASSESSMENT_PLAN, "instructions": ASSESSMENT_PLAN,
    "discharge instructions": ASSESSMENT_PLAN, "summary": ASSESSMENT_PLAN,
    "conclusion": ASSESSMENT_PLAN,
    # signature / attribution
    "signed": SIGNATURE, "signature": SIGNATURE, "dictated by": SIGNATURE,
    "dictated": SIGNATURE, "transcribed": SIGNATURE,
    "transcribed by": SIGNATURE, "electronically signed": SIGNATURE,
    "electronically signed by": SIGNATURE, "attending": SIGNATURE,
    "surgeon": SIGNATURE, "assistant": SIGNATURE, "assistants": SIGNATURE,
    "provider": SIGNATURE, "physician": SIGNATURE,
    "referring physician": SIGNATURE, "primary care": SIGNATURE,
    "pcp": SIGNATURE, "attn": SIGNATURE, "copy to": SIGNATURE,
    # Sign-on / sign-off trail between rotating providers.
    "cosigned": SIGNATURE, "co-signed": SIGNATURE, "cosigned by": SIGNATURE,
    "attested": SIGNATURE, "attestation": SIGNATURE, "author": SIGNATURE,
    "authored by": SIGNATURE, "note by": SIGNATURE, "scribe": SIGNATURE,
    "scribed by": SIGNATURE, "sign-out": SIGNATURE,
    "signout": SIGNATURE, "handoff": SIGNATURE, "hand-off": SIGNATURE,
    "resident": SIGNATURE, "fellow": SIGNATURE, "anesthesiologist": SIGNATURE,
    "anesthesia provider": SIGNATURE, "crna": SIGNATURE, "nurse": SIGNATURE,
    "rn": SIGNATURE, "circulator": SIGNATURE, "scrub": SIGNATURE,
    "consultant": SIGNATURE, "consulting physician": SIGNATURE,
    "reviewed by": SIGNATURE, "verified by": SIGNATURE, "entered by": SIGNATURE,
}
_KEYWORDS_BY_LENGTH = sorted(_KEYWORDS, key=len, reverse=True)

_SOAP_KIND = {"S": NARRATIVE, "O": MEASUREMENT,
              "A": ASSESSMENT_PLAN, "P": ASSESSMENT_PLAN}


def _soap_headers(text: str) -> list[tuple[int, int, str]]:
    """Single-letter SOAP headers, accepted only when the set is present.

    Returns [] unless at least three of S/O/A/P occur as headers, in
    order. Order matters: "A: yes ... S: no" is a questionnaire, not a
    SOAP note.
    """
    found = [(m.start("label"), m.end(), m.group("label"))
             for m in _SOAP_LETTER.finditer(text)]
    if len(found) < 3:
        return []
    # Longest in-order subsequence drawn from S, O, A, P.
    order = "SOAP"
    chosen: list[tuple[int, int, str]] = []
    want = 0
    for start, end, letter in found:
        idx = order.find(letter, want)
        if idx != -1:
            chosen.append((start, end, letter))
            want = idx + 1
    return chosen if len(chosen) >= 3 else []


def classify_label(label: str) -> str:
    """Longest keyword contained in the label wins; unknown labels are
    UNKNOWN, which is name-bearing — never the other way round."""
    low = re.sub(r"\s+", " ", label.strip().lower())
    for kw in _KEYWORDS_BY_LENGTH:
        if re.search(rf"(?<![a-z]){re.escape(kw)}(?![a-z])", low):
            return _KEYWORDS[kw]
    return UNKNOWN


# =====================================================================
# SIGNALS read from the whole document
# =====================================================================

# Non-Latin script presence. The multilingual model is expensive and was
# the single largest source of false positives on English notes, so it is
# only run when this signal is on.
from inkguard.talc import NON_LATIN_RE as _NON_LATIN  # shared vocabulary


@dataclass(frozen=True)
class Section:
    kind: str
    start: int          # start of the header label (or 0 for the preamble)
    content_start: int  # first character after the header's colon
    end: int
    # The header label is kept for debugging ONLY and must never be
    # serialized. A line like "JOHN SMITH:" parses as a header, and its
    # label is then a name. The operation log records `kind`, never this.
    label: str = field(default="", repr=False, compare=False)


# Clause boundaries — punctuation only, computed ONCE when the note is
# profiled.
#
# Several components need to know "does this phrase continue, or has it
# ended?" Each used to re-derive the answer with its own regex, and one
# did not check at all: the clinical-guard layer gathered the next two
# words after a surname straight across a sentence break, turned
# "Mcintyre, March 3, 2071. Stage IIIC" (an invented test note) into the
# phrase "Mcintyre March Stage", read that as eponym + qualifier, and
# dropped the patient's name from the redaction list.
#
# A clinical phrase never spans a comma, a full stop, a colon, a
# semicolon, a bracket or a line break. Hyphens and apostrophes do NOT
# break — "Guillain-Barre", "Wilson's disease" are single phrases.
CLAUSE_BREAK = re.compile(r"[.,;:!?()\[\]\n]")


@dataclass
class Profile:
    sections: list[Section]
    non_latin: bool
    sectioned: bool
    breaks: list[int] = field(default_factory=list, repr=False)
    _starts: list[int] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._starts = [s.start for s in self.sections]

    def clause_end(self, pos: int, text_len: int) -> int:
        """Position of the first clause break at or after `pos`."""
        i = bisect.bisect_left(self.breaks, pos)
        return self.breaks[i] if i < len(self.breaks) else text_len

    def kind_at(self, pos: int) -> str:
        if not self.sections:
            return UNKNOWN
        i = bisect.bisect_right(self._starts, pos) - 1
        if i < 0:
            return UNKNOWN
        sec = self.sections[i]
        return sec.kind if pos < sec.end else UNKNOWN

    def section_at(self, pos: int) -> Section | None:
        if not self.sections:
            return None
        i = bisect.bisect_right(self._starts, pos) - 1
        if i < 0:
            return None
        sec = self.sections[i]
        return sec if pos < sec.end else None

    def kinds_present(self) -> list[str]:
        seen: list[str] = []
        for s in self.sections:
            if s.kind not in seen:
                seen.append(s.kind)
        return seen

    def summary(self) -> dict:
        """Serialisable view: kinds and offsets only, never labels."""
        return {
            "sectioned": self.sectioned,
            "non_latin": self.non_latin,
            "sections": [{"kind": s.kind, "start": s.start, "end": s.end}
                         for s in self.sections],
        }


def profile(text: str) -> Profile:
    """Map the document's sections and read its global signals."""
    headers: list[tuple[int, int, str]] = []   # (start, content_start, label)

    def _add(start: int, end: int, label: str) -> None:
        content = end
        # MTSamples-style ":," and any following whitespace belong to the
        # header, not the content.
        while content < len(text) and text[content] in " ,\t":
            content += 1
        headers.append((start, content, label))

    for pattern in (_CAPS_HEADER, _TITLE_HEADER):
        for m in pattern.finditer(text):
            _add(m.start("label"), m.end(), m.group("label"))

    # Inline headers: the whole note pasted into one field. Guarded — only
    # labels that name a known section kind count.
    for m in _INLINE_TITLE_HEADER.finditer(text):
        if classify_label(m.group("label")) != UNKNOWN:
            _add(m.start("label"), m.end(), m.group("label"))

    # Lazy SOAP, by quorum only.
    for start, end, letter in _soap_headers(text):
        _add(start, end, f"SOAP:{letter}")

    # Several patterns can fire on the same header; keep the first per start.
    headers.sort()
    dedup: list[tuple[int, int, str]] = []
    for h in headers:
        if dedup and h[0] < dedup[-1][1]:
            continue
        dedup.append(h)

    sections: list[Section] = []
    if dedup and dedup[0][0] > 0:
        # Text before the first header: unknown provenance, name-bearing.
        sections.append(Section(UNKNOWN, 0, 0, dedup[0][0]))

    for i, (start, content, label) in enumerate(dedup):
        end = dedup[i + 1][0] if i + 1 < len(dedup) else len(text)
        kind = (_SOAP_KIND[label[-1]] if label.startswith("SOAP:")
                else classify_label(label))
        if kind == IDENTITY:
            # A demographics field is one line. Anything after a paragraph
            # break is not part of it, and calling it IDENTITY would give
            # it a name bonus it has not earned.
            brk = text.find("\n\n", content, end)
            if brk != -1:
                sections.append(Section(kind, start, content, brk, label))
                sections.append(Section(UNKNOWN, brk, brk, end))
                continue
        sections.append(Section(kind, start, content, end, label))

    if not sections:
        sections = [Section(UNKNOWN, 0, 0, len(text))]

    recognised = any(s.kind != UNKNOWN for s in sections)
    return Profile(sections=sections,
                   non_latin=bool(_NON_LATIN.search(text)),
                   sectioned=recognised,
                   breaks=[m.start() for m in CLAUSE_BREAK.finditer(text)])


def clause_end_local(text: str, pos: int) -> int:
    """Same rule as Profile.clause_end, for callers without a profile."""
    m = CLAUSE_BREAK.search(text, pos)
    return m.start() if m else len(text)
