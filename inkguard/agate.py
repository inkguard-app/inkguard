"""
Final check that every word left in a note is accounted for: a note has
no flags only if every word left in it is one the tool can name.

Rule: a word the tool cannot name means it cannot judge whether that word
identifies someone, so the note is flagged for a person to review.

Every other layer asks "is this PHI?". This one asks the opposite
question about everything that is LEFT: "do I know what this is?" A word
left in the output must have a meaning the tool can name:

  an English word          wordfreq / WordNet
  a clinical word          the open clinical vocabulary, chart shorthand,
                           drug and device names, medical word-building
  a number or measurement  values, doses, levels, scores — short numbers
  a code the tool knows    "T2", "L4-5", "q6h", "A1c", "R07.9"
  a placeholder            [NAME], [DATE], a generalised ZIP "552**"

What it cannot name is NOT UNDERSTOOD, and one such word is enough to flag
the note for review:

  unknown_word      a word no vocabulary holds ("Wrasmann", "Kovic")
  name_left_in      a word in the name registry, left in, with no clinical
                    reason in its context ("Mayberry", mid-sentence)
  possible_person   an UNSURE PERSON: a word read as a person only from how
                    it is used ("asked by Cseh ortho", "Hurt (hospitalist)")
  lowercase_name    a name-registry word written in lowercase, rare as
                    an English word ("ybarra")
  unknown_acronym   capitals no list explains — possibly initials ("JKD")
  unknown_code      letters and digits that look like an identifier
                    ("XK4471")
  long_number       seven or more digits nothing explained
  other_script      text in a script the English vocabulary cannot read
  initial_by_name   a lone initial beside a removed name ("J. [NAME]")
  date_left_in      a day and month left in ("March 14", "on 4/16")
  place_left_in     a city, town or county left in ("in Waco") — the tool
                    knows the word, and knows it is a place
  name_also_clinical  a word kept as clinical ("Babinski sign") that is also
                    a name removed elsewhere in the note. LISTED, NOT BLOCKING.
  unsure_removal    a word the tool removed without being sure it was PHI
                    (the [NAME] safety net). LISTED, NOT BLOCKING: it is
                    already gone, and over-redaction is the safer error.
  spelling_slip     a word one keystroke from a known word ("pateint").
                    LISTED, NOT BLOCKING (see below).
  gate_error        this check itself failed; the note is flagged.

The words themselves never leave this module in any log: only offsets and
reason codes. The review screen, which runs on the practice's computer,
reads the words from the note by offset.
"""
from __future__ import annotations

import functools
import re
from collections import Counter
from dataclasses import dataclass, field

# A SPELLING SLIP ("pateint", "hemorrage") is one keystroke from a word the
# tool knows. It does not hold the note on its own (clinicians misspell
# constantly, and a held pile everyone clicks through protects no one), but
# it is never silent either: it is listed, it is a question each reviewer
# must answer in the review workflow, and the count goes on the review
# report. A slip where a name would go ("Dr. Smitth")
# is read as a person and does hold the note.
# A PATIENT'S NAME KEPT AS A CLINICAL WORD ("Pt: [NAME]. Babinski sign
# present", the patient being Mr Babinski): the reading is right, so it does
# not hold the note, but both reviewers are asked to look.
NON_BLOCKING = frozenset({"unsure_removal", "spelling_slip", "name_also_clinical"})
REASONS = ("unknown_word", "name_left_in", "possible_person", "lowercase_name", "unknown_acronym",
           "unknown_code", "long_number", "other_script", "initial_by_name",
           "date_left_in", "place_left_in", "unsure_removal", "gate_error", "spelling_slip",
           "name_also_clinical")


@dataclass(frozen=True)
class NotUnderstood:
    start: int
    end: int
    reason: str

    def to_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "reason": self.reason}


@dataclass
class Comprehension:
    words: int = 0
    not_understood: list = field(default_factory=list)

    @property
    def cleared(self) -> bool:
        return not self.blocking

    @property
    def blocking(self) -> list:
        """Words LEFT IN that the tool cannot name. A word the tool removed
        without being sure is already gone: it is listed for the review
        screen but does not stop the note."""
        return [n for n in self.not_understood if n.reason not in NON_BLOCKING]

    def summary(self) -> dict:
        return {"words": self.words, "not_understood": len(self.blocking),
                "removed_unsure": len(self.not_understood) - len(self.blocking),
                "reasons": dict(Counter(n.reason for n in self.not_understood)),
                "cleared": self.cleared}


# ---------------------------------------------------------------------
# vocabularies (lazy; all built from lists the tool already ships)
# ---------------------------------------------------------------------

def _zipf(w: str) -> float:
    from inkguard.garnet import _zipf as z
    return z(w)


@functools.lru_cache(maxsize=None)
def _names() -> frozenset:
    from inkguard.name_registry import NAMES
    return NAMES


@functools.lru_cache(maxsize=None)
def _extra():
    """ALL SUBSPECIALTIES (tools/build_gate_vocab.py): words of every NCIt
    branch but places and organisations, EFO and the Cell Ontology, minus
    every name; their eponyms apart; the abbreviation Meta-Inventory; and
    instrument eponyms from FDA device brands."""
    from inkguard.lexicon_gate import (EXTRA_VOCAB, EXTRA_EPONYMS, INVENTORY_ABBREVIATIONS,
                                  DEVICE_EPONYMS)
    return EXTRA_VOCAB, EXTRA_EPONYMS, INVENTORY_ABBREVIATIONS, DEVICE_EPONYMS


@functools.lru_cache(maxsize=None)
def _known_lower() -> frozenset:
    """Lowercase words the tool can name: WordNet words that are nobody's
    name, the most frequent English words, the clinical vocabulary and the
    all-subspecialty vocabulary."""
    return _known_lower_base() | _extra()[0]


@functools.lru_cache(maxsize=None)
def _known_lower_base() -> frozenset:
    """The same without the all-subspecialty vocabulary: what a typo is
    measured against (90,000 rare terms would make almost any rare word
    "one slip away" from something)."""
    from inkguard.lexicon_english import ENGLISH_WORDS
    from inkguard.clinical_lexicon import ICD_VOCAB, _ALL_CLINICAL_TERMS
    words = set(ENGLISH_WORDS) | set(ICD_VOCAB) | {t for t in _ALL_CLINICAL_TERMS if " " not in t}
    try:
        from wordfreq import top_n_list
        words |= {w for w in top_n_list("en", 60000) if w.isalpha()}
    except Exception:
        pass
    return frozenset(words)


def _clinical(word: str) -> bool:
    from inkguard.clinical_lexicon import is_clinical_term, is_vocabulary_word
    from inkguard.garnet import medical_morphology
    return is_clinical_term(word) or is_vocabulary_word(word) or medical_morphology(word)


_ALPHA = "abcdefghijklmnopqrstuvwxyz"

from contextvars import ContextVar  # noqa: E402
# Words the patient's whole file explains (inkguard/pumice.py), for the
# duration of one assess() call.
_FILE_WORDS: ContextVar[frozenset] = ContextVar("file_words", default=frozenset())
# The Limited Data Set profile keeps dates and towns (45 CFR 164.514(e)).
_LDS: ContextVar[bool] = ContextVar("lds", default=False)
_MONTH_WORD = re.compile(r"(?i:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
                         r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")


def _typo_of_known(low: str) -> bool:
    """One slip of the keyboard away from a known word of 5+ letters:
    "pateint", "abdomin", "hemorrage". Lowercase only — a capitalised
    near-miss of a word is as likely to be a surname ("Parrish")."""
    # No real word is longer than about 45 letters, and every variant of a
    # long token is built in memory: a 9,000-letter run (text with its
    # spaces lost) would need several gigabytes of memory.
    if len(low) < 5 or len(low) > 45 or not low.isalpha():
        return False
    known = _known_lower_base()
    cands = set()
    for i in range(len(low) + 1):
        if i < len(low) and len(low) - 1 >= 5:
            cands.add(low[:i] + low[i + 1:])
        if i < len(low) - 1:
            cands.add(low[:i] + low[i + 1] + low[i] + low[i + 2:])
        for c in _ALPHA:
            if i < len(low) and c != low[i]:
                cands.add(low[:i] + c + low[i + 1:])
            cands.add(low[:i] + c + low[i:])
    cands.discard(low)
    if cands & known:
        return True
    # "urospetic" -> "uroseptic" (uro + septic): a slip away from a word
    # the tool can BUILD, checked only for the swapped-letter slips.
    swaps = {low[:i] + low[i + 1] + low[i] + low[i + 2:] for i in range(len(low) - 1)}
    return any(_derives(c, _known_word, compounds=False) for c in swaps)


# ---------------------------------------------------------------------
# token classification
# ---------------------------------------------------------------------

_TOKEN = re.compile(r"[^\W_][\w'’.\-/]*[^\W_]|[^\W_]")
_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z ]{1,20}\]|\d{3}\*\*")
_NUMBER = re.compile(r"^\d+(?:[.,:/\-]\d+)*$")
_UNITS = frozenset("""mg mcg ug g kg ml l dl cc cm mm m km in ft lb lbs oz h hr hrs min s sec
    d wk wks mo mos y yr yrs yo x mmhg cmh2o mmh2o bpm meq mmol mol iu u units unit pm am st nd rd th s
    fr ga gauge mm3 ul mcl dl ng pg mcg/kg mg/kg q qh qd qid tid bid prn po iv im sq sc
    ct mri pet ekg ecg ef ld hd pod dos gcs apgar""".split())
_LOCAL_PHONE_SHAPE = re.compile(r"\d{3}[-.\u2010\u2011\u2013]\d{4}")
# "555 0147": the two halves are separate words; the first is held when
# the second follows it after one space.
_LOCAL_PHONE_SPLIT = re.compile(r"[ \t]\d{4}(?![\d.,/:-])")
_UNIT_AFTER = re.compile(
    r"\s*(?:mg|mcg|ug|µg|g|kg|ml|mL|l|dl|cc|units?|iu|meq|mmol|ng|pg|cells|copies|"
    r"mm|cm|ms|hz|kcal|cal|steps|times|x\b|/|%)", re.IGNORECASE)
_CLINICAL_CODE = re.compile(
    r"^(?:[A-Za-z]{1,3}\d{1,3}(?:\.\d{1,3})?[a-z]?|\d{1,3}[A-Za-z]{1,5}|[A-Za-z]\d{1,2}[A-Za-z]\d?|"
    r"[A-Za-z]{1,5}-?\d{1,3}(?:[-/][A-Za-z]{0,2}\d{1,3})?|\d{1,2}-\d)$")
# Chart shorthand the ontologies do not tag as abbreviations. Gate-only:
# detection never reads this list, so "JP" here cannot shield initials
# from the name layers — it only stops the gate asking about a drain.
GATE_ABBREVIATIONS = frozenset("""
    KUB IVP NKDA NKA EBL EOMI PERRL PERRLA WNL ROM AROM PROM DTR DTRS ORIF CTA CTAB RRR NAD
    BMP CMP CBC UA UCX CXR ABG VBG LFT LFTS TPN PPN PICC NGT OGT JP TTP NT ND BS BSA BMI
    HPI ROS PMH PSH FH SH ADL ADLS IADL AAOX3 AOX3 AAO GCS NIHSS MAP CVP ICP CPP SVR PVR
    POD HD PACU OR ER ED ICU SICU MICU NICU PICU CCU CVICU PT OT SLP RT SW CM DC D/C PRN
    PO IV IM SQ SC SL PR NG GT PEG LR NS D5W TKO KVO QD QOD QHS QAM QPM BID TID QID Q4H
    Q6H Q8H Q12H STAT NPO DNR DNI POLST MOLST HCP POA AMA LWBS SOB DOE PND CP HA LOC
    BRBPR NSVD SVD LTCS CS VBAC EGA GA LMP EDC EDD FHT FHR NST BPP AFI SROM AROM PPROM
    MRN DOB SSN NPI DEA ID MR ACCT DOS MBI HICN RX DX TX HX SX FX CC AP LAT PA ABX
    PIV PIVS WBAT NWB PWB TXA RVR ATFL TKA THA TSA CRNA WOC SBAR RLE LLE RUE LUE PNA IVH CGA
    HCTZ PPX LIMA RIMA SVG LAD LCX RCA PDA OM EF LVEF RVSP TR MR AR AS MS PS VSD ASD PFO
    SSEP SSEPS EMG MEP MEPS PTU PCV PCV13 PCV20 PPSV23 CGC VUS RRSO CBIT SBO AVA SIGNOUT
    BP HR RR SPO SAT UO CR BUN NA CL CO WBC HGB HCT PLT INR PTT AVD RCHOP ANES VSS
""".split()) | frozenset(
    # Neurosurgical care acronyms: scores, devices, operations (lexicon_neuro.py).
    a.upper() for a in __import__("inkguard.lexicon_neuro", fromlist=["x"]).all_acronyms())
# Lowercase chart shorthand, gate-only.
GATE_SHORTHAND = frozenset("""
    mrn dob pt pts yo y/o w/ w/o s/p h/o c/o r/o f/u d/c b/l bilat approx abd ext neg pos
    hx dx tx sx fx rx meds cath exam labs vitals sats temp resp amb assist hosp adm disch
    yest tmrw wk wks mo mos yr yrs hr hrs min mins sec secs incr decr exac rhabdo appy chole
    abx ppx atorva dilt bupi anes lytes endo neuro psych ortho gyn uro nephro heme onc cards
    pulm gi id ent ophtho derm rheum meq pharmd appendicolith hctz sbar wbat nwb pwb ttwb
    dev devt mentating mentation upreg downreg hemolyzed hemolysed burr'd burred proband
    consultand imms vax
""".split())
# Genetic variant notation (HGVS): "c.68_69delAG", "p.Glu23fs", "c.2119T>C".
_HGVS = re.compile(r"^(?:[cgmnpr]\.)[A-Za-z0-9_+\-*>=?()]+$")
_SENTENCE_START = re.compile(r"(?:^|[.!?:;\n]\s*|^\s*[-*•]\s*|\n\s*\d+[.)]\s*)$")


@functools.lru_cache(maxsize=None)
def _gate_vocab():
    from inkguard.lexicon_gate import ABBREVIATIONS, BRAND_WORDS
    return ABBREVIATIONS, BRAND_WORDS


@functools.lru_cache(maxsize=None)
def _drug_brand_names() -> frozenset:
    from inkguard.lexicon_gate import DRUG_BRANDS_THAT_ARE_NAMES
    return DRUG_BRANDS_THAT_ARE_NAMES


def _drug_context(text: str, start: int, end: int) -> bool:
    """Medication talk around a word: a dose, route or frequency, a
    medication word, a switch ("instead of"), or another drug beside it."""
    from inkguard.garnet import _PHARM_WINDOW
    window = text[max(0, start - 60):min(len(text), end + 60)]
    if _PHARM_WINDOW.search(window) or re.search(
            r"\b(?:instead\s+of|switch(?:ed)?\s+to|trial\s+of|allergic\s+to|allergy\s+to|"
            r"tried|try|using|uses|takes|took|stopped|continue|continued)\b", window, re.I):
        return True
    brands = _gate_vocab()[1]
    return any(w.lower() in brands and _clinical(w)
               for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", window)
               if w != text[start:end])


@functools.lru_cache(maxsize=None)
def _name_free() -> frozenset:
    """Words that are in NO name list: WordNet (name-filtered) and brand
    words (name-filtered). The only sets a capitalised word may be derived
    from — the frequency list also holds "smith" and "baker"."""
    from inkguard.lexicon_english import ENGLISH_WORDS
    return frozenset(ENGLISH_WORDS) | _gate_vocab()[1] | _extra()[0]


_PREFIXES = tuple(sorted((
    "anti ante auto bi circum contra counter de dis endo epi ex extra hemi hyper hypo infra "
    "inter intra micro macro mid mini multi neo non over pan para peri poly post pre pro re "
    "retro semi sub super supra trans tri ultra un uni under hemo haemo lympho cysto gastro "
    "entero colo nephro neuro osteo arthro myo cardio angio hepato pneumo pyelo uretero urethro "
    "vesico chole cholecysto laparo thoraco broncho laryngo pharyngo rhino oto ophthalmo dermato "
    "hystero salpingo oophoro orchio orchi prostato spleno pancreato esophago duodeno jejuno ileo "
    "recto procto sigmoido spondylo radio fluoro electro echo vaso veno phlebo arterio thrombo "
    "fibro chondro adeno onco patho psycho immuno glyco lipo cyto histo myelo encephalo cerebro "
    "spino sacro lumbo cervico sero muco uro"
    ).split(), key=len, reverse=True))
_SUFFIX_SWAPS = (("ies", "y"), ("es", ""), ("s", ""), ("ied", "y"), ("ed", ""), ("ed", "e"),
                 ("ing", ""), ("ing", "e"), ("ized", "ize"), ("ized", ""), ("ization", ""),
                 ("ization", "ize"), ("isation", ""), ("ated", "ation"), ("ated", "ate"),
                 ("ically", "ic"), ("ally", "al"), ("ly", ""), ("ness", ""), ("ment", ""),
                 ("able", ""), ("ible", ""), ("ation", "e"), ("ation", ""),
                 ("ic", "y"), ("ic", "e"), ("ic", ""), ("tic", "sis"), ("ist", "y"),
                 ("er", ""), ("er", "e"), ("ae", "a"), ("i", "us"), ("a", "um"),
                 ("al", ""), ("al", "e"), ("ar", "e"), ("ous", ""), ("ous", "e"))


def _derives(low: str, known, depth: int = 0, compounds: bool = True) -> bool:
    """Is this word built from words the tool knows? Inflection
    ("fulgurated", "hemoclipped"), a prefix or medical combining form
    ("midaxillary", "antecolic", "lymphostasis"), or — lowercase only — two
    known words run together ("pursestring")."""
    if known(low):
        return True
    if depth >= 2 or len(low) < 5:
        return False
    for suf, rep in _SUFFIX_SWAPS:
        if low.endswith(suf) and len(low) - len(suf) >= 3:
            stem = low[:-len(suf)] + rep
            if known(stem):
                return True
            if len(stem) >= 4 and stem[-1] == stem[-2] and known(stem[:-1]):   # clipped -> clip
                return True
            if depth == 0 and _derives(stem, known, depth + 1, compounds):
                return True
    for pre in _PREFIXES:
        if low.startswith(pre) and len(low) - len(pre) >= 4:
            if _derives(low[len(pre):], known, depth + 1, compounds):
                return True
    if compounds:
        # Two words run together are judged against the vocabulary WITHOUT
        # the 90,000 rare terms: two short gene symbols make any word.
        part = _known_word_base if known is _known_word else known
        for i in range(4, len(low) - 3):
            if part(low[:i]) and part(low[i:]):
                return True
    return False


_DATE_CUE_BEFORE = re.compile(
    r"\b(?:on|since|until|till|from|through|thru|adm|admit(?:ted)?|seen|visit|appt|appointment|"
    r"dated?|as\s+of|last|next|d/c|discharged?|scheduled|f/u|follow[\s-]?up|return|rtc|due|"
    r"entry|entered|logged|noted|signed|called|recorded|updated|received|reviewed|resulted|"
    r"drawn|done|completed|performed|biopsy|surgery|procedure|labs?|imaging|mri|ct|echo|"
    r"results?|for|between|starting|started|ending|ended|through|to)\.?\s*(?:[:\-–]\s*)?$|[-–]\s*$",
    re.IGNORECASE)
_ORDINAL_CUE_BEFORE = re.compile(
    r"\b(?:the|on|until|till|since|by|from|through|thru|says?|said|probably|maybe|around|about|"
    r"before|after)\s+$", re.IGNORECASE)
_ORDINAL_NOUN_AFTER = re.compile(
    r"\s*(?:degree|line|floor|time|trimester|toe|finger|rib|digit|nerve|metatarsal|metacarpal|"
    r"ventricle|dose|cycle|day|week|year|month|stage|edition|grade|percentile|attempt|round|"
    r"visit|postop|post-op|pod|birthday|of|cranial|lumbar|thoracic|cervical|phalanx|molar|"
    r"tooth|intercostal|generation|century|decade|minute|hour|step|place|choice|opinion|"
    r"pass|look|try|episode|admission|surgery|operation|seizure|dose|course|year)",
    re.IGNORECASE)
_LOWER_TITLE_BEFORE = re.compile(r"\b(?:mr|mrs|ms|mx|dr|miss|sr|sra|fr)\.?[ \t]+$")


def _inflected_common(low: str) -> bool:
    """"asks", "visits", "called", "walking": a common word with an
    inflection. Judged by the stem's frequency, because the English list
    holds nouns only."""
    for suffix in ("s", "es", "ed", "ing", "er"):
        if low.endswith(suffix) and len(low) - len(suffix) >= 3:
            stem = low[:-len(suffix)]
            if _zipf(stem) >= 4.0 or _zipf(stem + "e") >= 4.0:
                return True
    return False


@functools.lru_cache(maxsize=None)
def _english_words() -> frozenset:
    from inkguard.lexicon_english import ENGLISH_WORDS
    return frozenset(ENGLISH_WORDS)


@functools.lru_cache(maxsize=None)
def _name_also() -> frozenset:
    from inkguard.lexicon_english import NAME_ALSO_WORDS
    return NAME_ALSO_WORDS


def _known_word_base(w: str) -> bool:
    return (w in _known_lower_base() or w in _gate_vocab()[1] or _clinical(w) or _zipf(w) >= 3.0)


def _known_word(w: str) -> bool:
    return _known_word_base(w) or w in _extra()[0]


def _known_name_free(w: str) -> bool:
    return w in _name_free() or _clinical(w)


# "ligament of Treitz", "fascia of Denonvilliers", "space of Retzius":
# a capitalised word after an anatomical or clinical noun and "of" is an
# eponym. The engine's other clinical bindings (a clinical noun after it,
# a dose, a device state) are read through garnet.clinically_bound.
_EPONYM_OF = re.compile(
    r"\b(?:ligament|fascia|space|canal|circle|loop|ampulla|sphincter|pouch|triangle|angle|"
    r"foramen|duct|node|line|plexus|crypts?|glands?|islets?|organ|tubercle|fossa|recess|bursa|"
    r"membrane|capsule|nerve|artery|vein|sinus|valve|band|bundle|cells?|disease|syndrome|sign|"
    r"test|maneuver|manoeuvre|procedure|operation|technique|incision|repair|method|position|"
    r"classification|criteria|scale|score|reflex|law|point|fracture|tumou?r)s?\s+of\s+(?:the\s+)?$",
    re.IGNORECASE)


def _knows(low: str) -> bool:
    from inkguard.clinical_lexicon import AMBIGUOUS_EPONYMS
    low = re.sub(r"(?:['’]s|s)$", "", low) if low not in AMBIGUOUS_EPONYMS else low
    return (low in AMBIGUOUS_EPONYMS or low in _gate_vocab()[1] or low in _drug_brand_names()
            or low in _name_also() or low in _name_free() or _zipf(low) >= 3.0
            or low in _extra()[1] or low in _extra()[3])


@functools.lru_cache(maxsize=None)
def _all_bigrams() -> frozenset:
    from inkguard.lexicon_gate import CLINICAL_BIGRAMS
    from inkguard.lexicon_neuro import bigrams
    return CLINICAL_BIGRAMS | bigrams()


def _fold1(c: str) -> str:
    import unicodedata
    d = unicodedata.normalize("NFKD", c)
    return d[0] if len(d) > 1 and unicodedata.combining(d[1]) else c


def _in_clinical_phrase(text: str, start: int, end: int) -> bool:
    """Part of a two-word clinical term with its neighbour: "vena cava",
    "fascia lata", "pars plana" — the pair is in the vocabulary even when
    the single word is not."""
    from inkguard.clinical_context import neuro_phrase_pattern
    lo, hi = max(0, start - 60), min(len(text), end + 60)
    # Accents read away, one character for one ("Frisén" reads "Frisen").
    window = text[:lo] + "".join(_fold1(c) for c in text[lo:hi])
    for m in neuro_phrase_pattern().finditer(window, lo, hi):
        if m.start() <= start and end <= m.end():
            return True                # "Oswestry Disability Index", "S2-alar-iliac screw"
    from inkguard.clinical_context import EPONYM_GRADE_PATTERN
    for m in EPONYM_GRADE_PATTERN.finditer(window, lo, hi):
        if m.start() <= start and end <= m.end():
            return True                # "Lenke 1A", "Knosp 4", "House-Brackmann IV"
    CLINICAL_BIGRAMS = _all_bigrams()
    word = text[start:end].lower()
    prev = re.search(r"([A-Za-z]{3,})[ \t]+$", text[max(0, start - 20):start])
    nxt = re.match(r"^[ \t]+([A-Za-z]{3,})", text[end:end + 20])
    return bool((prev and f"{prev.group(1).lower()} {word}" in CLINICAL_BIGRAMS)
                or (nxt and f"{word} {nxt.group(1).lower()}" in CLINICAL_BIGRAMS))


_ATTRIBUTION_EXTRA = re.compile(
    r"(?i:\b(?:asked|requested|called|paged|texted|notified|informed|signed|seen|evaluated|"
    r"examined|reviewed|discussed|staffed|covered|relieved|ordered|consulted|obtained|given|"
    r"received|provided|reported)\s+(?:by|with|to|from)\s+|\bconsent\s+(?:from|by)\s+)$")
# Things only a PERSON does, right after a word: "Grivet is POD 2",
# "Serral alert and oriented", "Topaz called back". A rare word as the
# subject of one of these is an unsure person, even if it is also a word.
_PERSON_VERB_AFTER = re.compile(
    r"^\s+(?:is|was)?\s*(?:alert|oriented|awake|consented|agrees|agreed|denies|denied|reports|"
    r"reported|states|stated|complains|complained|called\s+back|called|tolerating|tolerated|"
    r"ambulating|ambulated|feels|felt|wants|wanted|prefers|understands|verbalized|verbalizes|"
    r"is\s+POD|is\s+post-?op|is\s+an?\s+\d|was\s+seen|was\s+admitted|was\s+discharged|"
    r"presents|presented|returns|returned|arrived|declined|refused)\b", re.IGNORECASE)


_ROLE_IN_BRACKETS_AFTER = re.compile(
    r"^\s*\((?i:charge(?:\s+(?:nurse|rn))?|rn|lpn|md|do|np|pa|pa-c|crna|attending|resident|fellow|"
    r"intern|scrub|circulator|anesthesia|surgeon|hospitalist|intensivist|pcp|nurse|tech|rt|pt|ot|"
    r"sw|cm|case\s+manager|social\s+work(?:er)?|chaplain|interpreter|pharmacist|son|daughter|"
    r"wife|husband|mother|father|sister|brother|spouse|partner|friend|caregiver|guardian|poa)\)")


def _person_context(text: str, start: int, end: int) -> str:
    """'strong' for a title, a family relation, a possession ("X's wife"),
    a credential after the word or a role in brackets ("Hurt (hospitalist)");
    'weak' for an attribution before it ("asked by X"); '' for none."""
    from inkguard.garnet import (_TITLE_BEFORE, _RELATION_BEFORE, _CREDENTIAL_AFTER,
                           _PERSON_POSSESSION_AFTER, _ATTRIBUTION_BEFORE)
    line0 = text.rfind("\n", 0, start) + 1
    line1 = text.find("\n", end)
    line1 = len(text) if line1 < 0 else line1
    after = text[end:min(end + 30, line1)]
    before25 = text[max(line0, start - 25):start]
    # Same line only: "mother Olya Giambanco\nDate:" is not a relation of "Date".
    if (_TITLE_BEFORE.search(text[max(line0, start - 12):start])
            or _RELATION_BEFORE.search(text[max(line0, start - 40):start])
            or _CREDENTIAL_AFTER.match(after[:16])
            or _PERSON_POSSESSION_AFTER.match(after)
            or _ROLE_IN_BRACKETS_AFTER.match(after)):
        return "strong"
    if _ATTRIBUTION_BEFORE.search(before25) or _ATTRIBUTION_EXTRA.search(before25):
        return "weak"
    return ""


# Where two or three capitals are more likely someone's initials than an
# abbreviation: a sign-off ("-- JKD", "/jd", alone at the end of a line),
# after an attribution ("per KT", "d/w JM"), a title, or before a
# credential ("JKD RN").
_SIGNOFF_BEFORE = re.compile(r"(?:^|[-–—/~]|\b(?:per|by|cc|from|attn|signed|dictated|"
                             r"transcribed|initials?))[ \t:]*$", re.IGNORECASE)


def _initials_spot(text: str, start: int, end: int) -> bool:
    from inkguard.garnet import _ATTRIBUTION_BEFORE, _TITLE_BEFORE, _CREDENTIAL_AFTER
    line0 = text.rfind("\n", 0, start) + 1
    line1 = text.find("\n", end)
    line1 = len(text) if line1 < 0 else line1
    before = text[max(line0, start - 30):start]
    rest = text[end:line1]
    if _CREDENTIAL_AFTER.match(rest[:16]) or _TITLE_BEFORE.search(before[-12:]):
        return True
    if _ATTRIBUTION_BEFORE.search(before) or _ATTRIBUTION_EXTRA.search(before):
        return True
    # Last thing on its line, after a dash, slash or nothing at all.
    return not rest.strip(" \t.,;)") and bool(_SIGNOFF_BEFORE.search(before))


def _inventory_abbreviation(stem: str, text: str, start: int, end: int) -> bool:
    """The Meta-Inventory vouches for this short form, and it is not
    standing where initials stand."""
    if stem not in _extra()[2]:
        return False
    return len(stem) > 3 or not _initials_spot(text, start, end)


def _device_eponym(low: str, text: str, start: int, end: int) -> bool:
    """An instrument eponym ("Allis", "Kerrison", "Castroviejo") in the
    middle of an operation, with no person cue (checked by the caller)."""
    if low not in _extra()[3]:
        return False
    from inkguard.garnet import _PROCEDURAL_WINDOW, _DETERMINER_BEFORE, _FAMILY_AFTER
    if _FAMILY_AFTER.match(text[end:end + 20]):
        return False
    if _DETERMINER_BEFORE.search(text[max(0, start - 6):start]):
        return True
    return bool(_PROCEDURAL_WINDOW.search(text[max(0, start - 80):min(len(text), end + 80)]))


_MICRO = str.maketrans({"\u00b5": "u", "\u03bc": "u"})
_UNIT_EXTRA = frozenset("""gy cgy mgy hz khz mhz ma ua mv uv us um nm ms fr mosm lpm gtt t
    mmol umol nmol pmol mcl ul ml l dl kg g mg ug mcg ng pg iu u units unit meq cm mm m
    km in min hr hrs h s sec d wk mo yr kcal cal mmhg cmh2o mmh2o bpm psi atm c degc degf
    cm2 mm2 m2 cm3 mm3 kpa pa mph""".split())


def _unit_token(bare: str) -> bool:
    """A unit or a compound of units: "cmH2O", "µs", "mcg/kg/min", "µC/cm2"."""
    low = bare.translate(_MICRO).lower()
    parts = [p for p in re.split(r"[/·.*^]", low) if p]
    if not parts:
        return False
    return all(re.sub(r"\d+$", "", p) in _UNIT_EXTRA or p in _UNITS for p in parts)


def _camel_known(bare: str) -> bool:
    """Run-together clinical shorthand: "cEEG", "mJOA", "iNPH", "NeuroICU",
    "AComm". Every hump must be a known word, a known acronym, or a one- or
    two-letter lowercase modifier."""
    abbreviations, _brands = _gate_vocab()
    humps = re.findall(r"[a-z]+|[A-Z]{2,}(?![a-z])|[A-Z][a-z]+|[A-Z]", bare)
    if len(humps) < 2 or "".join(humps) != bare:
        return False
    def known(h: str, first: bool) -> bool:
        if h.isupper() and len(h) >= 2:
            return h in abbreviations or h in GATE_ABBREVIATIONS
        if h.islower() and first and len(h) <= 2:
            return True
        return _clinical(h.lower()) or _zipf(h.lower()) >= 3.5
    return all(known(h, i == 0) for i, h in enumerate(humps))


def _date_element_left(bare: str, text: str, start: int, end: int) -> bool:
    """A day and month with no year ("4/16", "10/07") beside a date cue or
    in a range ("3/21 - 4/2"), or a day written as an ordinal beside a cue
    ("until the 20th", "says 3rd"). A fraction ("1/2 tab"), a score ("4/10
    pain"), "3rd degree", "1st line" and "5th toe" are not."""
    before = text[max(0, start - 28):start]
    after = text[end:end + 30]
    if re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)", bare, re.IGNORECASE):
        return bool(_ORDINAL_CUE_BEFORE.search(before[-16:])
                    and not _ORDINAL_NOUN_AFTER.match(after))
    if re.fullmatch(r"(?:1[0-2]|0?[1-9])/(?:3[01]|[12]\d|0?[1-9])", bare) \
            and not re.match(r"\s*/", after[:2]):
        return (not re.match(r"\s*(?:tabs?|tablets?|caps?|cups?|tsp|tbsp|of\b|x\b|inch|in\b|"
                             r"strength|dose|pain|/10|scale|out\s+of)", after, re.IGNORECASE)
                and not re.search(r"\bpain\b[^\n]{0,12}$", before, re.IGNORECASE)
                and bool(_DATE_CUE_BEFORE.search(before)
                         or re.match(r"\s*[-–]\s*\d{1,2}/\d{1,2}\b", after)))
    return False


def _word_understood(tok: str, text: str, start: int, end: int) -> str | None:
    """None if understood, otherwise the reason."""
    from inkguard.talc import NON_LATIN_RE
    bare0 = tok.strip(".'’-/")
    if _date_element_left(bare0, text, start, end):
        return "date_left_in"
    after_number = bool(re.search(r"\d[ \t]?$", text[max(0, start - 3):start]))
    if bare0 and _unit_token(bare0) and (after_number or any(c.isdigit() for c in bare0)
                                         or bare0.translate(_MICRO) != bare0):
        return None                                # "60 µs", "10 cmH2O", "60 Gy"
    if NON_LATIN_RE.search(tok):
        return "other_script"
    # Accents: "Frisén", "Brown-Séquard" are read without them; a name with
    # accents is still a name ("Narváez" folds to a registry name).
    import unicodedata
    folded = "".join(c for c in unicodedata.normalize("NFKD", tok) if not unicodedata.combining(c))
    if folded != tok and folded.isascii():
        return _word_understood(folded, text, start, end)
    bare = tok.strip(".'’-/")
    if not bare:
        return None
    low = bare.lower()
    abbreviations, brands = _gate_vocab()
    from inkguard.lexicon_neuro import all_acronyms
    if bare in all_acronyms():                         # "AComm", "mJOA", "cEEG"
        return None
    if not bare.isupper() and not bare.islower() and not bare.istitle() and _camel_known(bare):
        return None

    has_digit = any(c.isdigit() for c in bare)
    has_alpha = any(c.isalpha() for c in bare)
    if has_digit and not has_alpha:
        # A local phone number has the shape 555-0147. Detection takes it only
        # beside a contact word (it could be a dose range), so when nothing
        # explains it the note is held ("Called HR 555-0147").
        # A unit after it ("500-1000 mg") explains it as a range.
        if _LOCAL_PHONE_SHAPE.fullmatch(bare) and not _UNIT_AFTER.match(text[end:end + 16]):
            return "long_number"
        if (len(bare) == 3 and bare.isdigit() and _LOCAL_PHONE_SPLIT.match(text[end:end + 6])
                and not re.search(r"\d[ \t]*$", text[max(0, start - 3):start])
                and not _UNIT_AFTER.match(text[end + 5:end + 21])):
            return "long_number"
        # The longest run of digits: "1900-0700" (a shift) is two times.
        if max(len(r) for r in re.findall(r"\d+", bare)) >= 7:
            return "long_number"
        # Hyphen-joined digit groups with no label ("7710-4426", "0093-77400"):
        # a record or plan number nobody labelled. A year range ("2071-2073"),
        # a dose range with a unit ("500-1000 mg") and a shift ("1900-0700",
        # two four-digit times) are not.
        groups = re.findall(r"\d+", bare)
        if (re.fullmatch(r"\d+(?:[-–]\d+)+", bare) and sum(map(len, groups)) >= 6
                and not all(len(g) == 4 and g[:2] in ("19", "20") for g in groups)
                and not all(len(g) == 4 and int(g) < 2400 for g in groups)
                and not _UNIT_AFTER.match(text[end:end + 16])):
            return "long_number"
        return None
    if has_digit:
        if _CLINICAL_CODE.match(bare) or bare.upper() in abbreviations or _HGVS.match(bare):
            return None
        # Codes clinicians write: GCS components ("E3VTM5"), ICD-10 with an
        # extension ("M80.08XA"), CPT with a modifier ("64721-LT"),
        # antibodies ("Anti-GQ1b", "AQP4-IgG"), blood gases ("PaCO2").
        if re.fullmatch(r"E[1-4]V(?:[1-5]|T|NT)M[1-6]|[A-TV-Z]\d{2}(?:\.[0-9A-Z]{1,4})?|"
                        r"\d{5}-[A-Z0-9]{2}|(?i:anti)-[A-Za-z0-9]{2,8}|[A-Za-z0-9]{2,8}-Ig[AGMEDY]|"
                        r"Pa(?:CO|O)2|EtCO2|SpO2|FiO2|ScvO2|SjvO2|PbtO2", bare):
            return None
        parts = re.findall(r"[A-Za-z]+|\d+", bare)
        letters = [p.lower() for p in parts if p.isalpha()]
        digits = [p for p in parts if p.isdigit()]
        # Letters and digit groups joined by hyphens with four or more
        # digits in all ("DL-59-2214", "TI-5513", "231-D-4402"): an
        # unlabelled code. Short clinical shapes ("T12-L1", "CA-125",
        # "COVID-19") have fewer digits and pass below.
        if ("-" in bare and len(digits) >= 1 and sum(map(len, digits)) >= 4
                and not re.fullmatch(r"[A-Za-z]{1,2}\d{1,2}[-–][A-Za-z]{0,2}\d{1,2}", bare)):
            return "unknown_code"
        if all(l in _UNITS or _clinical(l) or _zipf(l) >= 3.5 or l.upper() in abbreviations
               for l in letters) and all(len(d) <= 4 for d in digits):
            return None
        return "unknown_code"
    if len(bare) == 1:
        return None

    if low in GATE_SHORTHAND:
        return None
    # Contractions: "I'm", "don't", "we'll" — the base word is what counts.
    m = re.fullmatch(r"([A-Za-z]+)['’](m|re|ve|ll|d|t)", bare)
    if m:
        return None if (m.group(1).lower() in ("i", "we", "you", "they", "he", "she", "it", "don",
                                               "can", "won", "isn", "wasn", "didn", "doesn",
                                               "couldn", "shouldn", "wouldn", "aren", "weren",
                                               "hasn", "haven", "hadn", "ain", "that", "there")
                        or _zipf(m.group(1).lower()) >= 4.0) else "unknown_word"
    if "." in bare:                                # "p.r.n", "b.i.d", "a.m"
        undotted = bare.replace(".", "")
        if undotted.isalpha() and (_clinical(undotted.lower()) or undotted.upper() in GATE_ABBREVIATIONS
                                   or _zipf(undotted.lower()) >= 3.0):
            return None
    capital = bare[:1].isupper()
    has_upper = any(ch.isupper() for ch in bare)
    from inkguard.garnet import clinically_bound
    # A title, a relation or a credential marks a person — for a Title-case
    # word. "MS" after "with" is multiple sclerosis, not Ms.
    if capital and not bare.isupper():
        ctx = _person_context(text, start, end)
        if ctx == "strong" or (ctx == "weak" and _zipf(low) < 4.0):
            # A registry name, or a word read as a person ONLY from how it
            # is used (an "unsure person").
            return "name_left_in" if low in _names() else "possible_person"
        # A rare capitalised word doing what only people do.
        if (_zipf(low) < 3.5 and _PERSON_VERB_AFTER.match(text[end:end + 30])
                and not _clinical(low) and not _clinical(bare)):
            return "name_left_in" if low in _names() else "possible_person"
    # A month inside a date the Limited Data Set keeps: "Jun 9", "14 Sep".
    if (_LDS.get() and _MONTH_WORD.fullmatch(bare)
            and (re.match(r"^\.?,?[ \t]*\d", text[end:end + 5])
                 or re.search(r"\d(?:st|nd|rd|th)?[ \t]+(?:of[ \t]+)?$", text[max(0, start - 10):start]))):
        return None
    # The whole word first: "Steri-Strips", "non-Hodgkin lymphoma".
    if _clinical(bare) or _clinical(low) or _in_clinical_phrase(text, start, end):
        return None
    # The patient's own file uses this word clinically ("Pfannenstiel
    # incision" in the op note explains "Pfannenstiel" in the discharge).
    if re.sub(r"['’]s$", "", low) in _FILE_WORDS.get():
        return None
    # A clinical noun after a word ("Foley catheter") explains a word the
    # tool KNOWS as an eponym, device, brand or dictionary word. It does
    # not explain an unknown word: "asked by Cseh ortho" is a person.
    if has_upper and clinically_bound(text, start, end) and _knows(low):
        return None
    if capital and _EPONYM_OF.search(text[max(0, start - 40):start]):
        return None

    # Hyphenated or slashed compounds: every part must be understood.
    pieces = [p for p in re.split(r"[-/]", bare) if p]
    if len(pieces) > 1:
        for p in pieces:
            r = _word_understood(p, text, start, end)
            if r:
                return r
        return None

    word = bare.replace("’", "'")
    if word.endswith("'s"):
        word = word[:-2]
        low = word.lower()
    if len(word) < 2:
        return None

    if word.isupper() or (word[:-1].isupper() and word.endswith("s") and len(word) >= 3):
        stem = word[:-1] if (word.endswith("s") and not word.isupper()) else word
        if (stem in abbreviations or stem in GATE_ABBREVIATIONS
                or _clinical(stem) or _clinical(stem.lower())
                or _zipf(stem.lower()) >= 3.0 or stem.lower() in _known_lower_base()
                or stem.lower() in _UNITS):
            return None
        # The Meta-Inventory (clinical-note sources) — but never for two or
        # three capitals where initials stand; nor the rare-term vocabulary
        # for a short all-caps token ("KDR" is a gene AND someone's initials).
        if _inventory_abbreviation(stem, text, start, end):
            return None
        if len(stem) > 3 and stem.lower() in _extra()[0]:
            return None
        return "unknown_acronym"

    if word[:1].islower():
        # A lowercase word that is both a name and a dictionary word
        # ("stapler", "gauze") is read in its dictionary sense. A name that
        # is not a dictionary word ("garcia", "nguyen", "patel") is a name
        # however common it is in text: common surnames are frequent words
        # precisely because they are common surnames.
        # A function word that happens to be a rare surname ("her", "will")
        # is read as the word, and so is an inflected common verb ("asks",
        # "visits"): the English list holds nouns, so verbs are judged by
        # the frequency of their stem. After a lowercase title ("mrs lee")
        # a listed name is a name whatever its frequency.
        if low in _names() and low not in _name_also() and not _clinical(low) and low not in _english_words():
            titled = _LOWER_TITLE_BEFORE.search(text[max(0, start - 12):start])
            if titled or (_zipf(low) < 4.5 and not _inflected_common(low)
                          and not _derives(low, _known_name_free, compounds=False)):
                return "lowercase_name"
        if _zipf(low) >= 2.0 or _derives(low, _known_word):
            return None
        if _typo_of_known(low):
            return "spelling_slip"
        return "unknown_word"

    # Capitalised (or mixed case) word.
    from inkguard.clinical_lexicon import AMBIGUOUS_EPONYMS
    sentence_start = bool(_SENTENCE_START.search(text[max(0, start - 6):start]))
    # A field label at the start of a line: "Date:", "Dev:", "Imms:".
    line_start = text[text.rfind("\n", 0, start) + 1:start].strip(" \t-*•") == ""
    if (line_start and text[end:end + 1] == ":" and
            (low in _name_also() or _zipf(low) >= 3.0 or low in GATE_SHORTHAND)):
        return None
    if low in _names():
        if _zipf(low) >= 4.0:                         # "Will", "May", "Long"
            return None
        # "the Foley", "an Allis": an instrument named after a person is
        # a thing once it takes an article (and is not "the Smith family").
        from inkguard.garnet import _DETERMINER_BEFORE, _FAMILY_AFTER
        if ((low in AMBIGUOUS_EPONYMS or low in _drug_brand_names())
                and _DETERMINER_BEFORE.search(text[max(0, start - 6):start])
                and not _FAMILY_AFTER.match(text[end:end + 20])):
            return None
        # "try Zyrtec instead of Allegra": a drug brand that is also a
        # first name is a drug when medication talk is around it.
        if low in _drug_brand_names() and _drug_context(text, start, end):
            return None
        # "Kerrison used to open the foramen", "Allis clamps placed".
        if _device_eponym(low, text, start, end):
            return None
        if sentence_start and low in _name_also():
            return None                               # "Rose to 38.5", "Neck: Supple"
        if _eponym_use(text, start, end):             # "Ponte osteotomies", "Jamar dynamometer"
            return None
        return "name_left_in"
    if (_zipf(low) >= 3.0 or low in _name_free() or low in brands
            or low in _name_also()):                  # "Fascia closed ..."
        return None
    if _device_eponym(low, text, start, end):         # "widened with Kerrison"
        return None
    if _eponym_use(text, start, end):                 # "Ojemann stimulator", "Tumarkin crisis"
        return None
    if low in AMBIGUOUS_EPONYMS:
        return "name_left_in"
    if _derives(low, _known_name_free, compounds=False):
        return None
    return "unknown_word"


_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
          r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")
_DAY = r"(?:0?[1-9]|[12]\d|3[01])(?:st|nd|rd|th)?"
_NOT_A_QUANTITY = r"(?!\s*(?:mg|mcg|ml|units?|%|days?|weeks?|wks?|months?|years?|yrs?|hours?|hrs?|min|cm|mm|kg|lbs?)\b)"
_DATE_MONTH_DAY = re.compile(rf"(?P<d>\b{_MONTH}\.?\s+{_DAY})\b{_NOT_A_QUANTITY}")
_DATE_DAY_OF_MONTH = re.compile(rf"(?P<d>\b{_DAY}\s+(?:of\s+)?{_MONTH})\b")
_DATE_MONTH_YEAR = re.compile(rf"(?P<d>\b{_MONTH}\.?,?\s+(?:19|20)\d\d)\b")
_DATE_CUED_MD = re.compile(
    r"(?i:\b(?:on|since|seen|dated|from|until|till|by|starting|started|scheduled|f/u|follow[- ]?up|"
    r"appt|appointment|visit|admitted|discharged|line|called|call|surgery|procedure|dos|"
    r"signout|sign-?out|note|update|addendum|reassess|re-?check|re-?eval(?:uate|uation)?|"
    r"repeat|redraw|due|next|last)\s+)"
    r"(?P<d>(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01]))\b(?!/|\.\d)")
_DATE_MD_TIME = re.compile(
    r"(?P<d>(?<![\d/.])(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01]))\s+(?:\d{1,2}:\d{2}|(?:[01]\d|2[0-3])[0-5]\d)\b")
_DATE_MD_PAREN = re.compile(
    r"(?i:\b(?:s/p|status\s+post|prior|previous|last|since|on|from|dated|done|performed|"
    r"admitted|discharged|surgery|procedure|visit|seen)\b)[^()\n]{0,40}"
    r"\((?P<d>(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01]))\)")
_CAP_RUN = re.compile(r"[A-Z][A-Za-z'’.-]*(?:[ \t]+[A-Z][A-Za-z'’.-]*){0,2}")
_LOCATIVE_BEFORE = re.compile(
    r"(?i:\b(?:in|from|to|near|at|of|outside|around|lives?|living|moved|visiting|born|"
    r"relocated|resides?|residing|travel(?:l?ed)?|drove|driving|trip)\s+(?:the\s+)?)$")


@functools.lru_cache(maxsize=None)
def _places() -> frozenset:
    from inkguard.lexicon_gate import PLACES
    return PLACES


_US_STATE = (r"(?:A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|"
             r"O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY]|Alabama|Alaska|Arizona|Arkansas|"
             r"California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|"
             r"Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|"
             r"Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New\s+\w+|North\s+\w+|"
             r"Ohio|Oklahoma|Oregon|Pennsylvania|Rhode\s+Island|South\s+\w+|Tennessee|Texas|"
             r"Utah|Vermont|Virginia|Washington|West\s+Virginia|Wisconsin|Wyoming)")
_STATE_AFTER = re.compile(rf"^,?[ \t]+{_US_STATE}\b")
_STRONG_LOCATIVE = re.compile(
    r"(?i:\b(?:in|near|outside|around|lives?\s+in|living\s+in|moved\s+to|relocated\s+to|"
    r"resides?\s+in|residing\s+in|born\s+in|visiting|travel(?:l?ed)?\s+to|drove\s+to|"
    r"driving\s+to|trip\s+to|home\s+in|native\s+of)\s+(?:the\s+)?)$")


_ZIP_AFTER = re.compile(r"^[ \t]*[,/|]?[ \t]*\d{5}(?:-\d{4})?\b")
_STREET_BEFORE = re.compile(
    r"(?i:\b(?:st|street|ave|avenue|rd|road|blvd|boulevard|ln|lane|way|dr|drive|ct|court|pl|"
    r"place|ter|terrace|cir|circle|hwy|highway|pkwy|parkway|apt\s*\w+|unit\s*\w+)\.?[ \t]*[,/|][ \t]*)$")
_TRAVEL_BEFORE = re.compile(
    r"(?i:\b(?:travel(?:l?ing|l?ed)?|flew|flying|visiting|drove|driving|came|coming|moved|"
    r"relocated|transferred)\s+(?:in\s+)?(?:from|to)\s+)$")


_RELATION_RIGHT_BEFORE = re.compile(
    r"(?i:\b(?:wife|husband|son|daughter|mother|father|mom|dad|brother|sister|spouse|partner|"
    r"fianc[eé]e?|guardian|friend|neighbou?r|niece|nephew|aunt|uncle|grand(?:son|daughter|"
    r"mother|father|child)|cousin|boyfriend|girlfriend|caregiver|roommate|parent)(?:'s)?)[ \t,:]*$")


def _person_right_here(text: str, start: int, end: int) -> bool:
    """A person cue touching the word itself — the town test must not read
    "daughter Isel in Hickory" as Hickory being the daughter."""
    from inkguard.garnet import (_TITLE_BEFORE, _CREDENTIAL_AFTER, _PERSON_POSSESSION_AFTER,
                           _ATTRIBUTION_BEFORE)
    before = text[max(0, start - 30):start]
    after = text[end:end + 30]
    return bool(_TITLE_BEFORE.search(before[-12:]) or _RELATION_RIGHT_BEFORE.search(before)
                or _CREDENTIAL_AFTER.match(after[:16]) or _PERSON_POSSESSION_AFTER.match(after)
                or _ROLE_IN_BRACKETS_AFTER.match(after) or _ATTRIBUTION_BEFORE.search(before)
                or _ATTRIBUTION_EXTRA.search(before) or _PERSON_VERB_AFTER.match(after))


def _kept_town(text: str, start: int, end: int) -> bool:
    """LDS only: is this a town the profile may keep — no person cue, and
    clear place evidence: a state or a ZIP code after it, a street before
    it, or a clearly locative phrase ("lives in", "traveling from")."""
    if _person_right_here(text, start, end):
        return False
    before = text[max(0, start - 30):start]
    return bool(_STATE_AFTER.match(text[end:end + 30]) or _ZIP_AFTER.match(text[end:end + 14])
                or _STREET_BEFORE.search(before) or _STRONG_LOCATIVE.search(before[-24:])
                or _TRAVEL_BEFORE.search(before))


_EPONYM_NOUNS = frozenset("""
    osteotomy osteotomies transfer transfers dynamometer monofilament monofilaments fingers
    endoscope rod rods field fields curve curves angle angles test tests sign signs tube tubes
    catheter catheters plaque plaques criteria phenomenon stain reaction procedure procedures
    prosthesis classification grade grades scale scales score scores syndrome disease view
    views technique approach incision flap clamp retractor dissector elevator rongeur forceps
    scissors needle drain shunt valve frame pins holder table tongs malformation fissure
    aqueduct foramen triangle line lines point points reflex maneuver manoeuvre position
    questionnaire inventory index ratio cyst tumor tumour fracture palsy gait collar brace
""".split())


def _eponym_use(text: str, start: int, end: int) -> bool:
    """A capitalised word used as an eponym: a clinical noun follows within
    two words ("Ponte osteotomies", "Jamar dynamometer", "Semmes-Weinstein
    monofilament", "Cobb angle", "Dawson fingers"). The person cues are
    checked before this is asked."""
    from inkguard.clinical_lexicon import DISEASE_QUALIFIERS
    nxt = re.findall(r"[A-Za-z][A-Za-z'’-]*", text[end:end + 40])[:3]
    for i, w in enumerate(nxt):
        wl = w.lower()
        if wl in _EPONYM_NOUNS or wl in DISEASE_QUALIFIERS:
            return True
        # one connecting word may stand between: "Ponte type osteotomies",
        # "Semmes-Weinstein 5.07 monofilament"
        if i >= 1:
            break
    return False


def _place_reading(text: str, start: int, end: int, name: str, ntoks: int) -> bool:
    """Is this capitalised run being used as a place name here?"""
    from inkguard.garnet import clinically_bound
    from inkguard.clinical_lexicon import AMBIGUOUS_EPONYMS
    raw = text[start:end]
    if raw.isupper():                                # "LIMA" is an artery
        return False
    if _eponym_use(text, start, end):                # "Cobb angle", "Humphrey field"
        return False
    if clinically_bound(text, start, end) or (ntoks == 1 and _clinical(name)):
        return False
    from inkguard.garnet import _DETERMINER_BEFORE
    if (ntoks == 1 and (name in AMBIGUOUS_EPONYMS or name in _gate_vocab()[1])
            and _DETERMINER_BEFORE.search(text[max(0, start - 6):start])):
        return False                                 # "the Foley" is not Foley, AL
    before = text[max(0, start - 24):start]
    locative = bool(_LOCATIVE_BEFORE.search(before))
    if ntoks > 1:
        return True                                  # "Fort Wayne", "San Diego"
    if locative:
        return True
    if _SENTENCE_START.search(text[max(0, start - 6):start]):
        return _zipf(name) < 2.5                     # "Normal exam." / "Hope is ..."
    return _zipf(name) < 4.0                         # mid-sentence "Waco", not "Mobile unit"


def failed(why: str) -> Comprehension:
    """This check itself failed: the note is flagged (fail closed). `why`
    is not used; nothing about the failure is recorded here."""
    c = Comprehension(words=0)
    c.not_understood.append(NotUnderstood(0, 0, "gate_error"))
    return c


def assess(text: str, redactions, *, generalized_spans=(), lds: bool = False,
           file_words: frozenset = frozenset()) -> Comprehension:
    """See _assess. `file_words`: words the patient's whole file explains
    (inkguard/pumice.py), in scope for this one call only."""
    token = _FILE_WORDS.set(frozenset(file_words))
    token_lds = _LDS.set(bool(lds))
    try:
        return _assess(text, redactions, generalized_spans=generalized_spans, lds=lds)
    finally:
        _FILE_WORDS.reset(token)
        _LDS.reset(token_lds)


def _assess(text: str, redactions, *, generalized_spans=(), lds: bool = False) -> Comprehension:
    """Judge every word of `text` that is NOT inside a redaction or a Safe
    Harbor generalisation. `redactions` are hits with start/end/layer.
    `lds`: the Limited Data Set profile keeps dates and towns, so those
    are not reasons to hold the note."""
    covered: list[tuple[int, int]] = sorted((h.start, h.end) for h in redactions)
    covered += list(generalized_spans)
    covered.sort()
    starts = [s for s, _ in covered]

    import bisect

    def inside(s: int, e: int) -> bool:
        i = bisect.bisect_right(starts, s)
        for j in range(max(0, i - 3), min(len(covered), i + 2)):
            a, b = covered[j]
            if a <= s and e <= b:
                return True
        return False

    # Only REMOVED spans: "93 y/o F" is an age (generalized) and a sex, not
    # an initial beside a name.
    removed_only = sorted((h.start, h.end) for h in redactions)
    r_starts = [a for a, _ in removed_only]

    def touches(s: int, e: int) -> bool:
        """A lone initial written against a removed span: "J. [NAME]",
        "J [NAME]", "[NAME] J", "[NAME], J." — not "S: [NAME]"."""
        i = bisect.bisect_right(r_starts, e + 3)
        for j in range(max(0, i - 3), min(len(removed_only), i + 1)):
            a, b = removed_only[j]
            if a >= e and re.fullmatch(r"\.?[ \t]?", text[e:a]):
                return True
            if b <= s and re.fullmatch(r",?[ \t]?", text[b:s]):
                return True
        return False

    c = Comprehension()
    claimed: list[tuple[int, int]] = []
    # Words of the names this note removed, for name_also_clinical below.
    removed_names = {w.lower() for h in redactions
                     if getattr(h, "entity_type", "") == "PERSON"
                     for w in re.findall(r"[A-Za-zÀ-ɏ][\w'’-]{3,}", text[h.start:h.end])}

    def free(s: int, e: int) -> bool:
        return not any(a < e and s < b for a, b in covered)

    # DATES. A day-and-month left in is a date element Safe Harbor removes:
    # "March 14", "Mar 14th", "14th of March", "March 2024", and a bare
    # "4/16" where a date cue or a clock time marks it as a date.
    for rx in (() if lds else (_DATE_MONTH_DAY, _DATE_DAY_OF_MONTH, _DATE_MONTH_YEAR,
                                _DATE_CUED_MD, _DATE_MD_TIME, _DATE_MD_PAREN)):
        for m in rx.finditer(text):
            s0, e0 = m.span("d")
            if free(s0, e0) and not any(a < e0 and s0 < b for a, b in claimed):
                c.not_understood.append(NotUnderstood(s0, e0, "date_left_in"))
                claimed.append((s0, e0))

    # PLACES. A city, town or county left in: understood as a place, and a
    # place smaller than a state is an identifier. Common words that are
    # also places ("Normal", "Hope", "Mobile") count only after "in",
    # "from", "near" ... . Under the Limited Data Set a town MAY stay: it
    # is understood (as a place) and does not hold the note — a town that
    # is also a confirmed name was already removed (policy.
    # _kept_type_conflicts).
    for m in _CAP_RUN.finditer(text):
        toks = list(re.finditer(r"[A-Z][A-Za-z'’.-]*", m.group()))
        i = 0
        while i < len(toks):
            hit = None
            for n in (3, 2, 1):
                if i + n > len(toks):
                    continue
                s0 = m.start() + toks[i].start()
                e0 = m.start() + toks[i + n - 1].end()
                name = re.sub(r"\s+", " ", text[s0:e0]).rstrip(".").lower()
                if name in _places() and free(s0, e0) and _place_reading(text, s0, e0, name, n):
                    # Under the LDS a place is KEPT only on clear evidence
                    # (a state after it, "lives in" before it). "Call from
                    # Austin" is as likely a person: not claimed, so the
                    # word is judged like any other word below.
                    if lds and not _kept_town(text, s0, e0):
                        continue
                    hit = (s0, e0, n)
                    break
            if hit:
                if not lds:
                    c.not_understood.append(NotUnderstood(hit[0], hit[1], "place_left_in"))
                claimed.append((hit[0], hit[1]))
                i += hit[2]
            else:
                i += 1

    # Removals the tool was not sure of are not understood either.
    for h in redactions:
        if getattr(h, "layer", "") == "uncertain":
            c.not_understood.append(NotUnderstood(h.start, h.end, "unsure_removal"))

    for m in _TOKEN.finditer(text):
        s, e = m.start(), m.end()
        if inside(s, e):
            continue
        # Clip a token that runs into a redaction ("Smith's" -> "'s").
        tok = m.group()
        if any(a < e and s < b for a, b in covered):
            parts = [(s + p.start(), s + p.end()) for p in re.finditer(r"[^\W_][\w'’.]*", tok)]
            parts = [(a, b) for a, b in parts if not inside(a, b)]
            if not parts:
                continue
            s, e = parts[0]
            tok = text[s:e]
            if not tok.strip(".'’-/"):
                continue
        c.words += 1
        if _PLACEHOLDER.fullmatch(tok) or any(a <= s and e <= b for a, b in claimed):
            continue
        bare = tok.rstrip(".")
        # "I" and "A" are words unless written as initials ("A. [NAME]").
        if (len(bare) == 1 and bare.isupper() and touches(s, e)
                and (bare not in "IA" or tok.endswith("."))):
            c.not_understood.append(NotUnderstood(s, e, "initial_by_name"))
            continue
        reason = _word_understood(tok, text, s, e)
        if reason:
            c.not_understood.append(NotUnderstood(s, e, reason))
        elif bare[:1].isupper() and bare.lower().rstrip("'’s") in removed_names:
            c.not_understood.append(NotUnderstood(s, e, "name_also_clinical"))
    c.not_understood.sort(key=lambda n: n.start)
    return c
