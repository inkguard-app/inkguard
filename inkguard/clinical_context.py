"""
Clinical integrity verification.

The tool has TWO failure modes:

  UNDER-redaction → an identifier is left in the text.
  OVER-redaction  → clinical meaning is lost → the researcher, or any
                    program reading the notes, works from a damaged
                    record, while every count looks fine.

The second one is invisible without explicit measurement. This module
measures it.

Approach: inventory the clinically load-bearing elements of the
original text (lab values, vitals, doses, negations, temporal markers,
named conditions) and confirm each one survives into the redacted text.
Anything lost is reported as a clinical_integrity warning on the
report — visible to whoever reviews the note, and recorded in the operation log.

This is NOT a semantic equivalence proof. It's a concrete, checkable
inventory of the element classes that most often get clobbered. Treat
a warning as "a person should look at this note," not as a hard error.
"""
import functools
import re
from dataclasses import dataclass, field

from inkguard.clinical_lexicon import CLINICAL_UNITS, is_clinical_term


@dataclass
class ClinicalElement:
    kind: str       # "lab_value" | "vital" | "dose" | "negation" | "temporal" | "condition"
    text: str


@dataclass
class IntegrityReport:
    preserved: list[ClinicalElement] = field(default_factory=list)
    lost: list[ClinicalElement] = field(default_factory=list)

    @property
    def is_intact(self) -> bool:
        return not self.lost

    @property
    def score(self) -> float:
        total = len(self.preserved) + len(self.lost)
        return 1.0 if total == 0 else len(self.preserved) / total

    def to_dict(self) -> dict:
        return {
            "clinically_intact": self.is_intact,
            "integrity_score": round(self.score, 3),
            "elements_preserved": len(self.preserved),
            "elements_lost": [{"kind": e.kind, "text": e.text} for e in self.lost],
            # Listed so the report shows what was KEPT, not only what was
            # removed.
            "preserved_terms": [
                e.text for e in self.preserved
                if e.kind in ("condition", "lab_value", "vital", "dose")
            ],
        }


_UNIT_ALT = "|".join(re.escape(u) for u in sorted(CLINICAL_UNITS, key=len, reverse=True))

# Numeric value attached to a clinical unit: "900 mg", "6.2 mEq/L", "88%"
VALUE_PATTERN = re.compile(rf"\b\d+(?:\.\d+)?\s*(?:{_UNIT_ALT})\b", re.IGNORECASE)

# Instrument sizing conventions. "14 French" is a catheter gauge, not a
# nationality; on a 200-document test set it was the single most
# over-redacted term. A lexicon entry cannot fix
# it, because bare "French" genuinely is an ordinary English word; only
# the preceding number distinguishes the measurement from the adjective.
SIZING_PATTERN = re.compile(
    r"\b\d{1,3}\s*(?:French|Fr\.?|Ch\.?|Charr(?:iere)?|gauge|ga\.?)\b",
    re.IGNORECASE,
)

# Vitals in their usual shorthand: "BP 180/120", "HR 110", "SpO2 88%"
VITALS_PATTERN = re.compile(
    # Values are bounded to physiologic widths (<=3 integer digits). An
    # unbounded \d+ would make "T2331823" a temperature and protect a
    # record number from redaction. A value is never followed by "-digits":
    # that shape is an identifier, and a middle initial T before an SSN
    # ("WYATT T 900-14-7708") would make the SSN a temperature of 900.
    r"\b(?:BP|HR|RR|T|Temp|SpO2|SaO2|FiO2)\s*[:=]?\s*\d{1,3}(?:\.\d+)?(?:/\d{1,3})?\b(?![-\u2010\u2011\u2013]\d)",
    re.IGNORECASE,
)

# Negation is diagnostically decisive: "no chest pain", "denies fever",
# "without hemoptysis". Losing a negation inverts clinical meaning —
# the single most dangerous possible redaction artifact.
NEGATION_PATTERN = re.compile(
    r"\b(?:no|not|denies|denied|without|negative for|absent|ruled out|"
    r"r/o|non[-\s]?tender|afebrile|unremarkable)\b",
    re.IGNORECASE,
)

# Relative timing — onset/duration drives the entire differential.
TEMPORAL_PATTERN = re.compile(
    r"\b(?:\d+\s*(?:second|minute|hour|day|week|month|year)s?|"
    r"sudden(?:ly)?|acute(?:ly)?|chronic|gradual(?:ly)?|abrupt(?:ly)?|"
    r"intermittent|progressive|since|onset|duration)\b",
    re.IGNORECASE,
)

# Lab name followed by a bare number: "K 6.2", "Na 128", "WBC 14"
LAB_PATTERN = re.compile(
    r"\b(?:Na|K|Cl|HCO3|BUN|Cr|Glu|Ca|Mg|Phos|Hgb|Hct|WBC|Plt|INR|"
    r"AST|ALT|ALP|TSH|HbA1c|LDH|CRP|ESR|BNP|Trop|Lactate)\s*[:=]?\s*"
    # <=4 integer digits (platelets 250, glucose 1200). Unbounded, it
    # would make "K8055532" a potassium and shield an MRN.
    r"\d{1,4}(?:\.\d+)?\b",
    re.IGNORECASE,
)


# TNM staging: "Ta Nx Mx", "pT2a N0 M0", "T1b", "stage Ta". Two-letter
# fragments that NER reads as initials or a Vietnamese surname. A lone
# "Ta"/"Tis" is claimed only with a staging word or lesion noun beside it.
TNM_PATTERN = re.compile(
    r"\b[cpyr]?T(?:is|a|x|[0-4][a-d]?)\s*N(?:x|[0-3][a-c]?)\s*M(?:x|[01][a-c]?)\b"
    r"|\b[cpyr]?T(?:is|a|x|[0-4][a-d]?)(?=\s+(?:N[x0-3]|papillary|lesion|"
    r"tumou?r|disease|urothelial|transitional|carcinoma|bladder)\b)"
    r"|(?<=\bstage\s)T(?:is|a|x|[0-4][a-d]?)\b"
)

# GRADES AND CLASSES. "Engel class IA", "stage IIIB", "Simpson grade II",
# "type 2": a Roman numeral or number with a letter after a grading word is
# a grade, never a state ("IA" read as Iowa).
GRADE_PATTERN = re.compile(
    r"(?i:\b(?:class|grade|type|stage|category|tier|zone|level|group)\s+)"
    r"(?:[IVX]{1,4}[A-Da-d]?|[0-9]{1,2}[A-Da-d]?)\b")

# NAMED SCALES written without a grading word: "Lenke 1A", "modified
# Fisher 3", "Knosp 4", "House-Brackmann IV", "Meyerding II". Only scales
# whose eponym followed straight by a grade code is a clinical reading;
# surnames that are also common scale names elsewhere (Weber, Garden,
# Mason, Simpson, Denis) are left out, since "Weber B" beside a patient
# named Weber is ambiguous. The code must look like a grade.
_SCALE_EPONYMS = (
    r"Lenke|(?:modified\s+)?Fisher|Hunt[\s-]+(?:and\s+|&\s*)?Hess|Knosp|Koos|Borden|Cognard"
    r"|Meyerding|Pfirrmann|Modic|Spetzler[\s-]+Martin|House[\s-]+Brackmann|Engel|Frankel"
    r"|Fazekas|Mallampati|Salter[\s-]+Harris|Gustilo(?:[\s-]+Anderson)?|Kellgren[\s-]+Lawrence"
    r"|Hoehn\s+(?:and|&)\s+Yahr|Le\s?Fort|(?:modified\s+)?Ashworth|Hardy|Tönnis|Tonnis")
EPONYM_GRADE_PATTERN = re.compile(
    r"\b(?:" + _SCALE_EPONYMS + r")\s+"
    r"(?:[IVX]{1,4}[A-Da-d]?|[0-9]{1,2}(?:\.[05])?[A-Da-d]?)(?:[+N]|-(?!\w))?(?!\w)")

# SCORE COMPONENTS that look like codes: the Glasgow Coma Scale written as
# "E3V4M6" (or "E2VTM4"), the FOUR score "E4M4B4R4".
SCORE_COMPONENT_PATTERN = re.compile(
    r"\bE[1-4][ ,]?V(?:[1-5]|T|NT)[ ,]?M[1-6]\b|\bE[0-4]M[0-4]B[0-4]R[0-4]\b")

# Clock-face positions: "at the 6 o'clock position", "from 12 o'clock to
# 4 o'clock", "at the 1, 3, 5, 7, 9 and 11 o'clock positions". These are
# ANATOMIC ORIENTATION, not times, and every surgical note uses them.
CLOCK_PATTERN = re.compile(
    r"\b(?:approximately\s+|about\s+)?\d{1,2}"
    r"(?:\s*(?:,|and|to|-|through|or)\s*(?:the\s+)?\d{1,2})*"
    r"\s*o['’]?\s?clock\b"
    r"(?:\s+(?:to|through|-)\s+(?:the\s+)?\d{1,2}\s*o['’]?\s?clock\b)?",
    re.IGNORECASE,
)


def _inventory(text: str) -> list[ClinicalElement]:
    elements: list[ClinicalElement] = []

    for m in LAB_PATTERN.finditer(text):
        elements.append(ClinicalElement("lab_value", m.group(0)))
    for m in VITALS_PATTERN.finditer(text):
        elements.append(ClinicalElement("vital", m.group(0)))
    for m in VALUE_PATTERN.finditer(text):
        elements.append(ClinicalElement("dose", m.group(0)))
    for m in NEGATION_PATTERN.finditer(text):
        elements.append(ClinicalElement("negation", m.group(0)))
    for m in TEMPORAL_PATTERN.finditer(text):
        elements.append(ClinicalElement("temporal", m.group(0)))

    # Named clinical concepts (eponyms, drugs, scales) present in the text
    for token in re.findall(r"\b[A-Za-z][A-Za-z\-']{2,}\b", text):
        if is_clinical_term(token):
            elements.append(ClinicalElement("condition", token))

    return elements


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def verify(original: str, redacted: str) -> IntegrityReport:
    """Confirm every clinically load-bearing element in `original`
    still appears in `redacted`."""
    report = IntegrityReport()
    redacted_norm = _normalize(redacted)

    seen: set[tuple[str, str]] = set()
    for element in _inventory(original):
        key = (element.kind, _normalize(element.text))
        if key in seen:
            continue
        seen.add(key)

        if _normalize(element.text) in redacted_norm:
            report.preserved.append(element)
        else:
            report.lost.append(element)

    return report


@functools.lru_cache(maxsize=None)
def neuro_phrase_pattern() -> "re.Pattern":
    """Every multi-word phrase of lexicon_neuro.py whose words hold a capital
    in normal writing (an eponym or a product), as one pattern. Phrases of
    ordinary words need no protection and are left out."""
    from inkguard.lexicon_neuro import all_phrases
    phrases = sorted({p for group in all_phrases().values() for p in group
                      if " " in p and any(w[:1].isupper() or any(c.isdigit() for c in w)
                                          for w in p.split())},
                     key=len, reverse=True)
    body = "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in phrases)
    return re.compile(rf"(?i:(?<![\w-])(?:{body})(?![\w-]))")
