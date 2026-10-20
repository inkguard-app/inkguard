"""
RESIDUAL LINKAGE RISK — is this record still findable after redaction?

THE GAP THIS FILLS
------------------
Safe Harbor is a bright-line rule: remove the eighteen identifiers and,
absent actual knowledge that the rest identifies someone, the record
meets Safe Harbor, whatever remains. That is the point
of a safe harbor and it is also its blind spot, because the rule
permits retaining age under 90, the first three ZIP digits, sex, and
the entire clinical narrative.

A note reading "62-year-old female, ZIP 021**, Erdheim-Chester disease"
has had every one of the eighteen removed. It meets the Safe Harbor
list. Only a small number of people anywhere have that diagnosis, and
one of them may be a 62-year-old woman near Boston. Meeting the list and
being findable are different properties, and the list measures only the
first.

Health systems link records across institutions by DEMOGRAPHIC matching
— name, birthdate, sex, address — not by matching record numbers. That
is why the eighteen identifiers are the list they are: the rule exists
to break demographic matching. What it leaves standing is a partial
demographic tuple, and a rare enough diagnosis finishes the job.

TWO AXES, NOT ONE
-----------------
findability   How small the population is. Rarity plus retained
              demographics.
harm          What exposure costs. HIV is not rare; disclosure can
              still cost someone a job, a visa, or custody of a child.

A pure k-anonymity score measures only the first and would rate an HIV
note in a common demographic as low risk. Both are reported, and either
one alone can raise a flag.

WHAT THIS DELIBERATELY IS NOT
-----------------------------
NOT an Expert Determination. §164.514(b)(1) requires a qualified person
to apply statistical principles and put their name to the conclusion.
This module is a screening tool that tells a human where to look.

So it emits no population estimates and no counts. Printing
"population: 47" would imply a precision that the underlying prevalence
data does not support, and a wrong number is worse than no number
because it manufactures confidence. The output is a verdict, the
factors behind it, and an instruction to review.

Defaults are conservative: it would rather send a clean record to a
human than let a findable one through, because the review costs
minutes and the miss is unrecoverable.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from inkguard.rare_conditions import PREVALENCE_TIERS, SENSITIVE_CATEGORIES
from inkguard.basalt import Finding, Severity


# =====================================================================
# RETAINED DEMOGRAPHICS
#
# Attributes Safe Harbor PERMITS keeping, each of which narrows the
# population. Counted, not redacted — removing them is the user's
# call and would often destroy the record's research value.
# =====================================================================

_AGE = re.compile(
    r"\b(\d{1,3})[\s-]*(?:year|yr|y)[\s-]*(?:old|o)\b|\b(\d{1,3})\s*y/?o\b",
    re.IGNORECASE,
)
_SEX = re.compile(
    r"\b(?:male|female|man|woman|boy|girl|gentleman|lady|"
    r"transgender|nonbinary|non-binary)\b",
    re.IGNORECASE,
)
_ZIP3 = re.compile(r"\b\d{3}\*{2}\b")          # already generalized by policy
_RACE_ETHNICITY = re.compile(
    r"\b(?:caucasian|white|black|african[- ]american|hispanic|latino|latina|"
    r"asian|south\s+asian|east\s+asian|native\s+american|american\s+indian|"
    r"pacific\s+islander|middle\s+eastern|ashkenazi|sephardic|"
    r"lebanese|somali|ethiopian|nigerian|vietnamese|filipino|korean|"
    r"japanese|chinese|indian|pakistani|bangladeshi|mexican|"
    r"puerto\s+rican|cuban|dominican|haitian|jamaican)\b",
    re.IGNORECASE,
)
_OCCUPATION = re.compile(
    r"\b(?:physician|surgeon|nurse|attorney|lawyer|teacher|professor|"
    r"pilot|firefighter|police\s+officer|veteran|active\s+duty|"
    r"construction\s+worker|farmer|miner|chef|engineer)\b",
    re.IGNORECASE,
)
_PREGNANCY = re.compile(r"\b(?:G\d+P\d+|gravida|pregnan\w+|postpartum|EGA\s*\d+)\b",
                        re.IGNORECASE)

# SMALL POPULATIONS. Safe Harbor lets the state stay,
# and the first three ZIP digits where that area holds more than 20,000
# people. In a state or territory of under about 1.5 million people, or a
# rural, frontier or tribal community, or "the only hospital in town",
# the remaining details narrow to very few people. Counted as narrowing,
# never removed by the tool: removing them is the reviewer's call.
_SMALL_STATE = re.compile(
    r"\b(?:Wyoming|Vermont|Alaska|North\s+Dakota|South\s+Dakota|Delaware|Rhode\s+Island|Montana|"
    r"Maine|New\s+Hampshire|Hawaii|District\s+of\s+Columbia|Guam|American\s+Samoa|"
    r"Northern\s+Mariana\s+Islands|U\.?S\.?\s+Virgin\s+Islands)\b"
    r"|,\s*(?:WY|VT|AK|ND|SD|RI|MT|NH|GU|MP|VI)\b")
_RURAL = re.compile(
    r"\b(?:rural|frontier\s+(?:county|area|community)|remote\s+(?:community|village|area|island)|"
    r"tribal\s+(?:community|land|clinic|health)|reservation|pueblo|native\s+village|"
    r"Indian\s+Health\s+Service|\bIHS\b|critical\s+access\s+hospital|small\s+town|"
    r"island\s+community|only\s+(?:hospital|clinic|doctor|physician|pharmacy|school)\s+"
    r"(?:in|for)\s+(?:town|the\s+county|the\s+area|miles|the\s+island)|"
    r"(?:hours?|\d+\s+miles)\s+from\s+the\s+nearest\s+(?:hospital|clinic|town))\b",
    re.IGNORECASE)

# An age at or above 90 is itself an identifier under Safe Harbor
# (§164.514(b)(2)(i)(C)) and must be banded, so its presence after
# redaction is a finding on its own, not merely a narrowing attribute.
_ELDERLY_THRESHOLD = 90


@dataclass
class DemographicProfile:
    factors: list[str] = field(default_factory=list)
    exact_age: int | None = None
    small_population: bool = False

    @property
    def count(self) -> int:
        # A small population narrows more than any one attribute: it
        # counts twice.
        return len(self.factors) + (1 if self.small_population else 0)


def profile_demographics(text: str) -> DemographicProfile:
    p = DemographicProfile()

    ages = [int(g) for m in _AGE.finditer(text) for g in m.groups() if g]
    if ages:
        p.exact_age = max(ages)
        p.factors.append("exact age")
    if _SEX.search(text):
        p.factors.append("sex")
    if _ZIP3.search(text):
        p.factors.append("3-digit ZIP")
    if _RACE_ETHNICITY.search(text):
        p.factors.append("race/ethnicity or national origin")
    if _OCCUPATION.search(text):
        p.factors.append("occupation")
    if _PREGNANCY.search(text):
        p.factors.append("pregnancy status")
    if _SMALL_STATE.search(text):
        p.factors.append("small-population state or territory")
        p.small_population = True
    if _RURAL.search(text):
        p.factors.append("rural, remote or tribal community")
        p.small_population = True
    return p


# =====================================================================
# CONDITION MATCHING
# =====================================================================

def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9\s-]", " ", text.lower())


def find_rare_conditions(text: str) -> list[tuple[str, str]]:
    """Return [(tier, condition), ...] present in the text."""
    norm = _normalize(text)
    out: list[tuple[str, str]] = []
    for tier, terms in PREVALENCE_TIERS:
        for term in terms:
            if term in norm:
                out.append((tier, term))
    return out


def find_sensitive_categories(text: str) -> list[tuple[str, str, str]]:
    """Return [(category, why_it_matters, matched_term), ...]."""
    norm = _normalize(text)
    out: list[tuple[str, str, str]] = []
    for cat, (why, terms) in SENSITIVE_CATEGORIES.items():
        for term in terms:
            # The guard excludes HYPHENS as well as letters and digits,
            # so "band-aids" does not match "aids": a compound word is
            # one word.
            if re.search(rf"(?<![a-z0-9-]){re.escape(term)}(?![a-z0-9-])", norm):
                out.append((cat, why, term))
                break
    return out


# =====================================================================
# VERDICT
# =====================================================================

TIER_WEIGHT = {"ultra_rare": 3, "very_rare": 2, "rare": 1}


# =====================================================================
# NEWSWORTHY EVENTS
#
# Patients who arrive together from one event (a bus crash, a pile-up, an
# earthquake, a shooting, a building collapse) can be identified by the
# event alone: a date, a hospital and one news story name every one of
# them, with no identifier left in the note. The event is a quasi-
# identifier. It is flagged for review with the suggestion to remove the
# description of the event entirely; the reviewers may remove more than
# the tool did, and what they chose is recorded.
# =====================================================================
_EVENT_KINDS: tuple[tuple[str, str], ...] = (
    ("mass casualty incident", r"\bmass[\s-]+casualty\b|\bMCI\b|\bmulti(?:ple)?[\s-]+casualty\b|"
                               r"\bdisaster (?:plan|declared|response|victims?)\b|\bcode (?:triage|orange)\b|"
                               r"\b(?:START|SALT) triage\b|\btriage tag\b"),
    ("multi-vehicle crash", r"\bmulti[\s-]*(?:vehicle|car)\s+(?:collision|crash|accident|pile[\s-]?up)\b|"
                            r"\bpile[\s-]?up\b|\b(?:school\s+)?bus\s+(?:crash|accident|collision|rollover|"
                            r"overturned)\b|\btrain\s+(?:derailment|crash|collision)\b|\bderail\w*|"
                            r"\b(?:plane|aircraft|helicopter|small plane)\s+crash\b"),
    ("shooting or attack", r"\b(?:mass|school|active|workplace)\s+shoot(?:ing|er)\b|\bactive\s+shooter\b|"
                           r"\bshooting at (?:the |a )?(?:school|mall|church|concert|festival|office|club)\b|"
                           r"\bterror(?:ist|ism)\s+attack\b|\bbombing\b|\bvehicle[\s-]+ramming\b|"
                           r"\bstabbing spree\b|\b(?:mall|church|synagogue|mosque|temple|concert|festival|"
                           r"nightclub|club|office|parade|stadium|campus|supermarket|grocery store|"
                           r"theater|theatre)\s+shooting\b"),
    ("explosion or collapse", r"\bexplosion at\b|\bblast (?:injur\w+|wave)\b|\bbuilding collapse\b|"
                              r"\b(?:bridge|structure|stage|roof|balcony|scaffold)\s+collapse\b|"
                              r"\bcrowd crush\b|\bstampede\b|\bfireworks? (?:accident|explosion)\b|"
                              r"\b(?:chemical|gas|ammonia|chlorine)\s+(?:leak|release|spill|exposure at)\b"),
    ("natural disaster", r"\bearthquake\b|\btornado\b|\bhurricane\b|\btyphoon\b|\btsunami\b|"
                         r"\bwildfire\b|\bforest fire\b|\bflood(?:ing)?\s+(?:victims?|waters|rescue|event)\b|"
                         r"\bfloodwaters?\b|\blandslide\b|\bmudslide\b|\bavalanche\b|"
                         r"\bvolcan\w+ erupt\w*\b|\bheat wave\b|\bice storm\b"),
    ("people from the same event", r"\bsame (?:accident|incident|crash|collision|event|fire)\b|"
                                   r"\bother (?:victims|passengers|patients from the)\b|"
                                   r"\bfellow (?:passengers|students|workers)\b|"
                                   r"\b(?:one|two|three|several|many|multiple) (?:of (?:the |several |many )?)?"
                                   r"(?:victims|casualties) of\b|"
                                   r"\b(?:one|two|three|four|five|several|multiple|\d+) of (?:the )?"
                                   r"(?:\d+ )?(?:victims|passengers|students|children|workers|patients) "
                                   r"(?:from|in|injured|brought|transported)\b|"
                                   r"\b(?:siblings?|family members?|parents?) (?:were )?also "
                                   r"(?:admitted|injured|transported|brought in)\b"),
)
_EVENT_RX = [(k, re.compile(v, re.IGNORECASE)) for k, v in _EVENT_KINDS]
_SENTENCE = re.compile(r"[^.!?\n]*(?:[.!?]|$)")


def find_events(text: str) -> list[tuple[str, int, int]]:
    """(kind, start, end) for every SENTENCE that describes a newsworthy
    event: the sentence, not just the word, is what a reviewer would
    remove."""
    out: list[tuple[str, int, int]] = []
    for kind, rx in _EVENT_RX:
        for m in rx.finditer(text):
            s = max(text.rfind(c, 0, m.start()) for c in ".!?\n") + 1
            ends = [i for i in (text.find(c, m.end()) for c in ".!?\n") if i != -1]
            e = (min(ends) + 1) if ends else len(text)
            while s < e and text[s].isspace():
                s += 1
            if not any(a <= s and e <= b for _, a, b in out):
                out.append((kind, s, e))
    out.sort(key=lambda x: x[1])
    return out


# THE DETAILS THAT PIN AN EVENT DOWN: the kind of event
# can stay ("injured in an earthquake"); its name, the number of people in
# it and similar specifics are what let a news report find the patient.
# Dates and places are already removed as identifiers.
_NUM = (r"(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"dozens?|several|many|multiple|scores)")
_EVENT_COUNT = re.compile(
    rf"(?i:\b{_NUM}\s+(?:of\s+(?:the\s+)?)?(?:{_NUM}\s+)?(?:other\s+)?"
    r"(?:victims|casualties|students|passengers|people|persons|patients|children|kids|workers|"
    r"employees|attendees|spectators|residents|vehicles|cars|dead|killed|injured|fatalities)\b)")
_EVENT_NOUN = (r"(?:earthquake|quake|tornado|hurricane|typhoon|wildfire|fire|flood|landslide|"
               r"mudslide|shooting|bombing|attack|explosion|blast|collapse|crash|pile-?up|"
               r"derailment|rollover|stampede|crush|spill|leak|disaster)")
_EVENT_NAME = re.compile(
    rf"(?:\b(?:Hurricane|Typhoon|Tropical\s+Storm|Cyclone|Storm)\s+[A-Z][a-z]+"
    rf"|\b(?:[A-Z][\w'’-]*\s+){{1,4}}(?i:{_EVENT_NOUN})\b"
    rf"|\b(?:Route|Highway|Interstate|I-|US-|SR-)\s?\d+\s+(?i:{_EVENT_NOUN})\b)")


def event_details(text: str, events=None) -> list[tuple[str, int, int]]:
    """Inside each event sentence, the details that pin the event down:
    (what, start, end) with what = 'count' or 'name'. These are suggested
    for removal; the kind of event stays."""
    out: list[tuple[str, int, int]] = []
    for _kind, s, e in (events if events is not None else find_events(text)):
        part = text[s:e]
        for what, rx in (("name", _EVENT_NAME), ("count", _EVENT_COUNT)):
            for m in rx.finditer(part):
                a, b = s + m.start(), s + m.end()
                if what == "name":
                    # Only the proper name, not a sentence-start capital.
                    first = m.group().split()[0]
                    if (m.start() == 0 or part[:m.start()].rstrip().endswith((".", ":"))) \
                            and len(m.group().split()) == 2 and first.lower() in ("the", "a", "an"):
                        continue
                    # Keep the kind of event; remove only its name.
                    txt = text[a:b]
                    lead = re.match(r"(?:Hurricane|Typhoon|Tropical\s+Storm|Cyclone|Storm|The|the)\s+", txt)
                    if lead:
                        a += lead.end()
                    tail = re.search(rf"\s+(?i:{_EVENT_NOUN})$", text[a:b])
                    if tail:
                        b = a + tail.start()
                    if b <= a:
                        continue
                if not any(x < b and a < y for _, x, y in out):
                    out.append((what, a, b))
    out.sort(key=lambda x: x[1])
    return out


@dataclass
class LinkageReport:
    findability: str          # "high" | "elevated" | "low"
    harm: str                 # "high" | "elevated" | "low"
    verdict: str              # "review" | "caution" | "ok"
    factors: list[str] = field(default_factory=list)
    sensitive_categories: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    events: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return self.verdict == "review"

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "findability": self.findability,
            "harm_if_exposed": self.harm,
            "factors": self.factors,
            "sensitive_categories": self.sensitive_categories,
            "newsworthy_events": sorted({k for k, _, _ in self.events}),
            "disclaimer": (
                "Screening signal only. This is NOT an Expert Determination "
                "under 45 CFR 164.514(b)(1) and reports no population "
                "estimate. A qualified reviewer decides how this record "
                "may be used."
            ),
        }


def assess(redacted_text: str) -> LinkageReport:
    """Assess what remains AFTER redaction.

    Takes the redacted text, not the original, because the question is
    exactly "what survived" — running this on the original would score
    identifiers that the tool already removed and report risk that
    does not exist.
    """
    conditions = find_rare_conditions(redacted_text)
    sensitive = find_sensitive_categories(redacted_text)
    demo = profile_demographics(redacted_text)

    factors: list[str] = []
    rarity = max((TIER_WEIGHT[t] for t, _ in conditions), default=0)

    if conditions:
        best = max(conditions, key=lambda c: TIER_WEIGHT[c[0]])
        factors.append(f"{best[0].replace('_', ' ')} condition present ({best[1]})")
    if demo.factors:
        factors.append("retained demographics: " + ", ".join(demo.factors))

    # --- findability -------------------------------------------------
    # An ultra-rare condition isolates a person almost regardless of
    # what else is present, so it alone is sufficient. Below that,
    # rarity and demographic narrowing compound.
    score = rarity * 2 + demo.count
    # In a small population, an age and a sex are already close to one
    # person.
    if rarity >= 3 or score >= 7 or (demo.small_population and demo.count >= 4):
        findability = "high"
    elif rarity >= 1 and demo.count >= 2 or score >= 4:
        findability = "elevated"
    else:
        findability = "low"

    # --- harm --------------------------------------------------------
    cats = sorted({c for c, _, _ in sensitive})
    if len(cats) >= 2:
        harm = "high"
    elif cats:
        harm = "elevated"
    else:
        harm = "low"

    # --- events ------------------------------------------------------
    events = find_events(redacted_text)
    if events:
        factors.append("newsworthy event: " + ", ".join(sorted({k for k, _, _ in events})))

    # --- verdict -----------------------------------------------------
    # Either axis can force a review on its own. A findable record is a
    # privacy problem regardless of what it discloses, and a highly
    # damaging disclosure deserves a look even in a common demographic.
    if events or findability == "high" or harm == "high" or (
        findability == "elevated" and harm == "elevated"
    ):
        verdict = "review"
    elif findability == "elevated" or harm == "elevated":
        verdict = "caution"
    else:
        verdict = "ok"

    findings: list[Finding] = []
    if verdict != "ok":
        detail = "; ".join(factors) if factors else "retained attributes"
        if cats:
            detail += "; sensitive: " + ", ".join(cats)
        findings.append(Finding(
            code="RESIDUAL_LINKAGE_RISK",
            severity=Severity.DEGRADE if verdict == "review" else Severity.ADVISE,
            message=(
                f"Record may remain identifiable after redaction "
                f"(findability: {findability}, harm if exposed: {harm}). "
                f"{detail}."
            ),
            fix=(
                "The Safe Harbor method lists eighteen identifiers to remove; it "
                "does not measure whether what remains still describes one person. "
                "Before sharing, consider banding the age, dropping the "
                "3-digit ZIP, or generalizing the diagnosis. This is a "
                "screening signal, not an Expert Determination — a "
                "qualified reviewer makes the call."
            ),
            count=len(factors) + len(cats),
            sample=f"findability={findability}, harm={harm}",
        ))

    if events:
        findings.append(Finding(
            code="NEWSWORTHY_EVENT",
            severity=Severity.ADVISE,
            message=("The note describes an event that may have been in the news ("
                     + ", ".join(sorted({k for k, _, _ in events}))
                     + "). With a date and a hospital, an event can identify every patient "
                       "in it, even with all eighteen identifiers removed."),
            fix=("The kind of event may stay; what pins it down should go: its name, how many "
                 "were hurt, where and when. A reviewer may remove the whole description too, "
                 "and every choice is recorded. This is a reminder, not a rule: the responsible "
                 "clinician decides."),
            count=len(events),
            sample="event described",
        ))

    # Age >= 90 retained is a Safe Harbor defect in its own right.
    if demo.exact_age is not None and demo.exact_age >= _ELDERLY_THRESHOLD:
        findings.append(Finding(
            code="AGE_OVER_89_RETAINED",
            severity=Severity.DEGRADE,
            message=(
                f"An age of {_ELDERLY_THRESHOLD}+ survived redaction. Safe "
                f"Harbor §164.514(b)(2)(i)(C) requires ages over 89 to be "
                f"aggregated into a single '90 or older' category."
            ),
            fix="Band this age as '90 or older' before sharing.",
        ))

    return LinkageReport(
        findability=findability,
        harm=harm,
        verdict=verdict,
        factors=factors,
        sensitive_categories=cats,
        findings=findings,
        events=events,
    )


def enabled() -> bool:
    """On by default. It never blocks and never mutates text — it only
    attaches a warning — so the safe default is on."""
    return os.getenv("PHI_LINKAGE_SCORING", "true").lower() != "false"
