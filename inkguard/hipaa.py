"""
HIPAA Safe Harbor de-identification (45 CFR 164.514(b)(2)).

This module implements the legal standard. Safe Harbor names 18 specific
identifier categories. If all 18 are removed and the covered entity has no actual
knowledge the remainder could re-identify someone, the data is no
longer PHI and is out of HIPAA scope entirely.

THREE RULES, implemented explicitly here:

1. AGES. Ages under 90 are NOT identifiers and may be retained. Only
   ages 90+ must be removed or aggregated into "90 or older." This
   matters clinically: age drives differential diagnosis. Masking
   "67-year-old male" would remove one of the most diagnostically useful
   details in the note with no legal benefit.

2. DATES. All date elements more specific than YEAR must go — but the
   YEAR itself may stay (except the 90+ age edge case). More
   importantly, *relative* time ("3 days of symptoms", "onset 2 weeks
   ago") is NOT a date identifier and must be preserved. Clinical
   timelines are diagnostic. Losing "sudden onset 30 minutes ago"
   turns an acute presentation into an undated one.

3. ZIP CODES. The first three digits may be retained unless that
   3-digit geographic unit contains 20,000 or fewer people, in which
   case it must become 000. The restricted prefixes are enumerated by
   HHS and hardcoded below.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not make anyone HIPAA compliant. Safe Harbor is one of two
de-identification methods (the other is Expert Determination). Running
text through this does not substitute for a BAA, a risk assessment,
access controls, or encryption. See README.
"""
import re
from dataclasses import dataclass

# HHS-published list of 3-digit ZIP prefixes with population <= 20,000
# (the HHS list, based on 2000 Census data).
# These must be zeroed out entirely rather than truncated.
RESTRICTED_ZIP_PREFIXES: set[str] = {
    "036", "059", "063", "102", "203", "556", "692", "790", "821",
    "823", "830", "831", "878", "879", "884", "890", "893",
}

# The 18 Safe Harbor identifier categories, mapped to the entity types
# this tool's scanner emits. Used by coverage_report().
SAFE_HARBOR_MAP: dict[str, dict] = {
    "1_names":                  {"entities": ["PERSON"], "covered": True},
    "2_geographic_subdivisions": {"entities": ["LOCATION", "ZIP_CODE"], "covered": True},
    "3_dates":                  {"entities": ["DATE_TIME"], "covered": True},
    "4_telephone":              {"entities": ["PHONE_NUMBER"], "covered": True},
    "5_fax":                    {"entities": ["PHONE_NUMBER"], "covered": True},
    "6_email":                  {"entities": ["EMAIL_ADDRESS"], "covered": True},
    "7_ssn":                    {"entities": ["US_SSN"], "covered": True},
    "8_medical_record_number":  {"entities": ["MRN"], "covered": True},
    "9_health_plan_number":     {"entities": ["HEALTH_PLAN_ID"], "covered": True},
    "10_account_numbers":       {"entities": ["US_BANK_NUMBER", "CREDIT_CARD"], "covered": True},
    "11_certificate_license":   {"entities": ["US_DRIVER_LICENSE", "MEDICAL_LICENSE", "NPI"], "covered": True},
    "12_vehicle_identifiers":   {"entities": ["VEHICLE_ID"], "covered": True},
    "13_device_identifiers":    {"entities": ["DEVICE_ID"], "covered": True},
    "14_urls":                  {"entities": ["URL"], "covered": True},
    "15_ip_addresses":          {"entities": ["IP_ADDRESS"], "covered": True},
    "16_biometric_identifiers": {"entities": [], "covered": False,
                                 "note": "A text tool cannot detect fingerprints/voiceprints. "
                                         "Out of scope for text; becomes relevant "
                                         "only if audio/image content is added."},
    "17_facial_photographs":    {"entities": [], "covered": False,
                                 "note": "Pictures are not read. Inkguard reads typed "
                                         "text only; a photograph in a document is not checked."},
    "18_other_unique_id":       {"entities": ["MRN", "DEVICE_ID", "NPI"], "covered": True,
                                 "note": "Catch-all category; coverage is best-effort and "
                                         "may be incomplete."},
}


@dataclass
class AgeFinding:
    start: int
    end: int
    original: str
    replacement: str
    is_identifier: bool  # True only when age >= 90


# "89-year-old", "age 92", "92 y/o", "92yo", "aged 91"
AGE_PATTERN = re.compile(
    # "95 y.o." with dots too; the end is "no word
    # character follows", because "\b" never matches after a final dot.
    r"\b(?:(\d{1,3})\s*[-\s]?(?:year[-\s]?old|years?\s+old|y\.\s?o\.?|y/?o|yo|yrs?[-\s]?old)"
    r"|(?:age[d]?)\s*[:\s]\s*(\d{1,3}))(?!\w)",
    re.IGNORECASE,
)

# Appositive age: "Ezra Vantongeren, 54, presents with…", "Ms. Haddad (67) was
# seen". Common in dictated notes; NER reads the bare number as a date
# and masks it, which destroys the age while protecting nothing. The
# frame is narrow on purpose — a comma- or paren-enclosed 1–3 digit
# number directly after a name-like word AND followed by a clinical
# predicate — so "BP 120, 80, and" never qualifies. Ages >= 90 found
# here are generalized exactly like any other age.
APPOSITIVE_AGE_PATTERN = re.compile(
    r"(?:(?<=[A-Za-z\u00C0-\u024F.],\s)|(?<=[A-Za-z\u00C0-\u024F.]\s\()|(?<=[A-Za-z\u00C0-\u024F.]\())(\d{1,3})(?=\s*[,)]{1,2}\s*(?:an?\s+)?"
    r"(?:presents|presented|is|was|has|had|with|who|male|female|man|woman|"
    r"boy|girl|M|F|reports|returns|comes|came|here)\b)"
)

# Ages of 90 and over, in the shapes people actually write about family:
# "her mother Aster, 92, lives with them", "father is 91", "passed away at
# 97", "grandmother now ninety-one", "in her nineties". Safe Harbor makes
# every age over 89 an identifier whoever it belongs to, so this frame
# only needs a person cue nearby, never a clinical predicate. It is kept
# to 90-119 and refuses anything followed by a unit or a slash, so
# "SpO2 94%", "BP 108/90" and "glucose 110 mg/dL" never qualify.
_TENS = r"(?:ninety|one[\s-]hundred(?:\s+and)?)"
_ONES = r"(?:one|two|three|four|five|six|seven|eight|nine)"
_OLD_WORDS = rf"{_TENS}(?:[\s-]{_ONES})?"
_PERSON_CUE = (r"(?:mother|father|mom|mum|dad|grand\w*|great[\s-]grand\w*|aunt|uncle|"
               r"husband|wife|spouse|partner|widow\w*|neighbou?r|sister|brother|"
               r"in[\s-]law|resident|roommate|friend|she|he|who|pt|patient)")
OLD_AGE_PATTERN = re.compile(
    rf"(?:"
    # cue ... is/now/turned/aged/at 92
    rf"\b{_PERSON_CUE}\b[^.;\n]{{0,40}}?\b(?:is|now|turned|turns|aged?|at|of)\s+"
    rf"(?P<n1>9\d|1[01]\d|{_OLD_WORDS})(?![\d%/.,]?\d)(?!\s*(?:%|mg|mcg|ml|mmhg|bpm|kg|lb|/|°))"
    # Capitalised name, 92,  /  Name (92)
    # (the name may already be a placeholder by the time this runs)
    rf"|(?-i:\b[A-Z][A-Za-z\u00C0-\u024F'-]{{2,30}}|\[[A-Z_ ]{{2,20}}\]),\s(?P<n2>9\d|1[01]\d)(?=\s*[,)])"
    rf"|(?-i:\b[A-Z][A-Za-z\u00C0-\u024F'-]{{2,30}}|\[[A-Z_ ]{{2,20}}\])\s\((?P<n3>9\d|1[01]\d)\)"
    # spelled out with an age word: "ninety-one years", "ninety-one yo"
    rf"|\b(?P<n4>{_OLD_WORDS})(?=[\s-]*(?:years?|yrs?|y/?o|yo)\b)"
    # "in her nineties", "in his 90s"
    rf"|\b(?:in\s+(?:her|his|their)\s+)(?P<n5>nineties|90s|90's)"
    # Spelled out on its own: "(ninety three)", "grandmother Ada, ninety-two".
    # A count ("ninety percent", "ninety minutes") is refused by the unit.
    rf"|\b(?P<n7>{_OLD_WORDS})\b(?![\s-]*(?:percent|%|mg|mcg|ml|cc|minutes?|mins?|hours?|hrs?|days?|"
    rf"weeks?|months?|seconds?|secs?|lbs?|pounds?|kg|kilos?|degrees?|patients?|people|cases?|"
    rf"units?|tablets?|tabs?|pills?|steps?|times?|dollars?|cents?|bpm|mmhg|per\b|out\s+of))"
    # Chart shorthand: "91 y", "93 F", "(97 M)", "97M". A temperature
    # ("Temp 99 F") is refused in find_ages.
    rf"|(?-i:(?<![\w./°-])(?P<n6>9\d|1[01]\d)[ \t]?(?:y|yrs?|M|F)(?=[\s,;:)—–-]|$))"
    rf")",
    re.IGNORECASE,
)


def _old_age_value(word: str) -> int:
    w = word.lower().replace("-", " ")
    if w.isdigit():
        return int(w)
    if w in ("nineties", "90s", "90's"):
        return 90
    ones = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
            "seven": 7, "eight": 8, "nine": 9}
    base = 100 if "hundred" in w else 90
    last = w.split()[-1]
    return base + ones.get(last, 0)


# Relative/interval time — explicitly PRESERVED. Clinically essential.
#
# Two shapes: numeric intervals ("3 days ago") and relative-time words
# ("today", "yesterday", "the day", "last night"). The second shape was
# added after a 200-document test set showed that wordy relative
# expressions were a large share of date over-redaction.
#
# None of these carry an absolute date, so none are Safe Harbor
# identifiers. Month names and years are deliberately NOT listed here:
# "last April" and "March 2010" must stay redactable.
RELATIVE_TIME_PATTERN = re.compile(
    r"\b(?:"
    # numeric intervals: "3 days ago", "for 2 weeks", "x 5 days"
    # Counts appear as digits OR words in clinical prose — "for three
    # days" is as common as "for 3 days", and covering only digits left
    # the spelled-out half being redacted.
    r"(?:(?:for|over|about|approximately|nearly|almost|around|under|x|past|"
    r"last|within|onset|up\s+to|less\s+than|more\s+than)\s+)*"
    # Word counts include the indefinite article and vague quantities —
    # "about a month", "over an hour", "for many years", "half an hour"
    # are durations exactly as "for 3 days" is. The hyphen admits the
    # adjectival form: "2-week history", "five-month pregnancy".
    r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"several|many|few|a\s+few|a\s+couple(?:\s+of)?|couple(?:\s+of)?|"
    r"half\s+an?|an?)[\s-]*"
    r"(?:second|minute|hour|day|week|month|year)s?"
    r"(?:\s+and\s+a\s+half)?"
    r"(?:\s*(?:ago|prior|of\s+symptoms|duration|post[-\s]?op|pta))?"
    r"|"
    # bare relative adverbs and times of day
    r"(?:today|yesterday|tomorrow|tonight|currently|recently|presently|"
    r"overnight|nightly|daily|weekly|monthly|yearly|annual|annually|"
    r"later|earlier|"
    r"morning|afternoon|evening|night)"
    r"|"
    # determiner + time word: "this morning", "the day", "last night"
    r"(?:this|last|next|the|that|early|late)\s+"
    r"(?:(?:past|previous|prior|following|coming|same)\s+)?"
    r"(?:morning|afternoon|evening|night|day|week|weekend|month|year)"
    r"|"
    # ordinal course days: "the second postoperative day"
    r"(?:first|second|third|fourth|fifth|sixth|seventh|\d{1,2}(?:st|nd|rd|th))\s+"
    r"(?:post[-\s]?op(?:erative)?|hospital|illness)\s+day"
    r"|"
    # seasons without a year: "last fall" is relative; "fall 2019" keeps
    # its year and is out of scope for Safe Harbor anyway.
    r"(?:this|last|next|past|early|late)\s+(?:spring|summer|fall|autumn|winter)"
    r"|"
    # post-operative day references
    r"(?:post|hospital)[-\s]?op(?:erative)?\s+day\s+\d+"
    r"|pod\s*#?\s*\d+"
    r"|"
    # course-day ordinals: "day 2", "hospital day #4", "HD 3", "cycle 2
    # day 1". A day COUNT relative to an event is not a calendar date
    # and is not a Safe Harbor identifier; it is the timeline itself.
    # The lookahead refuses "day 3/14" — that is a date wearing a label.
    r"(?:(?:hospital|illness|treatment|icu|admission)\s+)?day\s*#?\s*\d{1,3}(?!\s*[/.\-]\s*\d)"
    r"|hd\s*#?\s*\d{1,3}"
    r")\b",
    re.IGNORECASE,
)

ZIP_PATTERN = re.compile(r"\b(\d{5})(?:-\d{4})?\b")

# A five-digit number is a ZIP only where it can be one; otherwise
# "CPT 99213, 71046, 93000" would be truncated to "992**, 710**, 930**"
# by the ZIP rule. Numbers are left alone when they are:
#   - in a line that names a code system (CPT, HCPCS, NDC, LOINC, SNOMED,
#     RxNorm) before them, with no address word in between;
#   - followed by a unit ("10000 units", "12000 cells/uL", "15000 mg");
#   - led by a dose, lab or money cue ("heparin 10000", "WBC 12000", "$15000").
# ADDRESS context always wins: a comma or a city/state word right before
# it, or a "zip" label, makes it a ZIP whatever else is on the line.
_ZIP_CODE_SYSTEM_BEFORE = re.compile(
    r"\b(?:CPT|HCPCS|NDC|LOINC|SNOMED|RxNorm|RVU|DRG|procedure\s+codes?|billing\s+codes?)\b"
    r"(?![^\n]*\b(?:address|street|st|ave|road|rd|lane|ln|blvd|drive|dr|zip)\b)[^\n]*$",
    re.IGNORECASE)
_ZIP_UNIT_AFTER = re.compile(
    r"\s*(?:units?\b|u\b|iu\b|mg\b|mcg\b|µg\b|ug\b|g\b|kg\b|ml\b|cc\b|l\b|cells\b|copies\b|"
    r"meq\b|mmol\b|mm\b|cm\b|kcal\b|cal\b|steps\b|lbs?\b|/|%|x\s*10|k\b)",
    re.IGNORECASE)
_ZIP_VALUE_BEFORE = re.compile(
    r"(?:\$|\b(?:wbc|plt|platelets?|heparin|insulin|units?|dose|ebl|ucx|cfu|anc|alc|"
    r"hgb|ck|cpk|bnp|ldh|ferritin|d-dimer|viral\s+load|vl|cd4|titer|iu)\s*[:=]?\s*(?:of\s+)?)$",
    re.IGNORECASE)


_ID_LABEL_BEFORE = re.compile(
    r"(?i:\b(?:mrn|mr\s*#|med(?:ical)?\s+rec(?:ord)?(?:\s+(?:no|number|#))?|acct|account|"
    r"chart|record|id|member\s+id|subscriber|policy|claim|encounter|enc|visit|fin|csn|har|pt\s+id|"
    r"patient\s+id|case|accession|specimen|order|ref(?:erence)?)\b\.?\s*(?:no\.?|number|#)?"
    r"\s*[:#]?\s*)$")


def zip_matches(text: str):
    """ZIP_PATTERN matches that stand where a ZIP code can stand."""
    for m in ZIP_PATTERN.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        before = text[line_start:m.start()]
        after = text[m.end():m.end() + 12]
        # A "City, ST" or a zip label right before it: a ZIP, even on a
        # line that also mentions CPT.
        # Strong address lead: a zip label, "City, ", "City/", or a state
        # code. A bare word before the number ("Missoula 11493") is only
        # a weak lead and loses to a lab or dose cue ("WBC 12000").
        address_led = bool(re.search(r"(?i:\bzip(?:\s*code)?\s*[:#]?\s*)$|[A-Za-z.]\s*[,/]\s*$"
                                     r"|\b[A-Z]{2}\s+$", before[-40:])) and not \
            re.search(r"(?:\d{5}(?:-\d{1,2})?\s*,\s*)$", before)
        if _ZIP_CODE_SYSTEM_BEFORE.search(before) and not re.search(
                r"\bzip\b", before, re.IGNORECASE):
            continue
        if _ZIP_UNIT_AFTER.match(after):
            continue
        # A record label right before it: "MRN 55120" is a record number;
        # truncating it as a ZIP would keep "551**".
        if _ID_LABEL_BEFORE.search(before[-24:]):
            continue
        # Another group of digits straight after it: part of a longer
        # number ("07700 900123", a phone), not a ZIP. ZIP+4 is "-dddd".
        if re.match(r"(?:[ \t]\d{3,}|-\d{5,})", after):
            continue
        # Part of a joined code ("BLU-0093-77400", "27-FA-68-00412",
        # "ROI-2071-04432"): cutting its five-digit tail as a ZIP keeps the
        # rest readable and looks deliberate. A ZIP stands on its own.
        if re.search(r"[A-Za-z0-9][-–]$", before) or re.match(r"[-–][A-Za-z0-9]", after):
            continue
        # Inside a web address: cutting "https://x.example.com/u/48532"
        # as a ZIP would keep the address from being removed.
        if re.search(r"(?:https?://|www\.)\S*$", before):
            continue
        if _ZIP_VALUE_BEFORE.search(before[-30:]) and not address_led:
            continue
        yield m


def find_ages(text: str) -> list[AgeFinding]:
    """Locate ages and classify them under Safe Harbor.
    Ages < 90 are returned with is_identifier=False — they are legal to
    retain and clinically important, so the policy engine leaves them."""
    findings = []
    matches = list(AGE_PATTERN.finditer(text)) + list(APPOSITIVE_AGE_PATTERN.finditer(text))
    for m in matches:
        raw_age = next((g for g in m.groups() if g), None)
        try:
            age = int(raw_age)
        except (TypeError, ValueError):
            continue

        if age >= 90:
            findings.append(AgeFinding(
                start=m.start(), end=m.end(), original=m.group(0),
                replacement="90 or older", is_identifier=True,
            ))
        else:
            findings.append(AgeFinding(
                start=m.start(), end=m.end(), original=m.group(0),
                replacement=m.group(0), is_identifier=False,
            ))
    taken = [(f.start, f.end) for f in findings if f.is_identifier]
    for m in OLD_AGE_PATTERN.finditer(text):
        name = next(k for k in ("n1", "n2", "n3", "n4", "n5", "n6", "n7") if m.group(k))
        s, e = m.start(name), m.end(name)
        if name == "n6" and re.search(r"(?i)\b(?:t|temp|temperature|tmax|tc|febrile|fever|afebrile|"
                                      r"axillary|oral|rectal|tympanic)\b[^\n]{0,12}$",
                                      text[max(0, s - 20):s]):
            continue
        if _old_age_value(m.group(name)) < 90 or any(s < b and a < e for a, b in taken):
            continue
        findings = [f for f in findings if not (f.start < e and s < f.end)]
        findings.append(AgeFinding(start=s, end=e, original=text[s:e],
                                   replacement="90 or older", is_identifier=True))
        taken.append((s, e))
    findings.sort(key=lambda f: f.start)
    return findings


def generalize_zip(text: str) -> tuple[str, int]:
    """Truncate ZIPs to 3 digits, or 000 for HHS-restricted prefixes.
    Returns (new_text, count_changed)."""
    count = 0
    out, last = [], 0
    for m in zip_matches(text):
        prefix = m.group(1)[:3]
        count += 1
        out.append(text[last:m.start()])
        out.append("000**" if prefix in RESTRICTED_ZIP_PREFIXES else f"{prefix}**")
        last = m.end()
    out.append(text[last:])
    return "".join(out), count


def protected_time_spans(text: str) -> list[tuple[int, int]]:
    """Character spans of relative-time phrases that must NOT be
    redacted even though they contain numbers and time words."""
    return [(m.start(), m.end()) for m in RELATIVE_TIME_PATTERN.finditer(text)]


def coverage_report() -> dict:
    """Machine-readable Safe Harbor coverage matrix."""
    covered = [k for k, v in SAFE_HARBOR_MAP.items() if v["covered"]]
    gaps = [
        {"identifier": k, "note": v.get("note", "")}
        for k, v in SAFE_HARBOR_MAP.items() if not v["covered"]
    ]
    return {
        "standard": "HIPAA Safe Harbor, 45 CFR 164.514(b)(2)",
        "identifiers_total": len(SAFE_HARBOR_MAP),
        "identifiers_covered": len(covered),
        "covered": covered,
        "known_gaps": gaps,
        "disclaimer": (
            "Automated Safe Harbor coverage is a technical control, not a "
            "compliance certification. De-identification still requires that "
            "the covered entity have no actual knowledge that residual "
            "information could identify an individual. Access controls, "
            "encryption, and a documented risk "
            "assessment remain separately required."
        ),
    }


# ---------------------------------------------------------------------
# Years that reveal an age over 89. Safe Harbor keeps a
# date's year EXCEPT "all elements of dates (including year) indicative of
# such age" (164.514(b)(2)(i)(C)). A birth year next to the note's own year
# gives the age back: "PROGRESS NOTE 2071 ... DOB 1979" is a 92-year-old even
# after "92 year old" became "90 or older". So when a note shows a patient
# aged 90 or over, every birth year, and every year 90 or more years before
# the note's latest year, becomes [DATE].
# ---------------------------------------------------------------------
_YEAR_TOKEN = re.compile(r"(?<![\d/.-])(1[6-9]\d{2}|20\d{2})(?![\d/.-]?\d)")
_BIRTH_CUE = re.compile(
    r"(?i)(?:\bD\.?O\.?B\.?|\bdate\s+of\s+birth|\bbirth\s*(?:date|day|year)?|\bborn|"
    r"\bb\.)[^\n\d]{0,12}(?:\[DATE\][^\n\d]{0,6})?$")


def guard_years_for_old_age(text: str, now_year: int | None = None) -> tuple[str, int]:
    """Replace years that would reveal an age over 89. Returns (text, changes)."""
    import datetime
    years = [(m.start(), m.end(), int(m.group(1))) for m in _YEAR_TOKEN.finditer(text)]
    if not years:
        return text, 0
    birth = {(a, b) for a, b, _ in years if _BIRTH_CUE.search(text[max(0, a - 30):a])}
    note_years = [y for a, b, y in years if (a, b) not in birth]
    ref = max([now_year or datetime.date.today().year] + note_years)
    old = "90 or older" in text or any(ref - y >= 90 for a, b, y in years if (a, b) in birth)
    doomed = [(a, b) for a, b, y in years
              if ref - y >= 90 or (old and (a, b) in birth)]
    for a, b in sorted(doomed, reverse=True):
        text = text[:a] + "[DATE]" + text[b:]
    return text, len(doomed)
