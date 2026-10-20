"""
FINAL INSPECTION — nothing leaves until it has been checked on the way
out.

WHY NOT JUST RE-RUN DETECTION
-----------------------------
Scanning the output with the same layers would pass the same things for
the same reasons. A verifier that shares the detector's blind spots is
not a second layer, it is the first layer twice. So this module checks
INVARIANTS — properties any correct output must have regardless of how
detection went — and a few high-precision patterns whose meaning does
not depend on context.

  1. NO CONFIRMED NAME SURVIVES.
     Every name the engine confirmed with strong evidence must be absent
     from the output. This catches PIPELINE losses rather than detection
     gaps. For example, a name confirmed by its label ("渡辺 淳") and then
     dropped when a higher-scoring MRN hit replaced the span covering it
     is invisible to every detection layer; an invariant on the output
     sees it.

  2. NO STRUCTURED IDENTIFIER SURVIVES.
     SSN (structurally validated), email, phone, labelled MRN — and,
     under Safe Harbor only, dates. These patterns are precise enough
     that a match in redacted output is a real finding. Clinical
     measurements are protected first, so a lab value is not mistaken
     for a phone number and does not quarantine a clean file.

  3. GRADE.
     A — no finding. B — uncertain tokens were masked, or linkage caution.
     C — linkage review. (The code also means to give C when clinical
     integrity is degraded, but it reads an `issues` attribute that
     clinical_context.IntegrityReport does not have, so at present that
     branch never fires.)
     F — a residual identifier: QUARANTINED.

Quarantine is fail-closed at the exit while fail_closed is on (the
default): policy then blocks a file that fails, so its redacted text is
not written out with the identifier in it.

Findings report counts and categories only — never the leaked value. A
diagnostic that prints the identifier it caught is itself a leak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from inkguard import clinical_context
from inkguard.garnet import NEVER_NAMES, TITLES, clinically_bound, _zipf

_SSN = re.compile(r"(?<!\d)(\d{3})[-.\s]?(\d{2})[-.\s]?(\d{4})(?!\d)")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_PHONE = re.compile(
    r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
    r"|(?<!\d)\+\d{1,3}(?:[-.\s]\d{1,5}){2,4}(?!\d)")
_MRN = re.compile(r"\b(?:MRN|Medical Record (?:Number|No\.?))\s*[:#]?\s*[A-Z0-9]{4,12}\b",
                  re.IGNORECASE)
_DATE = re.compile(
    # A run of four or more numbers ("1-3-6-12 day rule") is not a date.
    r"(?<![\d/.\-])(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}-\d{2}-\d{2})(?![\d/\-]|\.\d)"
    r"|\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\.?\s+\d{1,2},?\s+\d{4}\b")
_SENTENCE_START = re.compile(r"(?:^|[.!?\n]\s*|:\s*)$")
from inkguard.talc import CASELESS_RE as _CASELESS  # shared vocabulary


def _valid_ssn(a: str, g: str, s: str) -> bool:
    a_, g_, s_ = int(a), int(g), int(s)
    return not (a_ == 0 or a_ == 666 or a_ >= 900 or g_ == 0 or s_ == 0)


def _protected(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for pat in (clinical_context.VITALS_PATTERN, clinical_context.LAB_PATTERN,
                clinical_context.VALUE_PATTERN, clinical_context.SIZING_PATTERN):
        spans += [(m.start(), m.end()) for m in pat.finditer(text)]
    return spans


def _inside(s: int, e: int, spans) -> bool:
    return any(s < pe and ps < e for ps, pe in spans)


@dataclass
class ExitReport:
    grade: str = "A"
    quarantined: bool = False
    residual_names: int = 0
    residual_structured: dict = field(default_factory=dict)
    uncertain_masked: int = 0
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "quarantined": self.quarantined,
            "residual_confirmed_names": self.residual_names,
            "residual_structured_identifiers": dict(self.residual_structured),
            "uncertain_tokens_masked": self.uncertain_masked,
            "reasons": list(self.reasons),
        }


def _name_targets(names: set[str]) -> set[str]:
    out: set[str] = set()
    from inkguard.garnet import plausible_name
    for n in names:
        n = n.strip()
        if not n or n.lower() in NEVER_NAMES or not plausible_name(n):
            continue
        out.add(n)
        for part in n.split():
            low = part.lower().strip(".")
            if low in NEVER_NAMES or low in TITLES:
                continue
            from inkguard.garnet import not_a_name_part
            if not_a_name_part(part):
                continue
            if _CASELESS.search(part):
                if len(part) >= 2:
                    out.add(part)
            elif len(part) >= 3 and part[:1].isupper():
                out.add(part)
    return out


def _kind_word(t: str) -> bool:
    from inkguard.jasper import _WORKPLACE_KIND, _FACILITY_KIND
    return bool(re.fullmatch(rf"(?:{_WORKPLACE_KIND}|{_FACILITY_KIND})", t))


def residual_names(clean: str, confirmed: set[str]) -> int:
    """Count surviving occurrences of confirmed names, guarded the same way
    as propagation (shale): "Wilson disease" is not a residual of
    "Mrs. Wilson"."""
    count = 0
    for t in sorted(_name_targets(confirmed), key=len, reverse=True):
        if _CASELESS.search(t):
            finder = re.finditer(re.escape(t), clean)
        else:
            finder = re.finditer(rf"(?<![\w'\-]){re.escape(t)}(?![\w'\-])", clean)
        for m in finder:
            s, e = m.start(), m.end()
            if clinically_bound(clean, s, e):
                continue
            # The kind of a workplace or place of care kept after its
            # removed name ("[EMPLOYER] Mutual", "[FACILITY] Institute") is
            # the policy's own output, not a surviving name.
            if re.search(r"\[(?:EMPLOYER|FACILITY|REMOVED)\][ \t]+(?:[A-Z][\w'’-]*[ \t]+){0,3}$",
                         clean[max(0, s - 60):s]) and _kind_word(t):
                continue
            # Same reading as propagation (shale.py): an everyday-word
            # name at a sentence start is the WORD only before a modal
            # reading or a function word ("Will return", "Rose to 38.5").
            if (" " not in t and not _CASELESS.search(t)
                    and _zipf(t.lower()) >= 3.6
                    and _SENTENCE_START.search(clean[max(0, s - 3):s])):
                from inkguard.shale import _MODALS, _WORD_READING_AFTER, _PREDICATE_AFTER
                if not _PREDICATE_AFTER.match(clean[e:e + 20]) and (
                        t.lower() in _MODALS or _WORD_READING_AFTER.match(clean[e:e + 24])):
                    continue
            count += 1
    return count


def residual_structured(clean: str, check_dates: bool) -> dict[str, int]:
    prot = _protected(clean)
    found: dict[str, int] = {}

    def hit(kind: str) -> None:
        found[kind] = found.get(kind, 0) + 1

    for m in _SSN.finditer(clean):
        if _valid_ssn(*m.groups()) and not m.group(0).isdigit() \
                and not _inside(m.start(), m.end(), prot):
            hit("US_SSN")
    for m in _EMAIL.finditer(clean):
        hit("EMAIL_ADDRESS")
    for m in _PHONE.finditer(clean):
        if not _inside(m.start(), m.end(), prot):
            hit("PHONE_NUMBER")
    for m in _MRN.finditer(clean):
        hit("MRN")
    if check_dates:
        for m in _DATE.finditer(clean):
            if not _inside(m.start(), m.end(), prot):
                hit("DATE")
    return found


def inspect(clean: str, confirmed: set[str], *, check_dates: bool,
            uncertain_masked: int = 0, integrity=None, linkage=None) -> ExitReport:
    rep = ExitReport(uncertain_masked=uncertain_masked)
    rep.residual_names = residual_names(clean, confirmed)
    rep.residual_structured = residual_structured(clean, check_dates)

    if rep.residual_names:
        rep.reasons.append(f"{rep.residual_names} occurrence(s) of a confirmed name survived")
    for kind, n in rep.residual_structured.items():
        rep.reasons.append(f"{n} residual {kind}")

    if rep.residual_names or rep.residual_structured:
        rep.grade, rep.quarantined = "F", True
        return rep

    degraded = bool(integrity is not None and getattr(integrity, "issues", None))
    review = bool(linkage is not None and getattr(linkage, "verdict", "") == "review")
    caution = bool(linkage is not None and getattr(linkage, "verdict", "") == "caution")
    if review or degraded:
        rep.grade = "C"
        rep.reasons.append("linkage review" if review else "clinical integrity degraded")
    elif uncertain_masked or caution:
        rep.grade = "B"
        if uncertain_masked:
            rep.reasons.append(f"{uncertain_masked} uncertain token(s) masked as [NAME]")
        if caution:
            rep.reasons.append("linkage caution")
    return rep
