"""
ROLES — why a proper noun is in the note, read from the words around it.

Clinicians write names for reasons. A consultant's name at the foot of a
note is a sign-off; "Trauma attending X" records who was responsible; an
employer in the social history says something about the patient's time,
income or exposure; a clinic in the plan says where care continues. The
reason decides WHAT the noun is, and so which placeholder stands in for
it. It never decides WHETHER a name it has found is removed: every one it
finds is.

Three kinds of role are read here:

  PROVIDER ROLE   "attending", "anesthesiologist", "scrub tech", "signed
                  out to", "cosigned by" ... followed by a name. The role
                  word stays in the text, so the paper trail of who signed
                  on, who signed off and who was responsible survives with
                  [NAME] in place of the person.

  WORKPLACE       "<Name> Bakery", "works nights at <Name> Elementary
                  School", "Employer: <Name> Landscaping". The proper part
                  becomes [EMPLOYER]; the kind of workplace ("Bakery",
                  "Elementary School") stays, because that is the clinical
                  context — shift work, physical labour, exposures.
                  45 CFR 164.514(b)(2)(i)(A) lists employers' names among
                  the Safe Harbor identifiers, so they are always
                  removed: no setting can keep them (talc.keepable).

  FACILITY        "<Name> clinic", "<Name> Medical Center". A named place of
                  care — not one of the eighteen Safe Harbor identifiers,
                  but it narrows where the patient lives and is removed by
                  tool policy ([FACILITY]). Services ("Coumadin clinic",
                  "Hand clinic") are generic and stay; the name part of a
                  named hospital ("Mercy" in "Mercy Hospital") is removed.

A fourth, SITE CUES, is what a practice teaches its own copy: words that
stood before names the tool missed in that practice's own review
("trauma attending"). Staff copy each other's phrasing, so a cue that hid
a name twice will hide one again. Site cues can only make the engine
STRICTER — they raise a held name's score; nothing a site teaches can make
the tool keep a word it would otherwise remove.
"""
from __future__ import annotations

import functools
import json
import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RoleSpan:
    start: int
    end: int
    entity: str          # PERSON | EMPLOYER | FACILITY
    role: str            # the cue that placed it ("attending", "bakery", ...)
    tail_end: int = 0    # end of the generic kind word kept after it (workplace/facility)


# ---------------------------------------------------------------------
# token judgement
# ---------------------------------------------------------------------

_MONTHS_DAYS = frozenset(
    "january february march april may june july august september october "
    "november december jan feb mar apr jun jul aug sep sept oct nov dec monday "
    "tuesday wednesday thursday friday saturday sunday mon tue tues wed thu thur "
    "thurs fri sat sun".split())
_JOINERS = frozenset({"st.", "st", "saint", "san", "santa", "mt.", "mt", "mount", "&", "and", "of", "de", "la"})


def _zipf(w: str) -> float:
    from inkguard.garnet import _zipf as z
    return z(w)


def _names() -> frozenset:
    from inkguard.name_registry import NAMES
    return NAMES


def _clinical(w: str) -> bool:
    from inkguard.clinical_lexicon import is_clinical_term, is_vocabulary_word
    return is_clinical_term(w) or is_vocabulary_word(w)


def proper_token(tok: str) -> bool:
    """A capitalised token that names someone or something in particular:
    in the name registry, unknown to every vocabulary, or rare in English
    (Zipf < 3.0) — and not a clinical word or an acronym."""
    t = tok.strip(".,;:'’").replace("’s", "").replace("'s", "")
    if not t or not t[:1].isupper():
        return False
    low = t.lower()
    if low in _MONTHS_DAYS or low in _JOINERS:
        return False
    if t.isupper() and len(t) <= 5:       # ENT, GI, VA, OB: services
        return False
    if _clinical(low):
        return False
    if low in _names():
        # "Hand clinic", "Family Health": a name in the registry that is
        # also an everyday word is a service here. "Smith" (Zipf 4.3)
        # and "Mercy" (3.9) still count as names.
        return _zipf(low) < 4.5
    from inkguard.garnet import unknown_word
    return unknown_word(t) or _zipf(low) < 3.0


def _name_token_ok(tok: str) -> bool:
    """For provider-role frames: a token that can be part of a person's
    name. Stricter than proper_token on common words: 'Physician' after
    'Resident' is a job title, not a surname."""
    t = tok.strip(".,;:'’")
    if not t or not t[:1].isupper() or t.isupper() and len(t) > 1:
        return False
    low = t.lower()
    if low in _MONTHS_DAYS or _clinical(low):
        return False
    return low in _names() or _zipf(low) < 3.0


# ---------------------------------------------------------------------
# PROVIDER ROLE frames
# ---------------------------------------------------------------------

_ROLE = (
    r"attending|attg|attd|atdg|resident|fellow|intern|anesthesiologist|anesthetist|surgeon|co-?surgeon|"
    r"assistant\s+surgeon|first\s+assist(?:ant)?|hospitalist|intensivist|proceduralist|"
    r"[a-z]{3,}ologist|[a-z]{3,}iatrist|pcp|crna|scrub(?:\s+tech)?|circulator|"
    r"circulating(?:\s+(?:nurse|rn))?|charge\s+nurse|primary\s+nurse|bedside\s+nurse|"
    r"nurse|consultant|covering|cosigned\s+by|co-signed\s+by|countersigned\s+by|"
    r"signed\s+(?:out|off|over)\s+to|hand(?:ed)?\s*-?\s*off\s+to|care\s+transferred\s+to|"
    r"transferred\s+care\s+to|taken\s+over\s+by|relieved\s+by|paged|notified|informed|"
    r"author|scribe|scribed\s+by|note\s+by|entered\s+by|verified\s+by|attested\s+by|"
    # The rest of a surgical and rehab team.
    r"anaesthesia|anesthesia|anaesthetist|anaesthesiologist|registrar|house\s+officer|"
    r"perfusionist|technologist|neuromonitoring|radiographer|sonographer|therapist|"
    r"physiotherapist|case\s+manager|social\s+worker|coordinator|navigator|"
    r"(?:clinical|device|company|sales)\s+(?:specialist|rep(?:resentative)?)|rep|adjuster|"
    r"attorney|counsel|paralegal|caseworker|athletic\s+trainer|trainer|pharmacist|"
    r"cc|copied\s+to|dictated\s+by|transcribed\s+by|reviewed\s+by|read\s+by|interpreted\s+by"
)
_ROLE_FRAME = re.compile(
    rf"(?<![\w-])(?i:(?P<role>{_ROLE}))(?:[ \t]*[:,][ \t]*|[ \t]+)(?:(?i:dr)\.?[ \t]*)?"
    r"(?P<name>(?:[A-Z]\.[ \t]*)?[A-Z][A-Za-zÀ-ɏ'’-]+(?:[ \t]+[A-Z][A-Za-zÀ-ɏ'’-]*\.?){0,2})")


# "Obregon (charge) aware", "Keller (RN) notified": a name with its role
# in brackets right after it.
_ROLE_IN_BRACKETS = re.compile(
    r"(?P<name>[A-Z][A-Za-z'’-]+(?:[ \t]+[A-Z][A-Za-z'’-]+){0,2})[ \t]*\((?i:(?P<role>"
    r"charge(?:\s+(?:nurse|rn))?|rn|lpn|md|do|np|pa|pa-c|crna|attending|resident|fellow|"
    r"intern|scrub|circulator|anesthesia|surgeon|pcp|nurse|tech|rt|pt|ot|sw|cm|"
    r"case\s+manager|social\s+work(?:er)?|chaplain|interpreter|pharmacist|pharmacy))\)")


def provider_role_names(text: str) -> list[RoleSpan]:
    out: list[RoleSpan] = []
    for m in _ROLE_IN_BRACKETS.finditer(text):
        toks = [t for t in re.finditer(r"[A-Z][A-Za-z'’-]*", m.group("name"))]
        # Keep the trailing run of name-like tokens ("Obregon", not "Charge Obregon").
        keep = []
        for t in reversed(toks):
            if not _name_token_ok(t.group()):
                break
            keep.insert(0, t)
        if keep:
            base = m.start("name")
            out.append(RoleSpan(base + keep[0].start(), base + keep[-1].end(), "PERSON",
                                m.group("role").lower()))
    for m in _ROLE_FRAME.finditer(text):
        toks = list(re.finditer(r"[A-Z][A-Za-zÀ-ɏ'’-]*\.?", m.group("name")))
        keep = []
        for t in toks:
            if not _name_token_ok(t.group()):
                break
            keep.append(t)
        if not keep:
            continue
        base = m.start("name")
        s = base + keep[0].start()
        e = base + keep[-1].end()
        while e > s and text[e - 1] in ".,":
            e -= 1
        out.append(RoleSpan(s, e, "PERSON", re.sub(r"\s+", " ", m.group("role").lower())))
    return out


# ---------------------------------------------------------------------
# WORKPLACE and FACILITY
# ---------------------------------------------------------------------

_WORKPLACE_KIND = (
    r"Foods?|Bakery|Bakeries|Plumbing|Construction|Logistics|Landscaping|Trucking|Printing|"
    r"Auto\s+Body|Autobody|Auto\s+Repair|Automotive|Motors|Dental\s+Group|"
    r"(?:Elementary|Middle|High|Primary|Charter|Grammar)(?:\s+School)?|Academy|"
    r"Inc\.?|LLC|L\.L\.C\.|Corp\.?|Corporation|Company|Co\.|Industries|Manufacturing|"
    r"Enterprises|Farms?|Electric|Electrical|Roofing|Painting|Cleaning|Janitorial|"
    r"Transport|Transportation|Freight|Warehouse|Distribution|Market|Grocery|Groceries|"
    r"Supermarket|Restaurant|Diner|Caf[eé]|Pizza|Pizzeria|Salon|Barbershop|Hardware|"
    r"Supply|Lumber|Mills?|Steel|Mining|Bank|Credit\s+Union|Realty|Properties|"
    r"Law\s+(?:Firm|Office|Group)|Consulting|Solutions|Technologies|Systems|Staffing|"
    r"Security|Moving|Towing|Welding|Machining|Fabrication|Garage|Tires?|Brewery|"
    r"Winery|Dairy|Poultry|Meats|Seafood|Catering|Daycare|Child\s*Care|Casino|Hotel|"
    r"Motel|Inn|Resort|Airlines?|Courier|Delivery|Shipping|Packaging|Plastics|"
    r"Chemicals?|Textiles?|Furniture|Flooring|Carpentry|HVAC|Heating(?:\s+and\s+Cooling)?|"
    r"Masonry|Concrete|Excavation|Demolition|Group|Partners|Holdings|Services|"
    # Law firms and insurers, named in the records of injured patients.
    r"LLP|L\.L\.P\.|PLLC|P\.C\.|P\.A\.|Law\s+Offices?(?:\s+of)?|Attorneys(?:\s+at\s+Law)?|"
    r"Injury\s+Lawyers|Insurance(?:\s+(?:Company|Group|Agency))?|Mutual|Casualty|Assurance|"
    r"Indemnity|Claims\s+(?:Office|Services|Management)"
)
_FACILITY_KIND = (
    r"(?i:clinic|hospital|infirmary|hospice|pharmacy|sanitarium)|"
    r"Medical\s+(?:Center|Centre|Group|Associates|Clinic|Plaza|Building)|"
    r"Health(?:\s+(?:Center|Centre|System|Clinic|Partners|Services))?|Healthcare|Health\s+Care|"
    r"(?:Ambulatory\s+)?Surg(?:ery|ical)\s+(?:Center|Centre|Hospital)|Surgicenter|"
    r"Urgent\s+Care|Family\s+(?:Practice|Medicine|Health)|Primary\s+Care|Internal\s+Medicine|"
    r"Pediatrics|Paediatrics|Associates|Rehab(?:ilitation)?(?:\s+(?:Center|Hospital))?|"
    r"(?:Skilled\s+)?Nursing(?:\s+(?:Home|Center|Facility))|Care\s+Center|Dialysis(?:\s+Center)?|"
    r"Imaging(?:\s+Center)?|Radiology|Home\s+Health|Assisted\s+Living|Senior\s+Living|"
    r"Physical\s+Therapy|Institute|Cancer\s+(?:Center|Institute)|Memorial|"
    # Care settings around a neurosurgical patient, and beyond:
    # psychiatric and recovery care, infusion and surgery
    # centres, specialty practices, foster and group homes, shelters.
    r"Behavioral\s+Health|Behavioural\s+Health|Mental\s+Health(?:\s+Center)?|Psychiatric"
    r"(?:\s+(?:Hospital|Center|Institute|Unit))?|Recovery\s+Center|Treatment\s+Center|Detox|"
    r"Infusion\s+Center|Wellness\s+Center|Sports\s+Medicine|Orthopa?edics|Neurosurgery|"
    r"Neurology(?:\s+Associates)?|Spine\s+(?:Center|Institute|Associates)|Pain\s+(?:Center|Clinic|"
    r"Management|Institute)|Chiropractic|Acupuncture|Naturopathic|Family\s+Services|"
    r"Children'?s\s+Services|Foster\s+(?:Agency|Care)|Group\s+Home|Shelter|Adult\s+Day(?:\s+Center)?|"
    r"Memory\s+Care|Care\s+Home|Retirement\s+(?:Home|Community)|LTACH|Hospice\s+House|"
    r"Mobile\s+Clinic|Health\s+Department|Public\s+Health|"
    r"(?:Level\s+I{1,3}\s+)?Trauma\s+Center|(?:Comprehensive\s+)?Stroke\s+Center|"
    r"Neurosciences?\s+(?:Center|Institute)|Brain\s+(?:Tumou?r\s+)?(?:Center|Institute)|"
    r"Craniofacial\s+(?:Program|Center)|Open\s+MRI|Diagnostic\s+Imaging|Spinal\s+Injuries\s+Centre|"
    r"Eye\s+(?:Institute|Center|Associates)|Neuromodulation\s+Center|Movement\s+Disorders|"
    r"Hand\s+and\s+Nerve\s+Center|Rehabilitation\s+(?:Hospital|Institute|Center)|"
    r"Oral(?:\s+and\s+Maxillofacial)?\s+Surgery|Dental(?:\s+(?:Care|Center|Clinic|Associates|Group|"
    r"Office|Arts))?|Dentistry|Orthodontics|Periodontics|Endodontics|Smile\s+Center"
)
# A run of capitalised tokens (with Saint/Mt./&/of joiners) right before
# the kind word, on the same line.
_RUN = r"(?:(?:St\.|Mt\.|[A-Z][A-Za-z'’.-]*|&|of|de|la)[ \t]+){0,4}?[A-Z][A-Za-z'’-]*(?:['’]s)?"
_WORKPLACE_SUFFIX = re.compile(rf"(?<![\w-])(?P<run>{_RUN})[ \t]+(?P<kind>(?:{_WORKPLACE_KIND}))(?![\w-])")
_FACILITY_SUFFIX = re.compile(rf"(?<![\w-])(?P<run>{_RUN})[ \t]+(?P<kind>(?:{_FACILITY_KIND}))(?![\w-])")
# "works nights at X", "employed by X", "Employer: X", "retired from X".
_WORK_CUE = re.compile(
    r"(?i:\b(?:works?|worked|working|employed|employment|job|shifts?|retired|laid\s+off|"
    r"fired|quit|volunteers?|interns?)(?:[ \t]+[a-z]+){0,3}?[ \t]+(?:at|for|by|with|from)"
    r"|\bemployer|\bemployed\s+by|\bworkplace|\bplace\s+of\s+(?:work|employment))"
    r"[ \t]*:?[ \t]*(?:the[ \t]+)?"
    rf"(?P<run>(?:(?:St\.|[A-Z][A-Za-z'’.-]*|&|of)[ \t]+){{0,3}}[A-Z][A-Za-z'’-]*(?:['’]s)?)")


def _proper_extent(text: str, run_start: int, run: str) -> tuple[int, int] | None:
    """From the first proper token of the run to its end, pulling in a
    Saint/Mt. joiner directly before it. None if nothing in the run is
    proper ("Coumadin clinic", "Child Protective Services")."""
    toks = list(re.finditer(r"St\.|Mt\.|[A-Za-z][A-Za-z'’.-]*|&", run))
    first = next((i for i, t in enumerate(toks) if proper_token(t.group())), None)
    if first is None:
        return None
    if first > 0 and toks[first - 1].group().lower().rstrip(".") in ("st", "saint", "mt", "mount", "san", "santa"):
        first -= 1
    s = run_start + toks[first].start()
    e = run_start + toks[-1].end()
    # The run may have swallowed a label or a sentence start that is not
    # part of the name ("Plan Steuber" is not possible after a colon, but
    # "Follow Steuber" is): only tokens from the first proper one on count.
    return (s, e) if s < e else None


# Words that describe a kind of organisation rather than name one:
# "Child Protective Services", "Community Health", "Public Works".
_GENERIC_ORG_WORDS = frozenset(
    "child children protective social human family community public general local "
    "national state county city federal united american home health medical emergency "
    "fire police school fast food retail the a an our my his her their new old big small "
    "north south east west northern southern eastern western central regional district "
    "department dept office support "
    # Services named by what they treat: "Wound Care Center", "Sleep
    # Center", "Heart Institute" describe a service, not a place's name.
    "wound sleep heart vein vascular eye vision skin breast cancer stroke spine pain hand foot "
    "kidney liver lung weight diabetes dialysis infusion imaging women's womens children's "
    "cardiac surgical rehab rehabilitation behavioral urgent primary specialty memory "
    "anticoagulation coumadin travel student employee occupational sports "
    # Kinds of place, not names: "Walk-in clinic", "Outside hospital".
    "walk-in mobile free outside critical access long-term acute care located at provider "
    "transferred from teaching rural private satellite affiliated same another other referring "
    "this that previous prior nearest closest academic tertiary".split())


def _business_extent(text: str, run_start: int, run: str) -> tuple[int, int] | None:
    """For a WORKPLACE kind word only: a business named with ordinary
    words ("Pause Landscaping", "Sunrise Bakery") is still an employer's
    name. Taken when the run is capitalised mid-sentence (so the capital
    is not just a sentence start) and is not a generic descriptor."""
    before = text[:run_start].rstrip(" \t")
    at_start = not before or before[-1] in ".!?\n"
    toks = list(re.finditer(r"[A-Za-z][A-Za-z'’.-]*", run))
    # A line in capitals gives capital letters no meaning.
    if run.isupper():
        return None
    # At the start of a line or sentence a capital means less: a letterhead
    # or a sentence may open with the place's name ("Cedar Lantern
    # Rehabilitation Hospital discharge"), so two or more name words are
    # needed there.
    if at_start and sum(1 for t in toks if t.group()[:1].isupper()) < 2:
        return None
    keep = []
    for t in reversed(toks):
        w = t.group().strip(".").lower()
        if not t.group()[:1].isupper() or w in _GENERIC_ORG_WORDS or _clinical(w):
            break
        keep.insert(0, t)
    if not keep:
        return None
    return run_start + keep[0].start(), run_start + keep[-1].end()


def workplaces_and_facilities(text: str) -> list[RoleSpan]:
    out: list[RoleSpan] = []
    seen: list[tuple[int, int]] = []

    def add(s: int, e: int, entity: str, role: str, tail_end: int) -> None:
        if any(s < b and a < e for a, b in seen):
            return
        seen.append((s, e))
        out.append(RoleSpan(s, e, entity, role, tail_end))

    for rx, entity in ((_FACILITY_SUFFIX, "FACILITY"), (_WORKPLACE_SUFFIX, "EMPLOYER")):
        for m in rx.finditer(text):
            ext = _proper_extent(text, m.start("run"), m.group("run"))
            # A place named with ordinary words ("Sunrise Bakery",
            # "Riverside Behavioral Health", "Coastal Neurosurgery") is
            # still a name.
            if ext is None:
                ext = _business_extent(text, m.start("run"), m.group("run"))
            if ext:
                add(ext[0], ext[1], entity, m.group("kind").lower(), m.end("kind"))
    for m in _WORK_CUE.finditer(text):
        ext = _proper_extent(text, m.start("run"), m.group("run"))
        if ext:
            add(ext[0], ext[1], "EMPLOYER", "work cue", ext[1])
    return out


# ---------------------------------------------------------------------
# SITE CUES — what a practice teaches its own copy
# ---------------------------------------------------------------------

SITE_CUE_MIN_COUNT = 2


@functools.lru_cache(maxsize=4)
def _load_site_cues(path: str, mtime: float) -> tuple[str, ...]:
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        return ()
    cues = data.get("learned_cues", data) if isinstance(data, dict) else {}
    out = []
    for cue, n in (cues.items() if isinstance(cues, dict) else []):
        if isinstance(cue, str) and isinstance(n, int) and n >= SITE_CUE_MIN_COUNT:
            cue = cue.strip().lower()
            if re.fullmatch(r"[a-z][a-z/-]*(?: [a-z][a-z/-]*)?", cue):
                out.append(cue)
    return tuple(sorted(out))


def site_cues() -> tuple[str, ...]:
    path = os.getenv("PHI_SITE_CUES", "")
    if not path or not os.path.exists(path):
        return ()
    return _load_site_cues(path, os.path.getmtime(path))


def site_cue_before(text: str, start: int) -> str | None:
    """The learned cue standing right before this token on its line —
    directly, or with one capitalised token between ("attending Jianhai
    <Mayberry>")."""
    cues = site_cues()
    if not cues:
        return None
    line = text[text.rfind("\n", 0, start) + 1:start].lower()
    for cue in cues:
        if re.search(rf"(?<![a-z]){re.escape(cue)}[ \t:,]+(?:[a-z][a-z'’-]*[ \t]+)?$", line):
            return cue
    return None


# ---------------------------------------------------------------------
# READ THE BOTTOM FIRST — who wrote this note, and why
# ---------------------------------------------------------------------
# A clinician reading a stranger's note looks at the sign-off before the
# body: the author, their credential, their service, sometimes a pager.
# That tells the reader whose voice the note is in and why it exists —
# and it tells this tool two things:
#   1. the author's (and co-signers') names, so every other mention of
#      them in the body is found too ("Mayberry" in the plan, signed
#      "Jianhai Mayberry, MD" at the foot);
#   2. what kind of writer this is. Nursing documentation is written to
#      protect against incomplete records: it names more people (family at
#      bedside, who was paged, who was notified) in more places. For those
#      notes the [NAME] safety net runs in every section, not only where
#      names usually live.

_CREDENTIALS = {
    "physician": r"MD|M\.D\.|DO|D\.O\.|MBBS|MBChB",
    "nurse": r"RN|R\.N\.|BSN|MSN|LPN|LVN|CNA|CCRN|CEN",
    "app": r"NP|FNP|FNP-C|AGACNP|DNP|APRN|PA|PA-C|CNM|CRNA",
    "therapist": r"PT|DPT|OT|OTR|SLP|RT|RRT|LCSW|MSW|LMFT|PsyD|PhD",
    "pharmacist": r"PharmD|RPh",
    "student": r"MS[1-4]|MSIV|MSIII|medical\s+student|student",
    "scribe": r"scribe",
    # The rest of a surgical patient's team, and the people around an
    # injured patient's record: technologists in the
    # operating room and the reading room, coders and billers, device
    # representatives, case managers, and lawyers.
    "technologist": r"CST|CNIM|R\.?\s?EEG\s?T\.?|ARRT|RT\(R\)|RT\(MR\)|RT\(CT\)|RDMS|RVT|"
                    r"neuromonitoring\s+tech\w*|surgical\s+tech\w*|scrub\s+tech",
    "coder": r"CPC|CPC-A|CCS|RHIT|RHIA|coder|biller|billing\s+specialist",
    "rep": r"(?:clinical|field|sales|product)\s+(?:specialist|representative)|device\s+rep|"
           r"company\s+rep|vendor\s+rep",
    "case_manager": r"CCM|case\s+manager|utilization\s+review|UR\s+nurse",
    "legal": r"Esq\.?|J\.D\.|attorney(?:\s+at\s+law)?|paralegal|counsel\s+for|law\s+offices?",
}
_CRED_RX = {k: re.compile(rf"(?<![\w-])(?:{v})(?![\w-])") for k, v in _CREDENTIALS.items()}
_NOTE_KINDS = (
    # The kinds whose own words give them away come first.
    ("legal", r"letter of protection|deposition|subpoena|law offices?|attorney|independent medical "
              r"exam|\bime\b|records request|release of information|workers'? comp|claimant|"
              r"adjuster|litigation|personal injury"),
    ("controlled_substance", r"\bcures\b|\bpdmp\b|prescription (?:drug )?monitoring|controlled "
                             r"substance agreement|pain (?:contract|agreement)|\bmme\b|morphine "
                             r"milligram equivalent|urine drug screen|pill count"),
    ("death", r"pronounced (?:dead|deceased|at)|time of death|death note|death certificate|"
              r"autopsy|organ procurement|brain death|declared dead"),
    ("immunization", r"immuni[sz]ation record|vaccines? administered|vaccine lot|\bvis\b (?:given|"
                     r"provided)|vaccination record|\bcvx\b"),
    ("telehealth", r"telehealth|telemedicine|video visit|virtual visit|audio-only|tele-visit|"
                   r"\bzoom\b|doxy\.me|patient located at|provider located at"),
    ("radiology", r"(?m)^\s*(?:technique|comparison|clinical (?:history|indication)|exam(?:ination)?)"
                  r"\s*:.*$(?:[\s\S]*)^\s*impression\s*:"),
    ("emergency", r"emergency department|\bed course\b|triage note|\bems\b|ambulance|trauma (?:bay|"
                  r"activation)|level [i1] trauma"),
    ("urgent_care", r"urgent care|walk-in clinic|retail clinic"),
    ("rehab", r"physical therapy|occupational therapy|speech(?:-language)? (?:therapy|pathology)|"
              r"plan of care|\bfim\b|inpatient rehab|skilled nursing|\bsnf\b"),
    ("operative", r"operative|procedure note|op note|brief op|preop|postop|anesthesia"),
    ("discharge", r"discharge summary|discharge instructions|d/c summary"),
    ("consult", r"consult|consultation|recommendations"),
    ("nursing", r"nursing|shift note|nurse note|i&o|intake and output|mar\b"),
    ("portal", r"^\s*(?:hi|hello|hey|dear)\b|thanks|thank you|thx|sent from my"),
    ("work_status", r"work status|return to work|fitness for duty|employer"),
    ("billing", r"\bcpt\b|\bicd-10\b|claim|guarantor|units x"),
    ("psychiatric", r"\bmse\b|mental status|psychiatr"),
    ("progress", r"progress note|soap|subjective|interval"),
)
_CLOSING = re.compile(
    r"(?im)^[ \t]*(?:thanks|thank\s+you|thx|regards|best|sincerely|cheers|respectfully)[ \t]*,?[ \t]*"
    r"(?:\n[ \t]*)?(?P<name>[A-Z][A-Za-z'’-]+(?:[ \t]+[A-Z][A-Za-z'’.-]*){0,2})[ \t]*$")
_BARE_NAME_LINE = re.compile(
    r"(?m)^[ \t]*(?:[-—–]{1,2}[ \t]*)?(?P<name>[A-Z][A-Za-z'’-]+(?:[ \t]+[A-Z]\.?)?(?:[ \t]+[A-Z][A-Za-z'’-]+){1,2})"
    r"[ \t]*(?:,[ \t]*[A-Za-z.-]{2,8}(?:[ \t]*,[ \t]*[A-Za-z.-]{2,8})*)?[ \t]*(?:\([^)\n]{0,40}\))?[ \t]*$")


@dataclass(frozen=True)
class Authorship:
    names: tuple            # ((start, end), ...) — offsets only, never text
    author_class: str       # physician | nurse | app | therapist | ... | unknown
    note_kind: str          # operative | discharge | ... | unknown
    signoff_start: int      # where the sign-off region begins

    def summary(self) -> dict:
        return {"author_class": self.author_class, "note_kind": self.note_kind,
                "signoff_names": len(self.names)}


def read_signoff(text: str) -> Authorship:
    """Read the last lines of the note first: sign-off names, credential,
    and what kind of note this is. Offsets only leave this function."""
    lines = [m for m in re.finditer(r"[^\n]*\S[^\n]*", text)]
    tail = lines[-6:]
    region_start = tail[0].start() if tail else len(text)
    region = text[region_start:]
    names: list[tuple[int, int]] = []

    def take(s: int, e: int) -> None:
        toks = list(re.finditer(r"[A-Z][A-Za-z'’-]*\.?", text[s:e]))
        good = [t for t in toks if _name_token_ok(t.group()) or re.fullmatch(r"[A-Z]\.?", t.group())]
        if len(good) < 1 or not any(_name_token_ok(t.group()) for t in good):
            return
        ss, ee = s + good[0].start(), s + good[-1].end()
        while ee > ss and text[ee - 1] in ".,":
            ee -= 1
        if not any(ss < b and a < ee for a, b in names):
            names.append((ss, ee))

    for m in _BARE_NAME_LINE.finditer(region):
        # A name alone on one of the last lines — with an optional
        # credential or a role in brackets — is a signature.
        s = region_start + m.start("name")
        take(s, s + len(m.group("name")))
    for m in _CLOSING.finditer(text):
        take(m.start("name"), m.end("name"))

    # Credential: the one written in the sign-off region counts most.
    author = "unknown"
    for cls, rx in _CRED_RX.items():
        if rx.search(region):
            author = cls
            break
    if author == "unknown":
        for cls, rx in _CRED_RX.items():
            if cls in ("physician", "nurse", "app") and rx.search(text):
                author = cls
                break
    low = text.lower()
    kind = "unknown"
    for k, rx in _NOTE_KINDS:
        if re.search(rx, low if k != "portal" else low[:200] + " " + low[-200:], re.M):
            kind = k
            break
    if kind == "nursing" and author == "unknown":
        author = "nurse"
    return Authorship(tuple(names), author, kind, region_start)


# Author classes whose notes get the [NAME] safety net in every section.
STRICT_EVERYWHERE = frozenset({"nurse", "scribe", "student", "technologist", "coder", "rep",
                               "case_manager", "legal"})
# Kinds of document that name many people in passing, wherever a person
# fits: adjusters and attorneys, who checked the monitoring database, who
# pronounced and who was told, who gave the vaccine.
STRICT_KINDS = frozenset({"nursing", "legal", "controlled_substance", "death", "immunization",
                          "billing", "work_status"})


# ---------------------------------------------------------------------
# FAMILY AND HEADER NAMES — where capital letters stop helping
# ---------------------------------------------------------------------
# Legacy systems print whole notes in capitals, and people are named by
# their relation to the patient: "DAUGHTER JELENA MARKOVICH IN PERTH",
# "wife Odalys is supportive", "kids Sunny (4) and Jax (2)". A model
# that reads names by their capital letter is blind in an all-caps
# note, and a word-name ("Wren", "Lake", "Sunny") looks like a word.
# The relation word is the evidence here; the token after it is read
# against the name registry and the vocabularies, never its case.

_KIN = (
    r"daughters?|sons?|wife|husband|spouse|partner|fianc[eé]e?|mother|mom|mum|mama|"
    r"father|dad|papa|sister|sis|brother|bro|grand(?:mother|ma|father|pa|son|daughter|child(?:ren)?)|"
    r"great[\s-]grand(?:mother|father|ma|pa)|aunt(?:ie)?|uncle|niece|nephew|cousin|"
    r"step(?:mother|father|son|daughter|mom|dad)|in[\s-]law|kids?|child(?:ren)?|parents?|"
    r"boyfriend|girlfriend|friend|best\s+friend|roommate|neighbou?r|landlord|landlady|boss|"
    r"supervisor|coworker|co-worker|guardian|caregiver|carer|sponsor|ex-?(?:wife|husband)|"
    r"emergency\s+contact|next\s+of\s+kin|nok|poa|proxy|baby(?:\s+(?:boy|girl))?|"
    # Naming words and the patient label: "PT ODELL FANNING", "goes by
    # Billie", "named JUNIPER", "MR OKORO" (titles in capitals, which the
    # model does not read as titles).
    r"pt|patient|named|called|goes\s+by|aka|a\.k\.a\.|nickname[ds]?|call\s+me|known\s+as|prefers|"
    r"MR|MRS|MS|MX|DR"
)
_NAMING_CUE = re.compile(r"(?i)(?:goes\s+by|aka|a\.k\.a\.|nickname[ds]?|call\s+me|known\s+as|prefers)\b")
_KIN_CUE = re.compile(rf"(?i)\b(?:{_KIN})\b[\s:,(]*(?:(?:mr|mrs|ms|miss|mx)\.?\s+)?")
_KIN_STOP = frozenset(
    "is was are were has had have and or but in on at of to for from with by the a an who "
    "whom whose which that this also not no yes here there he she they them his her their "
    "him it its called calls says said states reports will would can could may might did "
    "does do been being as so if then now still present alive deceased died passed living "
    "lives brought came comes visiting visited at bedside home none unknown same".split())
_TOKEN = re.compile(r"[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ'’\-]*")


def _kin_token(tok: str) -> bool:
    t = tok.strip("'’-")
    if len(t) < 2 or not t[:1].isupper():
        return False
    low = t.lower().replace("’", "'")
    if low in _KIN_STOP or low in _MONTHS_DAYS:
        return False
    from inkguard.garnet import NEVER_NAMES
    if low in NEVER_NAMES:
        return False
    capsed = t.isupper()
    if capsed and len(t) <= 2:          # MI, CA, DM: findings, not people
        return False
    if low in _names() and not (capsed and _clinical(low)) and _zipf(low) < (5.2 if capsed else 5.5):
        return True
    if _clinical(low):
        return False
    # A short all-capital word that is on no name list is an acronym
    # ("Patient SSN is", "PT EVAL"), not a person.
    if capsed and len(t) <= 4:
        return False
    from inkguard.garnet import unknown_word
    return unknown_word(t.title() if capsed else t) or _zipf(low) < 3.0


def _name_run(text: str, i: int, limit: int = 3) -> tuple[int, int] | None:
    """The run of name tokens starting at i (up to `limit`), or None."""
    end = None
    n = 0
    pos = i
    while n < limit:
        m = _TOKEN.match(text, pos)
        if not m or not _kin_token(m.group()):
            break
        end = m.end()
        n += 1
        nxt = re.match(r"[ \t]+", text[end:])
        if not nxt:
            break
        pos = end + nxt.end()
    return (i, end) if end else None


_EPONYM_TAIL = re.compile(
    r"(?i)disease|syndrome|lymphoma|palsy|sign|test|disorder|thyroiditis|an(?:a)?emia|ataxia|"
    r"dystrophy|sarcoma|tumou?r|reflex|phenomenon|criteria|score|scale|procedure|operation|"
    r"repair|fracture|maneuver|manoeuvre|classification|rule|chorea|dementia|esophagus|"
    r"ulcer|node|cyst|contracture|deformity|neuroma|neuralgia|aneurysm|anomaly|type")


def kin_names(text: str) -> list[RoleSpan]:
    """Names standing after a relation word, and the further names in the
    same list: "kids Sunny (4) and Jax (2)", "children, Remy and Odile"."""
    out: list[RoleSpan] = []
    for m in _KIN_CUE.finditer(text):
        pos = m.end()
        # A title stands right before its name: "MS, CGC" is a degree and
        # a certification after a name, not Ms. CGC.
        if re.match(r"(?:MR|MRS|MS|MX|DR)\b", m.group()) and "," in m.group():
            continue
        # A name the text says someone goes by is a name whatever its
        # shape: "goes by JB", "aka Twelve", "nicknamed A1".
        if _NAMING_CUE.match(m.group()):
            nick = re.match(r"[\"'“‘]?([A-Za-z0-9][\w'’-]{0,24})", text[pos:])
            if nick and nick.group(1).lower() not in _KIN_STOP:
                a = pos + nick.start(1)
                out.append(RoleSpan(a, a + len(nick.group(1)), "PERSON", "kin"))
                continue
        for _ in range(4):
            run = _name_run(text, pos)
            if not run:
                break
            # "uncle Hodgkin lymphoma", "mother Graves disease": a name
            # followed by a clinical word is an eponym in a family history.
            after = re.match(r"[\s'’s]*([A-Za-z]+)", text[run[1]:])
            if after and (_clinical(after.group(1).lower()) or _EPONYM_TAIL.fullmatch(after.group(1))):
                break
            out.append(RoleSpan(run[0], run[1], "PERSON", "kin"))
            tail = re.match(r"\s*(?:\(\s*\d{1,3}\s*(?:y|yo|yrs?|mo|m)?\s*\))?\s*(?:,|&|\band\b|\+)\s*",
                            text[run[1]:], re.I)
            if not tail:
                break
            pos = run[1] + tail.end()
    return out


_HEADER_NAME = re.compile(
    r"(?P<v>(?<![\w'’-])(?:(?:Mc|Mac|De|Van|Von)[ ])?[A-Z][A-Za-zÀ-ɏ'’\-]+,"
    r"[ \t]*(?:[A-Z][A-Za-zÀ-ɏ'’\-]+(?:[ \t]+[A-Z]\.?)?|[A-Z]\.(?=[ \t])))"
    r"(?=[ \t]*(?:\(|--|—|–|\||$)|[ \t]{1,12}(?:MRN|Mrn|mrn|DOB|D\.O\.B|Dob|dob|AGE|Age|DOS|SEX|Sex|"
    r"\d{1,3}[ \t]?[YyMm]|\d{1,2}/\d{1,2}/\d{2,4}|#|Acct|ACCT|Member|MEMBER)"
    # A record number with no label, after two spaces: "RILEY, ERIN  40-532195".
    r"|[ \t]{2,}[A-Z]{0,3}-?\d[\d-]{4,}\b)", re.M)
_HEADER_LEAD = re.compile(r"(?i)(?:\b(?:re|pt|patient|name|member|regarding|subject)\s*[:#.]\s*)$")


def header_names(text: str) -> list[RoleSpan]:
    """"SURNAME, GIVEN M" in the shape every records system prints it,
    beside an MRN, a birth date or an age, or after "Re:" / "Patient:".

    Returned as two spans, surname and given name, so the comma between
    them is not part of any removal (the ledger judges every character
    of a detection)."""
    from inkguard.garnet import NEVER_NAMES
    out: list[RoleSpan] = []
    for m in _HEADER_NAME.finditer(text):
        s, e = m.span("v")
        led = bool(_HEADER_LEAD.search(text[max(0, s - 14):s]))
        after = text[e:e + 14]
        # A dash after the name in a header line ("REPORT — SURNAME, GIVEN —
        # MRN") is a label too when a record number follows it.
        dashed = bool(re.match(r"[ \t]*[—–][ \t]*[A-Z]{2,5}-?\d{4,}", after + text[e + 14:e + 30]))
        labelled = dashed or bool(re.match(r"[ \t]{1,12}(?:MRN|Mrn|mrn|DOB|D\.O\.B|Dob|dob|AGE|Age|DOS|SEX|Sex|"
                                 r"\d{1,3}[ \t]?[YyMm]|\d{1,2}/\d{1,2}/\d{2,4}|#|Acct|ACCT|Member|MEMBER)"
                                 r"|[ \t]{2,}[A-Z]{0,3}-?\d[\d-]{4,}\b", after))
        # A line that merely ends after "Word, Word" is a place as often as
        # a person ("München, Germany"): it counts only under a label.
        if not labelled and not led:
            continue
        comma = text.index(",", s)
        sur = (s, comma)
        g0 = comma + 1 + (len(text[comma + 1:e]) - len(text[comma + 1:e].lstrip()))
        given = (g0, e)
        parts = [p for p in re.split(r"[,\s]+", m.group("v")) if len(p.strip(".")) > 1]
        if not parts or any(p.lower().strip(".") in NEVER_NAMES for p in parts):
            continue
        # Capitals are how records systems print names ("SMITH, JOHN  MRN"):
        # each word is read as a name in title case, as the dash header does.
        ok = [_kin_token(p.title() if p.isupper() else p) for p in parts]
        # "HUNT, B.  DOB ...": a surname with only an initial, beside a
        # record label, is a person whatever the surname also means.
        initial_only = bool(re.fullmatch(r"[A-Z]\.", text[given[0]:given[1]].strip()))
        # Beside a record label, one word that reads as a name is enough:
        # "BABINSKI, LARS  MRN" is a person even though Babinski is a sign.
        if all(ok) or ((led or labelled) and any(ok)) or (initial_only and labelled):
            out.append(RoleSpan(sur[0], sur[1], "PERSON", "header"))
            out.append(RoleSpan(given[0], given[1], "PERSON", "header"))
    # "REPORT — 02/28/2071 — HARLAN QUELLMANN-DORSEY": a header line split
    # by dashes, ending in a name in capitals with no comma. Taken when
    # every word reads as a name and the line also carries a date or a
    # record number.
    for m in _DASH_HEADER_NAME.finditer(text):
        line = text[text.rfind("\n", 0, m.start()) + 1:m.end()]
        if not re.search(r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Z]{2,5}-?\d{4,}", line):
            continue
        toks = m.group("v").split()
        if all(_kin_token(t.title() if t.isupper() else t) for t in toks):
            out.append(RoleSpan(m.start("v"), m.end("v"), "PERSON", "header"))
    return out


_DASH_HEADER_NAME = re.compile(
    r"(?m)[—–|][ \t]*(?P<v>[A-Z][A-Z'’-]+(?:[ \t]+[A-Z][A-Z'’-]+){1,2})[ \t]*(?=$|[—–|])")


# ---------------------------------------------------------------------
# TOWNS — by their place in an address or after "in"/"from"
# ---------------------------------------------------------------------
# The model finds towns by capital letters too, so "DULUTH, MN 55805",
# "SON FERGAL IN MELBOURNE" and "12 QUAY STREET, GALWAY" can be missed.
# A town is read here only where the sentence puts one: before a state
# or province and a postcode, or after a locative word, and then only if
# the gazetteer knows the word and it is not an everyday English word
# ("Reading", "Mobile", "Hope").

_STATE_CODE = (r"A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|"
               r"O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY]|ON|BC|AB|QC|MB|SK|NS|NB|NL|PE|"
               r"NSW|VIC|QLD|TAS|NT|ACT")
_TOWN_BEFORE_STATE = re.compile(
    r"(?P<v>(?<![\w'’-])[A-Z][A-Za-z.'’\-]+(?:[ ][A-Z][A-Za-z.'’\-]+){0,2}),?[ \t]+"
    rf"(?:{_STATE_CODE})\.?,?[ \t]+(?:\d{{4,5}}(?:-\d{{4}})?|[A-Z]\d[A-Z][ \t]?\d[A-Z]\d)\b")
_TOWN_AFTER = re.compile(
    r"(?i:\b(?:in|from|near|to|outside|of|at|visiting|moved|relocated|living|lives|born)[ \t]+)"
    r"(?P<v>[A-Z][A-Za-z'’\-]+(?:[ ][A-Z][A-Za-z'’\-]+)?)")
_TOWN_AFTER_STREET = re.compile(
    r"(?i:\b(?:street|st|road|rd|avenue|ave|lane|ln|drive|dr|court|ct|crescent|cres|row|mews|close|"
    r"way|place|pl|terrace|boulevard|blvd|quay|crossing|apt\.?[ \t]*\w+|unit[ \t]*\w+|suite[ \t]*\w+))"
    r"\.?,[ \t]+(?P<v>[A-Z][A-Za-z'’\-]+(?:[ ][A-Z][A-Za-z'’\-]+)?)")


# Towns named by an everyday word; after "in" they are usually the word.
_TOWN_WORDS = frozenset(
    "bath orange mobile reading hope march deal wells bridge sale ware rugby harbor harbour "
    "independence liberty union commerce normal temple eden globe surprise opportunity "
    "enterprise friendship unity plain plains home house hospital center centre college "
    "university wellness bed rest recovery bay beach lake river falls springs hills valley "
    "park garden gardens grove cross crossing junction station ridge pine oak lima chili "
    "china turkey jordan chad niger georgia nice split".split())


def _town(words: str, strict: bool = True) -> bool:
    """A gazetteer town. `strict` (a locative frame) also refuses words
    common in English; an address frame (before a state and postcode)
    is evidence enough on its own."""
    from inkguard.lexicon_gate import PLACES
    w = words.strip(".,").lower()
    if w not in PLACES or _clinical(w):
        return False
    if strict and " " not in w and (w in _TOWN_WORDS or _zipf(w) >= 5.5):
        return False
    return True


def place_names(text: str) -> list[RoleSpan]:
    out: list[RoleSpan] = []
    for m in _TOWN_BEFORE_STATE.finditer(text):
        s, e = m.span("v")
        words = m.group("v")
        # Keep only the trailing words that make a town ("WEST DULUTH").
        toks = words.split(" ")
        for k in range(len(toks)):
            cand = " ".join(toks[k:])
            if _town(cand, strict=False) or (k == len(toks) - 1 and not _clinical(cand.lower())
                               and _zipf(cand.lower()) < 4.0):
                off = s + len(" ".join(toks[:k])) + (1 if k else 0)
                out.append(RoleSpan(off, e, "LOCATION", "town:state"))
                break
    for rx, tag in ((_TOWN_AFTER, "town:locative"), (_TOWN_AFTER_STREET, "town:address")):
        for m in rx.finditer(text):
            v = m.group("v")
            # Mixed-case towns are the model's to find; this frame is for
            # the capitals it cannot read, and for the address slot.
            if tag == "town:locative" and not v.split(" ")[0].isupper():
                continue
            s = m.start("v")
            # Two words if the pair is a town ("Mount Lawley"), else the first.
            if " " in v and _town(v):
                out.append(RoleSpan(s, s + len(v), "LOCATION", tag))
            elif _town(v.split(" ")[0]):
                out.append(RoleSpan(s, s + len(v.split(" ")[0]), "LOCATION", tag))
    return out
