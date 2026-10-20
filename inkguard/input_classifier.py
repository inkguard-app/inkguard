"""
Input classifier — recognize the SHAPE of incoming data.

WHY CLASSIFY AT ALL
-------------------
Clinical text arrives in a handful of recognizable forms, and the same
characters mean different things in each. Classification is not
labelling for its own sake; it selects a detection profile.

  "MR"        imaging report  → magnetic resonance (clinical)
              registration    → the title "Mr." (precedes a name)
  "84756213"  lab panel       → an accession or a value
              billing claim   → an account number (PHI)
  "Name:"     structured form → a field anchor: whatever follows IS a
                                name, no inference required
              narrative       → probably nothing

The biggest practical win is field anchoring. In a structured form,
"MRN: 4471" is certain, while NER has to guess. Anchored extraction on
recognized forms is both more precise and more complete than inference,
so the classifier's main job is deciding when anchoring applies.

The second win is the density expectation. A registration form that
produces zero identifiers is not a clean note — it is a detection
failure. Only the class tells you which of those you are looking at,
and that check catches silent misses nothing else would.

WHY RULES AND NOT A MODEL
-------------------------
Feature scoring, not machine learning. A compliance reviewer can read
the rules and a user can be told exactly why their input was
classified as it was. A model would need clinical training data
(which is PHI), would need revalidation on drift, and would turn every
misclassification into an unanswerable question. Transparency is worth
more than a few points of accuracy here.

Every classification returns its evidence and a confidence. Below the
confidence floor the profile falls back to the strictest setting
rather than a guess — a misread class must never relax detection.
"""
import re
from dataclasses import dataclass, field
from enum import Enum


class InputClass(str, Enum):
    STRUCTURED_FORM = "structured_form"      # Name: / DOB: / MRN: fields
    SOAP_NOTE = "soap_note"                  # S/O/A/P or HPI/ROS sections
    LAB_REPORT = "lab_report"                # analyte + value + units + range
    MEDICATION_LIST = "medication_list"      # drug + dose + route + frequency
    IMAGING_REPORT = "imaging_report"        # modality + findings + impression
    BILLING_CLAIM = "billing_claim"          # ICD/CPT codes, member/claim IDs
    PATIENT_MESSAGE = "patient_message"      # first person, informal
    CLINICAL_QUESTION = "clinical_question"  # short query-like text, usually no PHI
    NARRATIVE_NOTE = "narrative_note"        # free clinical prose
    UNKNOWN = "unknown"


@dataclass
class DetectionProfile:
    """How to scan a given class of input."""
    use_field_anchors: bool = False
    # Bare numbers are identifiers in some classes and measurements in
    # others. Getting this backwards either shreds lab values or leaks
    # account numbers.
    bare_numbers_suspect: bool = False
    # Expected identifier density. Used for the anomaly check: a class
    # that normally carries PHI and produced none is suspicious.
    expects_phi: bool = True
    emphasize: tuple[str, ...] = ()
    notes: str = ""


PROFILES: dict[InputClass, DetectionProfile] = {
    InputClass.STRUCTURED_FORM: DetectionProfile(
        use_field_anchors=True, bare_numbers_suspect=True, expects_phi=True,
        emphasize=("PERSON", "MRN", "US_SSN", "DATE_TIME", "PHONE_NUMBER",
                   "LOCATION", "HEALTH_PLAN_ID"),
        notes="Field labels are authoritative; anchored extraction beats inference.",
    ),
    InputClass.SOAP_NOTE: DetectionProfile(
        use_field_anchors=True, bare_numbers_suspect=False, expects_phi=True,
        emphasize=("PERSON", "MRN", "DATE_TIME"),
        notes="Identifiers cluster in the header; body is clinical prose.",
    ),
    InputClass.LAB_REPORT: DetectionProfile(
        use_field_anchors=True, bare_numbers_suspect=False, expects_phi=True,
        emphasize=("PERSON", "MRN", "DATE_TIME"),
        notes="Numbers are results. Never treat a bare value as an identifier.",
    ),
    InputClass.MEDICATION_LIST: DetectionProfile(
        bare_numbers_suspect=False, expects_phi=False,
        emphasize=("PERSON",),
        notes="Numbers are doses. Drug names must survive intact.",
    ),
    InputClass.IMAGING_REPORT: DetectionProfile(
        use_field_anchors=True, bare_numbers_suspect=False, expects_phi=True,
        emphasize=("PERSON", "MRN", "DATE_TIME"),
        notes="Modality abbreviations (CT, MR, US) are clinical here, not places.",
    ),
    InputClass.BILLING_CLAIM: DetectionProfile(
        use_field_anchors=True, bare_numbers_suspect=True, expects_phi=True,
        emphasize=("HEALTH_PLAN_ID", "MRN", "US_SSN", "PERSON", "NPI",
                   "US_BANK_NUMBER"),
        notes="Dense in account and member numbers. Bare numbers are suspect.",
    ),
    InputClass.PATIENT_MESSAGE: DetectionProfile(
        bare_numbers_suspect=False, expects_phi=True,
        emphasize=("PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "LOCATION"),
        notes="First person; self-identifying detail appears mid-sentence.",
    ),
    InputClass.CLINICAL_QUESTION: DetectionProfile(
        bare_numbers_suspect=False, expects_phi=False,
        notes="Short query-like text. Identifiers here are unexpected and worth noting.",
    ),
    InputClass.NARRATIVE_NOTE: DetectionProfile(
        bare_numbers_suspect=False, expects_phi=True,
        emphasize=("PERSON", "DATE_TIME", "LOCATION"),
    ),
    # UNKNOWN is deliberately the STRICTEST profile, not the loosest.
    # If the shape cannot be read, assume the input carries identifiers
    # and that bare numbers matter.
    InputClass.UNKNOWN: DetectionProfile(
        use_field_anchors=True, bare_numbers_suspect=True, expects_phi=True,
        notes="Unrecognized shape; strictest settings applied.",
    ),
}


@dataclass
class Classification:
    input_class: InputClass
    confidence: float
    evidence: list[str] = field(default_factory=list)

    @property
    def profile(self) -> DetectionProfile:
        # Below the floor, fall back to strict. A misread class must
        # never loosen detection.
        if self.confidence < 0.4:
            return PROFILES[InputClass.UNKNOWN]
        return PROFILES[self.input_class]

    def to_dict(self) -> dict:
        p = self.profile
        return {
            "class": self.input_class.value,
            "confidence": round(self.confidence, 2),
            "evidence": self.evidence,
            "profile": {
                "field_anchored_extraction": p.use_field_anchors,
                "bare_numbers_treated_as_identifiers": p.bare_numbers_suspect,
                "expects_identifiers": p.expects_phi,
                "notes": p.notes,
            },
        }


# =====================================================================
# FIELD ANCHORS — the highest-precision extraction available
# =====================================================================
# "MRN: 4471" is certain. NER has to guess. Where a label exists, the
# label wins: whatever follows it is that kind of identifier, whatever
# the model thinks.
FIELD_ANCHORS: dict[str, str] = {
    r"(?:patient\s+)?name": "PERSON",
    r"(?:pt\.?\s+)?full\s+name": "PERSON",
    r"first\s+name": "PERSON",
    r"last\s+name": "PERSON",
    r"surname": "PERSON",
    r"pt\.?\s+name": "PERSON",
    # "Patient ID" is an identifier, not a name — must precede the bare
    # "patient" label below so the alternation reaches it first.
    r"patient\s+(?:id|identifier|no\.?|number|#)": "MRN",
    # Bare "PATIENT:" is one of the most common header conventions in
    # clinical documents. Without this anchor, every other labelled field
    # in a registration header (DOB, MRN, SSN, phone, email, address)
    # would be anchored while the patient's own NAME fell through to NER,
    # which is weakest on exactly that field, and most of all on
    # non-Western names.
    r"patient": "PERSON",
    r"guarantor": "PERSON",
    r"emergency\s+contact": "PERSON",
    r"next\s+of\s+kin": "PERSON",
    r"ordering\s+(?:provider|physician)": "PERSON",
    r"attending": "PERSON",
    r"referring\s+(?:provider|physician)": "PERSON",
    r"d\.?o\.?b\.?|date\s+of\s+birth|birth\s*date": "DATE_TIME",
    r"admission\s+date|admit\s+date|discharge\s+date|date\s+of\s+service|collected|drawn": "DATE_TIME",
    r"m\.?r\.?n\.?|medical\s+record(?:\s+(?:number|no\.?))?|chart\s+(?:number|no\.?)": "MRN",
    r"account\s+(?:number|no\.?|#)|acct": "MRN",
    r"s\.?s\.?n\.?|social\s+security(?:\s+(?:number|no\.?))?": "US_SSN",
    r"(?:tele)?phone|tel|mobile|cell|contact\s+(?:number|no\.?)|fax": "PHONE_NUMBER",
    r"e-?mail": "EMAIL_ADDRESS",
    r"address|street|city|residence|home\s+address": "LOCATION",
    r"member\s+(?:id|number|no\.?)|policy\s+(?:number|no\.?)|subscriber\s+id|"
    r"group\s+(?:number|no\.?)|insurance(?:\s+(?:id|no\.?|number|#))?|payer\s+id|"
    r"plan\s+id|medicaid(?:\s+id)?|medicare(?:\s+id)?|mbi|hicn": "HEALTH_PLAN_ID",
    r"n\.?p\.?i\.?": "NPI",
    r"claim\s+(?:number|no\.?|id)": "HEALTH_PLAN_ID",
    r"device\s+(?:id|serial)|serial\s+(?:number|no\.?)": "DEVICE_ID",
    r"driver'?s?\s+licen[sc]e|dl\s+(?:number|no\.?)": "US_DRIVER_LICENSE",
}

# FIELD BOUNDARY. On a form line carrying several fields — "PATIENT:
# 渡辺 淳   MRN: 4471982" — a value that ran to the end of the line would
# swallow the MRN field, and "MRN:" would never be anchored if only
# line-start labels counted; a higher-scoring MRN hit could then replace
# the whole span and leave the name un-redacted.
#
# So a label may also start mid-line when it is set off the way form
# fields are (two or more spaces, a tab, ";" or "|"), and every value
# stops where the next label begins. Single-space prose is still NOT
# anchored mid-line: "noted by the patient: she..." is a sentence.
_LABEL_ALT = "|".join(FIELD_ANCHORS)
_ANCHOR_RE = re.compile(
    r"(?:^|(?<=[ \t]{2})|(?<=[;|\t]))[ \t]*(?:[-*\u2022]\s*)?"
    r"(" + _LABEL_ALT + r")[ \t]*[:=][ \t]*"
    r"(.+?)"
    # The value also ends where ANY other "Label:" begins after a gap of
    # two or more spaces — "Collected: Apr 18, 2023   Submitting MD: Ardis
    # Gaton" is a date field and a person field, not one long date. (Under
    # the Limited Data Set the date is KEPT, and without this boundary the
    # name would be kept with it.)
    r"(?=[ \t]+(?:" + _LABEL_ALT + r")[ \t]*[:=]|[ \t]{2,}[A-Za-z][A-Za-z .#/()'-]{0,30}?[ \t]*[:=]|"
    r"[ \t]*[;|]|[ \t]*$)",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class AnchoredField:
    label: str
    entity_type: str
    value: str
    start: int
    end: int
    label_start: int = 0
    label_end: int = 0


_SENTENCE_LEAD_RE = re.compile(
    r"^\W*(?:the|this|that|a|an|he|she|they|patient|pt\b|is|was|no|denies)\b",
    re.IGNORECASE,
)


def _is_name_shaped(value: str) -> bool:
    """Cheap check that an anchored PERSON value looks like a name
    rather than the opening of a sentence. Deliberately permissive:
    it rejects prose, not unusual names."""
    v = value.strip().strip(",;")
    if not v or len(v) > 60:
        return False
    if len(v.split()) > 5:
        return False
    if _SENTENCE_LEAD_RE.match(v):
        return False
    # must contain at least one letter; "12345" after "Patient:" is an
    # identifier the MRN anchor should have taken, not a name.
    return any(ch.isalpha() for ch in v)


def extract_anchored_fields(text: str) -> list[AnchoredField]:
    """Find `Label: value` lines and type the value from the label.

    Only the VALUE span is returned, never the label — redacting the
    label would destroy the structure of the form and make the output
    unreadable, while protecting nothing.
    """
    out: list[AnchoredField] = []
    for m in _ANCHOR_RE.finditer(text):
        label = m.group(1)
        value = m.group(2)
        if not value or value.lower() in {"n/a", "na", "none", "unknown", "-", "--"}:
            continue

        entity = "PERSON"
        for pattern, ent in FIELD_ANCHORS.items():
            if re.fullmatch(pattern, label, re.IGNORECASE):
                entity = ent
                break

        # A trailing identifier is split off the name, but only when it is a
        # real identifier label followed by its number at the VERY END of the
        # value: "Patient: Hubert Lindqvist MRN 4471822" is a name and a
        # record number (a PDF line loses the double space that would
        # otherwise separate them). The head stays a name and the number is
        # claimed as its own identifier, so the whole span is still covered.
        # If anything else follows the number — another name, as in "Grace
        # Age 60 Hope" — nothing is split and the whole value is removed as a
        # name (safe). Only end-anchored, labelled identifiers split; loose
        # words like "age", "room" or "bed" never do.
        # A header line often carries several: "Patient: Rosalind Ferraro
        # MRN 0071-4482   DOB 11/02/1979". Each trailing label and number is
        # peeled off in turn, so the name is left on its own.
        tails: list[AnchoredField] = []
        if entity == "PERSON":
            v2 = m.start(2)
            for _ in range(4):
                mt = _PERSON_TAIL_ID.search(value)
                if not (mt and mt.start() > 0):
                    break
                tails.append(AnchoredField(
                    mt.group(1), _TAIL_ID_TYPE[_tail_key(mt.group(1))], mt.group(2),
                    v2 + mt.start(2), v2 + mt.end(2),
                    label_start=v2 + mt.start(1), label_end=v2 + mt.end(1)))
                value = value[:mt.start()]

        # NAME-SHAPE GUARD. Anchoring deliberately does not inspect the
        # value — the label is the evidence. The one exception is PERSON,
        # because a document that uses "Patient:" to introduce narrative
        # ("Patient: The patient is a 30-year-old female who...") would
        # otherwise have the entire line claimed as a name and redacted.
        # That failure destroys clinical content, so a person-shaped
        # value is required: short, and not a sentence. This costs
        # nothing on real forms, where names are one to four tokens.
        # The split-off identifier is claimed whatever the name head looks
        # like, so a trailing record number is never left behind.
        out.extend(tails)

        if entity == "PERSON" and not _is_name_shaped(value):
            continue

        # A date field ends with its date. "DOB: 11/02/1979. Follow up." is a
        # date and then a sentence; untrimmed, the sentence would be removed
        # with the date.
        # Only a value that STARTS with a whole date followed by sentence
        # punctuation is trimmed; anything else is still removed whole.
        if entity == "DATE_TIME":
            md = _LEADING_DATE.match(value)
            if md and re.match(r"[.;,]\s+\S", value[md.end():]):
                value = value[:md.end()]

        v_start = m.start(2)
        out.append(AnchoredField(
            label, entity, value, v_start, v_start + len(value),
            label_start=m.start(1), label_end=m.end(1),
        ))
    return out


_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_LEADING_DATE = re.compile(
    r"(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?"
    r"|" + _MONTH + r"\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+" + _MONTH + r"\s+\d{4}"
    r"|\d{1,2}" + _MONTH + r"\d{2,4})"
    r"(?:\s+\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?)?",
    re.IGNORECASE)


# A real identifier label and its number at the very END of a name value.
# Only labels that are genuinely identifiers split a name; loose words do
# not. Requires a digit so a surname like "Price" or "Bell" is never read
# as a label.
_PERSON_TAIL_ID = re.compile(
    r"\s+(m\.?r\.?n\.?|medical\s+record(?:\s+(?:number|no\.?))?|chart\s+(?:number|no\.?)|"
    r"acct\.?|account(?:\s+(?:number|no\.?))?|d\.?o\.?b\.?|date\s+of\s+birth|"
    r"s\.?s\.?n\.?|social\s+security(?:\s+(?:number|no\.?))?|n\.?p\.?i\.?)"
    r"[ \t#:.=-]*([0-9][0-9 .\-/]*[0-9]|[0-9])\s*$",
    re.IGNORECASE)


def _tail_key(label: str) -> str:
    s = re.sub(r"[^a-z]", "", label.lower())
    if s.startswith("dob") or s.startswith("dateofbirth"):
        return "dob"
    if s.startswith("ssn") or s.startswith("socialsecurity"):
        return "ssn"
    if s.startswith("npi"):
        return "npi"
    if s.startswith("acct") or s.startswith("account"):
        return "acct"
    return "mrn"


_TAIL_ID_TYPE = {"mrn": "MRN", "acct": "MRN", "dob": "DATE_TIME",
                 "ssn": "US_SSN", "npi": "NPI"}


# =====================================================================
# CLASSIFICATION SIGNALS
# =====================================================================
SOAP_HEADERS = re.compile(
    r"^\s*(?:S|O|A|P|Subjective|Objective|Assessment|Plan|HPI|ROS|PMH|PSH|"
    r"FH|SH|Chief\s+Complaint|CC|Physical\s+Exam|PE|Medications|Allergies|"
    r"Impression|Disposition)\s*[:.\-]",
    re.IGNORECASE | re.MULTILINE,
)

LAB_LINE = re.compile(
    r"\b[A-Za-z][A-Za-z0-9\s\-]{1,24}[:=]?\s*\d+(?:\.\d+)?\s*"
    r"(?:mg/dL|mmol/L|mEq/L|g/dL|ng/mL|pg/mL|U/L|IU/L|mm/hr|%|K/uL|M/uL|fL|pg)"
    r"(?:\s*(?:\(|\[)?\s*(?:H|L|HIGH|LOW|ref|normal))?",
    re.IGNORECASE,
)

MED_LINE = re.compile(
    r"\b[A-Za-z][a-z]{3,}\s+\d+(?:\.\d+)?\s*(?:mg|mcg|g|units?|mL|IU)\b"
    r"[^\n]{0,30}?\b(?:PO|IV|IM|SQ|SC|PR|SL|topical|inhaled|daily|BID|TID|QID|"
    r"QHS|PRN|q\d+h|once|weekly)\b",
    re.IGNORECASE,
)

IMAGING_MARKERS = re.compile(
    r"\b(?:findings|impression|technique|comparison|contrast|"
    r"radiologist|no acute|unremarkable study)\b\s*[:.]?",
    re.IGNORECASE,
)

IMAGING_MODALITY = re.compile(
    r"\b(?:CT|MRI|MR|US|ultrasound|x-?ray|CXR|KUB|PET|DEXA|fluoroscopy|"
    r"mammogram|echocardiogram|angiogram)\b",
    re.IGNORECASE,
)

BILLING_CODES = re.compile(
    r"\b(?:ICD-?10|ICD-?9|CPT|HCPCS|DRG|POS|NDC)\b|"
    r"\b[A-TV-Z]\d{2}(?:\.\d{1,4})?\b|"      # ICD-10 form
    r"\b\d{5}\b(?=\s*(?:CPT|procedure|code))",
    re.IGNORECASE,
)

FIRST_PERSON = re.compile(
    r"\b(?:I|I'm|I've|my|me|myself|we|our)\b", re.IGNORECASE
)

QUESTION_MARKERS = re.compile(
    r"^\s*(?:what|how|when|why|which|is|are|can|should|does|do|explain|"
    r"describe|compare|list|define)\b|\?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def classify(text: str) -> Classification:
    """Score every class and return the strongest, with its evidence."""
    if not text or not text.strip():
        return Classification(InputClass.UNKNOWN, 0.0, ["empty input"])

    lines = [l for l in text.splitlines() if l.strip()]
    n_lines = max(len(lines), 1)
    scores: dict[InputClass, float] = {}
    evidence: dict[InputClass, list[str]] = {}

    def add(cls: InputClass, pts: float, why: str):
        scores[cls] = scores.get(cls, 0.0) + pts
        evidence.setdefault(cls, []).append(why)

    # Structured form — labelled fields
    anchors = extract_anchored_fields(text)
    if anchors:
        ratio = len(anchors) / n_lines
        if len(anchors) >= 3 or ratio > 0.3:
            add(InputClass.STRUCTURED_FORM, min(0.35 + ratio, 0.85),
                f"{len(anchors)} labelled field(s): "
                + ", ".join(sorted({a.label.lower() for a in anchors})[:4]))
        else:
            add(InputClass.STRUCTURED_FORM, 0.2,
                f"{len(anchors)} labelled field(s)")

    # SOAP / clinical note sections
    soap = SOAP_HEADERS.findall(text)
    if soap:
        add(InputClass.SOAP_NOTE, min(0.3 + 0.12 * len(soap), 0.85),
            f"{len(soap)} clinical section header(s)")

    # Lab report
    labs = LAB_LINE.findall(text)
    if len(labs) >= 2:
        add(InputClass.LAB_REPORT, min(0.3 + 0.1 * len(labs), 0.85),
            f"{len(labs)} analyte/value/unit line(s)")

    # Medication list
    meds = MED_LINE.findall(text)
    if meds:
        add(InputClass.MEDICATION_LIST, min(0.3 + 0.15 * len(meds), 0.85),
            f"{len(meds)} drug/dose/route line(s)")

    # Imaging report — needs BOTH a modality and report structure, since
    # a modality alone appears in any note that ordered a scan.
    if IMAGING_MODALITY.search(text) and IMAGING_MARKERS.search(text):
        marks = len(IMAGING_MARKERS.findall(text))
        add(InputClass.IMAGING_REPORT, min(0.4 + 0.1 * marks, 0.85),
            "imaging modality with findings/impression structure")

    # Billing
    codes = BILLING_CODES.findall(text)
    if len(codes) >= 2:
        add(InputClass.BILLING_CLAIM, min(0.35 + 0.1 * len(codes), 0.85),
            f"{len(codes)} billing/diagnosis code marker(s)")

    # Patient message — first person, no clinical structure
    fp = len(FIRST_PERSON.findall(text))
    if fp >= 2 and not soap and not anchors:
        add(InputClass.PATIENT_MESSAGE, min(0.3 + 0.08 * fp, 0.75),
            f"{fp} first-person reference(s), no clinical structure")

    # Short query-like text
    if QUESTION_MARKERS.search(text) and len(text) < 400 and not anchors:
        add(InputClass.CLINICAL_QUESTION, 0.55, "question form, no patient fields")

    if not scores:
        # Prose with clinical vocabulary but no structure
        if len(text.split()) > 12:
            return Classification(InputClass.NARRATIVE_NOTE, 0.5,
                                  ["unstructured clinical prose"])
        return Classification(InputClass.UNKNOWN, 0.0, ["no recognizable structure"])

    best = max(scores, key=scores.get)
    return Classification(best, min(scores[best], 0.95), evidence[best])


def density_anomaly(cls: Classification, identifier_count: int) -> str | None:
    """Flag when the identifier count contradicts the class.

    A registration form with no identifiers is not a clean form — it is
    almost certainly a detection failure, and nothing else in the
    pipeline would notice, because "found nothing" looks identical to
    "there was nothing to find". The class is what distinguishes them.
    """
    profile = cls.profile
    if profile.expects_phi and identifier_count == 0 and cls.confidence >= 0.5:
        return (
            f"Input looks like a {cls.input_class.value.replace('_', ' ')}, which "
            "normally contains patient identifiers, but none were detected. "
            "Check that it truly holds no identifiers, rather than that detection missed them."
        )
    if not profile.expects_phi and identifier_count >= 3:
        return (
            f"Input looks like a {cls.input_class.value.replace('_', ' ')}, which "
            f"normally contains no patient identifiers, but {identifier_count} "
            "were found. Check whether this file holds patient data you did not expect."
        )
    return None
