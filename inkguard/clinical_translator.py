"""
Clinical translator — the pre-scan disambiguation layer.

WHY THIS EXISTS
---------------
Clinical shorthand collides with PHI categories, and the collision runs
in both directions inside a single sentence:

    "MS exacerbation, LA enlargement on echo. Patient resides in CT."

  * LA here is the left atrium. NER reads Louisiana and redacts it —
    clinical content destroyed.
  * CT here is Connecticut. A state is not itself a Safe Harbor
    identifier (the rule covers geographic units smaller than a state),
    but NER reads a CT scan, so the place goes unread; where a town or
    ZIP stands beside it, that context is lost too.

Neither error is fixable by the lexicon, because the same token is
clinical in one position and an identifier in another. Only context
resolves it. That is this layer's whole job.

WHAT IT DOES AND DOES NOT DO
----------------------------
It ANNOTATES; it does not rewrite. The layer returns span verdicts that
the scanner consumes as evidence. The note that comes in is the note
that gets scanned and written out, minus redactions.

Rewriting was the obvious alternative — expand "MI" to "myocardial
infarction" and scan cleaner text. It is rejected on purpose:
an expansion that guesses wrong silently corrupts clinical meaning
before anyone can inspect it, which is the exact failure the integrity
checker exists to catch. Annotation is recoverable; rewriting is not.
An opt-in `clarify()` is provided for callers who want an expanded copy.
It is off by default, is not used in the redaction path, and logs
nothing itself.

COMPLEXITY GATING
-----------------
Most notes are plain prose and need none of this. `complexity_score`
measures shorthand density and ambiguous-token presence, and the
scanner only invokes the resolver when the score clears a threshold.
Simple notes take the fast path; dense shorthand gets the expensive
treatment. That keeps the time taken proportional to the difficulty of the
input rather than paying for the worst case on every note.
"""
import re
from dataclasses import dataclass
from enum import Enum


class Sense(str, Enum):
    CLINICAL = "clinical"        # protect from redaction
    IDENTIFIER = "identifier"    # force redaction — this IS PHI
    UNRESOLVED = "unresolved"    # ambiguous; fail toward redaction


@dataclass
class Resolution:
    token: str
    start: int
    end: int
    sense: Sense
    expansion: str          # what it most likely means
    confidence: float
    rationale: str          # why — surfaced in the audit log


# ---------------------------------------------------------------------
# Tokens that are BOTH clinical shorthand and a PHI-category term.
# Each carries the cues that discriminate the two readings.
#
# `clinical_cues`  — words near the token that indicate the medical sense
# (place cues are shared by every token: see GEO_CONTEXT below)
# ---------------------------------------------------------------------
AMBIGUOUS_TOKENS: dict[str, dict] = {
    "CT": {
        "clinical": "computed tomography",
        "identifier": "Connecticut",
        "clinical_cues": ["head", "chest", "abdomen", "pelvis", "scan", "angiography",
                          "contrast", "imaging", "noncontrast", "cta", "showed",
                          "negative", "positive", "revealed", "obtained", "ordered"],
    },
    "MI": {
        "clinical": "myocardial infarction",
        "identifier": "Michigan",
        "clinical_cues": ["stemi", "nstemi", "troponin", "ecg", "ekg", "chest",
                          "cardiac", "acute", "prior", "ruled", "anterior",
                          "inferior", "history", "post"],
    },
    "SC": {
        "clinical": "subcutaneous",
        "identifier": "South Carolina",
        "clinical_cues": ["units", "injection", "insulin", "heparin", "enoxaparin",
                          "administered", "give", "inject", "daily", "bid", "qhs",
                          "mg", "ml", "route"],
    },
    "US": {
        "clinical": "ultrasound",
        "identifier": "United States",
        "clinical_cues": ["abdomen", "pelvic", "renal", "doppler", "guided",
                          "showed", "revealed", "transvaginal", "bedside", "fast",
                          "scan", "imaging", "obstetric"],
    },
    "PA": {
        "clinical": "posteroanterior / physician assistant",
        "identifier": "Pennsylvania",
        "clinical_cues": ["lateral", "cxr", "chest", "film", "view", "radiograph",
                          "xray", "x-ray", "catheter", "pressure", "seen", "consult"],
    },
    "OR": {
        "clinical": "operating room",
        "identifier": "Oregon",
        "clinical_cues": ["taken", "to", "emergent", "urgent", "scheduled", "booked",
                          "surgery", "surgical", "intraoperative", "post-op", "lap"],
    },
    "LA": {
        "clinical": "left atrium",
        "identifier": "Louisiana / Los Angeles",
        "clinical_cues": ["enlargement", "dilated", "echo", "atrial", "appendage",
                          "volume", "pressure", "thrombus", "size", "diameter"],
    },
    "MS": {
        "clinical": "multiple sclerosis / mitral stenosis / morphine sulfate",
        "identifier": "Mississippi",
        "clinical_cues": ["exacerbation", "relapsing", "remitting", "lesions", "mri",
                          "demyelinating", "valve", "mitral", "gradient", "rheumatic",
                          "flare", "plaques"],
    },
    "MD": {
        "clinical": "doctor of medicine",
        "identifier": "Maryland",
        "clinical_cues": ["dr", "attending", "resident", "signed", "consult",
                          "physician", "ordered", "per"],
    },
    "VA": {
        "clinical": "Veterans Affairs facility",
        "identifier": "Virginia",
        "clinical_cues": ["hospital", "medical", "center", "clinic", "system",
                          "admitted", "transferred", "veteran"],
    },
    "IN": {
        "clinical": "intranasal",
        "identifier": "Indiana",
        "clinical_cues": ["spray", "naloxone", "midazolam", "route", "administered",
                          "given", "dose"],
    },
    "ID": {
        "clinical": "infectious disease / intradermal",
        "identifier": "Idaho",
        "clinical_cues": ["consult", "workup", "service", "ppd", "tuberculin",
                          "culture", "antibiotic", "fever"],
    },
    "OK": {
        "clinical": "(colloquial) stable",
        "identifier": "Oklahoma",
        "clinical_cues": ["vitals", "doing", "appears", "feels", "stable"],
    },
    "ME": {
        "clinical": "medical examiner",
        "identifier": "Maine",
        "clinical_cues": ["case", "referred", "autopsy", "coroner", "death",
                          "notified"],
    },
    "AL": {
        "clinical": "arterial line",
        "identifier": "Alabama",
        "clinical_cues": ["placed", "radial", "femoral", "arterial", "line",
                          "monitoring"],
    },
    "DE": {
        "clinical": "(rare) drug eluting",
        "identifier": "Delaware",
        "clinical_cues": ["stent", "eluting", "coronary"],
    },
    "MT": {
        "clinical": "(rare) medical technologist",
        "identifier": "Montana",
        "clinical_cues": ["lab", "technologist", "specimen"],
    },
    "NE": {
        "clinical": "(rare) norepinephrine",
        "identifier": "Nebraska",
        "clinical_cues": ["drip", "pressor", "titrated", "infusion", "mcg"],
    },
    "MO": {
        "clinical": "months old",
        "identifier": "Missouri",
        "clinical_cues": ["infant", "child", "old", "age", "weight", "feeding"],
    },
    "MR": {
        "clinical": "mitral regurgitation",
        "identifier": "(title) Mr.",
        "clinical_cues": ["mitral", "echo", "regurgitation", "severe", "moderate",
                          "murmur", "valve", "jet"],
    },
}

# Cues that a token is being used as a place, whichever token it is.
# Deliberately checked BEFORE clinical cues: an explicit geographic
# frame ("resides in CT") is stronger evidence than a nearby medical
# word, and mistaking an address for a scan can leave the town or ZIP
# beside it unnoticed.
GEO_CONTEXT = re.compile(
    r"\b(?:resides?|residing|lives?|living|located|address|from|moved|"
    r"relocated|born|transferred\s+from|admitted\s+from|discharged\s+to|"
    r"home|state\s+of|city|county|zip)\b",
    re.IGNORECASE,
)

# "Springfield, MA 01103" — a city-comma-STATE-ZIP frame is decisive.
ADDRESS_FRAME = re.compile(
    r"[A-Z][a-z]+,\s*(?:[A-Z]{2})\b(?:\s+\d{5})?"
)

# Shorthand markers used to judge how dense a note is.
SHORTHAND_MARKERS = re.compile(
    r"\b(?:q\.?[dhi]\.?|b\.?i\.?d|t\.?i\.?d|q\.?i\.?d|p\.?o|p\.?r\.?n|"
    r"s/p|c/o|r/o|w/|w/o|h/o|n/v|s/s|y/o|pt|hx|dx|tx|sx|ddx|"
    r"[a-z]{2,5}/[a-z]{2,5})\b",
    re.IGNORECASE,
)


US_STATE_CODES: frozenset[str] = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI",
    "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC",
    "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT",
    "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR", "VI", "GU",
})


def _window(text: str, start: int, end: int, radius: int = 60) -> str:
    return text[max(0, start - radius):min(len(text), end + radius)].lower()


_ID_LABEL_BEFORE = re.compile(
    r"\b(?:member|patient|pt|policy|subscriber|group|record|employee|student|"
    r"user|photo|staff|account|claim|provider|national|tax|unique|encounter|"
    r"visit|client|case|study|subject|sample|specimen|device|order|caller|"
    r"login|badge)\s*$", re.IGNORECASE)
_ID_LABEL_AFTER = re.compile(
    r"^\s*(?:[:#]|no\.?\b|number\b|nbr\b|card\b|badge\b|bracelet\b|band\b)",
    re.IGNORECASE)


def resolve(text: str) -> list[Resolution]:
    """Disambiguate every ambiguous token in the text.

    Resolution order is deliberate:
      1. explicit address frame  → identifier (strongest signal)
      2. geographic context cue  → identifier
      3. clinical context cue    → clinical
      4. neither                 → UNRESOLVED, which the scanner treats
                                   as redactable

    Step 4 is the fail-closed default carried down to this layer: an
    abbreviation we cannot place gets redacted rather than assumed
    harmless. Over-redacting one ambiguous token is a reported
    annoyance; misreading a state code can leave a town or ZIP beside
    it unnoticed.
    """
    out: list[Resolution] = []
    address_spans = [(m.start(), m.end()) for m in ADDRESS_FRAME.finditer(text)]

    for m in re.finditer(r"\b([A-Z]{2})\b", text):
        token = m.group(1)
        spec = AMBIGUOUS_TOKENS.get(token)
        s, e = m.start(), m.end()

        # An explicit "City, ST ZIP" frame is decisive for ANY state
        # code, not only the clinically ambiguous ones. A state is not
        # itself a Safe Harbor identifier (only geographic units smaller
        # than a state are), but the frame marks the town and ZIP beside
        # it as geography, so the state code is resolved here as part of
        # the address (and redacted, which is more than the rule
        # requires) rather than relying on NER noticing it.
        if token in US_STATE_CODES and any(s >= a and e <= b for a, b in address_spans):
            out.append(Resolution(
                token, s, e, Sense.IDENTIFIER,
                spec["identifier"] if spec else f"US state ({token})", 0.95,
                "appears inside a city, ST ZIP address frame",
            ))
            continue

        if spec is None:
            continue

        # 1b. "ID" as IDENTIFICATION — "Member ID:", "Patient ID #",
        #     "photo ID", "ID number". The most common use of the token
        #     in a form, and a sense the table does not hold: without this
        #     it would fall to UNRESOLVED, be redacted as Idaho, and drag
        #     the "Member ID:" field label into the redaction with it.
        if token == "ID" and (_ID_LABEL_BEFORE.search(text[max(0, s - 20):s])
                              or _ID_LABEL_AFTER.match(text[e:e + 16])):
            out.append(Resolution(
                token, s, e, Sense.CLINICAL, "identification (field label)", 0.9,
                "record-keeping word adjacent: an ID label, not Idaho",
            ))
            continue

        win = _window(text, s, e)

        # 2. Geographic framing beats clinical cues — but ONLY when the
        #    cue immediately precedes the token.
        #    A wide symmetric window is wrong here: in
        #    "LA enlargement on echo. Patient resides in CT."
        #    a 60-char window around LA reaches "resides in" at the far
        #    end of the sentence and misreads the left atrium as
        #    Louisiana. Place framing is a LEFT-adjacent construction
        #    ("resides in X", "transferred from X"), so only the three
        #    words immediately before the token are consulted.
        # Geographic framing does not cross a sentence boundary. In
        # "...transferred from Springfield, MA 01103. US abdomen..."
        # the word "from" belongs to the previous sentence and must not
        # make the ultrasound into United States.
        left_raw = text[max(0, s - 40):s]
        left = re.split(r"[.;!?]\s", left_raw)[-1]
        preceding_words = re.findall(r"[A-Za-z]+", left)[-3:]
        if preceding_words and GEO_CONTEXT.search(" ".join(preceding_words)):
            out.append(Resolution(
                token, s, e, Sense.IDENTIFIER, spec["identifier"], 0.85,
                "geographic framing immediately before token",
            ))
            continue

        # 3. Clinical cues, weighted toward the right-adjacent word.
        #    Clinical shorthand modifies what follows it ("CT head",
        #    "US abdomen", "LA enlargement"), so the token immediately
        #    after carries the most signal.
        right = text[e:e + 40]
        next_word = (re.findall(r"[A-Za-z]+", right) or [""])[0].lower()
        hits = [c for c in spec["clinical_cues"] if re.search(rf"\b{re.escape(c)}\b", win)]
        if next_word in spec["clinical_cues"]:
            out.append(Resolution(
                token, s, e, Sense.CLINICAL, spec["clinical"], 0.9,
                f"clinical term immediately follows: {next_word}",
            ))
            continue
        if hits:
            out.append(Resolution(
                token, s, e, Sense.CLINICAL, spec["clinical"],
                min(0.6 + 0.1 * len(hits), 0.95),
                f"clinical context: {', '.join(hits[:3])}",
            ))
            continue

        # 4. Cannot place it — fail toward redaction
        out.append(Resolution(
            token, s, e, Sense.UNRESOLVED, spec["identifier"], 0.4,
            "no disambiguating context; defaulting to redaction",
        ))

    return out


def complexity_score(text: str) -> float:
    """How much shorthand is in here? 0.0 = plain prose, 1.0 = dense.

    Used to gate the resolver: plain prose skips it entirely. Three
    signals, each normalized and capped so no single one dominates:
    shorthand markers, ambiguous two-letter tokens, and overall
    abbreviation density.
    """
    words = re.findall(r"\S+", text)
    if not words:
        return 0.0
    n = len(words)

    shorthand = len(SHORTHAND_MARKERS.findall(text))
    ambiguous = sum(
        1 for m in re.finditer(r"\b([A-Z]{2})\b", text)
        if m.group(1) in AMBIGUOUS_TOKENS
    )
    all_caps = len(re.findall(r"\b[A-Z]{2,6}\b", text))

    score = (
        min(shorthand / n * 6, 0.4)
        + min(ambiguous * 0.2, 0.4)
        + min(all_caps / n * 2, 0.2)
    )
    return round(min(score, 1.0), 3)


COMPLEXITY_THRESHOLD = 0.15


def needs_translation(text: str) -> bool:
    """Gate. Plain prose takes the fast path."""
    return complexity_score(text) >= COMPLEXITY_THRESHOLD


def clarify(text: str) -> tuple[str, list[Resolution]]:
    """OPT-IN: return a copy with clinical abbreviations expanded.

    Off by default and not used in the redaction path. A wrong
    expansion silently changes clinical meaning, so a caller that wants
    this must ask for it and must log it; this function logs nothing
    itself. Identifier-sense and
    unresolved tokens are never expanded — only the confidently
    clinical ones.
    """
    resolutions = resolve(text)
    out = text
    for r in sorted(resolutions, key=lambda r: r.start, reverse=True):
        if r.sense is Sense.CLINICAL and r.confidence >= 0.7:
            out = out[:r.start] + f"{r.token} ({r.expansion})" + out[r.end:]
    return out, resolutions
