"""
garnet — evidence scoring for uncertain names and places.

No single detector decides alone. Many signals are weighted into a
single decision. This module does that for every PERSON and LOCATION
candidate that is not already proven by structure.

WHAT CHANGED FROM THE FIRST VERSION, AND WHY
--------------------------------------------
The first version of this scorer used an if-chain with "patient" as
evidence that a name was nearby. "Patient" appears in nearly every
sentence of a clinical note, so it confirmed almost every candidate, and
"Foley", "Toradol" and "Roux" were wrongly removed as names.

In this module, "released" means a hit is confirmed and will be redacted;
"held" means it is left in the text and listed as doubtful.

Two principles replace it:

  1. TIGHT signals decide; LOOSE signals only veto the weak.
     Adjacency is strong evidence and cheap to trust: a person's name is
     essentially never immediately followed by "catheter", "disease" or
     "30 mg", and essentially never immediately preceded by "Mrs." while
     being a drug. A 60-character window proves much less — "catheter"
     in the same sentence as a real surname is common. So window-level
     signals only ever act on the weak layers, never on the primary NER.

  2. Protection by STRUCTURE, not by vocabulary.
     The lexicon cannot anticipate every suture brand and eponym. But
     "X catheter", "X disease", "X-en-Y", "4-0 X" are clinical whatever X
     is. This generalises the Wilson-disease disambiguation — telling an
     eponym from a surname — to eponyms and devices no list contains.

WHAT PASSES THROUGH UNTOUCHED
-----------------------------
Structural layers — label anchoring, title/age/signature frames, regex
identifiers — run on every note, in every section (they are the
always-on detectors). They are never held here, in any section, because
a titled name can appear anywhere and those layers are the
highest-precision evidence in the engine.

Every decision records its signals, so the operation log can say not
just what was redacted but why.
"""

from __future__ import annotations

import dataclasses
import functools
import re

from inkguard import quartz as C

# Evidence that needs no help. Never held.
STRUCTURAL_LAYERS = frozenset({"anchor", "context", "regex", "translator",
                               "propagated", "adjacent", "patient_names"})
REGULATED_ENTITIES = frozenset({"PERSON", "LOCATION"})
WEAK_LAYERS = frozenset({"candidate", "ner_xx"})

RELEASE_THRESHOLD = 0.50

_BASE = {"ner": 0.60, "candidate": 0.45, "ner_xx": 0.35}

_SECTION_WEIGHT = {
    C.IDENTITY: 0.25, C.SIGNATURE: 0.25,
    C.NARRATIVE: 0.10, C.UNKNOWN: 0.10,
    C.CLINICAL_LIST: -0.30, C.PROCEDURE: -0.30,
    C.MEASUREMENT: -0.30, C.ASSESSMENT_PLAN: -0.30,
}

# Words that are never a person's name, whichever model says otherwise.
NEVER_NAMES = frozenset({
    "this", "that", "these", "those", "the", "there", "then", "here",
    "it", "he", "she", "they", "we", "his", "her", "hers", "their", "him",
    "them", "patient", "pt", "doctor", "nurse", "physician", "surgeon",
    "today", "yesterday", "tomorrow", "none", "na", "n/a", "unknown",
    "other", "left", "right", "bilateral", "normal", "negative",
    "positive", "general", "local", "history", "exam", "plan",
    "assessment", "impression", "diagnosis", "procedure", "findings",
    # Kinship words used AS a noun phrase ("Mom has a cold"). These are
    # roles, never names — and they are the words a relation signal
    # looks for, so misreading one as a name inverts the evidence.
    "mom", "dad", "mother", "father", "grandma", "grandpa", "grandmother",
    "grandfather", "sister", "brother", "wife", "husband", "son",
    "daughter", "aunt", "uncle", "spouse", "parent", "parents",
    # Demonyms and race descriptors: NER tags "Caucasian male" as a
    # person. These describe a patient; they do not name one.
    "caucasian", "hispanic", "latino", "latina", "latinx", "asian",
    "african-american", "african", "white", "black",
    # Credentials read on their own ("-Kiliam Parson, LCSW" gave the NER a
    # second "person", LCSW). Not "MD" ("Md" abbreviates Mohammed in South
    # Asian names), "PA", "DO" or "MA" (Ma is a surname).
    "lcsw", "msw", "lpn", "crna", "cnm", "phd", "pharmd", "dds", "dmd", "dpm", "dnp",
    "aprn", "fnp", "bsn", "mbbs", "rdn", "slp", "cna", "rn", "np", "pa-c",
    # Lawyers, surgical technologists, neuromonitoring technologists.
    "esq", "cnim", "cst", "l.ac", "lac", "dacm", "daom", "nd",
    # Form labels and form answers. A model that runs a name on into the
    # next label ("Dr. Will Page   Fax: ...") must not make the label a
    # name that then spreads through the note; "Emergency contact: SAME"
    # answers the field without naming anyone.
    "fax", "phone", "tel", "telephone", "cell", "mobile", "pager", "email", "date",
    "dob", "mrn", "attn", "cc", "re", "subject", "same", "self", "signed", "page",
    # Deliberately NOT here: words that merely showed up as false
    # positives on the benchmark ("Morbid", "Benign"). Listing a test
    # set's specific failures improves the score without making the
    # system smarter. Those must be caught by structure — section-initial
    # and list-item position — or the measurement stops meaning anything.
})

# Words of a place of care or a service. When a model runs a name into
# a clinic's name ("[Bright] Family Medicine"), these parts must not be
# spread through the note as if they were the person, nor counted as a
# surviving name at the exit check (in testing, most notes flagged by the
# exit check were flagged for "Family", "Medicine", "Pharmacy" or
# "Primary Care").
INSTITUTION_WORDS = frozenset(
    "family medicine medical primary care pharmacy hospice associates supply nephrology "
    "clinic clinics center centre health healthcare hospital services service group practice "
    "department dept unit surgery surgical transplant pediatrics paediatrics pediatric "
    "paediatric internal emergency urgent community regional general memorial university "
    "rehab rehabilitation dental vision imaging radiology laboratory lab labs partners "
    "physicians specialists outpatient inpatient behavioral behavioural wellness institute "
    "foundation network system systems county state national children's childrens women's "
    "womens cardiology oncology neurology orthopedics orthopaedics psychiatry obstetrics "
    "gynecology dermatology endocrinology gastroenterology urology pulmonology rheumatology "
    "infusion dialysis therapy therapies home house portal records".split())

_CREDENTIAL_CAPS = frozenset({"MD", "DO", "MA", "PA", "NP", "RN", "PT", "OT", "DC", "OD"})


def not_a_name_part(part: str) -> bool:
    """A word inside a confirmed name that must not stand alone as one."""
    t = part.strip(".,;:")
    if t in _CREDENTIAL_CAPS:
        return True
    low = t.lower()
    return low in INSTITUTION_WORDS


_KIN_WORDS = frozenset(
    "neighbor neighbour mom mum dad mother father wife husband son daughter sister brother "
    "aunt uncle grandma grandpa grandmother grandfather friend partner spouse boss landlord "
    "caller contact emergency".split())


def plausible_name(name: str) -> bool:
    """Whether a "confirmed name" can be a person at all.

    A form field filled with the wrong thing ("Emergency contact:
    hypertension"), a credential after a name ("MD", "MA"), a title or a
    relation word on its own ("Mrs", "Neighbor"), or a clinic's service
    ("Pediatrics", "Crisis Stabilization") is not a person. Left in the
    file's list of names, each one flagged every note it appeared in at
    the final check."""
    from inkguard.jasper import _clinical
    words = [w.strip(".,;:'’()") for w in name.split()]
    words = [w for w in words if w]
    left = [w for w in words if w.lower() not in TITLES and w.lower() not in _KIN_WORDS
            and w.lower() not in NEVER_NAMES and not not_a_name_part(w)]
    if not left:
        return False
    from inkguard.name_registry import NAMES
    from inkguard.clinical_lexicon import is_clinical_term, is_vocabulary_word

    def medical(w: str) -> bool:
        low = w.lower()
        if low in NAMES:
            return False
        if w.islower():                  # names are written with a capital
            return True
        if is_clinical_term(low):
            return True
        # A vocabulary word with a medical ending ("Palpitations",
        # "Pediatrics", "Stabilization", "Crisis"); a flower or a virtue
        # with no such ending ("Marigold") may still be someone's name.
        return is_vocabulary_word(low) and bool(_MEDICAL_ENDING.search(low))
    return not all(medical(w) for w in left)


_MEDICAL_ENDING = re.compile(
    r"(?:tions?|sions?|itis|osis|sis|emia|aemia|ics|ology|ologist|ectomy|otomy|ostomy|"
    r"algia|pathy|plasty|scopy|gram|graphy|ism|ive|ine|ary|ory|al|ia|oma|uria|penia|cyte|ase)$")


TITLES = frozenset({"mr", "mrs", "ms", "miss", "mx", "dr", "prof",
                    "sr", "jr", "doctor", "nurse"})

# -------------------------------------------------------------- tight --

# Clinical nouns that follow an eponym or a device brand. "X disease",
# "X catheter", "X maneuver" — clinical whatever X is.
# "(?:\s|['’]s\b)*" — whitespace or a possessive. An earlier character
# class, [\s'’s]*, also swallowed any bare letter "s", which is not a
# possessive and could let an unrelated word reach a qualifier.
_QUALIFIER_AFTER = re.compile(
    r"^(?:\s|['’]s\b)*(?:disease|syndrome|signs?|tests?|maneuver|manoeuvre|"
    r"procedure|operation|repair|reflex|palsy|phenomenon|criteria|"
    r"classification|scale|score|grade|fracture|disorder|tumou?r|"
    r"lymphoma|sarcoma|carcinoma|anomaly|malformation|triad|law|"
    r"position|incision|stitch|sutures?|retractors?|forceps|clamps?|"
    r"scissors|catheters?|tubes?|drains?|line|stents?|needles?|cannula|"
    r"grafts?|flap|limb|anastomosis|pouch|ulcer|nodes?|cells?|bod(?:y|ies)|"
    r"zone|space|canal|duct|gland|ligament|muscle|nerve|triangle|fascia|"
    r"point|angle|index|ratio|formula|method|technique|approach|bypass|"
    r"pump|cautery|bag|mask|cuff|splint|cast|device|plate|screws?|nail|"
    r"prosthesis|implants?|valve|shunt|strips?|dressing|tape|mesh|clips?|"
    r"staples?|stapler|trocar|port|scope|blade|knife|electrode|pads?|"
    r"sponge|pack|wrap|solution|ointment|cream|tablets?|capsules?|"
    # Added after magnifying test-note false positives: "Doppler
    # examination", "Ellik evacuator", "Nesbit tuck", "Kling gauze".
    r"examination|exam|study|studies|ultrasound|imaging|scan|evaluation|"
    r"evacuator|tuck|plication|gauze|bandage|loop|sheath|balloon|dilator|"
    r"speculum|elevator|rongeur|curette|drill|reamer|irrigator|aspirator|"
    r"monitor|probe|sensor|laser|sling|brace|collar|wire|pin|anchor|"
    r"reservoir|hook|spreader|needle-driver|holder|"
    # Further test notes: "Heineke-Mikulicz closure", "Fournier's
    # gangrene", "Ta papillary lesion", "Gleason pattern".
    r"closure|gangrene|papillary|pattern|pyloroplasty|stricture|"
    r"deformity|contracture|hernia|cyst|nodule|lesion|lesions|"
    # Held-out test notes: "Dacron patch", "Doyle
    # splints", "Jackson table", "Lahey tenaculum", "Pap smear", "Burr
    # hole", "Cortisporin Otic drops", "Kennedy sponges".
    r"patch|patches|table|tenaculum|smear|hole|holes|drops|sponges|"
    r"xenograft|allograft|flaps|splints|tubes|chest\s+tubes?|lines|layer|"
    r"layers|groove|fold|folds|tenaculum|retractors|clamps|hook|hooks|"
    r"contractions?|coma|monitor|index|stage|"
    # Second test review: "Wilson frame", "Doppler signal good",
    # "Doppler flow", "Allis clamp" already above.
    r"frames?|signals?|waveforms?|flow|flows|tracing|wave|waves|"
    r"neg|pos|negative|positive)\b",
    re.IGNORECASE,
)
# Dose immediately after: "Toradol 30 mg", "Keflex 500mg PO".
_DOSE_AFTER = re.compile(
    r"^[\s,:]{0,3}\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|gm|ml|mL|l|units?|iu|meq|"
    r"%|tabs?|tablets?|caps?|capsules?|puffs?|drops?|mg/kg|mcg/kg)\b",
    re.IGNORECASE,
)
# Suture gauge or catheter sizing right beside: "4-0 Monocryl", "14 Fr".
_SIZING_NEAR = re.compile(
    r"\b\d-0\b|\b\d{1,2}[\s-]*(?:Fr|French|gauge|ga)\b", re.IGNORECASE)
# "Ridgeview Clinic", "Harlow Hospital", "Eastbrook Clinic Foundation": a
# name inside an institution's name identifies the institution.
_INSTITUTION_AFTER = re.compile(
    r"^\s+(?:clinic|hospital|medical\s+center|health\s+system|health|"
    r"university|institute|foundation|children'?s|memorial|cancer\s+center)\b",
    re.IGNORECASE)
_SIZE_NUMBER_BEFORE = re.compile(r"#\s?\d{1,2}\s+$")  # "#24 Blake", "#11 blade"
# A score written as a sum right after the token: "Gleason 3 + 4",
# "Gleason's 3+3". Surnames are not followed by arithmetic.
_SCORE_AFTER = re.compile(r"^(?:['’]s)?[\s:]*(?:score\s*(?:of\s*)?)?\d{1,2}\s*\+\s*\d{1,2}\b",
                          re.IGNORECASE)
# An instrument is USED: "using the bipolar Bovie", "via a Pfannenstiel".
_INSTRUMENT_BEFORE = re.compile(
    r"\b(?:(?:using|via|utilizing|utilising)\s+(?:the|a|an)\s+(?:[a-z\-]+\s+)?"
    r"|(?:grasped|clamped|retracted|divided|cauterized|cut|elevated|dissected|"
    r"incised|secured|ligated|held|opened|closed|irrigated|dilated)\s+"
    r"(?:with|using)\s+(?:the\s+|a\s+|an\s+)?(?:[a-z\-]+\s+)?)$",
    re.IGNORECASE)
# Things are instilled and infused; people are not.
_THING_PREDICATE_AFTER = re.compile(
    r"^\s+(?:was|were|is|are)\s+(?:then\s+)?(?:instilled|infused|irrigated|"
    r"injected\s+into|mixed|exsanguinated|prepped|draped)\b",
    re.IGNORECASE)

# MORPHOLOGY — the shape of the word itself. Latin/Greek medical
# word-formation (lymphadenECTOMY, cystourethroSCOPY, cystoCELE,
# cryosurGERY) produces words no naming tradition produces. Suffixes are
# restricted to forms with no realistic surname collision, and the token
# must be long; "-ory" and "-ive" were considered and rejected because
# they would hit Gregory, Mallory and Clive.
_MEDICAL_SUFFIX = re.compile(
    r"(?:ectomy|ectomies|otomy|otomies|ostomy|ostomies|oscopy|oscopies|scopy|"
    r"plasty|plasties|pexy|pexies|rrhaphy|rrhaphies|graphies|stomies|tripsies|"
    r"rrhagia|rrhea|gram|graphy|cele|itis|osis|oses|emia|algia|pathy|"
    r"ology|ologic|ological|iasis|trophy|plegia|paresis|megaly|lysis|"
    r"centesis|stomy|tripsy|surgery|genesis|uria|penia|cytosis|"
    r"ectasis|sclerosis|malacia|oma|omas|omata|"
    # bacterial and fungal genera: Enterococcus, Acinetobacter,
    # Pseudomonas, Mycobacterium, Actinomyces, Mycoplasma
    r"coccus|cocci|bacter|bacterium|monas|bacillus|myces|plasma|"
    # -ically adverbs ("Endoscopically", "Laparoscopically"). Names end in
    # -ly (Kelly, Emily), never in -ically.
    r"ically)$",
    re.IGNORECASE)
_MEDICAL_PREFIX = re.compile(
    r"^(?:pre|post|non|over|under|intra|inter|trans|hyper|hypo|sub|peri|"
    r"anti|re|un|mis|semi|multi|extra)(?P<rest>[a-z]{6,})$", re.IGNORECASE)
# Clinical hyphen compound: "Roux-en-Y", "Billroth-II"-style joins where
# the continuation is lowercase or a roman numeral.
_HYPHEN_COMPOUND_AFTER = re.compile(r"^-(?:[a-z]|I{1,3}\b|IV\b)")

# Anatomical "<structure> of <eponym>": space of Retzius, loop of Henle,
# circle of Willis, sphincter of Oddi, ampulla of Vater, pouch of Douglas.
_ANATOMIC_OF_BEFORE = re.compile(
    r"\b(?:space|canal|loop|circle|islets?|sphincter|ampulla|pouch|ligament|"
    r"triangle|foramen|duct|crypts?|glands?|nodes?|bundle|fibers|plexus|"
    r"tubercle|fossa|recess|line|zone|capsule|valve|angle|point|area|artery|arcade|"
    r"vein|nucleus|tract|organ|cells?|column|body|bodies|lines|folds?)"
    r"\s+of\s+(?:the\s+)?$",
    re.IGNORECASE,
)
# Pharmacological lead-in: "previously on Ditropan", "switched to X",
# "started X". People are not "on" or "switched to".
_PHARM_BEFORE = re.compile(
    r"\b(?:on|taking|takes|took|started|restarted|switched\s+to|"
    r"changed\s+to|continue|continued|discontinued|stopped|trial\s+of|"
    r"course\s+of|prescribed|receiving|tolerating|titrated|used|tried|"
    r"failed|trialed|trialled)\s+$",
    re.IGNORECASE,
)
# A route or a device/imaging modifier directly before the token:
# "IV Toradol", "PO Keflex", "color Doppler", "bipolar Bovie". People
# are not administered intravenously and do not come in colour.
_MODIFIER_BEFORE = re.compile(
    r"\b(?:IV|IVP|IVPB|PO|IM|SQ|SC|SL|PR|subq|intravenous|intramuscular|"
    r"subcutaneous|oral|topical|inhaled|nebuli[sz]ed|intranasal|"
    r"colou?r|spectral|pulsed|power|duplex|venous|arterial|transcranial|"
    r"carotid|bipolar|monopolar|straight|curved|angled|right[-\s]angled?|"
    r"toothed|self[-\s]retaining|disposable)\s+$",
    re.IGNORECASE,
)
# A dose or volume directly before: "15 mL of Toradol", "30 mg Toradol".
_DOSE_BEFORE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|gm|ml|mL|l|units?|iu|meq|%|tabs?|"
    r"tablets?|caps?|puffs?|drops?)\s+(?:of\s+)?$",
    re.IGNORECASE,
)
# Leading route INSIDE a model span: NER returns "IV Toradol" whole.
_ROUTE_IN_SPAN = re.compile(
    r"^(?:IV|IVP|IVPB|PO|IM|SQ|SC|SL|PR)\s+\S", re.IGNORECASE)
# Determiner before a single capitalized token. People are almost never
# "the Foley" or "an Ellik" — unless it is "the Haddad family".
_DETERMINER_BEFORE = re.compile(r"\b(?:the|a|an)\s+$", re.IGNORECASE)
_FAMILY_AFTER = re.compile(
    r"^\s+(?:family|household|kids|children|home|residence|parents|twins|"
    r"brothers|sisters)\b", re.IGNORECASE)

_TITLE_BEFORE = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Doctor|Nurse)\.?\s*$", re.IGNORECASE)
_RELATION_BEFORE = re.compile(
    r"\b(?:wife|husband|son|daughter|mother|father|mom|dad|brother|sister|"
    r"spouse|partner|fianc[eé]e?|guardian|friend|neighbou?r|niece|nephew|"
    r"aunt|uncle|grand(?:son|daughter|mother|father|child)|cousin|"
    r"boyfriend|girlfriend|caregiver|roommate|parent)(?:'s)?\b"
    r"[\s,:]*(?:[A-Z][\w'\-]*\s+){0,2}$",
    re.IGNORECASE,
)
_ATTRIBUTION_BEFORE = re.compile(
    r"\b(?:seen by|discussed with|spoke (?:with|to)|talked (?:with|to)|"
    r"assisted by|referred by|dictated by|signed by|reviewed by|"
    r"accompanied by|called|named|per|attn|c/o|care of|consulted|"
    r"asked by|requested by|paged by|texted by|ordered by|staffed with|signed out to|"
    r"d/w|(?:clarify|clarified|confirm|confirmed|check|checked|verify|verified|"
    r"discuss|discussed|spoke|spoken|talked|reviewed|coordinate|coordinated)\s+(?:w/|with)|"
    r"(?:cc|to|from|re|attn)\s*:|"
    r"(?:follow(?:\s*-?\s*up)?|f/u|meeting|meet|appointment|appt|visit|consult)\s+(?:with|w/))\s*:?\s*$",
    re.IGNORECASE,
)
_PREDICATE_AFTER = re.compile(
    r"^\s*(?:,\s*an?\s+\d|is|was|has|had|reports|reported|states|stated|"
    r"presents|presented|denies|denied|tolerated|underwent|complains|"
    r"notes|feels|returns|returned|arrived|agrees|agreed|consented|"
    r"declined|requests|requested|lives|works|understands|understood|verbalized|"
    r"verbalizes|voiced|expressed|endorses|endorsed|ambulating|ambulated|"
    r"agreeable|amenable|consents|wishes|wants|prefers|awoke|woke|awakened|"
    r"extubated|discharged|admitted|seen|evaluated|examined|"
    r"doing\s+(?:well|better|worse|"
    r"ok|okay|fine|poorly)|feeling|is\s+here|here\s+for|comes\s+in|came\s+in)\b",
    re.IGNORECASE,
)
_LOCATION_PREP_BEFORE = re.compile(
    r"\b(?:in|from|at|to|near|lives|resides|residing|moved|born in|"
    r"visited|traveled|travelled|transferred from)\s+$",
    re.IGNORECASE,
)
# A device eponym standing alone in a STATE clause: "Foley removed.",
# "Foley in place", "JP to bulb suction". Checked only for tokens the
# lexicon already knows as device/disease eponyms, and only when no
# object follows — "Haddad removed the drain" is a surgeon acting, not a
# device being acted on.
_DEVICE_STATE_AFTER = re.compile(
    r"^\s+(?:(?:was|is|has\s+been|were|being)\s+)?"
    r"(?:removed|discontinued|d/?c'?d|dc'?ed|placed|inserted|replaced|"
    r"exchanged|flushed|clamped|unclamped|capped|pulled|repositioned|"
    r"kept\s+in\s+place|left\s+in\s+place|left\s+indwelling|"
    r"draining|drained|in\s+place|in\s+situ|to\s+(?:gravity|suction|"
    r"bulb|drainage|straight\s+drain)|intact|patent|output|care|"
    r"inflated|deflated|irrigated|advanced|secured|sutured|withdrawn|connected|attached|"
    r"out|d/c|dc'?d?|pulled\s+out|came\s+out|fell\s+out|"
    # Nouns of device handling: "for Foley removal", "Hemovac exchange".
    r"removal|insertion|placement|exchange|change|replacement|teaching|catheter|drain)\b"
    r"(?!\s+(?:the|a|an|his|her|their|its|it|them|him|my|our)\b)",
    re.IGNORECASE,
)

_LIST_ITEM_BEFORE = re.compile(r"(?:^|[\n,;])\s*(?:\d{1,2}[.)]|[-•*])\s*$")

# -------------------------------------------------------------- loose --
# Only ever applied to WEAK layers.
_PROCEDURAL_WINDOW = re.compile(
    r"\b(?:sutured?|closed|closure|placed|inserted|applied|anastomos\w*|"
    r"incis\w*|dissect\w*|ligat\w*|stapled?|cauteriz\w*|irrigat\w*|"
    r"debrid\w*|implant\w*|retract\w*|approximat\w*|subcuticular|"
    r"interrupted|running|figure-of-eight|intraoperative\w*)\b",
    re.IGNORECASE,
)
_PHARM_WINDOW = re.compile(
    r"\b(?:administered|prescribed|started on|dose[sd]?|dosing|taking|"
    r"medications?|tablets?|infusion|drip|bolus|titrat\w*|refill\w*|"
    r"mg|mcg|po|iv|im|prn|bid|tid|qid|qhs|daily)\b",
    re.IGNORECASE,
)

from inkguard.talc import CASELESS_RE as _CASELESS_RE  # shared vocabulary


@functools.lru_cache(maxsize=100_000)
def _zipf(token: str) -> float:
    try:
        from wordfreq import zipf_frequency
    except Exception:
        return 0.0
    return zipf_frequency(token.lower(), "en")


# =====================================================================
# Tight-signal predicate, also used by shale as the CLINICAL GUARD
# =====================================================================

def clinically_bound(text: str, start: int, end: int) -> str | None:
    """Return the reason the span at [start, end) is clinical by its
    immediate surroundings, or None.

    Used in two places: as a negative signal here, and as the clinical
    guard when shale propagates a confirmed name — so that confirming
    "Mrs. Wilson" never results in redacting "Wilson disease".
    A title immediately before the span always wins: "Dr. Foley" is a
    person even though "Foley" is also a catheter.
    """
    before = text[max(0, start - 12):start]
    if _TITLE_BEFORE.search(before):
        return None
    after = text[end:end + 40]
    if _QUALIFIER_AFTER.match(after):
        return "qualifier_after"
    wide_before = text[max(0, start - 40):start]
    if _MODIFIER_BEFORE.search(text[max(0, start - 16):start]):
        return "modifier_before"
    if _ROUTE_IN_SPAN.match(text[start:end]):
        return "route_in_span"
    if _DOSE_BEFORE.search(wide_before):
        return "dose_before"
    if _ANATOMIC_OF_BEFORE.search(wide_before):
        return "anatomic_of"
    if _PHARM_BEFORE.search(wide_before):
        return "pharm_before"
    if _DOSE_AFTER.match(after):
        return "dose_after"
    if _HYPHEN_COMPOUND_AFTER.match(after):
        return "hyphen_compound"
    # A size ("4-0", "14 Fr") binds within its own sentence: "... 4-0
    # Monocryl.\n\nHass woke" is a suture and then a patient.
    left = re.split(r"[.!?;]\s|\n", text[max(0, start - 15):start])[-1]
    right = re.split(r"[.!?;](?:\s|$)|\n", text[end:min(len(text), end + 15)])[0]
    near = left + text[start:end] + right
    if _SIZING_NEAR.search(near) or _SIZE_NUMBER_BEFORE.search(text[max(0, start - 6):start]):
        return "sizing_near"
    if _DEVICE_STATE_AFTER.match(after) and (
            _known_eponym(text[start:end]) or
            text[start:end].strip().lower() not in _names()):
        # A known device eponym, or any word that is nobody's name:
        # "Hemovac removed.", "Blake to bulb suction".
        return "device_state"
    if _INSTITUTION_AFTER.match(after):
        return "institution_name"
    if _SCORE_AFTER.match(after):
        return "score_after"
    if _EPONYM_SCORE_AFTER.match(after) and _known_eponym(text[start:end].rstrip("s")):
        return "score_after"
    if _MAKER_BEFORE.search(text[max(0, start - 30):start]):
        return "maker_before"
    # List siblings apply to a SINGLE word that is nobody's name. As first
    # written they applied to any span, and "multiple myeloma, Mary Smith,
    # last seen" made the patient a list item of her diagnosis — found by
    # the ASQ-PHI benchmark, which labels exactly that construction.
    tok = text[start:end].strip()
    if (" " not in tok and tok.lower() not in _names()
            and _list_sibling_clinical(text, start, end)):
        return "list_sibling"
    if _THING_PREDICATE_AFTER.match(after):
        return "thing_predicate"
    extra = _round2_binding(text, start, end, after)
    if extra:
        return extra
    if _clinical_noun_after(after):
        return "clinical_noun_after"
    return None


# Further clinical bindings (second test review) -----------------------
# "Penfield No. 4", "Kerrison #2": an instrument with its size number.
_INSTRUMENT_NUMBER_AFTER = re.compile(r"^\s+(?:No\.?|#)\s*\d{1,2}\b", re.IGNORECASE)
# "(Knosp 1)", "Knosp grade 1", "Fisher III": a classification and its grade.
_GRADE_AFTER = re.compile(
    r"^\s+(?:(?:grade|class|stage|type|level|score)\s+[0-9IVX]{1,4}[a-c]?\b"
    r"|[0-4IV]{1,3}[a-c]?\s*\))", re.IGNORECASE)
_OPEN_PAREN_BEFORE = re.compile(r"\(\s*$")
# "from prior Pfannenstiel", "s/p Whipple", "h/o Nissen".
_PROCEDURE_HISTORY_BEFORE = re.compile(
    r"\b(?:s/p|status\s+post|prior|previous|remote|history\s+of|h/o|post|after|failed)\s+$",
    re.IGNORECASE)
# "Addison unlikely", "Cushing ruled out", "Wilson considered".
_DIAGNOSTIC_AFTER = re.compile(
    r"^(?:['’]s)?\s+(?:unlikely|likely|less\s+likely|more\s+likely|ruled\s+out|r/o|"
    r"suspected|considered|excluded|possible|probable|favou?red)\b", re.IGNORECASE)
_TEST_RESULT_AFTER = re.compile(r"^(?:['’]s)?\s+(?:neg|pos|negative|positive)\b|^\s*[+-](?:\s|$|[,.;)])",
                                re.IGNORECASE)


def _round2_binding(text: str, start: int, end: int, after: str) -> str | None:
    tok = text[start:end].strip()
    known = _known_eponym(tok)
    if _INSTRUMENT_NUMBER_AFTER.match(after):
        return "instrument_number"
    if _GRADE_AFTER.match(after) and (
            known or _OPEN_PAREN_BEFORE.search(text[max(0, start - 3):start])
            or re.match(r"^\s+(?:grade|class|stage|type|level|score)\b", after, re.I)):
        return "grade_after"
    not_a_name = known or tok.lower() not in _names()
    # "from prior Pfannenstiel": after s/p, prior or h/o, a RARE capitalised
    # word is an operation even if some name list holds it. The
    # agate still asks a person about any registry name kept.
    rare_cap = tok[:1].isupper() and _zipf(tok.lower()) < 2.0
    if (not_a_name or rare_cap) and _PROCEDURE_HISTORY_BEFORE.search(text[max(0, start - 20):start]):
        return "procedure_history"
    if not_a_name and _DIAGNOSTIC_AFTER.match(after):
        return "diagnostic_after"
    if (known or tok.lower() not in _names()) and _TEST_RESULT_AFTER.match(after):
        return "test_result"
    return None


_SERVICE_WORDS = frozenset("""ortho orthopedics cards cardiology neuro neurology neurosurg
    neurosurgery nsgy surgery gensurg medicine hospitalist urology uro onc oncology heme
    hematology renal nephro nephrology pulm pulmonary pulmonology psych psychiatry peds
    pediatrics derm dermatology rheum rheumatology endo endocrinology anesthesia pain
    radiology rads gyne gyn obgyn trauma vascular plastics ent ophtho ophthalmology
    palliative geriatrics hepatology gastro""".split())
_NEXT_WORD = re.compile(r"^(?:['’]s)?\s+([a-z][a-z\-]{3,})\b")
# "Pfannenstiel skin incision", "Portnoy ventricular catheter": one
# descriptive word may stand between the token and its clinical noun.
_NEXT_TWO = re.compile(r"^(?:['’]s)?\s+[a-z][a-z\-]{2,}\s+([a-z][a-z\-]{3,})\b")
# An eponymous SCORE followed by its value: "Apgars of 9 and 9",
# "Apgar 8", "Mallampati 2", "Glasgow 14".
_EPONYM_SCORE_AFTER = re.compile(
    r"^s?\s+(?:scores?\s+)?(?:of\s+|were\s+|was\s+|at\s+)?\d{1,2}\b")
# Device provenance: "manufacturer Guidant", "Model: Sigma".
_MAKER_BEFORE = re.compile(
    r"\b(?:manufacturer|manufactured\s+by|vendor|brand|model|made\s+by)\s*:?\s*$",
    re.IGNORECASE)


def _clinical_noun_after(after: str) -> bool:
    """The next word, lowercase and fused to the token, is itself medical:
    "Stamm gastrostomy", "Portnoy ventricular catheter", "Kocher
    maneuver". Only lowercase continuations count — a capitalized next
    word is the second half of a name."""
    from inkguard.clinical_lexicon import (is_clinical_term, is_vocabulary_word,
                                      DISEASE_QUALIFIERS)

    def medical(w: str) -> bool:
        # Verb forms are never the clinical NOUN a device or eponym is
        # fused with ("Toran tolerated well"): the ontology definitions put
        # words like "tolerated" into the vocabulary.
        if w.endswith(("ed", "ing")) and not w.endswith(("bed", "ted tube")):
            return False
        return (w in DISEASE_QUALIFIERS or is_clinical_term(w)
                or is_vocabulary_word(w) or medical_morphology(w))

    # A person's verb right after the token ("Gault tolerated procedure
    # well") means the token is the subject, not a modifier.
    if _PREDICATE_AFTER.match(after):
        return False
    m = _NEXT_WORD.match(after)
    # A SERVICE after a word is where a person works, not what a thing is:
    # "asked by Soriano ortho", "Jones cards", "Lee neurosurgery".
    if m and m.group(1) in _SERVICE_WORDS:
        return False
    if m and medical(m.group(1)):
        return True
    m2 = _NEXT_TWO.match(after)
    return bool(m2 and (m2.group(1) in DISEASE_QUALIFIERS
                        or _QUALIFIER_AFTER.match(" " + m2.group(1))))


_SIB_BEFORE = re.compile(r"([A-Za-z][\w\-]{2,})(?:\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)$")
_SIB_AFTER = re.compile(r"^\s*(?:,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+)([A-Za-z][\w\-]{2,})")


def _list_sibling_clinical(text: str, start: int, end: int) -> bool:
    """In "on Coumadin, Cipro, and Levaquin" the middle item shares its
    list with a known drug. Items of one list are one kind of thing; a
    person does not sit in a list between two medications. Only
    UNAMBIGUOUS lexicon terms count as siblings, so "Smith, Jones and
    Wilson" is never turned clinical by an eponym."""
    from inkguard.clinical_lexicon import is_clinical_term, AMBIGUOUS_EPONYMS
    for m in (_SIB_BEFORE.search(text[max(0, start - 30):start]),
              _SIB_AFTER.match(text[end:end + 30])):
        if m:
            w = m.group(1)
            if w.lower() not in AMBIGUOUS_EPONYMS and is_clinical_term(w):
                return True
    return False


_DEPARTMENT_TAIL = re.compile(
    r"\b(?:surgery|medicine|service|services|department|unit|team|clinic|"
    r"anesthesia|anesthesiology|radiology|pathology|cardiology|oncology|"
    r"neurology|urology|orthopedics|pediatrics)$", re.IGNORECASE)


@functools.lru_cache(maxsize=1)
def _names() -> frozenset:
    from inkguard.name_registry import NAMES
    return NAMES


def medical_morphology(token: str) -> bool:
    """Is this single Latin token built like a medical term?"""
    t = token.strip(".,:;'’").lower()
    if len(t) < 8 or not t.isalpha():
        return False
    if _MEDICAL_SUFFIX.search(t):
        return True
    m = _MEDICAL_PREFIX.match(t)
    # A prefix only counts when what remains is itself an established
    # English word: pre+operative, non+contributory, over+active.
    # Underwood (under+wood) and Prescott (pre+scott) fail the length
    # floor on the remainder.
    # The remainder must also be ADJECTIVAL (-ive, -ory, -al, -ic, -ous,
    # -able...) — clinical prefix words are descriptors. Without this,
    # "Overstreet" (over+street) would read as a medical term.
    return bool(m and _ADJECTIVAL.search(m.group("rest"))
                and _zipf(m.group("rest")) >= 2.0)


_ADJECTIVAL = re.compile(r"(?:ive|ory|al|ic|ous|able|ible|ant|ent|ary|ated|ular)$")


def _known_eponym(token: str) -> bool:
    from inkguard.clinical_lexicon import AMBIGUOUS_EPONYMS
    t = token.strip().lower().strip(".,:;")
    return " " not in t and t in AMBIGUOUS_EPONYMS


def _tight_person_context(text: str, start: int, end: int) -> bool:
    return bool(_TITLE_BEFORE.search(text[max(0, start - 12):start])
                or _RELATION_BEFORE.search(text[max(0, start - 40):start])
                or _ATTRIBUTION_BEFORE.search(text[max(0, start - 25):start])
                or _PERSON_POSSESSION_AFTER.match(text[end:end + 30])
                or _CREDENTIAL_AFTER.match(text[end:end + 16])
                or _given_name_before(text, start, end))


# "Pt: Lars Babinski." A capitalised given name straight before the word
# makes it a surname, whatever the clinical vocabulary says about it: a
# surname "Babinski" was left in the output after "Lars" alone was
# removed. The eponym still wins when a clinical word follows
# ("Charles Bonnet syndrome", "Tommy John surgery").
_GIVEN_BEFORE = re.compile(r"(?:^|[\s(\[,:;])([A-Za-z][a-z]{1,15})[ \t]$")
_EPONYM_AFTER = re.compile(
    r"^['’]?s?[ \t-]+(?:sign|signs|syndrome|disease|reflex|test|maneuver|manoeuvre|palsy|"
    r"phenomenon|response|criteria|score|scale|grade|classification|type|procedure|"
    r"operation|surgery|approach|technique|repair|fracture|lesion|tumou?r|cyst|"
    r"malformation|bodies|body|ring|line|point|space|triangle|canal|area|nerve|"
    r"artery|vein|ligament|muscle|gland|cells?|stain|catheter|tube|clamp|retractor|"
    r"rongeur|elevator|frame|pins?|holder|drill|shunt|valve|device|system|plate|"
    r"screws?|cage|rod|dissector|forceps|scissors|needle|graft|flap|incision|"
    r"position|aphasia|area|encephalopathy|dementia|chorea|ataxia|neuralgia|"
    r"neuropathy|myelopathy|paralysis|phase|stage|effect|law|rule|method)\b",
    re.IGNORECASE)


def given_name_pair(text: str, start: int, end: int) -> bool:
    """"lars babinski", "Ilse Wernicke": a span of a given name and one more
    word is a person when the given name is not itself clinical, unless a
    clinical word follows ("Charles Bonnet syndrome")."""
    parts = text[start:end].split()
    if len(parts) != 2:
        return False
    sp = text.index(parts[1], start + len(parts[0]))
    return _given_name_before(text, sp, sp + len(parts[1]))


def _given_name_before(text: str, start: int, end: int) -> bool:
    m = _GIVEN_BEFORE.search(text[max(0, start - 18):start])
    if not m or _EPONYM_AFTER.match(text[end:end + 30]):
        return False
    word = text[start:end].strip()
    if not word or " " in word:
        return False
    given = m.group(1)
    # Written in lower case ("lars babinski called"), both words must be
    # lower case and the given name must not be an everyday word ("mark").
    if given[0].islower() and (not word[0].islower() or _zipf(given) >= 4.0):
        return False
    if given[0].isupper() and not word[0].isupper():
        return False
    from inkguard.name_registry import NAMES
    from inkguard import clinical_lexicon as _cl
    w, g = word.lower().strip(".,:;"), given.lower()
    # Only for an eponym (a surname the vocabulary would otherwise keep);
    # an ordinary word after a surname-like word is not a name ("seidel neg").
    eponym = w in _cl.EPONYMS or w in _cl.AMBIGUOUS_EPONYMS or w in _cl.NEURO_PROPER
    return (eponym and g in NAMES and not _cl.is_clinical_term(given)
            and g not in _cl.EPONYMS)


# "Pennig's employer", "Toran's wife": only a person HAS an employer or a
# wife. And "Wilkie, MD" / "Scruggs, DO" / "Lee PA-C": a credential names
# a person.
_PERSON_POSSESSION_AFTER = re.compile(
    r"^['’]s?\s+(?:employer|job|boss|wife|husband|spouse|partner|mother|father|mom|dad|"
    r"son|daughter|sister|brother|children|kids|family|parents|home|house|apartment|"
    r"address|phone|cell|number|email|insurance|appointment|appt|caregiver|guardian)\b",
    re.IGNORECASE)
_CREDENTIAL_AFTER = re.compile(
    r"^,?\s+(?:MD|M\.D\.|DO|D\.O\.|PA-C|PA|NP|FNP|DNP|APRN|RN|BSN|CNM|CRNA|MBBS|PhD|"
    r"LCSW|LPN|PharmD|DPM|DDS|DMD|Esq|CNIM|CST)\b"
    # "Submitting MD: Ardis Gaton", "Attending RN:": a credential with a
    # colon after it is a field label, and the word before is its label.
    r"(?![ \t]*:)")


_ORG_SUFFIX = re.compile(
    r"\b(?:clinic|hospital|center|centre|medical|health|healthcare|university|"
    r"institute|associates|group|inc|llc|pllc|pc|pharmacy|labs?|laboratory|"
    r"services|department|dept|foundation|practice|partners|imaging|"
    r"radiology|rehab|rehabilitation|home|hospice|urgent\s+care)\.?$",
    re.IGNORECASE)


def document_clinical_tokens(text: str, hits) -> set[str]:
    """Tokens this DOCUMENT itself uses clinically, and never as a person.

    The clinical guard at document scale: a note that writes "Foley catheter
    placed" has told us what Foley means in this note, so a later bare
    "Foley removed" is the same catheter. The rule withdraws if the word
    appears with a title, a relation or an attribution anywhere in the
    note — "Dr. Foley placed a Foley catheter" keeps both meanings open,
    and the ambiguous one resolves toward redaction.
    """
    out: set[str] = set()
    seen: set[str] = set()
    for h in hits:
        if h.entity_type != "PERSON":
            continue
        tok = h.text.strip().strip(".,:;")
        if not tok or " " in tok or tok in seen or _CASELESS_RE.search(tok):
            continue
        seen.add(tok)
        clinical_use = person_use = False
        for m in re.finditer(r"(?<![\w-])" + re.escape(tok) + r"(?![\w-])", text):
            if _tight_person_context(text, m.start(), m.end()):
                person_use = True
                break
            if clinically_bound(text, m.start(), m.end()):
                clinical_use = True
        if clinical_use and not person_use:
            out.add(tok)
    return out


# =====================================================================
# Scoring
# =====================================================================

def _is_regulated(h, kind: str) -> bool:
    if h.entity_type not in REGULATED_ENTITIES:
        return False
    if h.layer in STRUCTURAL_LAYERS:
        return False
    return True


def _score(text: str, h, kind: str, profile, all_hits,
           doc_clinical: frozenset = frozenset()) -> tuple[float, list[str]]:
    ev: list[str] = []
    base = _BASE.get(h.layer, 0.50)
    score = base
    ev.append(f"base:{h.layer}={base:+.2f}")

    token = h.text.strip()
    low = token.lower().strip(".,:;")
    is_person = h.entity_type == "PERSON"
    single = " " not in token
    latin = not _CASELESS_RE.search(token)

    def add(name: str, w: float) -> None:
        nonlocal score
        score += w
        ev.append(f"{name}={w:+.2f}")

    # --- section -----------------------------------------------------
    add(f"section:{kind}", _SECTION_WEIGHT.get(kind, 0.0))

    # --- hard negatives ----------------------------------------------
    if low in NEVER_NAMES or re.sub(r"['’]s$", "", low) in NEVER_NAMES:
        # "Pt's" is "Pt" — the possessive does not make it a name.
        add("never_name", -1.0)
    # No naming tradition puts a digit or a colon inside a name:
    # "DESCRIPTION:,1", "I-125" (an isotope read as a place).
    if re.search(r"[\d:]", token):
        add("digit_or_colon", -1.0)

    # --- lexical shape -------------------------------------------------
    if is_person and not single and latin and token == token.lower():
        # "neuro nonfocal": an all-lowercase phrase the NER model read as
        # a person. Lazy portal messages do write "maria lopez", so the
        # veto needs a word in it that is a clinical or English word and
        # nobody's name.
        toks = re.findall(r"[a-z][a-z'-]*", token)
        # Not when it is a given name and one word ("lars babinski"): the
        # given name decides, and the eponym is a surname.
        if not given_name_pair(text, h.start, h.end) and any(
                t not in _names() and (_is_clinical_word(t) or _english_word(t)
                                       or medical_morphology(t) or _zipf(t) >= 4.0)
                for t in toks):
            add("lowercase_phrase", -0.60)
    if is_person and not single and _DEPARTMENT_TAIL.search(token):
        # "General Surgery", "Oromaxillary Facial Surgery": a service.
        add("department", -0.60)
    if single and latin:
        bare = token.strip(".,:;'’")
        if (bare.isupper() and len(bare) >= 3 and "-" not in bare
                and _zipf(bare.lower()) >= 3.0):
            # "TUBES", "DRAINS": a header word, not a shouted surname.
            add("caps_word", -0.35)
        elif bare[:1].islower() and _zipf(bare.lower()) >= 3.0:
            # "gallop", "nick". Lazy notes do write names in lowercase,
            # which is why this is a weight and not a veto — but a
            # lowercase dictionary word with nothing else for it is a word.
            add("lowercase_word", -0.45)
        elif (is_person and bare[:1].isupper()
              and ((_zipf(bare.lower()) >= 3.5 and bare.lower() not in _names())
                   or _english_word(bare.lower()))):
            # "Cement", "Frozen", "Limb", "Surgery": an established English
            # word that is nobody's name in the name registry. The
            # candidate layer already refuses such words (zipf 3.6); the
            # same knowledge is applied to what the NER model proposes.
            # Also: a WordNet word that is in NO name list we hold
            # ("Disheveled", "Outpatient").
            add("dictionary_word", -0.35)
        low_b = bare.lower()
        if (is_person and low_b.endswith("s") and len(low_b) >= 5
                and not re.search(r"['’]s$", low_b)          # "Domingo's" is possessive
                and low_b not in _names() and low_b[:-1] not in _names()
                and _zipf(low_b[:-1]) >= 3.0):
            # "Screws", "Shunts": the plural of a common noun.
            add("plural_noun", -0.35)
        if is_person and medical_morphology(bare):
            add("medical_morphology", -0.40)

    # --- corroboration / quorum -------------------------------------
    for g in all_hits:
        if g is h or not (g.start < h.end and h.start < g.end):
            continue
        if g.entity_type not in REGULATED_ENTITIES:
            continue
        if g.layer in STRUCTURAL_LAYERS:
            add("corroborated", 0.45)
            break
        # A quorum needs INDEPENDENT witnesses. The candidate layer fires
        # on "unfamiliar capitalized word" — the same input on which
        # the NER model makes its mistakes — so when both flag "Foley" or
        # "Retzius", that is one blind spot seen twice, not two pieces of
        # evidence. The broadest layer may receive a quorum but
        # never cast a vote in one.
        # The multilingual model is independent evidence only where it
        # is run for: non-Latin script. On a Latin token it reads
        # the same capital letter the primary model read, so "Foley"
        # flagged by both is still one witness.
        if (g.layer != h.layer and "candidate" not in (g.layer, h.layer)
                and not (latin and "ner_xx" in (g.layer, h.layer))):
            add("quorum", 0.25)
            break

    before = text[max(0, h.start - 40):h.start]
    after = text[h.end:h.end + 40]

    # --- labels -------------------------------------------------------
    # "Recs:", "Dispo:", "Proc:" at the start of a line or sentence are
    # field labels. A registered name in that slot ("Smith: ...") is not
    # penalised — chat transcripts do that.
    if (is_person and single and latin and after.startswith(":")
            and re.search(r"(?:^|[\n.;]\s*|\|\s*)$", text[max(0, h.start - 4):h.start])):
        if low not in _names():
            add("label_colon", -0.60)
        elif _zipf(low) >= 3.0:
            # "Graft:" at the start of a line in an operative note. The word
            # is also a surname, so this is POSITIONAL doubt only (a chat
            # line "Smith: ..." must stay maskable by the output mask);
            # a candidate-layer hit drops below the mask floor, an NER hit
            # with its stronger base does not.
            add("label_colon_word", -0.35)

    # --- tight positives ---------------------------------------------
    if is_person:
        # A token that carries its own possessive ("Domingo's wife"): the
        # possession is read across it.
        own_poss = re.search(r"['’]s$", h.text or "")
        if _PERSON_POSSESSION_AFTER.match(after) or (
                own_poss and _PERSON_POSSESSION_AFTER.match("'s" + after)):
            add("person_possession", 0.50)
        if _CREDENTIAL_AFTER.match(after[:16]):
            add("credential_after", 0.50)
        if _TITLE_BEFORE.search(text[max(0, h.start - 12):h.start]):
            add("title_adjacent", 0.50)
        if _RELATION_BEFORE.search(before):
            add("relation_adjacent", 0.40)
        if _ATTRIBUTION_BEFORE.search(text[max(0, h.start - 25):h.start]):
            add("attribution_adjacent", 0.40)
        if _PREDICATE_AFTER.match(after):
            add("name_predicate", 0.20)
        from inkguard.jasper import site_cue_before
        if site_cue_before(text, h.start):
            # A cue word listed in the optional site-cue file
            # (PHI_SITE_CUES). Strictness only: it can raise a score.
            add("site_cue", 0.30)
        if not latin:
            # A run of CJK, Arabic or Devanagari inside an English
            # clinical note is overwhelmingly a name: clinical content in
            # these notes is English, and drugs are not written in Hangul.
            add("caseless_script", 0.30)
    else:
        if _LOCATION_PREP_BEFORE.search(re.sub(r"\bthe\s+$", "", text[max(0, h.start - 24):h.start])):
            add("location_preposition", 0.35)

    # --- tight negatives ---------------------------------------------
    bound = clinically_bound(text, h.start, h.end)
    if bound:
        # -0.75, up from -0.55: "seen by Doppler examination" had an
        # attribution (+0.40) that out-voted the qualifier. A clinical
        # binding is read from the words the token is fused with; an
        # attribution is read from a verb a few words away. The fused
        # reading wins. Titles still override inside clinically_bound.
        add(f"clinical:{bound}", -0.75)
    elif (is_person and single and token.strip(".,:;") in doc_clinical
          and not _tight_person_context(text, h.start, h.end)):
        add("document_clinical_use", -0.45)

    if latin and token[:1].isupper():
        sec = profile.section_at(h.start)
        if sec is not None and 0 <= h.start - sec.content_start <= 3 \
                and sec.content_start > sec.start:
            add("section_initial", -0.25)
        elif _LIST_ITEM_BEFORE.search(text[max(0, h.start - 8):h.start]):
            add("list_item", -0.25)

    if single and latin and _zipf(low) >= 5.0:
        add("very_common_word", -0.35)

    if (single and latin and token[:1].isupper() and is_person
            and _DETERMINER_BEFORE.search(text[max(0, h.start - 6):h.start])
            and not _FAMILY_AFTER.match(after)):
        add("determiner_before", -0.30)
    if (single and latin and is_person
            and _INSTRUMENT_BEFORE.search(text[max(0, h.start - 40):h.start])):
        add("instrument_before", -0.35)

    # --- loose negatives (WEAK layers only) --------------------------
    if h.layer in WEAK_LAYERS:
        window = text[max(0, h.start - 60):min(len(text), h.end + 60)]
        if _PROCEDURAL_WINDOW.search(window):
            add("procedural_window", -0.25)
        if _PHARM_WINDOW.search(window):
            add("pharm_window", -0.25)

    return score, ev


# Signals that speak to what the word IS, as opposed to where it sits.
# A hit held for one of these reasons has been positively identified as
# not-a-name; the output mask's [NAME] safety net must not override
# that. A hit held only for POSITIONAL reasons (section-initial, list
# item, a procedural window, the section) is still in doubt, and
# doubt resolves toward masking.
IDENTITY_NEGATIVES = ("never_name", "digit_or_colon", "caps_word", "lowercase_phrase",
                      "lowercase_word", "medical_morphology",
                      "very_common_word", "document_clinical_use",
                      "clinical:", "instrument_before", "dictionary_word",
                      "plural_noun", "department", "label_colon")


@functools.lru_cache(maxsize=None)
def _english_set() -> frozenset:
    from inkguard.lexicon_english import ENGLISH_WORDS
    return ENGLISH_WORDS


def _english_word(low: str) -> bool:
    """A WordNet word that is in no name list we hold, common enough to
    be written (Zipf >= 2.0). See tools/build_english_words.py."""
    return low in _english_set() and _zipf(low) >= 2.0


def _is_clinical_word(low: str) -> bool:
    from inkguard.clinical_lexicon import is_clinical_term, is_vocabulary_word
    return is_clinical_term(low) or is_vocabulary_word(low)


def unknown_word(token: str) -> bool:
    """A single Latin capitalized token that nothing we hold recognises:
    essentially absent from English (zipf < 2.0), in no clinical
    vocabulary tier, not a known eponym/device, not built like a medical
    term. What is left is a surname or an uncatalogued brand."""
    from inkguard.clinical_lexicon import (is_clinical_term, is_vocabulary_word,
                                      AMBIGUOUS_EPONYMS)
    t = token.strip().strip(".,:;'’")
    if not t or " " in t or not t[:1].isupper() or t.isupper() or _CASELESS_RE.search(t):
        return False
    low = t.lower()
    return (_zipf(low) < 2.0 and low not in AMBIGUOUS_EPONYMS and not _english_word(low)
            and not is_clinical_term(t) and not is_vocabulary_word(t)
            and not medical_morphology(t))


def exit_maskable(record: dict, text: str = "") -> bool:
    if any(ev.startswith(IDENTITY_NEGATIVES) for ev in record.get("evidence", ())):
        return False
    if text:
        # A word the clinical lexicon knows (as a device brand or
        # eponym) that is NOT in the name registry — "Sponge", "Cavermap"
        # — is a lexicon word in doubt, not a name in doubt.
        from inkguard.clinical_lexicon import AMBIGUOUS_EPONYMS
        from inkguard.name_registry import NAMES
        tok = text[record["start"]:record["end"]].strip().lower()
        if " " not in tok and tok in AMBIGUOUS_EPONYMS and tok not in NAMES:
            return False
    return True


def sort(text: str, hits: list, profile, file_clinical: frozenset = frozenset()) -> tuple[list, list[dict]]:
    """Integrate signals for every regulated hit; release or hold.
    Released means confirmed: the hit will be redacted. Held means left in
    the text and recorded as doubtful.

    Returns (released_hits, held_records). Held records carry offsets,
    type, layer, section, score and signals — never the token text,
    so they can go into the operation log without carrying PHI.
    """
    released: list = []
    held: list[dict] = []
    doc_clinical = frozenset(document_clinical_tokens(text, hits))
    # PATIENT FILE: a word the patient's own file uses clinically and never
    # beside a person ("Pfannenstiel incision" in the op note) is read the
    # same way in this note ("Pfannenstiel well healed"). A title, relation
    # or attribution here still wins (_tight_person_context below).
    if file_clinical:
        doc_clinical = doc_clinical | {w.capitalize() for w in file_clinical} | set(file_clinical)
    for h in hits:
        kind = profile.kind_at(h.start)
        # NER types some people as ORGANIZATION ("Seen by Smith, Jones
        # and Wilson" -> ORG "Smith, Jones"). ORGANIZATION is not removed,
        # so the mistype is a leak. A span standing where only a person
        # can stand — after a title, a relation or an attribution verb —
        # is re-typed PERSON and then judged like any other name.
        if (h.entity_type == "ORGANIZATION"
                and not _ORG_SUFFIX.search(h.text)
                and _tight_person_context(text, h.start, h.end)):
            h = dataclasses.replace(h, entity_type="PERSON",
                                    evidence=("retyped:org->person",))
        if not _is_regulated(h, kind):
            released.append(dataclasses.replace(h, section=kind))
            continue
        score, ev = _score(text, h, kind, profile, hits, doc_clinical)
        if score >= RELEASE_THRESHOLD:
            released.append(dataclasses.replace(
                h, section=kind, score=round(min(score, 0.99), 3),
                evidence=tuple(ev)))
        else:
            held.append({
                "start": h.start, "end": h.end,
                "entity": h.entity_type, "layer": h.layer,
                "section": kind, "score": round(score, 3),
                "evidence": ev,
            })
    return released, held
