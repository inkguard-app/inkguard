"""
talc — the shared type registry and script ranges every component uses.

WHY THIS EXISTS
---------------
The defects this module guards against are data lost at a HANDOFF
between components, not failures inside one. For example:

  - dedupe dropping a confirmed name while merging overlapping spans;
  - an entity filter discarding month-name dates because the recognizer
    said "DATE_MONTHNAME" and the filter only knew "DATE_TIME";
  - an eponym check reading across a sentence boundary it has no
    knowledge of and declaring a surname clinical;
  - a date-shift wrapper swallowing a refusal and leaving the original
    dates in place.

When each component keeps its own definitions, nothing checks what
crosses between them, and fixing each instance leaves the class open.
This module addresses the class with two things every component shares:

1. A VOCABULARY. Every entity type is declared once: its canonical
   parent, whether it is PHI, which Safe Harbor identifier it is, and
   whether a Limited Data Set may retain it. Recognizers can invent new
   type names freely; downstream components resolve them through this
   table. An UNREGISTERED type is treated as PHI and flagged — the
   system fails toward redaction and asks for the vocabulary to grow,
   rather than silently dropping something it does not understand.

2. A LEDGER. Every detection must end in exactly one recorded fate:

     REDACTED        covered by the output redactions
     CLINICAL        subtracted as medical vocabulary (with the reason)
     HELD            held by garnet (with its score)
     NOT_PHI         a type this profile does not remove (ORGANIZATION,
                     or dates under a Limited Data Set)

   A detection with none of these is LOST — a component dropped it
   without saying why. That is the signature of a pipeline bug, and the
   engine fails closed on it.

Nothing in the ledger carries token text. Offsets, types, layers, fates
and reasons only.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------
# VOCABULARY
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class EntitySpec:
    canonical: str
    phi: bool
    safe_harbor: str = ""      # 45 CFR 164.514(b)(2)(i) item
    lds_retains: bool = False  # 164.514(e): LDS may keep it


def _s(c, phi=True, sh="", lds=False):
    return EntitySpec(c, phi, sh, lds)


REGISTRY: dict[str, EntitySpec] = {
    # names
    "PERSON":            _s("PERSON", sh="(A) names"),
    # geography
    "LOCATION":          _s("LOCATION", sh="(B) geographic subdivisions", lds=True),
    "LOCATION_STREET":   _s("LOCATION", sh="(B) street address"),
    "ZIP_CODE":          _s("LOCATION", sh="(B) ZIP code", lds=True),
    "GPE":               _s("LOCATION", sh="(B) geographic subdivisions", lds=True),
    # dates
    "DATE_TIME":         _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_MONTHNAME":    _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_MD":           _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_MD_TIME":      _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_MD_PAREN":     _s("DATE_TIME", sh="(C) dates", lds=True),
    "DOB":               _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_OF_BIRTH":     _s("DATE_TIME", sh="(C) dates", lds=True),
    "AGE":               _s("AGE", sh="(C) ages over 89", lds=True),
    # contact
    "PHONE_NUMBER":      _s("PHONE_NUMBER", sh="(D) telephone numbers"),
    "PHONE_INTL":        _s("PHONE_NUMBER", sh="(D) telephone numbers"),
    "PHONE_EXTENSION":   _s("PHONE_NUMBER", sh="(D) telephone numbers (pager / extension)"),
    "PHONE_LOCAL":       _s("PHONE_NUMBER", sh="(D) telephone numbers (local, seven digits)"),
    "MRN_HEADER":        _s("MRN", sh="(H) medical record numbers (in a record header)"),
    "PHONE_NATIONAL":    _s("PHONE_NUMBER", sh="(D) telephone numbers (national, leading 0)"),
    "PHONE_LOOKALIKE":   _s("PHONE_NUMBER", sh="(D) telephone numbers (letter O or l typed for a digit)"),
    "GEO_COORDINATES":   _s("LOCATION", sh="(B) geographic subdivisions (map coordinates)"),
    "FAX_NUMBER":        _s("FAX_NUMBER", sh="(E) fax numbers"),
    "EMAIL_ADDRESS":     _s("EMAIL_ADDRESS", sh="(F) email addresses"),
    # numbers
    "US_SSN":            _s("US_SSN", sh="(G) social security numbers"),
    "US_ITIN":           _s("US_SSN", sh="(G)/(R) taxpayer identification"),
    "US_SSN_PARTIAL":    _s("US_SSN", sh="(G) social security numbers (last four digits)"),
    "MRN":               _s("MRN", sh="(H) medical record numbers"),
    "HEALTH_PLAN_ID":    _s("HEALTH_PLAN_ID", sh="(I) health plan beneficiary numbers"),
    "US_BANK_NUMBER":    _s("ACCOUNT_NUMBER", sh="(J) account numbers"),
    "CREDIT_CARD":       _s("ACCOUNT_NUMBER", sh="(J) account numbers"),
    "IBAN_CODE":         _s("ACCOUNT_NUMBER", sh="(J) account numbers"),
    "CRYPTO":            _s("ACCOUNT_NUMBER", sh="(J) account numbers"),
    "US_DRIVER_LICENSE": _s("LICENSE_NUMBER", sh="(K) certificate/license numbers"),
    "US_PASSPORT":       _s("LICENSE_NUMBER", sh="(K)/(R) certificate/license numbers"),
    "MEDICAL_LICENSE":   _s("LICENSE_NUMBER", sh="(K) certificate/license numbers"),
    "NPI":               _s("LICENSE_NUMBER", sh="(K) certificate/license numbers"),
    "VEHICLE_ID":        _s("VEHICLE_ID", sh="(L) vehicle identifiers"),
    "VEHICLE_PLATE":     _s("VEHICLE_ID", sh="(L) vehicle identifiers (licence plates)"),
    "DEVICE_SERIAL":     _s("DEVICE_ID", sh="(M) device identifiers (serials and device names)"),
    "URL_SCHEME":        _s("URL", sh="(N) web URLs"),
    "USERNAME":          _s("UNIQUE_ID", sh="(R) login names"),
    "USERNAME_ACTOR":    _s("UNIQUE_ID", sh="(R) login names"),
    "USERNAME_COMMENT":  _s("UNIQUE_ID", sh="(R) login names"),
    "USERNAME_STAMP":    _s("UNIQUE_ID", sh="(R) login names"),
    "USERNAME_HANDLE":   _s("UNIQUE_ID", sh="(R) login names"),
    "DATE_MY":           _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_MONTHNAME_ANYCASE": _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_ORDINAL":      _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_HOLIDAY":      _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_MD_MORE":      _s("DATE_TIME", sh="(C) dates", lds=True),
    "DATE_MD_BEFORE":    _s("DATE_TIME", sh="(C) dates", lds=True),
    "LOCATION_STREET_WIDE":  _s("LOCATION", sh="(B) street address"),
    "LOCATION_STREET_UNIT":  _s("LOCATION", sh="(B) street address"),
    "LOCATION_STREET_LED":   _s("LOCATION", sh="(B) street address"),
    "LOCATION_STREET_INTL":  _s("LOCATION", sh="(B) street address"),
    "POSTCODE_INTL":     _s("LOCATION", sh="(B) postal codes", lds=True),
    "HEALTH_PLAN_WIDE":  _s("HEALTH_PLAN_ID", sh="(I) health plan beneficiary numbers"),
    "ID_LABELLED":       _s("UNIQUE_ID", sh="(R) any other unique identifying number or code"),
    "ID_WORDCODE":       _s("UNIQUE_ID", sh="(R) any other unique identifying number or code"),
    "SPOKEN_DIGITS":     _s("UNIQUE_ID", sh="(R) numbers spoken in words"),
    "DEVICE_ID":         _s("DEVICE_ID", sh="(M) device identifiers"),
    "URL":               _s("URL", sh="(N) web URLs"),
    "IP_ADDRESS":        _s("IP_ADDRESS", sh="(O) IP addresses"),
    "BIOMETRIC_ID":      _s("BIOMETRIC_ID", sh="(P) biometric identifiers"),
    "UNIQUE_ID":         _s("UNIQUE_ID", sh="(R) any other unique identifying number or code"),
    "UNIQUE_ID_PREFIXED": _s("UNIQUE_ID", sh="(R) any other unique identifying number or code"),
    "UNIQUE_ID_BARE":    _s("UNIQUE_ID", sh="(R) any other unique identifying number or code"),
    "FULL_FACE_PHOTO":   _s("FULL_FACE_PHOTO", sh="(Q) full-face photographs"),
    # Roles read by inkguard/jasper.py
    "EMPLOYER":          _s("EMPLOYER", sh="(A) names of employers"),
    "FACILITY":          _s("FACILITY", sh="tool policy: named place of care (beyond the 18)"),
    "EVENT":             _s("EVENT", sh="tool policy: a newsworthy event that can identify the people in it (beyond the 18)"),
    # NOT PHI under this tool's policy
    "ORGANIZATION":      _s("ORGANIZATION", phi=False),
    "NRP":               _s("NRP", phi=False),
}


# What a reader sees in place of each identifier. Plain words in square
# brackets, one per Safe Harbor family, whichever recognizer found it.
# (Dates are handled separately: the year is kept where Safe Harbor
# allows it — see policy._date_placeholder.)
PLACEHOLDER: dict[str, str] = {
    "PERSON": "[NAME]", "LOCATION": "[LOCATION]", "DATE_TIME": "[DATE]",
    "AGE": "[AGE]", "PHONE_NUMBER": "[PHONE]", "FAX_NUMBER": "[FAX]",
    "EMAIL_ADDRESS": "[EMAIL]", "US_SSN": "[SSN]", "MRN": "[MRN]",
    "HEALTH_PLAN_ID": "[PLAN ID]", "ACCOUNT_NUMBER": "[ACCOUNT]",
    "LICENSE_NUMBER": "[LICENSE]", "VEHICLE_ID": "[VEHICLE ID]",
    "DEVICE_ID": "[DEVICE ID]", "URL": "[URL]", "IP_ADDRESS": "[IP]",
    "BIOMETRIC_ID": "[BIOMETRIC]", "FULL_FACE_PHOTO": "[PHOTO]", "UNIQUE_ID": "[ID]",
    "EMPLOYER": "[EMPLOYER]", "FACILITY": "[FACILITY]",
    "EVENT": "[EVENT DESCRIPTION REMOVED]",
}


def placeholder(entity: str) -> str:
    return PLACEHOLDER.get(canonical(entity), "[REDACTED]")


def spec(entity: str) -> EntitySpec:
    """Resolve an entity type. UNREGISTERED types are PHI: when the engine
    meets a word it does not know, it redacts and asks, never drops."""
    return REGISTRY.get(entity) or EntitySpec(entity, True, "(R) unregistered — treated as PHI")


def is_registered(entity: str) -> bool:
    return entity in REGISTRY


def canonical(entity: str) -> str:
    return spec(entity).canonical


def keepable(entity: str) -> bool:
    """May a deployment choose to keep this type? Only types removed by
    tool policy, never a Safe Harbor identifier (whose spec names its
    164.514(b)(2)(i) item, "(A)".."(R)") and never an unregistered type."""
    sp = spec(entity)
    return bool(sp.phi) and is_registered(entity) and not sp.safe_harbor.startswith("(")


def refused_keeps(keep) -> list[str]:
    """Entries in a keep list that will be ignored, for start-up warnings."""
    return sorted(k for k in (keep or ()) if spec(k).phi and not keepable(k))


def removed_under(entity: str, profile_is_lds: bool,
                  configured: set[str] | None = None,
                  keep: set[str] | None = None) -> bool:
    """Is this entity removed under the active profile?

    Resolution goes through the canonical parent, so a recognizer that
    emits a new subtype ("DATE_MONTHNAME") inherits its parent's policy.

    FAIL TOWARD REDACTION. `configured` is not an allow-list: as one, it
    would be a second vocabulary to keep up to date by hand, and a
    registered PHI family missing from it would be detected and then kept.
    Every PHI family is removed unless a deployment names it in `keep`
    (settings.keep_entities, empty by default). `configured` is accepted
    for compatibility and does not narrow anything.

    SAFE HARBOR IDENTIFIERS CANNOT BE KEPT. `keep` only reaches types
    removed by TOOL policy (FACILITY). A name, an employer, a date, an
    ID — any of the eighteen, and any unregistered type — is removed
    whatever the configuration says. The Limited Data Set profile's own
    retention rules are separate and unchanged.
    """
    sp = spec(entity)
    if not sp.phi:
        return False
    if keep and (entity in keep or sp.canonical in keep) and keepable(entity):
        return False
    if profile_is_lds and sp.lds_retains:
        return False
    return True


# ---------------------------------------------------------------------
# SCRIPTS — one definition, imported by every component
# ---------------------------------------------------------------------
# One definition, so that every component treats the same characters
# alike: a copied character class drifts, and a copy missing the Japanese
# iteration mark 々 would split "佐々木" (Sasaki, one of the most common
# surnames in Japan) into "佐" and "木" in one component and keep it whole
# in another.
#
# Caseless scripts: no capital letter marks a proper noun, so a run of
# these inside an English note is treated as a name candidate.
SCRIPT_RANGES: dict[str, str] = {
    "arabic":     "\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF",
    "hebrew":     "\u0590-\u05FF",
    "devanagari": "\u0900-\u097F",
    "indic":      "\u0980-\u0DFF",          # Bengali .. Sinhala
    "thai":       "\u0E00-\u0E7F",
    "cjk":        "\u3005-\u3007\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF",
    "kana":       "\u3040-\u309F\u30A0-\u30FF\uFF66-\uFF9F",
    "hangul":     "\u1100-\u11FF\uAC00-\uD7AF",
}
CASELESS = "".join(SCRIPT_RANGES.values())
CASELESS_RE = __import__("re").compile(f"[{CASELESS}]")
# Cased non-Latin scripts: they mark proper nouns with capitals, so they
# are handled like Latin, but they still count as non-Latin script.
CASED_NON_LATIN = "\u0370-\u03FF\u0400-\u04FF"
NON_LATIN_RE = __import__("re").compile(f"[{CASELESS}{CASED_NON_LATIN}]")


def script_of(ch: str) -> str:
    import re as _re
    for name, rng in SCRIPT_RANGES.items():
        if _re.match(f"[{rng}]", ch):
            return "cjk" if name == "kana" else name
    if _re.match(f"[{CASED_NON_LATIN}]", ch):
        return "cyrillic_greek"
    return ""


# ---------------------------------------------------------------------
# LEDGER
# ---------------------------------------------------------------------

REDACTED, CLINICAL, HELD, NOT_PHI, LOST = "REDACTED", "CLINICAL", "HELD", "NOT_PHI", "LOST"


@dataclass
class Entry:
    layer: str
    entity: str
    start: int
    end: int
    fate: str = ""
    reason: str = ""

    def key(self) -> tuple:
        return (self.layer, self.entity, self.start, self.end)

    def to_dict(self) -> dict:
        return {"layer": self.layer, "entity": self.entity,
                "canonical": canonical(self.entity),
                "start": self.start, "end": self.end,
                "fate": self.fate, "reason": self.reason}


@dataclass
class Ledger:
    entries: dict = field(default_factory=dict)
    unregistered: set = field(default_factory=set)

    def detect(self, hits) -> None:
        for h in hits:
            e = Entry(h.layer, h.entity_type, h.start, h.end)
            self.entries.setdefault(e.key(), e)
            if not is_registered(h.entity_type):
                self.unregistered.add(h.entity_type)

    def _mark(self, h, fate: str, reason: str) -> None:
        e = self.entries.get((h.layer, h.entity_type, h.start, h.end))
        if e is not None and not e.fate:
            e.fate, e.reason = fate, reason

    def mark_removed(self, before, after, fate: str, reason: str) -> None:
        """Record every hit present in `before` and absent from `after`."""
        kept = {(h.layer, h.entity_type, h.start, h.end) for h in after}
        for h in before:
            if (h.layer, h.entity_type, h.start, h.end) not in kept:
                self._mark(h, fate, reason)

    def mark_within(self, start: int, end: int, fate: str, reason: str,
                    canonical_types: tuple = ()) -> None:
        """Settle every unsettled detection lying wholly inside [start, end)
        — whichever layer made it. Dedupe merges detections from several
        layers into one hit; a step that rules on the hit must rule on all
        of them, or the others are reported lost."""
        for e in self.entries.values():
            if e.fate or e.start < start or e.end > end:
                continue
            if canonical_types and canonical(e.entity) not in canonical_types:
                continue
            e.fate, e.reason = fate, reason

    def mark_held(self, held_records) -> None:
        for r in held_records:
            e = self.entries.get((r["layer"], r["entity"], r["start"], r["end"]))
            if e is not None and not e.fate:
                e.fate, e.reason = HELD, f"garnet score {r['score']:+.2f}"

    def settle(self, final_redactions, not_phi: callable, text: str = "") -> list[Entry]:
        """Assign every remaining entry a terminal fate. Returns the LOST.

        An entry is REDACTED if the output redactions cover every one of
        its characters, NOT_PHI if its type is not removed under this
        profile, and LOST otherwise — detected, not explained away, and
        not covered.
        """
        covered: set[int] = set()
        for h in final_redactions:
            covered.update(range(h.start, h.end))
        lost: list[Entry] = []
        for e in self.entries.values():
            if e.fate:
                continue
            # Judge the characters that CARRY CONTENT. Redactions are
            # trimmed of edge whitespace and punctuation (dedupe does this
            # deliberately), so a detection of "Aisha Haddad   " whose
            # redaction is "Aisha Haddad" is fully redacted; comparing raw
            # offsets would report it as lost.
            # Coverage is judged FIRST: a span typed ORGANIZATION that was
            # re-typed as a name and redacted is REDACTED, not "kept".
            s, t = _content_bounds(text, e.start, e.end, canonical(e.entity))
            if s >= t:
                e.fate, e.reason = NOT_PHI, "title or role word only"
            # Visible characters only: a space inside "Jun 9th" split between
            # a name span and a date span carries no identity.
            elif all(p in covered for p in range(s, t) if not text or not text[p].isspace()):
                e.fate = REDACTED
            elif not_phi(e.entity):
                e.fate, e.reason = NOT_PHI, "type not removed under profile"
            else:
                e.fate, e.reason = LOST, "detected but neither redacted nor explained"
                lost.append(e)
        return lost

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for e in self.entries.values():
            counts[e.fate or "UNSETTLED"] = counts.get(e.fate or "UNSETTLED", 0) + 1
        return {"fates": counts, "unregistered_entities": sorted(self.unregistered)}


_EDGE = " \t\n,.;:()[]"

# Titles and role words carry no identity ("Dr.", "Patient", "Mrs.").
# Dedupe trims them off the front of a PERSON span so that "Patient Aisha
# Haddad" and "Aisha Haddad" are the same person to the pseudonym layer;
# the ledger must apply the SAME trim, or it reports the untouched "Dr."
# as a lost detection. One definition, used by both.
LEADING_ROLE = __import__("re").compile(
    r"(?:(?:patient|pt|mr|mrs|ms|mx|miss|dr|doctor|prof|nurse|the|"
    # Relation words stay: "Daughter [NAME] at bedside" keeps the family
    # context clinicians write it for.
    r"wife|husband|spouse|partner|son|daughter|mother|father|mom|dad|brother|sister|"
    r"grandmother|grandfather|grandson|granddaughter|aunt|uncle|niece|nephew|cousin|"
    r"fianc[eé]e?|guardian|caregiver|boyfriend|girlfriend|friend|neighbou?r|roommate|"
    r"stepmother|stepfather|stepson|stepdaughter|charge|attending|resident|"
    r"his|her|their|my|our|adult|older|younger|elder|"
    # An instruction before a name is not part of it: NER reads "Call Rose"
    # at the start of a line as one person.
    r"call|ask|tell|contact|notify|notified|remind|message|email|phone|phoned|text|paged)"
    r"\b\.?[\s,:]*)+",
    __import__("re").IGNORECASE)


# Credentials after a name are not part of it: "Lofties md", "Kim RN".
# Same rule for dedupe and the ledger, like LEADING_ROLE.
TRAILING_CREDENTIAL = __import__("re").compile(
    r"(?:[\s,]+(?:md|m\.d\.|do|rn|np|pa|pa-c|crna|lpn|dpt|phd|pharmd|mbbs|facs|facp|ms|cgc|"
    r"fnp|aprn|dnp|lcsw|msw|mph|esq|cnim|cst|l\.ac|lac|dacm|atc)\.?"
    # "Upshaw, Endocrinology" — a service after a comma is not the
    # name; "Arsenio Acct" — a record label run into the name.
    r"|,\s*(?:endocrinology|cardiology|neurology|neurosurgery|surgery|medicine|oncology|"
    r"hematology|nephrology|pulmonology|gastroenterology|urology|orthopedics|orthopaedics|"
    r"psychiatry|pediatrics|obstetrics|gynecology|radiology|dermatology|rheumatology|"
    r"infectious\s+diseases?|internal\s+medicine|family\s+medicine|emergency\s+medicine|"
    r"anesthesia|anesthesiology|ophthalmology|otolaryngology|ent|gi)"
    r"|\s+(?:acct|mrn|dob|ssn|id|fin|csn)#?)+$",
    __import__("re").IGNORECASE)


def _content_bounds(text: str, start: int, end: int,
                    entity: str = "") -> tuple[int, int]:
    """Span with edge whitespace/punctuation trimmed — the same trimming
    dedupe applies (edge punctuation, and leading titles on PERSON).
    Without the text, fall back to the raw span: strict, never lenient."""
    if not text:
        return start, end
    while start < end and text[start] in _EDGE:
        start += 1
    while end > start and text[end - 1] in _EDGE:
        end -= 1
    if entity == "PERSON":
        m = LEADING_ROLE.match(text, start, end)
        if m:
            start = m.end()
        t = TRAILING_CREDENTIAL.search(text[start:end])
        if t and t.start() > 0:
            end = start + t.start()
        # A detection that is ONLY a credential or record label ("Acct",
        # "MD") carries no identity: the same words dedupe trims off a name.
        if TRAILING_CREDENTIAL.fullmatch(" " + text[start:end]):
            return start, start
    return start, end
