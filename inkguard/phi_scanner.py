"""
Multi-layer PHI detection.

Finds possible identifiers with several independent detectors: fixed
patterns, form labels, sentence frames ("Mrs. X", "X is a 45-year-old"),
language models (Presidio with spaCy, and a multilingual model) and broad
name candidates. It then removes medical words and protected measurements,
weighs doubtful names and places (garnet), spreads confirmed names (shale)
and merges overlapping hits. See PIPELINE ORCHESTRATION below.

The detectors are additive: a name one detector misses can still be
caught by another. Later steps can still drop or hold a hit (the
clinical guard, garnet).
"""
import functools
import os
import re
import dataclasses
from inkguard import clinical_lexicon as _clinical_lexicon
from dataclasses import dataclass, field
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
import tldextract as _tldextract
import tldextract.tldextract as _tldextract_mod

# No network, ever. Presidio's email check calls tldextract, whose default
# downloads the public-suffix list from the internet on first use and writes
# a cache in the user's home folder. Point it at the copy bundled inside the
# library instead: no download, no cache file. tests/test_no_network.py runs
# the engine with the network switched off to check it.
_tldextract_mod.TLD_EXTRACTOR = _tldextract.TLDExtract(
    cache_dir=None, suffix_list_urls=(), fallback_to_snapshot=True)

from inkguard.config import settings
from inkguard.clinical_lexicon import (is_clinical_term, AMBIGUOUS_EPONYMS,
                                  eponym_phrase_continues)
from inkguard.quartz import clause_end_local as _clause_end_local
from inkguard import hipaa
from inkguard import clinical_context
from inkguard import clinical_translator as translator
from inkguard import input_classifier as classifier


@dataclass
class PHIHit:
    entity_type: str
    start: int
    end: int
    text: str
    score: float
    layer: str  # "regex" | "ner" | "anchor" | "context" | "candidate" | ...
    # Section the hit sits in, assigned by quartz.
    section: str = ""
    # Why it was confirmed: the signals garnet weighed. Recorded so
    # the operation log can say not only what was redacted but why.
    evidence: tuple = ()


# Recorded in each run's operation log, so a result can be traced to the
# vocabulary that produced it. Bump when the lexicon data changes.
LEXICON_VERSION = "v2"

# ---------------------------------------------------------------------
# Layer 1: fixed patterns. Each pattern matches a fixed shape or a label.
# Each hit is given a fixed score (0.95; generic identifiers 0.90) so it
# outranks model guesses; loose families are checked again in _shape_ok.
# ---------------------------------------------------------------------
REGEX_PATTERNS: dict[str, re.Pattern] = {
    "US_SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "US_SSN_PARTIAL": re.compile(
        r"(?i:\b(?:ssn|ss\s*#|social(?:\s+security)?(?:\s+(?:no\.?|number|#))?)\s*"
        r"(?:\(\s*)?(?:last\s*(?:4|four)(?:\s+digits)?|ending(?:\s+in)?|xxx-xx-)\s*(?:\))?\s*[:#]?\s*(?:is\s+)?)"
        r"(?P<v>\d{4})(?!\d)"
        r"|(?i:\blast\s*(?:4|four)(?:\s+digits)?\s+(?:of\s+(?:the\s+|her\s+|his\s+)?)?"
        r"(?:ssn|social(?:\s+security)?)(?:\s+(?:no\.?|number|#))?\s*[:#]?\s*(?:is\s+|was\s+)?)"
        r"(?P<x>\d{4})(?!\d)"),
    # A national number written with a leading 0 ("07700 900123",
    # "0161 496 0000"): ten or eleven digits in groups.
    "PHONE_NATIONAL": re.compile(
        r"(?<![\d.,/-])0\d{2,4}[ -]\d{3,4}[ -]?\d{3,4}(?![\d.,/-])"),
    # US phone: word-boundary anchored
    "PHONE_NUMBER": re.compile(r"(?:\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b)"),
    # POINT OF CONTACT at a sign-off: "pager 4-1234", "x4412", "ext. 22",
    # "Vocera 5567", "cell 555-0199". Internal numbers reach one person.
    # "x" needs four digits so "x1 each" and "x3 days" stay.
    "PHONE_EXTENSION": re.compile(
        r"(?i:\b(?:pager|pgr|beeper|bleep|ext\.?|extension|vocera|spectralink|"
        r"cell|mobile|direct\s+line|call\s*back|callback)\s*(?:#|no\.?|number)?\s*[:#]?\s*)"
        r"(?P<v>\d[\d-]{2,9}\d)(?![\d/]|\.\d)"
        r"|(?<![\w/])[xX](?P<x>\d{4,6})\b"),
    # A record number standing after a "SURNAME, GIVEN" header with no
    # label: "RILEY, ERIN  40-532195". The header shape is the label.
    "MRN_HEADER": re.compile(
        r"^[ \t]*[A-ZÀ-Þ][A-ZÀ-Þ'’\-]+(?:[ \t][A-ZÀ-Þ][A-ZÀ-Þ'’\-]+)?,[ \t]*[A-ZÀ-Þ][A-ZÀ-Þ'’\-]+"
        r"(?:[ \t]+[A-ZÀ-Þ][A-ZÀ-Þ'’.\-]*){0,2}[ \t]{2,}(?P<v>[A-Z]{0,3}-?\d[\d-]{4,})\b", re.M),
    # International phone: starts with + so \b does not anchor; use lookaround
    "PHONE_INTL":   re.compile(r"(?<!\d)\+\d{1,3}(?:[-.\s]\d{1,6}){2,4}(?!\d)"),
    # LOCAL SEVEN-DIGIT NUMBER after a contact word: "Telephone 555-0147",
    # "ph 555 0147", "fax 555.0147". The ten-digit pattern above misses
    # these, and the anchored-field extractor only sees them when the label
    # ends in a colon — so a note that writes the number the way people
    # actually write it was passing through. A seven-digit number on its
    # own is NOT taken: it could be a dose, a range or an internal code.
    # The contact word and the separator inside the number are both
    # required, which keeps lab values ("ph 7.4") and counts out.
    "PHONE_LOCAL": re.compile(
        r"(?i:\b(?:phone|telephone|tel|ph|fax|pager|pgr|beeper|cell|mobile|contact(?:ed)?|"
        r"reach(?:ed)?|call(?:ed)?|text(?:ed)?|number|no|update[sd]?\s+\S+(?:\s+\S+)?\s+at)\.?\s*"
        r"(?:(?:her|him|them|me|us|pt|patient|mom|dad|mother|father|wife|husband|son|daughter|"
        r"family|caregiver)\s+)?(?:at|on|is|number|no\.?|#)?\s*[:#]?\s*)"
        r"(?P<v>\d{3}[-.\s]\d{4})(?![\d-])"),
    # A phone number typed with a letter for a digit: "555-O159", "555-0l23"
    # Seven or ten characters in phone shape, at least
    # five real digits, and at least one O/o/l/I where a digit belongs.
    "PHONE_LOOKALIKE": re.compile(
        r"(?<![\w-])(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.]"
        r"(?=[0-9OolI]{4}(?![\w-]))(?=[0-9]*[OolI])[0-9OolI]{4}(?![\w-])"),
    # Map coordinates: "12.3456, -123.4567". Both
    # numbers need three or more decimals, which no vital sign or dose has.
    "GEO_COORDINATES": re.compile(
        r"(?<![\d.])[-+]?\d{1,2}\.\d{3,}\s*,\s*[-+]?\d{1,3}\.\d{3,}(?![\d.])"),
    "EMAIL_ADDRESS": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    # MRN: formats vary widely, but "MRN" / "Medical Record Number" as a
    # label followed by an alphanumeric value is a very common shape.
    # Length 4-15: the "MRN" label is itself the strong signal, so the
    # identifier after it does not need to be long to be trusted. A
    # 5-character MRN ("MRN F9988") was missed by an earlier 6-10 window.
    # Value only (named group v): the label is structure, and claiming it
    # made "MRN" itself a redaction that no detection explained.
    "MRN": re.compile(r"\b(?:MRN|Medical Record (?:Number|No\.?))\s*[:#]?\s*(?P<v>[A-Z0-9][A-Z0-9-]{3,14})\b", re.IGNORECASE),
    "NPI": re.compile(r"\b(?:NPI)\s*[:#]?\s*(\d{10})\b", re.IGNORECASE),
    "HEALTH_PLAN_ID": re.compile(r"\b(?:Member ID|Health Plan ID|Policy (?:Number|No\.?))\s*[:#]?\s*(?P<v>[A-Z0-9]{6,15})\b", re.IGNORECASE),
    "DEVICE_ID": re.compile(r"\b(?:Device (?:ID|Serial)|Serial (?:No\.?|Number))\s*[:#]?\s*([A-Z0-9-]{6,20})\b", re.IGNORECASE),
    # Numeric date formats: slash, dash, ISO, dot-separated
    "DATE_TIME": re.compile(
        r"\b(?:"
        r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}"     # 03/14/1962  03-14-1962
        r"|\d{4}-\d{2}-\d{2}"                    # 1962-03-14
        r"|\d{1,2}\.\d{1,2}\.\d{2,4}"            # 03.14.1962
        # 14MAR2071, 14Mar71: lab and older EHR exports
        r"|\d{1,2}(?i:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\d{2,4}"
        r")"
        # An ISO 8601 time joined by "T" belongs to the date: the old
        # trailing \b failed before the "T", so "2071-03-14T10:22", the
        # default timestamp of FHIR and most EHR exports, was not a date at
        # all.
        r"(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?"
        r"(?![\dA-Za-z])"
    ),
    # Month/day WITHOUT a year, licensed by a temporal lead-in: "on 4/13",
    # "since 11/2", "DOS: 3/14". Month and day are Safe Harbor date
    # elements even with no year. The lead-in is required because bare
    # "5/5" is motor strength, "1/2" a tablet and "2/6" a murmur grade.
    # Found on test notes: NER caught "4/14" and missed "4/13" in the
    # very next sentence.
    "DATE_MD": re.compile(
        r"(?:\b(?:on|since|dated|until|thru|through|DOS:?|date:?|faxed|scheduled|seen|visit|"
        r"appt|course|sent|called|drawn|collected|performed|done|surgery|admitted|"
        r"discharged|CT|MRI|XR|US|echo|EKG|ECG|labs?|f/u|fu|follow[- ]?up|comparison:?|"
        r"from|to|by|due|last|next|starting|started|stopped|expires?|"
        # A note's own date after its title or a follow-up verb: "NIGHT
        # SIGNOUT 7/24", "PROGRESS NOTE 2/10", "to reassess 2/11".
        r"signout|sign-?out|note|update|addendum|reassess|re-?check|re-?eval(?:uate|uation)?|"
        r"repeat|redraw|H&P|given|received|administered|infused|"
        # "FMLA forms faxed to HR 4/5"
        r"(?:faxed|sent|mailed|emailed|submitted)\s+to\s+[A-Za-z&]{1,20})\s+|^[ \t]*)"
        r"(?P<v>(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01]))\b"
        r"(?![/\-]\d|[ \t]*(?:tabs?|tablets?|strength|dose|of|murmur|power|reflex(?:es)?|mg|pain|"
        r"severity|intensity)\b)",
        re.IGNORECASE | re.MULTILINE,
    ),
    # A month/day with a 24-hour time after it: "-[signer] 5/1 0340",
    # "4/16 02:15" — a timestamp, never a ratio (held-out patient files).
    "DATE_MD_TIME": re.compile(
        r"(?<![\d/.])(?P<v>(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01]))"
        r"(?=\s+(?:[01]\d|2[0-3]):?[0-5]\d\b)"),
    # A month/day in brackets after a history cue: "s/p lap chole (6/22)",
    # "Prior K 5.9 (7/6)". A bare "(4/5)" strength score has no cue.
    "DATE_MD_PAREN": re.compile(
        r"(?i:\b(?:s/p|status\s+post|prior|previous|last|since|on|from|dated|done|"
        r"performed|admitted|discharged|surgery|procedure|visit|seen)\b)[^()\n]{0,40}"
        r"\((?P<v>(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01]))\)"),
    # Month-name dates, so they are found even when the language model
    # misses them.
    "DATE_MONTHNAME": re.compile(
        r"\b(?:"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
        r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{2,4}"
        r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:of\s+)?"
        r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
        r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
        r"\.?,?\s+\d{2,4}"
        # Month and ordinal day with no year ("May 30th", "Apr 5th"): month
        # and day are Safe Harbor date elements on their own. The ordinal
        # suffix is required, so "May 3 patients" never qualifies.
        r"|(?:January|February|March|April|May|June|July|August|September|October|November|December"
        r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}(?:st|nd|rd|th)"
        # Month name and plain day ("F/u June 6", "Feb 22"). "May" is left
        # out here — "may 3 times" — and needs the ordinal form above.
        r"|(?:January|February|March|April|June|July|August|September|October|November|December"
        r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)\.?\s+(?:[12]\d|3[01]|0?[1-9])"
        r"(?![\d:]|\s*(?:mg|mcg|%|patients?|cases?|units?|times|weeks?|days?|months?|years?)\b)"
        r")\b",
        re.IGNORECASE,
    ),
    "US_DRIVER_LICENSE": re.compile(r"\b[A-Z]{1,2}\d{6,8}\b"),
    # Street address: number, one to three capitalised words, a street
    # type. "123 Maple Street", "4521 N. Oak Ave, Apt 3B".
    "LOCATION_STREET": re.compile(
        # [ \t] not \s: an address lives on one line. "8 Sep 2021\n\nDear
        # Dr. X" read as number + words + "Dr" (Drive) across blank lines.
        r"\b\d{1,6}[ \t]+(?:[NSEW]\.?[ \t]+)?(?:[A-Z][a-z]+[ \t]+){1,3}"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way|"
        r"Place|Pl|Terrace|Ter|Parkway|Pkwy|Circle|Cir|Highway|Hwy|Square|Sq)\b\.?"
        r"(?:,?\s+(?:Apt|Apartment|Suite|Ste|Unit|#)\.?\s*[A-Za-z0-9-]+)?"),
    # Any identifier introduced by an identifier word: "med rec #99887766",
    # "MedRec# CM-112233", "Policy No: 789-456-123", "HICN: B123456789",
    # "case #JH-998877", "(ID: 987654321)". The value must carry at least
    # four digits and may not be a quantity ("plan 5000 units").
    "UNIQUE_ID": re.compile(
        r"\b(?:mrn|med\.?\s*rec(?:ords?)?|medrec|medical\s+records?(?:\s+show)?|record|chart|"
        r"acct|account|case|ref(?:erence)?\.?(?:\s+code)?|code|claim|policy(?:\s+(?:no\.?|number))?|"
        r"(?:ins(?:urance|ur)?|health)\s+(?:plan|id)|plan\s+(?:id|no\.?|number|is)|insurance|"
        r"member(?:\s+id)?|subscriber(?:\s+id)?|insurer\s+id|hicn|mbi|hbn|hmo\s+id|ppo\s+id|"
        r"health\s+id|patient\s+id|pt\s+id|id|no\.|number)"
        r"(?:\s+(?:on\s+file|number|no\.?|#))?\s*[:#]?\s*(?:is\s+)?#?\s*"
        r"(?P<v>(?=(?:[A-Z-]*\d){4})[A-Z]{0,4}-?\d[\dA-Z-]{3,16})\b"
        r"(?!\s*(?:mg|mcg|g|ml|units?|iu|meq|%|mm|cm|kg|lbs?|tabs?|times|x)\b)",
        re.IGNORECASE),
    # Letter-prefixed identifier anywhere: "RX-87654321", "SP-112233",
    # "UHC730475957" (three or four letters then six or more digits).
    "UNIQUE_ID_PREFIXED": re.compile(
        r"\b(?P<v>[A-Z]{2,4}-\d{5,10}|[A-Z]{3,4}\d{6,12}|[A-Z]\d{3,6}-\d{3,6})\b"),
    # A bare long number standing alone: "(56421310)", "stored under
    # 48315494", "/29606539". Seven to ten digits with no unit is not a
    # measurement; phone formats are claimed by the phone recognizers.
    # "dd-ddd-ddd" is a record-number shape, not a phone (ddd-ddd-dddd).
    "UNIQUE_ID_BARE": re.compile(
        r"(?<![\d$.,-])(?P<v>\d{7,10}|\d{2,3}-\d{3}-\d{3})(?![\d,-]|\.\d)"
        r"(?!\s*(?:mg|mcg|g|ml|mL|units?|iu|cells|copies|/|x|%))"),
    # WEB ADDRESS by its scheme or "www.", whatever the ending. The model's
    # URL reader checks the top-level domain, so a portal address on a
    # private or unusual domain passed through untouched (in testing,
    # nearly all such portal links were missed).
    "URL_SCHEME": re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>\"'()\[\]]*[^\s<>\"'()\[\].,;:!?]"),
    # LOGIN NAMES. Labelled ("username: twieczorek.tac", "login = ruskin.w"),
    # stamped by a system ("Printed by rtamsin 04/18/2081", "LOGGED BY
    # SDAVIS FROM 10.12.40.9"), or a document comment's author tag
    # ("Comment [AM1]"). An ordinary word in the slot is not taken: see
    # _regex_scan.
    "USERNAME": re.compile(
        r"(?i:\b(?:user\s*(?:name|id)|login(?:\s*id)?|log-?on|handle|screen\s*name|gamer\s*tag|"
        r"portal\s*(?:user(?:name)?|id|login))\s*(?:is\s+|=\s*|:\s*|#\s*)?)"
        r"(?P<v>@?[A-Za-z][\w.\-]{1,40}[A-Za-z0-9])"),
    "USERNAME_ACTOR": re.compile(
        r"(?i:\b(?:printed|sent|documented|logged|read|entered|edited|reviewed|updated|accessed|"
        r"opened|modified|created|charted|scanned|released|faxed|transcribed)\s+by\s+(?:user\s+)?)"
        r"(?P<v>[A-Za-z][A-Za-z0-9._-]{2,30})(?=\s+(?:\d|from\b|on\b|at\b|via\b|@)|\s*[.;,]?\s*$)",
        re.M),
    "USERNAME_COMMENT": re.compile(r"(?i:\bcomment\s*\[)(?P<v>[A-Za-z]{1,4}\d{1,3})\]"
                                   r"|\[(?i:comment)\s+(?P<x>[A-Za-z]{1,4}\d{1,3})\s*:"),
    # VEHICLE PLATES after a vehicle word: "plate BKM 6215", "car (plate MN
    # GRX-4471)", "the van MNL 4473", "PLATE 7KRM204 ARIZONA". The value
    # must hold both a letter and a digit (checked in _regex_scan).
    "VEHICLE_PLATE": re.compile(
        r"(?i:\b(?:licen[cs]e\s+plate|plate|registration|reg\.?|vehicle|car|van|truck|"
        r"suv|sedan|motorbike|motorcycle)\s*(?:no\.?|number|#|:)?\s*(?:is\s+)?)"
        r"(?P<v>(?:[A-Z]{2}\s)?[A-Z0-9]{1,4}[-\s]?[A-Z0-9]{2,5})(?![\w-])"),
    # DEVICE NAMES AND SERIALS after a device word: "mic SN 3319-0X7",
    # "device FB-CHARGE-8841-TW", "pairs to phone as OBRADOVIC_M_METER",
    # "workstation WS-LAKE-0446". Needs a digit or an underscore.
    "DEVICE_SERIAL": re.compile(
        r"(?i:\b(?:serial(?:\s+(?:no\.?|number))?|s/n|sn|device(?:\s+id)?|unit|meter|monitor|"
        r"pump|workstation|terminal|bt\s+name|bluetooth\s+name|pairs\s+to\s+phone\s+as|"
        r"pairing\s+name|wi-?fi(?:\s+name)?|ssid|network)\s*(?:#|:)?\s*(?:sn\s+)?)"
        r"(?=[A-Za-z0-9_-]*[\d_])(?P<v>[A-Za-z0-9][A-Za-z0-9_-]{3,30}[A-Za-z0-9])"),
    # MONTH AND YEAR: "02/2094", "next due 07/2084". A month is a date
    # element under Safe Harbor even without its day.
    "DATE_MY": re.compile(r"(?<![\d/.])(?P<v>(?:0?[1-9]|1[0-2])/(?:19|20)\d{2})(?![\d/])"),
    # MONTH NAMES IN ANY CASE with a year or a day: "DEC 2090", "DEC 24
    # 2074", "Feb 2072". Legacy all-caps notes print months in capitals.
    "DATE_MONTHNAME_ANYCASE": re.compile(
        r"(?i)\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?[ \t]+(?:(?:[12]\d|3[01]|0?[1-9])(?:st|nd|rd|th)?,?[ \t]+)?(?:19|20)\d{2}\b"
        r"|(?i:\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?[ \t]+(?:[12]\d|3[01]|0?[1-9])(?:st|nd|rd|th)?)(?=[ \t]*(?:[,.;)]|$|\d{1,2}:\d\d))"),
    # DAY OF THE MONTH spoken: "on the 15th", "x the 15th", "the first of
    # October", "on the sixteenth". Never "the third time" or "the 5th
    # digit": a following noun that counts something rules it out.
    "DATE_ORDINAL": re.compile(
        r"(?i:\b(?:on|of|since|by|until|till|from|before|after|x|starting|through|around|about|due)[ \t]+the[ \t]+)"
        r"(?P<v>(?i:\d{1,2}(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|twenty[- ](?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)|thirtieth|thirty[- ]first)(?:[ \t]+of[ \t]+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?))?)\b"
        r"(?![ \t]+(?i:time|times|dose|doses|day|days|week|weeks|month|percentile|digit|rib|floor|grade|"
        r"trimester|cycle|cranial|toe|finger|metacarpal|metatarsal|vertebra|degree|line|attempt|round|"
        r"visit|try|one|child|baby|pregnancy|of[ \t]+(?:the|these|those|them|his|her|their|\d)))"),
    # NAMED DAYS that fix a calendar date.
    "DATE_HOLIDAY": re.compile(
        r"(?i)\b(?:christmas(?:[ \t]+(?:day|eve))?|xmas(?:[ \t]+eve)?|boxing[ \t]+day|thanksgiving(?:[ \t]+day)?|"
        r"new[ \t]+year'?s(?:[ \t]+(?:day|eve))?|independence[ \t]+day|fourth[ \t]+of[ \t]+july|july[ \t]+4th|"
        r"halloween|valentine'?s[ \t]+day|labou?r[ \t]+day|memorial[ \t]+day|veterans[ \t]+day|"
        r"easter(?:[ \t]+(?:sunday|monday))?|mother'?s[ \t]+day|father'?s[ \t]+day|st\.?[ \t]+patrick'?s[ \t]+day|"
        r"juneteenth|diwali|hanukkah|rosh[ \t]+hashanah|yom[ \t]+kippur|lunar[ \t]+new[ \t]+year|"
        r"canada[ \t]+day|anzac[ \t]+day|bastille[ \t]+day)\b"),
    # MONTH/DAY after more of the words people put before a date ("lost
    # job 4/17", "night of 9/2", "filled 07/24", "dx 4/26"), and the
    # day-first form when the day cannot be a month ("FALL ON 23/01").
    "DATE_MD_MORE": re.compile(
        r"(?i:\b(?:night|evening|morning|afternoon|weekend|week)[ \t]+of[ \t]+|"
        r"\b(?:midnight|dx|diagnosed|filled|refilled|stops?|starts?|ends?|ended|began|begins|before|after|"
        r"saw[ \t]+(?:him|her|them|pt)|discharge|court[ \t]+date|hearing|injury|onset|job|quit|moved|"
        r"arrived|left|returned|born|died|delivered|placed|replaced|inserted|removed|dictated|"
        r"transcribed|printed|signed|faxed|called|phoned|emailed|messaged|texted|visited|fell|fall|"
        r"injured|admit(?:ted)?|d/c|dc'?d|post-?op|op|radius|appointment|appt|apt)[ \t]+(?:on[ \t]+)?)"
        r"(?P<v>(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])|(?:1[3-9]|2\d|3[01])/(?:0?[1-9]|1[0-2]))"
        r"(?![/\-]?\d|[ \t]*(?i:tabs?|tablets?|strength|dose|murmur|power|reflex(?:es)?|mg|pain|severity))"),
    # MONTH/DAY before the thing it dates: "the 4/17 appointment", "his
    # 9/2 visit", "a 3/15 court date".
    "DATE_MD_BEFORE": re.compile(
        r"(?<![\d/.])(?P<v>(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01]))(?![/\-]?\d)"
        r"(?=[ \t]+(?i:appointment|appt|visit|follow[- ]?up|f/u|surgery|procedure|court|hearing|"
        r"deadline|deposition|admission|discharge|clinic|call|session|class|meeting|flight|"
        r"birthday|party|wedding|funeral|delivery|induction)\b)"),
    # STREETS IN CAPITALS, on more street kinds, with the unit. The kinds
    # include every street suffix in USPS Publication 28 (Appendix C1),
    # added after generated test addresses ("119 Catherine Viaduct",
    # "2530 Natalie Forge") were missed.
    "LOCATION_STREET_WIDE": re.compile(
        r"\b\d{1,6}[A-Za-z]?(?:-\d{1,4})?[ \t]+(?:[NSEW]{1,2}\.?[ \t]+)?(?:[A-Z][A-Za-z'’.\-]*[ \t]+){1,3}"
        r"(?=[A-Z])(?i:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|La|Ln|Drive|Dr|Court|Ct|Way|Place|Pl|Terrace|Ter|Tce|Parkway|Pkwy|Circle|Cir|Highway|Hwy|Square|Sq|Row|Mews|Close|Crescent|Cres|Rise|Bend|Crossing|Xing|Quay|Hollow|Run|Trail|Trl|Path|Walk|Gardens|Gdns|Grove|Heights|Hts|Hill|Point|Ridge|Loop|Plaza|Alley|Green|View|Vale|Esplanade|Promenade|Parade|Pde|Wynd|Landing|Commons|Cove|Glen|Meadows?|Pike|Turnpike|Causeway|Embankment|Broadway|Annex|Arcade|Bayou|Beach|Bluffs?|Bottom|Branch|Bridge|Brooks?|Burgs?|Bypass|Canyon|Cape|Cliffs?|Corners?|Course|Courts|Coves|Creek|Crest|Crossroads?|Curve|Dale|Divide|Drives|Estates?|Expressway|Extensions?|Falls?|Ferry|Fields?|Flats?|Fords?|Forges?|Forks?|Fort|Freeway|Gateway|Glens|Greens|Groves|Harbors?|Haven|Hills|Inlet|Islands?|Isle|Junctions?|Knolls?|Lakes?|Loaf|Lodge|Mall|Manors?|Mills?|Motorway|Mount|Mountains?|Neck|Orchard|Oval|Overpass|Parks?|Parkways|Pass|Passage|Pines?|Plains?|Points|Ports?|Prairie|Radial|Ramp|Ranch|Rapids?|Ridges|River|Roads|Route|Rue|Shoals?|Shores?|Skyway|Springs?|Spurs?|Squares|Stravenue|Stream|Streets|Summit|Throughway|Trace|Track|Trafficway|Trailer|Tunnel|Underpass|Valleys?|Viaduct|Views|Villages?|Ville|Vista|Walks|Ways)\b\.?(?:[ \t]+(?:NW|NE|SW|SE)\b)?(?:,?[ \t]+(?i:apt|apartment|suite|ste|unit|flat|fl|floor|rm|room|#)\.?[ \t]*#?[A-Za-z0-9-]+)?"),
    # A number, a capitalised street word and a unit: "5521 OLEANDER APT 2".
    "LOCATION_STREET_UNIT": re.compile(
        r"\b\d{1,6}[ \t]+(?:[NSEW][ \t]+)?[A-Z][A-Za-z'’\-]+(?:[ \t]+[A-Z][A-Za-z'’\-]+){0,2},?[ \t]+"
        r"(?i:apt|apartment|unit|suite|flat|#)\.?[ \t]*#?[A-Za-z0-9-]+"),
    # After "lives at" / "address" / "mail it to": the number and its
    # street words, whatever the street kind ("3320 W LISBON").
    "LOCATION_STREET_LED": re.compile(
        r"(?i:\b(?:lives?|living|resides?|residing|staying|address(?:[ \t]+on[ \t]+file)?|"
        r"mail(?:ed)?(?:[ \t]+\w+)?[ \t]+to|send[ \t]+(?:it[ \t]+)?to|moved[ \t]+to|located[ \t]+at)\b[:\s]+(?:at[ \t]+)?)"
        r"(?P<v>\d{1,6}[ \t]+(?:[NSEW]\.?[ \t]+)?[A-Z][A-Za-z'’\-]+(?:[ \t]+[A-Z][A-Za-z'’\-]+){0,2}(?:,?[ \t]+(?i:apt|apartment|suite|ste|unit|flat|fl|floor|rm|room|#)\.?[ \t]*#?[A-Za-z0-9-]+)?)"),
    # Streets abroad: "ul. Długa 12/4", "STRANDGATEN 24", "Calle Ocotillo
    # 3340", "3340 CALLE OCOTILLO", "PO Box 4417".
    "LOCATION_STREET_INTL": re.compile(
        r"\bul\.[ \t]+[A-ZÀ-ɏ][\wÀ-ɏ]+(?:[ \t]+[A-ZÀ-ɏ][\wÀ-ɏ]+)?[ \t]+\d{1,4}(?:/\d{1,4})?"
        r"|\b[A-ZÀ-ɏ][\wÀ-ɏ]*(?i:gaten|gatan|gata|vej|v[äa]gen|veien|stra(?:ß|ss)e|str\.|weg|laan|straat|gasse|platz)[ \t]+\d{1,4}[A-Za-z]?\b"
        r"|\b\d{1,6}[ \t]+(?i:calle|avenida|av\.|rua|via|camino|paseo|rue|chemin|carrer)[ \t]+[A-ZÀ-ɏ][\wÀ-ɏ]+(?:[ \t]+[A-ZÀ-ɏ][\wÀ-ɏ]+)?"
        r"|\b(?i:calle|avenida|rua|rue|carrer)[ \t]+[A-ZÀ-ɏ][\wÀ-ɏ]+(?:[ \t]+[A-ZÀ-ɏ][\wÀ-ɏ]+)?,?[ \t]+\d{1,5}\b"
        r"|\b\d{1,5}[ \t]+[A-Z][\w]+-(?:ro|gil|dong|gu)\b"
        r"|(?i:\b(?:p\.?[ \t]*o\.?[ \t]*box|post[ \t]+office[ \t]+box|postfach|apartado|private[ \t]+bag)[ \t]+\d{1,7}\b)"),
    # POSTCODES ABROAD by their shape: Canada "M9C 5K1", UK "SW1A 1AA",
    # Poland "31-028 Kraków", Australia "WA 6008".
    "POSTCODE_INTL": re.compile(
        r"\b(?P<v>[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z][ \t]?\d[ABCEGHJ-NPRSTV-Z]\d)\b"
        r"|\b(?P<x>[A-Z]{1,2}\d[A-Z\d]?[ \t]\d[A-Z]{2})\b"
        r"|(?<![\d-])(?P<y>\d{2}-\d{3})(?=[ \t]+[A-ZÀ-ɏ][a-zà-ɏ])"
        r"|(?:\b(?:WA|NSW|VIC|QLD|SA|TAS|NT|ACT)[ \t]+)(?P<z>\d{4})\b"),
    # More login shapes: a system stamp "BY twrobel FROM 10.44.2.188",
    # a document's comment or edit author "[Comment by fcarrick:",
    # "[DELETED by isolheim:", "LAST EDIT RACHTERB FROM", a social handle
    # "@toby_k_osei", a sender id on a "From:" line, and a lowercase
    # system id after "by" ("transcribed ... by tranmed_04").
    "USERNAME_STAMP": re.compile(
        r"(?i:\b(?:by|edit(?:ed)?)[ \t]+(?:user[ \t]+)?)(?P<v>[A-Za-z][A-Za-z0-9._-]{2,30})(?=[ \t]+(?i:from|at)[ \t]+\d{1,3}\.\d)"
        r"|(?i:\b(?:comment(?:ed)?|tracked[ \t]+(?:insertion|deletion|change)|inserted|deleted|last[ \t]+edit(?:ed)?|edited|modified)"
        r"(?:[ \t]+by)?[ \t]*[:\-]?[ \t]*)(?P<x>[A-Za-z][A-Za-z0-9._]{2,30})(?=[ \t]*[:\],]|[ \t]+\d|[ \t]+(?i:from)\b)"),
    "USERNAME_HANDLE": re.compile(
        r"(?<![\w@.])@(?P<v>[A-Za-z][\w.]{2,30}[A-Za-z0-9_])"
        r"|(?i:\b(?:user|aka|a\.k\.a\.|speaker[ \t]+profile|login[ \t]+name|handle(?:[ \t]+for[ \t]+[a-z]+\b)?|"
        r"xbox(?:[ \t]+name)?|psn|discord|snap(?:chat)?|insta(?:gram)?|ig|tiktok|twitter)[ \t]*[:=]?[ \t]*)"
        r"\b(?P<x>[A-Za-z][A-Za-z0-9._-]{2,30}[A-Za-z0-9])"
        r"|(?i:\bfrom:[^\n]{0,60}?)(?<![\w.@])(?P<y>[A-Za-z][A-Za-z_.]*\d+[A-Za-z_.\d]*)(?![\w@])"
        r"|(?i:\bby[ \t]+)(?P<z>[a-z][a-z0-9]*[._][\w.]*[a-z0-9]|[a-z]{3,}\d{1,4}[a-z]*)\b"),
    # PLAN NUMBERS AS PEOPLE WRITE THEM: in groups, with spaces or dashes,
    # after any of the names a plan number goes by ("Member ID LMH 4471
    # 90238 W", "OHIP 4417-293-806-KM", "my Medicaid number is OH 0044 718
    # 6602", "Ins: VTM 4402 9918 03"). Digit and length checked in
    # _shape_ok.
    "HEALTH_PLAN_WIDE": re.compile(
        r"(?i:\b(?:member(?:[ \t]+(?:id|#|no\.?|number))?|subscriber(?:[ \t]+id)?|health[ \t]*plan(?:[ \t]+id)?|"
        r"plan[ \t]+id|policy(?:[ \t]+(?:no\.?|number|#))?|medicaid(?:[ \t]+(?:number|no\.?|#|id))?|"
        r"medicare(?:[ \t]+advantage)?(?:[ \t]+(?:number|no\.?|#|id|card))?|ohip|health[ \t]+card|"
        r"ins(?:urance)?(?:[ \t]+(?:member[ \t]+)?id)?|phn|nhs(?:[ \t]+(?:no\.?|number))?|"
        r"group(?:[ \t]+(?:no\.?|#|number))?)[ \t]*[:#(]?[ \t]*(?:is[ \t]+)?)"
        r"(?P<v>[A-Z0-9]{1,8}(?:[ -]?[A-Z0-9]{1,10}){0,5})(?![\w-])"),
    # OTHER LABELLED NUMBERS: a report, file, case, docket, job, claim,
    # passport, tracking or consent number, written however it was given.
    "ID_LABELLED": re.compile(
        r"(?i:\b(?:report|file|session|case|docket|job|claim|ref(?:erence)?|confirmation|conf|"
        r"tracking|tarcking|passport|visa|permit|alien|a\#|uscis|badge|employee|student|locker|"
        r"requisition|accession|encounter|enc|visit|csn|fin|har|ticket|incident|court|ur|chart|pin|access[ \t]+code|"
        r"activation[ \t]+code|release[ \t]+form|form|voucher|rx)"
        r"[ \t]*(?:no\.?|number|numbre|num|#|id|code)?[ \t]*[:#]?[ \t]*(?:is[ \t]+)?)"
        r"(?P<v>[A-Z0-9][A-Z0-9]{0,8}(?:[ -][A-Z0-9]{1,10}){0,4})(?![\w-])"
        r"(?![ \t]*(?i:mg|mcg|ml|units?|tabs?|hours?|hrs?|days?|weeks?|%))"),
    # CODE WORDS: "ORANGE-HERON-42" — two words and a number, joined.
    "ID_WORDCODE": re.compile(r"\b(?P<v>[A-Z]{3,}-[A-Z]{3,}-\d{2,})\b"),
    # NUMBERS SPOKEN IN WORDS, four digits or more: "my pin is four four
    # zero eight", "eight oh two five five five oh one three seven".
    "SPOKEN_DIGITS": re.compile(
        r"(?i)\b(?:(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|niner)[ \t,-]+){3,}"
        r"(?:zero|oh|one|two|three|four|five|six|seven|eight|nine|niner)\b"),
    "IP_ADDRESS": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}


# ---------------------------------------------------------------------
# BROAD RECOGNIZERS — normalize-then-match, with a specificity gate.
#
# The fixed patterns above are exact-shape: "123-45-6789" matches and
# "123 45 6789" does not. A format-variant stress test put prose recall
# at 50% for SSN and 67% for MRN, while phone/date/email -- which were
# written flexibly -- were all found in that test. Enumerating separator
# variants does not fix this; the next unlisted shape misses too.
#
# So the pattern adapts to the input instead: match a
# separator-agnostic skeleton, then VERIFY what was captured. Wide
# capture alone would remove ordinary words and numbers, so every broad
# match is followed by a specificity check that can reject it.
# ---------------------------------------------------------------------

# 3-2-4 digits with any (or no) separator, independently chosen.
_SSN_SKELETON = re.compile(r"\b(\d{3})[-.\s]?(\d{2})[-.\s]?(\d{4})\b")


def _is_valid_ssn(area: str, group: str, serial: str) -> bool:
    """SSA structural rules. This is the verification step: a 9-digit run
    is a weak signal on its own -- accession numbers and device serials
    look identical -- but real SSNs cannot use these ranges, so this broad
    finder skips invalid combinations (the strict ddd-dd-dddd pattern
    still removes them)."""
    a, g, s = int(area), int(group), int(serial)
    if a == 0 or a == 666 or a >= 900:
        return False
    if g == 0 or s == 0:
        return False
    return True


def _promiscuous_ssn(text: str) -> list[PHIHit]:
    hits = []
    for m in _SSN_SKELETON.finditer(text):
        area, group, serial = m.group(1), m.group(2), m.group(3)
        if not _is_valid_ssn(area, group, serial):
            continue
        # A separator-free 9-digit run is the weakest form: it collides
        # with accession and account numbers, so it scores lower and the
        # clinical layers get a chance to subtract it.
        separated = not m.group(0).isdigit()
        hits.append(PHIHit(
            entity_type="US_SSN",
            start=m.start(), end=m.end(), text=m.group(0),
            score=0.95 if separated else 0.6,
            layer="regex",
        ))
    return hits


# An identifier-shaped token: letters and digits mixed, or a digit run,
# 4-12 characters. Far too broad on its own -- hence the context gate.
_MRN_CANDIDATE = re.compile(r"\b(?=[A-Z0-9-]{4,12}\b)(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]{3,11}\b")
_MRN_CONTEXT = re.compile(
    r"\b(?:mrn|medical\s+record|chart|record|account|patient\s+id|encounter)\b",
    re.IGNORECASE,
)


_DATE_SHAPED = re.compile(
    r"(?:19|20)\d\d[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])|"
    r"(?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])[-/.](?:19|20)?\d\d")


def _promiscuous_mrn(text: str) -> list[PHIHit]:
    """Bare "F9988" in prose is indistinguishable from a lab accession or
    a device code, so shape alone must not redact. A record-keeping word
    within 40 characters is what licenses the match -- broad capture,
    gated on context rather than on the token's form."""
    hits = []
    for m in _MRN_CANDIDATE.finditer(text):
        # A bare four-digit year is not a record number: Safe Harbor keeps
        # the year, and "admitted in 2022 (MRN ...)" put one in range.
        if re.fullmatch(r"(?:19|20)\d\d", m.group(0)):
            continue
        # Nor is a whole date ("DOB: 1977-06-28" beside "MRN:"). The date
        # layers remove it under Safe Harbor; under the Limited Data Set it
        # is KEPT, and a record-number reading of it contradicted that.
        if _DATE_SHAPED.fullmatch(m.group(0)):
            continue
        # Nor a token with three digits or fewer: spinal levels ("L4-5",
        # "C5-6", "T12-L1"), scores and stages sat beside an MRN and were
        # removed as record numbers. A record number
        # carries more digits than that.
        if sum(c.isdigit() for c in m.group(0)) <= 3:
            continue
        window = text[max(0, m.start() - 40):m.end() + 40]
        if not _MRN_CONTEXT.search(window):
            continue
        hits.append(PHIHit(
            entity_type="MRN",
            start=m.start(), end=m.end(), text=m.group(0),
            score=0.7,
            layer="regex",
        ))
    return hits


def _shape_ok(entity: str, value: str) -> bool:
    """The few regex families whose slot can hold an ordinary word."""
    if entity in ("USERNAME", "USERNAME_ACTOR", "USERNAME_STAMP", "USERNAME_HANDLE"):
        if len(value) < 5 and not re.search(r"[._]", value):     # A1C, T2DM, G2P1
            return False
        if re.search(r"[\d._@-]", value):
            return True
        if value.isupper() and len(value) >= 4:      # SDAVIS, JSTEADMAN
            return True
        from inkguard.lexicon_english import ENGLISH_WORDS
        return value.lower() not in ENGLISH_WORDS and not _is_common_english(value)
    if entity in ("HEALTH_PLAN_WIDE", "ID_LABELLED"):
        alnum = re.sub(r"[^A-Za-z0-9]", "", value)
        if not re.search(r"\d", value) or len(alnum) < 4:
            return False
        # A bare year or a small count after "case" / "form" is not an id.
        if re.fullmatch(r"(?:19|20)\d\d", alnum) or (alnum.isdigit() and len(alnum) < 5
                                                       and entity == "ID_LABELLED"):
            return False
        return True
    if entity == "VEHICLE_PLATE":
        return bool(re.search(r"[A-Z]", value) and re.search(r"\d", value))
    if entity == "DEVICE_SERIAL":
        return bool(re.search(r"[\d_]", value)) and not value.isdigit() or \
            (value.isdigit() and len(value) >= 6)
    return True


def _regex_scan(text: str) -> list[PHIHit]:
    hits = []
    for entity, pattern in REGEX_PATTERNS.items():
        for m in pattern.finditer(text):
            named = [k for k in ("v", "x", "y", "z") if k in pattern.groupindex]
            g = next((k for k in named if m.group(k) is not None), 0)
            if not _shape_ok(entity, m.group(g)):
                continue
            hits.append(PHIHit(
                entity_type=entity,
                start=m.start(g), end=m.end(g),
                text=m.group(g),
                # Generic identifiers yield the label to a specific type
                # (MRN, plan ID) when both claim the same characters.
                score=0.90 if entity.startswith("UNIQUE_ID") else 0.95,
                layer="regex",
            ))
    return hits


# ---------------------------------------------------------------------
# Layer 2/3: Presidio engine with custom healthcare recognizers bolted
# on. Built once, on first use or at warmup(), and reused across notes,
# because loading the model takes seconds.
# ---------------------------------------------------------------------
def _build_nlp_engine():
    """Build the spaCy NLP engine EXPLICITLY.

    This is not optional boilerplate. A bare AnalyzerEngine() loads
    Presidio's packaged default config, which specifies en_core_web_lg.
    If that model is not installed, spaCy raises OSError [E050] — and
    because the analyzer is built lazily on first use, the app would
    start normally and then hold EVERY note it was given.

    So the model is named here, read from the environment, and verified
    at start-up, where a missing model is reported plainly.

    MODEL CHOICE IS A RECALL DECISION, not a packaging detail:
      en_core_web_lg  ~590 MB, best NER recall on person names. Default,
                      because a missed name cannot be taken back once a
                      file is shared.
      en_core_web_sm  ~12 MB, materially worse on the exact case that
                      matters most here — rare and non-Western surnames,
                      which are already this system's weakest axis.
    The model is fixed; a smaller one is used only when the large one is
    not installed, with a warning.
    """
    import spacy
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    model_name = "en_core_web_lg"

    # Verify the model is actually present before Presidio tries to load
    # it, so the error names the fix instead of surfacing as a spaCy
    # stack trace three layers down.
    try:
        spacy.load(model_name)
    except OSError as exc:
        fallback = "en_core_web_sm"
        if model_name != fallback:
            try:
                spacy.load(fallback)
            except OSError:
                raise RuntimeError(
                    f"spaCy model {model_name!r} is not installed and neither "
                    f"is the {fallback!r} fallback. Install one: "
                    f"python -m spacy download {model_name}"
                ) from exc
            # Degrading is allowed, silently degrading is not.
            import warnings
            warnings.warn(
                f"Inkguard: spaCy model {model_name!r} is not installed; using "
                f"{fallback!r} instead. Name detection is noticeably weaker "
                f"with the small model. Install {model_name} for normal use.",
                RuntimeWarning, stacklevel=2,
            )
            model_name = fallback
        else:
            raise RuntimeError(
                f"spaCy model {model_name!r} is not installed. "
                f"Install it: python -m spacy download {model_name}"
            ) from exc

    return NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": model_name}],
    }).create_engine()


def _build_analyzer() -> AnalyzerEngine:
    analyzer = AnalyzerEngine(nlp_engine=_build_nlp_engine())

    custom_recognizers = [
        PatternRecognizer(
            supported_entity="MRN",
            patterns=[Pattern(name="mrn_pattern", regex=r"\b(?:MRN|Medical Record (?:Number|No\.?))\s*[:#]?\s*([A-Z0-9]{4,12})\b", score=0.85)],
            context=["patient", "record", "chart", "hospital"],
        ),
        PatternRecognizer(
            supported_entity="NPI",
            patterns=[Pattern(name="npi_pattern", regex=r"\b\d{10}\b", score=0.3)],
            context=["npi", "provider", "national provider identifier"],
        ),
        PatternRecognizer(
            supported_entity="HEALTH_PLAN_ID",
            # TWO defects fixed here, together worth 32% of all over-redaction
            # on a 200-document clinical benchmark:
            #
            # 1. "plan" was a context word. In clinical text PLAN is the P in
            #    SOAP — essentially every note has a PLAN section — so the
            #    context boost fired constantly and promoted ordinary words
            #    ("possible", "nursing", "discharge", "patient") to insurance
            #    identifiers. A context cue must be rare in the target domain;
            #    this one was near-universal in it.
            # 2. Presidio applies IGNORECASE by default, so [A-Z0-9]{6,15}
            #    matched lowercase prose. (?-i:...) forces case-sensitivity and
            #    the lookahead requires at least one digit, because a real plan
            #    identifier has digits and an English word does not.
            patterns=[Pattern(name="plan_id_pattern",
                              regex=r"\b(?-i:(?=[A-Z0-9]*\d)[A-Z0-9]{6,15})\b",
                              score=0.2)],
            context=["member", "subscriber", "policy", "insurance", "payer"],
        ),
    ]
    for rec in custom_recognizers:
        analyzer.registry.add_recognizer(rec)

    return analyzer


_analyzer_engine: AnalyzerEngine | None = None


def get_analyzer() -> AnalyzerEngine:
    global _analyzer_engine
    if _analyzer_engine is None:
        _analyzer_engine = _build_analyzer()
    return _analyzer_engine


def _ner_scan(text: str) -> list[PHIHit]:
    analyzer = get_analyzer()
    # score_threshold is the noise filter: a low-specificity pattern
    # (e.g. a bare "10 digits" or "6-15 alphanumerics" recognizer) only
    # survives into a real hit if context words nearby boosted its
    # score past this bar. Below it, it's not a "low-confidence PHI
    # hit" — it's not a hit at all. This threshold decides what counts
    # as a hit in the first place; what happens to a hit is decided
    # later (the clinical guard, garnet, policy.py), and uncertain hits
    # are flagged for review.
    results = analyzer.analyze(
        text=text, language="en", entities=None,
        score_threshold=settings.confidence_threshold,
    )
    hits = []
    for r in results:
        etype = r.entity_type
        # A ten-digit number shaped like a North American phone number
        # passes the NHS-number checksum often enough to be labelled one;
        # in these notes it is a phone number.
        if etype == "UK_NHS" and _NANP_SHAPE.fullmatch(text[r.start:r.end]):
            etype = "PHONE_NUMBER"
        hits.append(PHIHit(
            entity_type=etype,
            start=r.start, end=r.end,
            text=text[r.start:r.end],
            score=r.score,
            layer="ner",
        ))
    return hits


_NANP_SHAPE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}")


def _dedupe(hits: list[PHIHit]) -> list[PHIHit]:
    """Resolve overlapping hits WITHOUT losing coverage.

    Earlier, on overlap, the higher-scoring hit replaced the other whole.
    When a long span overlapped a shorter, higher-scoring one, the part
    of the long span outside the overlap was left uncovered. For example,
    the anchor for "PATIENT:" ran into the next
    field and produced "渡辺 淳   MRN: 4471982"; the MRN hit scored
    higher and replaced it; the name was no longer covered by anything.
    Latin names survived only because NER caught them separately. Names
    that only the structural layers can see — CJK, rare surnames — were
    erased by any higher-scoring neighbour.

    Now resolution is per character:
      1. every character covered by ANY hit remains covered;
      2. each character takes the label of the highest-scoring hit
         covering it (ties: the longer hit, then the earlier one);
      3. contiguous characters with the same entity type merge back into
         one hit, trimmed of edge whitespace and punctuation.
    Coverage of identifier characters is kept; only edge whitespace and
    punctuation, role words before a name and credentials after it are
    trimmed.
    """
    if not hits:
        return []

    # 0. A NON-PHI label must never own a character a PHI label also
    #    claims. NER tagged "Babett Mentzel-Warmer" as ORGANIZATION
    #    (score 0.85); the candidate layer tagged "Mentzel-Warmer" as
    #    PERSON (0.45); ownership by score relabelled the surname as an
    #    organization, the profile then kept organizations, and the name
    #    was left in. The ledger caught it as LOST and blocked the note —
    #    the fail-safe worked, the pipeline was wrong. Relabelling toward
    #    a type the profile KEEPS is data loss, not a relabel.
    #
    #    And a non-PHI span that overlaps a PERSON is itself taken as
    #    PERSON across its full width: an "organization" containing a
    #    detected surname is a mis-typed name, and leaving "Babett" out
    #    would leak the given name.
    from inkguard.talc import spec as _spec
    phi_person = [h for h in hits if h.entity_type == "PERSON"]
    promoted: list[PHIHit] = []
    from inkguard.garnet import _ORG_SUFFIX
    for h in hits:
        if not _spec(h.entity_type).phi and not _ORG_SUFFIX.search(h.text) and any(
                p.start < h.end and h.start < p.end for p in phi_person):
            h = dataclasses.replace(h, entity_type="PERSON")
        promoted.append(h)
    hits = promoted

    order = sorted(range(len(hits)),
                   key=lambda i: (not _spec(hits[i].entity_type).phi,
                                  -hits[i].score,
                                  -(hits[i].end - hits[i].start),
                                  hits[i].start))
    owner: dict[int, int] = {}                 # char position -> hit index
    for i in order:
        h = hits[i]
        for pos in range(h.start, h.end):
            if pos not in owner:
                owner[pos] = i

    # Walk covered characters in order, grouping contiguous characters of
    # the same entity type. Each character's text comes from whichever hit
    # owns it, so a run can span several source hits and still merge.
    groups: list[list] = []        # [start, end, entity, best_idx, chars]
    for pos in sorted(owner):
        i = owner[pos]
        h = hits[i]
        k = pos - h.start
        ch = h.text[k] if 0 <= k < len(h.text) else " "
        g = groups[-1] if groups else None
        # Same CANONICAL type merges: "the 15th of January 2022" is one
        # date even when NER typed "the" DATE_TIME and the regex typed the
        # rest DATE_MONTHNAME. Split, the space between them was covered
        # by neither, and the ledger (rightly) reported a lost detection.
        if g is not None and g[1] == pos and _canonical_type(g[2]) == _canonical_type(h.entity_type):
            g[1] = pos + 1
            g[4].append(ch)
            if h.score > hits[g[3]].score:
                g[3] = i
        else:
            groups.append([pos, pos + 1, h.entity_type, i, [ch]])

    out: list[PHIHit] = []
    edge = " \t\n,.;:()[]"
    for start, end, entity, best, chars in groups:
        frag = "".join(chars)
        # Brackets are trimmed only when unbalanced inside the span:
        # "(555) 867-5309" keeps its "(", or the output reads
        # "(<PHONE_NUMBER>".
        e_lead = edge.replace("(", "").replace("[", "") \
            if (")" in frag or "]" in frag) else edge
        e_trail = edge.replace(")", "").replace("]", "") \
            if ("(" in frag or "[" in frag) else edge
        lead = len(frag) - len(frag.lstrip(e_lead))
        trail = len(frag) - len(frag.rstrip(e_trail))
        s2, e2 = start + lead, end - trail
        if e2 <= s2:
            continue
        src = hits[best]
        body = frag[lead:len(frag) - trail]
        if entity == "PERSON":
            # Role and title words are not part of the name. NER often
            # returns "Patient Aisha Haddad"; left in, the pseudonym
            # layer sees a different person from "Aisha Haddad" two lines
            # down and issues a second code. "Mr." stays in the text too:
            # it is grammar, and clinically useful (sex), not identity.
            m = _LEADING_ROLE.match(body)
            if m and m.end() < len(body):
                s2 += m.end()
                body = body[m.end():]
            t = _TRAILING_CREDENTIAL.search(body)
            if t and t.start() > 0:
                e2 = s2 + t.start()
                body = body[:t.start()]
        out.append(PHIHit(
            entity_type=entity, start=s2, end=e2,
            text=body,
            score=src.score, layer=src.layer,
            section=src.section, evidence=src.evidence,
        ))
    return out


from inkguard.talc import LEADING_ROLE as _LEADING_ROLE  # shared with the ledger
from inkguard.talc import TRAILING_CREDENTIAL as _TRAILING_CREDENTIAL  # shared with the ledger

_ALPHA_SPAN_RE = re.compile(r"[A-Za-z][A-Za-z\-'\s]*")

_NAME_TITLE_RE = re.compile(
    r"\b(?:mr|mrs|ms|miss|dr|doctor|patient|pt|resident|attending|"
    r"nurse|rn|md|do|np|pa)\.?\s*$",
    re.IGNORECASE,
)


def _is_clinical_in_context(text: str, hit: PHIHit, profile=None) -> bool:
    """Decide whether a hit is a medical concept, using the words
    around it — not just the matched span.

    NER usually tags only the eponym itself ("Wilson"), not the full
    concept ("Wilson disease"), so a span-only lexicon check fails on
    exactly the cases that matter most. We therefore look one or two
    tokens to the right for a disease qualifier.

    The guard in the other direction: if the span is immediately
    preceded by a name title ("Mrs. Wilson", "Patient Jones"), it is a
    PERSON regardless of any qualifier that happens to follow. Titles
    beat qualifiers, because leaking a patient name is the worse of
    the two failure modes.
    """
    # A NAME TITLE (or a relation / attribution: "Per Cide", "wife Rosarius",
    # "discussed with Soles") BEFORE A SINGLE WORD WINS OVER EVERY LEXICON TIER.
    # "Dr. Crohn", "Mr. Babinski", "Mrs. Bovie": 247, 694 and 226 people in
    # the 2000 US Census carry those surnames. Before this, only the weak
    # vocabulary tier honoured a title; an always-clinical eponym did not.
    # A clinical ACRONYM written in capitals ("F/u with PCP", "d/w ICU")
    # yields only to a real title: people's names are not written "PCP".
    # A name read from the STRUCTURE of the note ("SURNAME, GIVEN  MRN"
    # headers, sign-offs, relation and role frames) is a person whatever
    # the vocabulary says: "BABINSKI, LARS  MRN" was once left in because
    # Babinski is also a clinical sign.
    if hit.entity_type == "PERSON" and any(
            ev == "signoff" or ev in ("role:header",) or ev.startswith("role:kin")
            for ev in (hit.evidence or ())):
        return False
    if hit.entity_type == "PERSON":
        from inkguard.garnet import given_name_pair
        if given_name_pair(text, hit.start, hit.end):
            return False
    _tok = hit.text.strip()
    _caps_acronym = (_tok.isupper() and 2 <= len(_tok) <= 6 and _tok.isalpha()
                     and is_clinical_term(_tok))
    if (hit.entity_type in ("PERSON", "ORGANIZATION") and " " not in _tok
            and (_NAME_TITLE_RE.search(text[max(0, hit.start - 20):hit.start])
                 or (not _caps_acronym and _tight_person_context_at(text, hit.start, hit.end)))):
        return False

    # Unambiguous concept — no context needed.
    if is_clinical_term(hit.text):
        return True

    # ICD description vocabulary ("urethra", "cystocele") — clinical
    # unless a name title or relation word stands right before it.
    if _clinical_lexicon.is_vocabulary_word(hit.text) and not \
            _NAME_TITLE_RE.search(text[max(0, hit.start - 20):hit.start]):
        return True

    # CONTEXT EXPANSION IS DELIBERATELY NARROW.
    # An earlier version expanded every hit rightward and asked "does
    # this window contain a clinical word?" That let identifiers through: the
    # phone number in "phone 555-867-5309, has Wernicke encephalopathy"
    # expands to a window containing "Wernicke" and escapes redaction.
    # Adjacency to a medical word must never protect anything.
    #
    # Expansion therefore fires only when the hit is ITSELF an
    # ambiguous eponym that needs a qualifier to disambiguate
    # ("Wilson" → "Wilson disease"). Structured identifiers (digits,
    # IDs, emails) are never expanded, because no qualifier could make
    # an SSN clinical.
    if not _ALPHA_SPAN_RE.fullmatch(hit.text.strip()):
        return False

    hit_tokens = [t for t in re.split(r"[\s\-]+", hit.text.strip().lower()) if t]
    if not any(t in AMBIGUOUS_EPONYMS for t in hit_tokens):
        return False

    preceding = text[max(0, hit.start - 20):hit.start]
    if _NAME_TITLE_RE.search(preceding):
        return False

    # Look right for a disease qualifier ("disease", "criteria", "palsy").
    #
    # An earlier version took the next two words,
    # glued them to the hit, and asked is_clinical_term() about the
    # composite. Two flaws compounded:
    #   - the tail crossed clause breaks, so "Mcintyre, December 14,
    #     1979. Stage IIIC" became "Mcintyre December Stage";
    #   - is_clinical_term() says yes if ANY word in a span is medical,
    #     so "Anderson has hypertension" or "Haddad takes metoprolol"
    #     were read as clinical phrases.
    # Any surname in the ambiguous-eponym set (many are real
    # surnames that are also device brands) followed within two words by
    # a medical word was dropped from the redaction list.
    #
    # Now: stop at the clause boundary quartz computed,
    # and walk forward IN ORDER from the hit's own last token. Only an
    # unbroken chain of eponyms/clinical words ending in a qualifier
    # makes the hit clinical.
    if profile is not None:
        stop = profile.clause_end(hit.end, len(text))
    else:
        stop = _clause_end_local(text, hit.end)
    tail = text[hit.end:min(stop, hit.end + 60)]
    tail = re.sub(r"^['’]s\b", "", tail)          # "Wilson's disease"
    following = [t.lower() for t in
                 re.findall(r"[A-Za-z][A-Za-z']*", tail.replace("-", " "))][:4]
    following = [t[:-2] if t.endswith("'s") else t for t in following]
    return eponym_phrase_continues(hit_tokens[-1], following)


def _subtract_clinical(text: str, hits: list[PHIHit], profile=None) -> list[PHIHit]:
    """Remove hits that are actually medical concepts, not PHI.

    This step removes hits that are medical concepts, not identifiers.
    A general-purpose name model often labels eponyms such as
    "Wernicke", "Crohn", "Graves", "Osler" and "Cushing" as PERSON,
    because in its training text they are surnames. In clinical text
    they are diagnoses.

    Removing them would not protect anyone — no patient is named by
    "Guillain-Barre" — and would erase clinical meaning from the note,
    so clinical terms are subtracted here BEFORE any redaction decision
    is made.

    Also protects elements Safe Harbor allows to stay: ages under 90
    and relative time expressions ("3 days ago"), both of which carry
    clinical meaning.
    """
    protected_spans = hipaa.protected_time_spans(text)

    # CLINICAL TRANSLATOR (gated on complexity).
    # Resolves shorthand that collides with PHI categories. This pushes
    # in BOTH directions, which is why it cannot live in the lexicon:
    #   CLINICAL   -> protect ("LA enlargement" is the left atrium)
    #   IDENTIFIER -> force redaction ("resides in CT" is Connecticut)
    # The same token gets opposite treatment depending on context, and
    # only this layer can see that context.
    forced_identifier_spans: list[tuple[int, int]] = []
    if translator.needs_translation(text):
        for res in translator.resolve(text):
            if res.sense is translator.Sense.CLINICAL and res.confidence >= 0.6:
                protected_spans.append((res.start, res.end))
            elif res.sense in (translator.Sense.IDENTIFIER, translator.Sense.UNRESOLVED):
                # UNRESOLVED lands here on purpose: an abbreviation we
                # cannot place is redacted rather than assumed safe.
                forced_identifier_spans.append((res.start, res.end))
    for age in hipaa.find_ages(text):
        # Ages < 90: allowed to stay under Safe Harbor, and clinically important.
        # Ages >= 90: still protected from blind NER redaction here,
        # because policy._apply_safe_harbor_rules must be the thing
        # that transforms them into "90 or older" — if NER masks the
        # span first as <DATE_TIME>, the Safe Harbor generalization
        # never runs and we lose the age band entirely.
        protected_spans.append((age.start, age.end))

    # Vitals and lab values must never be redacted. NER reads "HR 110"
    # and "BP 180/120" as dates/numbers and will happily mask them.
    # Masking a vital sign removes clinical meaning a reader needs: a
    # hypertensive emergency cannot be seen once its numbers are masked.
    # These are clinical measurements, not identifiers.
    for pattern in (clinical_context.VITALS_PATTERN,
                    clinical_context.LAB_PATTERN,
                    clinical_context.VALUE_PATTERN,
                    clinical_context.SIZING_PATTERN,
                    clinical_context.TNM_PATTERN,
                    clinical_context.CLOCK_PATTERN,
                    clinical_context.GRADE_PATTERN,
                    clinical_context.EPONYM_GRADE_PATTERN,
                    clinical_context.SCORE_COMPONENT_PATTERN):
        for m in pattern.finditer(text):
            protected_spans.append((m.start(), m.end()))
    # Named neurosurgical-care phrases written out in full ("circle of
    # Willis", "Hunt and Hess grade", "Mayfield head holder"): the whole
    # phrase is clinical, in any case, wherever it stands.
    for m in clinical_context.neuro_phrase_pattern().finditer(text):
        protected_spans.append((m.start(), m.end()))

    # ZIP codes must reach the Safe Harbor generalizer intact. If NER
    # masks "90210" as <DATE_TIME> first, the 3-digit truncation rule
    # never runs and we lose the geographic band that Safe Harbor
    # explicitly permits retaining.
    # Not when the five digits are part of a longer labelled identifier:
    # "MRN 00588-112" is a record number, and protecting "00588" as a ZIP
    # dropped the whole MRN and then printed "MRN 005**-112".
    _id_kinds = ("MRN", "HEALTH_PLAN_ID", "UNIQUE_ID", "UNIQUE_ID_PREFIXED", "US_BANK_NUMBER",
                 "MEDICAL_LICENSE", "NPI", "DEVICE_ID", "DEVICE_SERIAL", "VEHICLE_PLATE")
    id_spans = [(h.start, h.end) for h in hits
                if h.entity_type in _id_kinds and h.layer in ("regex", "anchor")]
    for m in hipaa.zip_matches(text):
        if any(a <= m.start() and m.end() <= b and (b - a) > (m.end() - m.start())
               for a, b in id_spans):
            continue
        protected_spans.append((m.start(), m.end()))

    # Field LABELS are structure, never PHI. Protecting them keeps any
    # pattern from removing a label and leaving the form unreadable —
    # labels redacted, values redacted, nothing left to interpret.
    for fieldv in classifier.extract_anchored_fields(text):
        protected_spans.append((fieldv.label_start, fieldv.label_end))

    kept: list[PHIHit] = []
    for h in hits:
        # A span the translator resolved as a geographic identifier is
        # PHI regardless of what the lexicon thinks of the token.
        if any(h.start < e and h.end > s for s, e in forced_identifier_spans):
            kept.append(h)
            continue
        if _is_clinical_in_context(text, h, profile):
            continue
        # DATE ELEMENT VALIDATION. Safe Harbor removes the elements of a
        # date SMALLER THAN A YEAR — month, day — and explicitly permits
        # the year. A model-proposed DATE_TIME that names no month, no
        # weekday, no holiday and no numeric month/day is therefore not
        # a Safe Harbor date element: "the 1980s", "his 20s", "39 plus
        # weeks", "L4", "O2", "DIAGNOSES,1", "160/88". Structural
        # layers (regex, anchors) are not second-guessed here.
        if (h.entity_type == "DATE_TIME" and h.layer in ("ner", "ner_xx")
                and not _is_date_element(h.text)):
            continue
        # A model date that starts with ordinary words ("LAST FILLING JAN
        # 2088") starts at its first date word instead: the words before it
        # are clinical text, and they were removed with the date.
        if h.entity_type == "DATE_TIME" and h.layer in ("ner", "ner_xx"):
            h = _trim_date_lead(h)
        # A licence/ID number the model proposes must be ID-shaped:
        # "x1", "x2" (counts: "Hemovac x1") are not driver licences.
        if (h.entity_type in ("US_DRIVER_LICENSE", "US_PASSPORT", "US_BANK_NUMBER")
                and h.layer in ("ner", "ner_xx")
                and (len(re.sub(r"\W", "", h.text)) < 5
                     or re.fullmatch(r"[xX]\s*\d{1,3}", h.text.strip()))):
            continue
        # A LOCATION the model proposes must contain a proper noun:
        # "the inferotemporal quadrant" is anatomy.
        if (h.entity_type in ("LOCATION", "GPE") and h.layer in ("ner", "ner_xx")
                and not re.search(r"[A-Z]", h.text) and not non_latin_text(h.text)):
            continue
        # A bare one- or two-digit number is never a Safe Harbor date
        # element: a year needs four digits and a day-of-month identifies
        # nothing without its month. NER nonetheless labels "83" (a
        # pulse), "36" (a hematocrit) and "1+" (a prostate grade) as
        # DATE_TIME. Values of 90 and up are left alone — a bare "94"
        # could be an age, and ages >= 90 ARE identifiers.
        if (h.entity_type == "DATE_TIME" and h.layer in ("ner", "ner_xx")
                and _BARE_SMALL_NUMBER.fullmatch(h.text.strip())
                and int(h.text.strip().rstrip("+")) < 90):
            continue
        # Overlap (not just containment): NER spans can straddle the
        # edge of a protected vital, e.g. matching "110. COPD" — any
        # intersection with protected clinical data disqualifies the hit.
        if any(h.start < e and h.end > s for s, e in protected_spans):
            continue
        kept.append(h)
    return kept


_DATE_WORD = re.compile(
    r"(?i)^(?:\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|mon|tue|wed|thu|fri|sat|sun|"
    r"first|second|third|fourth|fifth|tenth|twentieth|thirtieth|christmas|thanksgiving|easter|"
    r"new\s+year|memorial|labor|independence|halloween|valentine|mid|early|late|the|on|of)")


def _trim_date_lead(h: PHIHit) -> PHIHit:
    """Drop leading words before the first date-like word of a model date."""
    words = list(re.finditer(r"\S+", h.text))
    for i, w in enumerate(words):
        if _DATE_WORD.match(w.group()):
            if i == 0:
                return h
            off = w.start()
            return dataclasses.replace(h, start=h.start + off, text=h.text[off:])
    return h


_ID_LABEL_PREFIX = re.compile(
    r"^(?:MRN|Medical Record (?:Number|No\.?)|Member ID|Health Plan ID|Policy (?:Number|No\.?))"
    r"\s*[:#]?\s*", re.IGNORECASE)


def _trim_id_labels(hits: list[PHIHit]) -> list[PHIHit]:
    """Presidio's pattern recognizers return "MRN 1122334455" whole. The
    label is structure; only the value is the identifier."""
    out = []
    for h in hits:
        m = _ID_LABEL_PREFIX.match(h.text)
        if m and m.end() < len(h.text):
            h = dataclasses.replace(h, start=h.start + m.end(), text=h.text[m.end():])
        out.append(h)
    return out


# Field labels a model reads as part of a name: "Attg: Ashcombe" and
# "Teodor Acct/MRN" came back whole, so the label was removed with the name.
# A name never ends in a colon, and never holds an ID label.
_NAME_LEAD_LABEL = re.compile(r"^\S+:\s*")
_NAME_TAIL_LABEL = re.compile(
    r"\s+(?:(?:acct|mrn|dob|fin|csn|ssn|rm|room|bed|pt|id|ph|tel|ext)\b[/:#.\w]*)+$",
    re.IGNORECASE)


def _trim_name_labels(hits: list[PHIHit]) -> list[PHIHit]:
    out = []
    for h in hits:
        if h.entity_type in ("PERSON", "ORGANIZATION", "NRP", "LOCATION"):
            m = _NAME_LEAD_LABEL.match(h.text)
            if m and m.end() < len(h.text):
                h = dataclasses.replace(h, start=h.start + m.end(), text=h.text[m.end():])
            m = _NAME_TAIL_LABEL.search(h.text)
            if m and m.start() > 0:
                h = dataclasses.replace(h, end=h.start + m.start(), text=h.text[:m.start()])
        out.append(h)
    return out


def _split_at_newlines(hits: list[PHIHit]) -> list[PHIHit]:
    out: list[PHIHit] = []
    for h in hits:
        if "\n" not in h.text:
            out.append(h)
            continue
        off = 0
        for part in h.text.split("\n"):
            stripped = part.strip()
            if stripped:
                lead = len(part) - len(part.lstrip())
                s0 = h.start + off + lead
                out.append(dataclasses.replace(
                    h, start=s0, end=s0 + len(stripped), text=stripped))
            off += len(part) + 1
    return out


_MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
           r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
_DATE_ELEMENT = re.compile(
    rf"\b(?:{_MONTHS})\b\.?"
    r"|\b(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?\b"
    r"|\b(?:christmas|thanksgiving|easter|halloween|new\s+year'?s?|"
    r"independence\s+day|labou?r\s+day|memorial\s+day|valentine'?s?|"
    r"hanukkah|ramadan|eid|diwali|passover|yom\s+kippur|rosh\s+hashanah|"
    r"lunar\s+new\s+year|juneteenth|veterans\s+day|mother'?s\s+day|"
    r"father'?s\s+day)\b"
    r"|\b(?:0?[1-9]|1[0-2])\s*[/\-.]\s*(?:0?[1-9]|[12]\d|3[01])\b"
    r"|\b(?:0?[1-9]|[12]\d|3[01])\s*[/\-.]\s*(?:0?[1-9]|1[0-2])\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\s+of\b",
    re.IGNORECASE,
)


def _is_date_element(s: str) -> bool:
    return bool(_DATE_ELEMENT.search(s))


def non_latin_text(s: str) -> bool:
    from inkguard.talc import NON_LATIN_RE
    return bool(NON_LATIN_RE.search(s))


def _canonical_type(entity: str) -> str:
    from inkguard.talc import canonical
    return canonical(entity)


def _tight_person_context_at(text: str, start: int, end: int) -> bool:
    from inkguard.garnet import _tight_person_context
    return _tight_person_context(text, start, end)


_BARE_SMALL_NUMBER = re.compile(r"\d{1,2}\+?")


def _translator_hits(text: str) -> list[PHIHit]:
    """Identifiers the translator found that NER missed entirely.

    NER will not flag "CT" in "resides in CT" as a LOCATION — it reads
    a scan. So subtracting hits is not enough; the translator must also
    ADD them, or the geographic identifier passes through untouched.
    This is the leak half of the two-way problem.
    """
    if not translator.needs_translation(text):
        return []

    hits: list[PHIHit] = []
    for res in translator.resolve(text):
        if res.sense is translator.Sense.IDENTIFIER:
            # A state name is a Safe Harbor identifier only by policy —
            # the rule removes subdivisions SMALLER than a state, so the
            # state itself may stay under Safe Harbor. Configurable rather
            # than hardcoded, because it is a user's risk call.
            if not settings.redact_state_names:
                continue
            hits.append(PHIHit(
                entity_type="LOCATION",
                start=res.start, end=res.end,
                text=res.token, score=res.confidence,
                layer="translator",
            ))
    return hits


# ---------------------------------------------------------------------
# Layer 5: CONTEXTUAL NAME INFERENCE.
#
# Every other name layer asks "do I recognize this token?" -- NER from
# training data, the registry pass from name lists. Both fail the same
# way, on names they have never seen, and that failure is not evenly
# distributed: it falls hardest on non-Western names. "Aisha Haddad" and
# "Tobias Mbeki" were missed on the benchmark while "Sarah Wilson" was
# caught.
#
# This layer asks a different question: does the SENTENCE STRUCTURE say
# a name belongs here? "Mrs. X", "X is a 45-year-old", "Dictated by: X"
# are name-bearing frames regardless of what X is. A frame match needs
# no recognition of the name itself, so it works on names no list will
# ever contain.
#
# Precision matters here: a loose rule would remove every unfamiliar
# capitalised word. So each frame requires explicit name-bearing
# structure, and any span that is known clinical vocabulary is rejected
# before it is claimed -- "Patient Controlled Analgesia" must not become
# a person.
# ---------------------------------------------------------------------
# SCRIPT-AGNOSTIC NAME TOKEN.
#
# Every other name layer asks "do I recognize this?" — NER from its
# training distribution, the registry pass from name lists, and the
# earlier version of this pattern from Latin letter case. All three depend
# on the token's form: they fail on the same input, which is precisely
# what defence in depth is supposed to prevent.
#
# Being a name is not a property of the alphabet. It is a property of the
# POSITION. In "X is a patient of record", X is a name whether it is
# written Haddad, Хаддад, هدّاد or 長谷川. Encoding the structure as an
# A-Z rule threw that away and cost 60.8% person recall on an
# independent corpus — 13% on Japanese, 17% on Korean.
#
# So a name-shaped token is EITHER:
#   - a title-case Latin word (case is real evidence where it exists), OR
#   - a run of letters in a script that HAS NO CASE, where the absence of
#     a case signal must not be read as absence of a name.
_UC = r"A-Z\xc0-\xd6\xd8-\xdeĀ-ɎḀ-Ỿ"
_LC = r"a-z\xdf-\xf6\xf8-\xffā-ɏḁ-ỿ"

# Caseless scripts: Arabic, Devanagari, CJK ideographs, Hiragana,
# Katakana, Hangul, Hebrew, Thai. Cyrillic and Greek are deliberately
# EXCLUDED here — they do have case, so they belong in the Latin-style
# branch below rather than matching as an undifferentiated run.
from inkguard.talc import CASELESS as _CASELESS  # shared vocabulary (talc)
# Cased non-Latin (Cyrillic, Greek): treated like Latin — capital then body.
_UC_OTHER = r"Ѐ-ЯΑ-Ω"
_LC_OTHER = r"а-яα-ω"

_LATIN_NAME = rf"[{_UC}{_UC_OTHER}][{_LC}{_LC_OTHER}]{{1,}}(?:['\-][{_UC}{_UC_OTHER}]?[{_LC}{_LC_OTHER}]+)*"
# Combining marks (Unicode category Mn/Mc) that form part of a name in
# Arabic and Indic scripts. Excluded from the ranges above, they were
# being clipped off the end of a captured name.
_MARKS = r"ً-ٰٟऀ-ःऺ-ॏ॑-ॗॢ-ॣ"

# Two or more caseless letters. A lone ideograph carries too little
# evidence to claim on its own in a weak frame.
_CASELESS_NAME = rf"[{_CASELESS}][{_CASELESS}{_MARKS}]+"
_NAME_TOKEN = rf"(?:{_LATIN_NAME}|{_CASELESS_NAME})"

# LOOSE variant: permits a SINGLE caseless character.
#
# Used only in frames with a strong left anchor — an explicit title or
# label ("Patient:", "Dr.", "Attending:"). Chinese and Japanese given
# names are often one character (王 伟; 長谷川 稔), so the strict form silently
# dropped the given name while capturing the surname. Where a label has
# already established that a name follows, one character is enough; in
# the unanchored frames it is not, which is why there are two variants
# rather than one relaxed rule everywhere.
_CASELESS_NAME_LOOSE = rf"[{_CASELESS}][{_CASELESS}{_MARKS}]*"
_NAME_TOKEN_LOOSE = rf"(?:{_LATIN_NAME}|{_CASELESS_NAME_LOOSE})"

_NAME_FRAMES: tuple[re.Pattern, ...] = (
    # Title followed by a name: Mrs. Haddad, Dr. Mbeki, "PATIENT: Okafor".
    # The separator must allow a colon and the title must match in any
    # case: real headers are written "PATIENT:" in caps, and without both
    # of those this frame silently missed every labelled name in a
    # document that had too few other labels to classify as a form.
    # The captured span is still required to be title-case, so relaxing
    # the title does not loosen what counts as a name.
    #
    # The case-insensitivity is SCOPED to the title with (?i:...). A
    # whole-pattern re.IGNORECASE flag also un-anchors the name token --
    # [A-Z][a-z]+ starts matching lowercase words -- and the frame then
    # claims "denies chest pain" out of "Patient denies chest pain".
    # The title may be any case; the name must still be title-case.
    # The trailing \b is gone on purpose. Devanagari and Arabic names end
    # in combining vowel marks (शर्मा ends U+093E), and a word boundary
    # after a Mark truncated the captured span — the name was claimed but
    # its last character was left in the output.
    #
    # The PERIOD is allowed only after abbreviations. "Mr." is a title;
    # "...explained to the patient.  Consent was obtained" is a sentence
    # end, and allowing "." after the full words made "Consent" and "At"
    # (from "the patient.  At this point") into names on test notes.
    re.compile(rf"\b(?i:Mr|Mrs|Ms|Dr|Pt)\s*[.,:]?\s+"
               rf"({_NAME_TOKEN_LOOSE}(?:[ \t]+{_NAME_TOKEN_LOOSE}){{0,2}})"),
    # The same with an initial first: "Dr. E. Narváez", "Mr. J. Okafor".
    re.compile(rf"\b(?i:Mr|Mrs|Ms|Dr)\.?[ \t]+([A-Z]\.[ \t]*{_NAME_TOKEN}(?:[ \t]+{_NAME_TOKEN_LOOSE}){{0,1}})"),
    re.compile(rf"\b(?i:Miss|Doctor|Patient|Name|Proband|Consultand)\s*[,:]?[ \t]+"
               rf"({_NAME_TOKEN_LOOSE}(?:[ \t]+{_NAME_TOKEN_LOOSE}){{0,2}})"),
    # Name in apposition with an age: "Aisha Haddad is a 45-year-old"
    #
    # ONE-OR-TWO tokens, not exactly two. Korean names are routinely
    # written as a single token (김민준) and Chinese given names as one
    # character, so demanding two tokens made this frame Western-only —
    # it scored 13% on Japanese and 17% on Korean. The frame's evidence
    # is the apposition with an age, which is just as strong for one
    # token as for two.
    # FIRST token strict, SUBSEQUENT tokens loose. "長谷川 稔" has a
    # one-character given name, so a strict second token rejected the
    # whole match — and the optional-token form did not rescue it,
    # because the token was present and merely failed the test. The
    # leading token carries the evidential weight; once it plus the
    # apposition have established a name, a one-character continuation
    # is part of that name.
    # Up to two describing words may sit before the age: "Mason Ward is a
    # pleasant 45-year-old" (dictated-note style) left both names in when
    # the frame demanded the age straight after "a".
    re.compile(rf"\b({_NAME_TOKEN}(?:[ \t]+{_NAME_TOKEN_LOOSE})?)\s+(?:is|was)\s+an?\s+"
               rf"(?:[a-z]+(?:-[a-z]+)?\s+){{0,2}}\d{{1,3}}[-\s]?(?:year|yo\b|y/o)"),
    # Name in apposition with a bare age: "Tilma, 67, presents with edema".
    # The dictated form of the age frame; the age between commas plus a
    # clinical predicate is the evidence, as with "is a 67-year-old".
    re.compile(rf"(?:^|(?<=[\n.:;]\s)|(?<=[\n.:;]))[ \t]*({_NAME_TOKEN}(?:[ \t]+{_NAME_TOKEN_LOOSE})?),[ \t]*\d{{1,3}}"
               rf"(?:[ \t]*(?:y\.?\s?o\.?|y/o|yo|years?[ -]old|mo|m\.?o\.?))?,[ \t]*"
               rf"(?:an?\s+)?(?:presents|presented|is|was|has|had|with|who|reports|"
               rf"returns|comes|came|male|female|man|woman|here|admitted|seen|evaluated|referred|"
               rf"brought|transferred|s/p|status|boy|girl|infant|child|toddler|who)\b", re.MULTILINE),
    # SIGNATURES. "-Marble" at the end of a note, "— Ares Scruggs, DO",
    # "Brad Wilkie" alone on the last line, "Judith Guyer, MD, Internal
    # Medicine". A dash-led name ending a line, or any name carrying a
    # clinical credential, is a signature.
    re.compile(rf"(?:^|(?<=[\s.]))[-—–]{{1,2}}[ \t]?({_NAME_TOKEN}(?:[ \t]+{_NAME_TOKEN_LOOSE}){{0,2}})[ \t]*"
               rf"(?=,|[ \t]*$|[ \t]+(?:MD|DO|PA-C|NP|RN)\b|[ \t]+(?i:md|do|np|rn|pa|pa-c|lpn|crna)\b"
               rf"|[ \t]+\d{{1,2}}/\d{{1,2}}\b|[ \t]+(?:[01]\d|2[0-3])[0-5]\d\b|[ \t]+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?[ \t]+\d)",
               re.MULTILINE),
    re.compile(rf"\b({_NAME_TOKEN}(?:[ \t]+{_NAME_TOKEN_LOOSE}){{0,2}}),?[ \t]+"
               rf"(?:MD|M\.D\.|DO|D\.O\.|PA-C|NP|FNP|DNP|APRN|RN|CNM|CRNA|MBBS|PhD|LCSW|PharmD)\b"),
    # "Last, First" alone on one of a document's first lines (chart headers).
    re.compile(rf"\A(?:[^\n]*\n){{0,2}}[ \t]*({_NAME_TOKEN},[ \t]*{_NAME_TOKEN})[ \t]*$", re.MULTILINE),
    # Name declared as the patient: "Tobias Mbeki is a patient of record"
    re.compile(rf"\b({_NAME_TOKEN}(?:[ \t]+{_NAME_TOKEN_LOOSE})?)\s+(?:is|was)\s+"
               rf"(?:a|the|our)\s+patient\b"),
    # Attribution and signature blocks
    re.compile(rf"\b(?:Signed|Dictated\s+by|Electronically\s+signed\s+by|Transcribed\s+by|"
               rf"Attending|Attg|Attd|Atdg|Provider|Physician|Surgeon|Referring)\s*:?\s*(?:Dr\.?\s*)?"
               rf"({_NAME_TOKEN_LOOSE}(?:[ \t]+{_NAME_TOKEN_LOOSE}){{0,2}})"),
)


# SIGN-OUT LISTS. A night or cross-cover list is
# a checklist of patients, each with a room and a to-do:
#   "-Lanning-rm 712-CHF exac, recheck BMP"   "-Deann L.-rm 714-DKA"
#   "called re Golmar bed 14"                "(the Lanning in 716, NOT ...)"
# The word standing against a room or bed number is the patient.
_SIGNOUT_FRAMES: tuple[re.Pattern, ...] = (
    re.compile(r"(?m)^[ \t]*[-*•][ \t]*([A-Z][A-Za-z'’]+(?:[ \t]+(?:[A-Z]\.?|[A-Z][A-Za-z'’]+)){0,2})"
               r"\.?[ \t]*[-–,:/][ \t]*(?i:rm|room|bed|bd|rm#|room#)\s*#?\s*\d{1,4}"),
    re.compile(rf"(?i:\b(?:re|regarding|about|for|pt|patient))[ \t]+({_NAME_TOKEN})[ \t,]+"
               rf"(?i:rm|room|bed|bd)\s*#?\s*\d{{1,4}}\b"),
    re.compile(rf"\(\s*(?i:the)[ \t]+({_NAME_TOKEN})[ \t]+(?i:in|on)[ \t]+(?:(?i:rm|room|bed)\s*)?\d{{2,4}}\b"),
    # "Lucky S. s/p lap chole": a first name and an initial opening a
    # sign-out line, then what happened to that patient.
    re.compile(r"(?m)^[ \t]*(?:[-*•][ \t]*)?([A-Z][a-z'’]{2,}[ \t]+[A-Z]\.)[ \t]+"
               r"(?i:s/p|status\s+post|is\s+an?\b|POD\s*#?\d|w/|with\b|here\s+for|admitted|presents)"),
    # "endocrine (Boatswain) following", "cards (Ilunga) aware": a service
    # and, in brackets, the clinician on it — then what that person does.
    re.compile(rf"(?i:\b(?:endo(?:crine)?|cards|cardiology|renal|nephro(?:logy)?|GI|ID|pulm(?:onary)?|"
               rf"onc(?:ology)?|heme(?:/onc)?|neuro(?:logy|surg(?:ery)?)?|surgery|surg|ortho(?:pedics)?|"
               rf"psych(?:iatry)?|palliative(?:\s+care)?|urology|uro|ENT|derm(?:atology)?|"
               rf"rheum(?:atology)?|gyn|OB|peds|nutrition|PT|OT|SW|social\s+work|case\s+management|"
               rf"CM|wound\s+care|WOCN|IR|anesthesia|team|service|consult(?:ant)?|PCP))[ \t]*"
               rf"\([ \t]*({_NAME_TOKEN})[ \t]*\)[ \t]*"
               rf"(?i:following|f/u|aware|consulted|recs?\b|recommend\w*|to\s+see|saw|seeing|"
               rf"signed\s+off|on\s+board|involved|at\s+bedside|called|paged|notified|agrees?)"),
)
_NAME_FRAMES = _NAME_FRAMES + _SIGNOUT_FRAMES


# Words a frame must never accept as a name. The age-apposition frame was
# relaxed from two tokens to one so single-token names (김민준) match
# "X is a 45-year-old" — and that also made it match "This is a
# 30-year-old female", the single most common opening sentence in a
# clinical note. Frames are structural and bypass garnet, so the
# exclusion has to live here, at the source.
_FRAME_NON_NAMES = frozenset({
    "this", "that", "these", "those", "the", "there", "then", "here", "it",
    "he", "she", "they", "we", "who", "which", "one", "each", "someone",
    "anyone", "everyone", "nobody", "patient", "pt", "infant", "child",
    "baby", "boy", "girl", "man", "woman", "gentleman", "lady", "male",
    "female", "resident", "veteran", "client", "subject", "today",
    "currently", "now", "also", "previously", "again", "mother", "father",
    "husband", "wife", "son", "daughter", "sister", "brother",
})


_MONTH_DAY_WORDS = frozenset(
    "jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july "
    "august september october november december mon tue tues wed thu thur thurs fri sat sun "
    "monday tuesday wednesday thursday friday saturday sunday".split())


def _zipf_safe(w: str) -> float:
    try:
        from wordfreq import zipf_frequency
        return zipf_frequency(w, "en")
    except Exception:       # without wordfreq, trim nothing extra
        return 0.0


def _trim_frame_span(span: str, start: int) -> tuple[str, int] | None:
    """Strip non-name words from both ends of a frame capture."""
    parts = list(re.finditer(r"\S+", span))

    def non_name(tok: str) -> bool:
        low = tok.lower().strip(".,:;")
        if low in _FRAME_NON_NAMES:
            return True
        # "Attending Physician", "Resident Surgeon": a job title in the
        # name slot. A Latin word common in English (Zipf >= 3.5) that is
        # nobody's name in the registry is not part of a name.
        if low in _MONTH_DAY_WORDS:
            return True
        from inkguard.name_registry import NAMES
        return (low.isalpha() and low.isascii() and low not in NAMES
                and _zipf_safe(low) >= 3.5)

    while parts and non_name(parts[0].group()):
        parts.pop(0)
    while parts and non_name(parts[-1].group()):
        parts.pop()
    if not parts:
        return None
    s, e = parts[0].start(), parts[-1].end()
    return span[s:e], start + s


def _contextual_name_hits(text: str) -> list[PHIHit]:
    hits: list[PHIHit] = []
    for pattern in _NAME_FRAMES:
        for m in pattern.finditer(text):
            trimmed = _trim_frame_span(m.group(1), m.start(1))
            if trimmed is None:
                continue
            span, span_start = trimmed
            # A frame is evidence of a name, never a licence to redact
            # medical vocabulary that happens to sit in the slot.
            if is_clinical_term(span):
                continue
            tokens = [t for t in re.split(r"[\s\-']+", span) if t]
            if all(is_clinical_term(t) for t in tokens):
                continue
            # All-caps stretches are section headers, not names.
            if span.isupper():
                continue
            hits.append(PHIHit(
                entity_type="PERSON",
                start=span_start, end=span_start + len(span),
                text=span, score=0.9,
                layer="context",
            ))
    return hits


# ---------------------------------------------------------------------
# Layer 6: MULTILINGUAL NER, run alongside the English model.
#
# Not a replacement and not a fallback — a second model with different
# training data and therefore different blind spots. It runs when the note
# contains non-Latin script.
#
# Optional by construction. If the model is absent this layer reports
# nothing and every other layer is unaffected.
# ---------------------------------------------------------------------
_MULTILINGUAL_LABELS = {"PER": "PERSON", "PERSON": "PERSON",
                        "LOC": "LOCATION", "GPE": "LOCATION",
                        "ORG": "ORGANIZATION"}
_xx_nlp = None
_xx_tried = False


def _get_multilingual():
    global _xx_nlp, _xx_tried
    if not _xx_tried:
        _xx_tried = True
        try:
            import spacy
            _xx_nlp = spacy.load("xx_ent_wiki_sm")
        except Exception:
            _xx_nlp = None
    return _xx_nlp


def _multilingual_ner_hits(text: str) -> list[PHIHit]:
    nlp = _get_multilingual()
    if nlp is None:
        return []
    hits: list[PHIHit] = []
    try:
        doc = nlp(text)
    except Exception:
        return []
    for ent in doc.ents:
        mapped = _MULTILINGUAL_LABELS.get(ent.label_)
        if mapped is None:
            continue
        hits.append(PHIHit(
            entity_type=mapped,
            start=ent.start_char, end=ent.end_char,
            text=ent.text,
            # Scored below the primary model: this is corroboration, and a
            # weaker model should not outvote a stronger one during dedupe.
            score=0.55,
            layer="ner_xx",
        ))
    return hits


# ---------------------------------------------------------------------
# Layer 7: UNRECOGNIZED-TOKEN NAME CANDIDATES.
#
# Why this layer can afford to be broad: a capitalised word it does not
# recognise is proposed as a name, and the clinical-vocabulary check
# removes medical words such as "Wilson", "Crohn", "Foley" and
# "Guillain-Barre" afterwards (see _subtract_clinical). Broad proposal is
# only reasonable because that later step exists.
#
# The language model alone found 67% of English names in plain prose in
# one test, with nothing to back it up. This layer does not
# ask "do I recognize this name?" — it asks "is there any reason to
# believe this token is NOT a name?", which is the opposite question and
# therefore fails differently.
# ---------------------------------------------------------------------
_CANDIDATE_TOKEN = re.compile(
    rf"(?<![\w'\-])({_LATIN_NAME}|[{_CASELESS}][{_CASELESS}{_MARKS}]+)(?![\w'\-])"
)

# A token this common in general English is a word, not a name. Tuned
# against the corpus: "Wilson" sits at 4.53 and is caught anyway by the
# clinical layer; "Mcintyre" at 3.10 and "Haddad" at 2.63 must survive
# this filter to be detected at all.
_COMMON_ENGLISH_ZIPF = 3.6

# Positions where a capitalized token is sentence mechanics rather than a
# name. Not currently used: sentence position is no longer a veto (see
# _candidate_name_hits).
_SENTENCE_START = re.compile(r"(?:^|[.!?:;\n]\s*|\n)$")

_UNIT_LIKE = re.compile(
    r"^(?:mg|mcg|kg|mL|dL|cm|mm|IU|BID|TID|QID|QHS|PRN|PO|IV|IM|SQ|"
    r"CT|MRI|ECG|EKG|CBC|CMP|BUN|ICU|ER|OR|ED|HPI|ROS|PMH|PSH|FH|SH|"
    r"BP|HR|RR|O2|SpO2|Wt|Ht|Hgb|Hct|WBC|RBC|Na|K|Cl|Ca|Mg|Cr|Gluc)$",
    re.IGNORECASE,
)


@functools.lru_cache(maxsize=100_000)
def _is_common_english(token: str) -> bool:
    try:
        from wordfreq import zipf_frequency
    except Exception:
        return False
    return zipf_frequency(token.lower(), "en") >= _COMMON_ENGLISH_ZIPF


_HYPHEN_PREFIXES = frozenset(
    "pre post non anti co sub peri intra inter extra para supra infra trans re de semi hemi "
    "sacro ilio lumbo thoraco cervico retro endo epi hyper hypo micro macro multi poly mid "
    "self well ex neo pseudo".split())


@functools.lru_cache(maxsize=50_000)
def _hyphenated_clinical(token: str) -> bool:
    """"Pre-albumin", "Non-reassuring", "Sacro-iliitis": a hyphenated word
    whose joined form is clinical vocabulary, or whose parts are a prefix
    and clinical or common words. A double-barrelled name ("Mary-Deann",
    "Okafor-Haddad") has no such part."""
    if "-" not in token:
        return False
    parts = [p for p in token.lower().split("-") if p]
    if len(parts) < 2 or not all(p.isalpha() for p in parts):
        return False
    joined = "".join(parts)
    if is_clinical_term(joined) or _clinical_lexicon.is_vocabulary_word(joined):
        return True
    def ok(p: str) -> bool:
        return (p in _HYPHEN_PREFIXES or is_clinical_term(p)
                or _clinical_lexicon.is_vocabulary_word(p) or _is_common_english(p))
    return all(ok(p) for p in parts) and (parts[0] in _HYPHEN_PREFIXES or any(
        is_clinical_term(p) or _clinical_lexicon.is_vocabulary_word(p) for p in parts))


# Verb and plural endings only. "-ly" made "Havely" an adverb of "have".
_INFLECTIONS = (("ing", ""), ("ing", "e"), ("ed", ""), ("ed", "e"), ("ied", "y"),
                ("ies", "y"), ("es", ""), ("s", ""))


@functools.lru_cache(maxsize=100_000)
def _is_clinical_stem(stem: str) -> bool:
    try:
        from inkguard.clinical_lexicon import is_clinical_term
        return bool(is_clinical_term(stem))
    except Exception:  # noqa: BLE001
        return False


def _inflection_of_common_english(token: str) -> bool:
    """"Tolerating", "Ambulated", "Improving": a form of a common English
    word that no name list holds ("tolerate"), and not itself in any name
    list. The word frequency list rates the inflected form lower than the
    base word ("tolerating" 2.75, "tolerate" 3.1), which made it look rare
    enough to be a surname at the start of a sentence."""
    low = token.lower()
    if not low.isalpha() or len(low) < 6:
        return False
    from inkguard.name_registry import NAMES
    if low in NAMES:
        return False
    from inkguard.garnet import _english_set
    english = _english_set()
    try:
        from wordfreq import zipf_frequency
    except Exception:
        return False
    for suf, rep in _INFLECTIONS:
        if low.endswith(suf) and len(low) - len(suf) >= 3:
            stem = low[:-len(suf)] + rep
            for st in (stem, stem[:-1] if len(stem) >= 4 and stem[-1] == stem[-2] else None):
                if (st and st in english and st not in NAMES
                        and zipf_frequency(st, "en") >= _COMMON_ENGLISH_ZIPF - 0.6):
                    return True
                # A verb form of a dictionary or clinical word, however rare
                # the word ("Ambulating" from ambulate, "Palpated"): not a
                # surname unless a name list holds it.
                if (st and suf in ("ing", "ed") and st not in NAMES
                        and (st in english or _is_clinical_stem(st))):
                    return True
    return False


_CANDIDATE_PREDICATE = re.compile(
    r"^\s+(?:reports|reported|states|stated|denies|denied|presents|presented|"
    r"complains|returns|returned|is\s+a\s+\d|was\s+seen|was\s+admitted|"
    r"feels|notes|agrees|declined|tolerated|underwent|arrived|lives|"
    r"doing\s+(?:well|better|worse|ok|okay|fine|poorly)|is\s+doing|was\s+doing|"
    r"feeling|is\s+here|here\s+for|comes\s+in|came\s+in)\b",
    re.IGNORECASE)


def _candidate_name_hits(text: str) -> list[PHIHit]:
    hits: list[PHIHit] = []
    for m in _CANDIDATE_TOKEN.finditer(text):
        tok = m.group(1)

        caseless = bool(re.match(rf"[{_CASELESS}]", tok))
        # Three letters is the floor for Latin (below it: units, initials,
        # abbreviations). It is NOT a floor for CJK: "渡辺", "王", "李娜"
        # are complete names in one or two characters, and an earlier
        # version silently discarded every two-character surname.
        if (len(tok) < 3 and not caseless) or tok.isupper():
            continue                      # headers and acronyms
        if _UNIT_LIKE.match(tok):
            continue
        # Where only a person can stand — after "discussed with", "per",
        # "wife" — even a dictionary or lexicon word is proposed ("Plan
        # discussed with Flesh", "Per Cide"). Garnet and the lexicon
        # veto still judge it; this only stops the word from being
        # silently skipped.
        person_slot = tok[0].isupper() and _tight_person_context_at(text, m.start(1), m.end(1))
        if not person_slot:
            if is_clinical_term(tok) or _clinical_lexicon.is_vocabulary_word(tok):
                continue                  # cheap pre-check; the real
                                          # subtraction still runs later
            if (_is_common_english(tok) or _inflection_of_common_english(tok)
                    or _hyphenated_clinical(tok)):
                continue
        # Sentence position is not a veto: a capitalised word that is not
        # common English, clinical vocabulary or an acronym (all excluded
        # above) is most often a surname even at a sentence start ("S:
        # Meskin reports less dyspnea", "Reason for visit: Nagasawa needs
        # refill"). A dictionary word in the same slot ("Nausea is
        # improving") is still stopped by the checks above and by garnet.
        # Garnet still weighs position (section_initial) and the
        # section; obsidian masks what it holds as [NAME].

        hits.append(PHIHit(
            entity_type="PERSON",
            start=m.start(1), end=m.end(1),
            text=tok,
            # Deliberately low. High enough to clear the threshold, low
            # enough that any stronger layer wins during dedupe, and the
            # clinical subtraction still has its say.
            score=0.45,
            layer="candidate",
        ))
    return hits


# ---------------------------------------------------------------------
# The first evidence scorer lived here. It used "patient" as evidence of
# a nearby name, which is present in nearly every sentence of a clinical
# note, so it confirmed almost every candidate. Replaced
# by inkguard/garnet.py (evidence scoring) and inkguard/shale.py.
# ---------------------------------------------------------------------


def _anchored_hits(text: str) -> list[PHIHit]:
    """Identifiers extracted from labelled form fields.

    A label is stronger evidence than any model inference. "MRN: 4471"
    is certain; NER sees four digits and shrugs. On structured input
    this catches identifiers the other layers miss entirely — short
    MRNs, unusual name formats, non-English names, initials — because
    it never has to recognize the VALUE at all, only the LABEL.

    Only the value span is claimed. Redacting the label too would
    destroy the form's structure while protecting nothing.
    """
    cls = classifier.classify(text)
    if not cls.profile.use_field_anchors:
        return []

    hits: list[PHIHit] = []
    for fieldv in classifier.extract_anchored_fields(text):
        hits.append(PHIHit(
            entity_type=fieldv.entity_type,
            start=fieldv.start, end=fieldv.end,
            text=fieldv.value, score=0.99,  # label-anchored: outranks any model
            layer="anchor",
        ))
    return hits


# =====================================================================
# PIPELINE ORCHESTRATION
# =====================================================================
#
# Order of operations, and the reason for each position:
#
#   1  quartz — sections      profile the note: sections + global signals.
#                             Cheap, so it runs on everything; its job is to
#                             decide what else is worth running.
#   2  always-on detectors    structural layers (labels, frames, sign-offs,
#                             regex IDs) and the primary NER. Always run,
#                             everywhere.
#   3  on-demand detectors    the multilingual model is RUN only when
#                             non-Latin script is present: expensive, and the
#                             largest single source of false positives on
#                             English notes. The candidate layer is cheap, so it
#                             always runs and is gated afterwards instead.
#   4  clinical guard         subtract known clinical vocabulary and
#                             protected measurements (_subtract_clinical).
#   5  garnet — evidence      weigh the evidence for every non-structural name
#                             and place candidate; release (redact) or hold
#                             (leave in, listed as doubtful).
#   6  shale — adjacent       extend confirmed names over adjacent name tokens.
#      tokens
#   7  shale — spread seed    propagate strongly confirmed names to every other
#      names                  occurrence, and apply names already confirmed in
#                             the patient's other notes.
#   8  span ownership         collapse overlaps (_dedupe).
#   9  jasper — roles         retype workplaces and facilities; drop
#                             credentials read as places and partial web
#                             addresses.
#
# Every stage always runs. There is no setting that weakens redaction: the
# stages below are fixed on, so what the audit trail records is what ran.

def _flag(name: str, default: bool = True) -> bool:
    """Stage switches are fixed; kept so the stage list reads as a checklist."""
    return default


@dataclass
class ScanTrace:
    """What the pipeline did, without any PHI.

    Offsets, counts, section kinds and signals only. Never token
    text, never section labels (a label can itself be a name).
    """
    profile: dict = field(default_factory=dict)
    expressed: dict = field(default_factory=dict)
    raw_counts: dict = field(default_factory=dict)
    held: list = field(default_factory=list)
    adjacent_tokens: int = 0
    propagated: int = 0
    from_memory: int = 0
    seed_count: int = 0
    # Kept in RAM so the caller can carry them to the patient's other
    # notes. NOT serialised.
    confirmed_names: set = field(default_factory=set, repr=False)
    # The detection ledger (talc.Ledger): every detection's fate. Settled
    # by the policy layer once the final redaction set is known.
    ledger: object = field(default=None, repr=False)
    # Who wrote the note and why (jasper.read_signoff): class and kind only.
    authorship: dict = field(default_factory=dict)


def _make_hit(entity, start, end, text, score, layer, evidence=()):
    return PHIHit(entity_type=entity, start=start, end=end, text=text,
                  score=score, layer=layer, evidence=tuple(evidence))


def structural_scan(text: str) -> list[PHIHit]:
    """Only the structural layers — labels and frames. Regular
    expressions, no model. Used for the cross-note seed-name pre-pass."""
    return _anchored_hits(text) + _contextual_name_hits(text)


def scan_with_trace(text: str, memory: set | None = None,
                    typed_memory: dict | None = None,
                    file_clinical: frozenset = frozenset()) -> tuple[list[PHIHit], ScanTrace]:
    """Run the pipeline. Returns (hits, trace).

    `memory` is a set of names already confirmed in OTHER notes for the
    same patient. It is used and never stored here.

    Raises on internal failure — callers MUST treat exceptions here as a
    signal to fail closed (see policy.py), never swallow them.
    """
    from inkguard import quartz, garnet, shale, talc

    trace = ScanTrace()
    ledger = talc.Ledger()
    trace.ledger = ledger

    try:
        # 1 quartz — sections -------------------------------------------
        prof = quartz.profile(text)
        if not _flag("PHI_SECTIONS"):
            prof = quartz.Profile(
                sections=[quartz.Section(quartz.UNKNOWN, 0, 0, len(text))],
                non_latin=prof.non_latin, sectioned=False,
                # The clause map is sentence-level structure, not section
                # structure: switching sections off must not remove it.
                breaks=prof.breaks)
        trace.profile = prof.summary()

        # 2 always-on detectors -----------------------------------------
        ner_hits = _ner_scan(text)

        context_hits = _contextual_name_hits(text)

        from inkguard import jasper
        # READ THE BOTTOM FIRST: the sign-off names who wrote the note, and
        # their credential says what kind of writer they are. Sign-off
        # names become seed names, so shale spreads them into the body.
        authorship = jasper.read_signoff(text)
        trace.authorship = authorship.summary()
        role_hits = [PHIHit("PERSON", s_, e_, text[s_:e_], 0.90, "context",
                            evidence=("signoff",)) for s_, e_ in authorship.names]
        role_hits += [PHIHit("PERSON", r.start, r.end, text[r.start:r.end], 0.90, "context",
                             evidence=(f"role:{r.role}",))
                      for r in jasper.provider_role_names(text)]
        # Family members by their relation word, and "SURNAME, GIVEN"
        # headers: both read without trusting capital letters.
        role_hits += [PHIHit("PERSON", r.start, r.end, text[r.start:r.end], 0.90, "context",
                             evidence=(f"role:{r.role}",))
                      for r in jasper.kin_names(text) + jasper.header_names(text)]
        role_hits += [PHIHit("GPE", r.start, r.end, text[r.start:r.end], 0.90, "context",
                             evidence=(r.role,))
                      for r in jasper.place_names(text)]
        context_hits = context_hits + role_hits

        layers: dict[str, list[PHIHit]] = {
            "regex": _regex_scan(text),
            "ner": ner_hits,
            "translator": _translator_hits(text),
            "anchor": _anchored_hits(text),
            "context": context_hits,
            "promiscuous": _promiscuous_ssn(text) + _promiscuous_mrn(text),
        }

        # 3 on-demand detectors -----------------------------------------
        # The multilingual model runs when non-Latin script is present.
        run_xx = _flag("PHI_MULTILINGUAL_NER") and (
            prof.non_latin or not _flag("PHI_MULTILINGUAL_ON_DEMAND"))
        trace.expressed = {"multilingual_ner": run_xx,
                           "candidate_names": _flag("PHI_AGGRESSIVE_NAMES")}
        layers["ner_xx"] = _multilingual_ner_hits(text) if run_xx else []
        layers["candidate"] = _candidate_name_hits(text)

        # Statistical models read across line breaks; names never span
        # them. NER returned "John Smith\nMetoprolol" as one PERSON, and
        # the span then had to be judged whole — clinical, and the name
        # was left in, or a name, and the drug was removed. Split first, so
        # each line's piece is judged on its own.
        for k in ("ner", "ner_xx", "context"):
            layers[k] = _split_at_newlines(layers[k])
        layers["ner"] = _trim_id_labels(layers["ner"])
        for k in ("ner", "ner_xx"):
            layers[k] = _trim_name_labels(layers[k])

        trace.raw_counts = {k: len(v) for k, v in layers.items()}
        all_hits = [h for v in layers.values() for h in v]
        ledger.detect(all_hits)

        # 4 clinical guard ----------------------------------------------
        before = all_hits
        all_hits = _subtract_clinical(text, all_hits, prof)
        ledger.mark_removed(before, all_hits, talc.CLINICAL,
                            "clinical vocabulary or protected measurement")

        # 5 garnet — evidence -------------------------------------------
        if _flag("PHI_GARNET"):
            all_hits, held = garnet.sort(text, all_hits, prof, file_clinical=file_clinical)
            trace.held = held
            ledger.mark_held(held)
        else:
            import dataclasses as _dc
            all_hits = [_dc.replace(h, section=prof.kind_at(h.start)) for h in all_hits]

        guarded = shale.make_clinical_guard(
            lambda t, h: _is_clinical_in_context(t, h, prof))

        # 6 shale — adjacent tokens -------------------------------------
        if _flag("PHI_ADJACENT_NAMES"):
            all_hits, trace.adjacent_tokens = shale.expand_adjacent(
                text, all_hits, _ADJACENT_TOKEN, guarded)

        # 7 shale — spread seed names -----------------------------------
        seeds = shale.seed_names_from(all_hits)
        trace.seed_count = len(seeds)
        if _flag("PHI_SHALE"):
            new, trace.propagated = shale.propagate(
                text, all_hits, seeds, guarded, _make_hit, layer="propagated")
            all_hits += new
            # Second mention (see shale.propagate, cue_only).
            weak = shale.weak_seed_names_from(all_hits) - seeds
            if weak:
                new, n2 = shale.propagate(
                    text, all_hits, weak, guarded, _make_hit, layer="propagated", cue_only=True)
                all_hits += new
                trace.propagated += n2
            if memory:
                mem_only = set(memory) - seeds
                new, trace.from_memory = shale.propagate(
                    text, all_hits, mem_only, guarded, _make_hit, layer="patient_names")
                all_hits += new
            if typed_memory:
                all_hits += _typed_memory_hits(text, all_hits, typed_memory)
        trace.confirmed_names = seeds

        # 8 span ownership ----------------------------------------------
        result = _dedupe(all_hits)

        # 9 jasper — roles ----------------------------------------------
        # Workplaces and named places of care, read from the kind word
        # after them or the work cue before them (inkguard/jasper.py).
        result = _apply_place_roles(text, result, ledger, trace)
        if typed_memory:
            # A name the patient's file knows as a workplace or facility is
            # labelled as one here too ("back at Supico" -> [EMPLOYER]).
            import dataclasses as _dcx
            result = [_dcx.replace(h, entity_type=typed_memory[h.text.strip()])
                      if h.entity_type == "PERSON" and h.text.strip() in typed_memory else h
                      for h in result]
        result = _drop_credentials_as_places(text, result, ledger)
        result = _drop_partial_urls(text, result, ledger)

        return result, trace

    except Exception:
        raise     # fail-closed: the caller sees the exception and holds the note


_CREDENTIAL_TOKEN = re.compile(
    r"(?:MD|M\.D\.|DO|D\.O\.|PA|PA-C|NP|RN|LPN|CRNA|DPT|PT|OT|MBBS|PhD|PharmD|FACS|FACP|"
    r"MS|MA|MPH|MSN|BSN|CGC|LCSW|MSW|RD|CNM|FNP|APRN|DNP|CRNP|PsyD|OTR|CCC|SLP|RRT|CNA|"
    r"FAAP|FACC|FACOG|FRCSC|FRCPC|Esq|CNIM|CST|L\.Ac|LAc|DACM|ND|DC|ATC)")


def _typed_memory_hits(text: str, hits: list[PHIHit], typed: dict) -> list[PHIHit]:
    """Workplaces and facilities named anywhere in the patient's file, found
    again in this note: "back at Supico next week" after "works for Supico
    Bakery" in the admission note. Capitalised, whole-word occurrences only,
    never where the words are clinically bound."""
    from inkguard.garnet import clinically_bound
    out: list[PHIHit] = []
    taken = [(h.start, h.end) for h in hits]
    for name, entity in sorted(typed.items(), key=lambda kv: -len(kv[0])):
        if len(name) < 3:
            continue
        for m in re.finditer(rf"(?<![\w'’-]){re.escape(name)}(?=['’]s\b|[^\w'’-]|$)", text):
            s, e = m.start(), m.end()
            if any(s < b and a < e for a, b in taken) or clinically_bound(text, s, e):
                continue
            out.append(PHIHit(entity, s, e, m.group(), 0.85, "patient_names", evidence=("patient_file",)))
            taken.append((s, e))
    return out


def _drop_partial_urls(text: str, hits: list[PHIHit], ledger) -> list[PHIHit]:
    """A web address ends where its token ends. A URL match that stops
    inside a longer token ("p.Gl" out of "p.Glu23fs", a genetic variant)
    or that has no scheme, "www" or slash and is shorter than 8 characters
    is not a URL. Nor is one with two dots in a row: no host name has an
    empty label, and "12/18/2074...no" was read as the site "2074...no"."""
    from inkguard import talc
    out = []
    for h in hits:
        if talc.canonical(h.entity_type) == "URL":
            nxt = text[h.end:h.end + 1]
            short = (len(h.text) < 8 and not re.search(r"(?i)https?://|www\.|/", h.text))
            empty_label = ".." in h.text.split("/", 3)[2 if "://" in h.text else 0]
            if (nxt and nxt.isalnum()) or short or empty_label:
                ledger.mark_within(h.start, h.end, talc.NOT_PHI, "not a web address", ("URL",))
                # An earlier step may have trimmed this hit ("2074...no" lost
                # "2074" to the date and arrived here as "no"). The ruling
                # covers the detection it was cut from too, or that one is
                # reported lost and the whole file stops.
                for e in ledger.entries.values():
                    if (not e.fate and talc.canonical(e.entity) == "URL"
                            and e.start < h.end and h.start < e.end):
                        e.fate, e.reason = talc.NOT_PHI, "not a web address"
                continue
        out.append(h)
    return out


def _drop_credentials_as_places(text: str, hits: list[PHIHit], ledger) -> list[PHIHit]:
    """"Jianhai Mayberry, MD" — the NER model reads the credential as
    Maryland. A state code standing right after a name and a comma, on a
    signature line, is a credential."""
    from inkguard import talc, jasper
    import dataclasses as _dc
    # A place-typed span that a provider-role frame reads as a person
    # ("Obregon (charge) aware") is a person: the role outranks the
    # gazetteer. Both are removed; the placeholder should say which.
    role_spans = {(r.start, r.end): r.role for r in jasper.provider_role_names(text)}
    hits = [_dc.replace(h, entity_type="PERSON", evidence=tuple(h.evidence) + (f"role:{role_spans[(h.start, h.end)]}",))
            if talc.canonical(h.entity_type) == "LOCATION" and (h.start, h.end) in role_spans else h
            for h in hits]
    out = []
    for h in hits:
        if (talc.canonical(h.entity_type) == "LOCATION"
                and _CREDENTIAL_TOKEN.fullmatch(h.text.strip())
                and re.search(r"[A-Za-z.\]]\s*,?\s*$", text[max(0, h.start - 40):h.start])
                and (any(talc.canonical(g.entity_type) == "PERSON" and 0 <= h.start - g.end <= 3
                         for g in hits)
                     # ... or after another credential: "Olson, MS, CGC".
                     or re.search(r"(?:MD|DO|RN|NP|PA|MS|MA|PhD|MPH|CGC|FNP|APRN)\s*,\s*$",
                                  text[max(0, h.start - 12):h.start]))):
            ledger.mark_within(h.start, h.end, talc.NOT_PHI, "clinical credential after a name",
                               ("LOCATION",))
            continue
        out.append(h)
    return out


def _apply_place_roles(text: str, hits: list[PHIHit], ledger, trace) -> list[PHIHit]:
    """Retype and trim hits that fall on a workplace or facility name.

    "Supico Bakery" found as a PERSON becomes EMPLOYER "Supico" and the
    kind word "Bakery" stays. A detection wholly inside the name plus its
    kind word is replaced; one that reaches outside it is left alone (the
    name is then already covered by it). Held garnet records on the name
    are dropped — the doubt is resolved."""
    from inkguard import talc, jasper
    spans = jasper.workplaces_and_facilities(text)
    if not spans:
        return hits
    out = list(hits)
    new: list[PHIHit] = []
    for r in spans:
        whole_end = max(r.end, r.tail_end)
        inside = [h for h in out if h.start < whole_end and r.start < h.end]
        # A non-PHI span (NER's ORGANIZATION "Golden Foods Inc.") may
        # reach past the kind word; it is simply replaced. A PHI span that
        # reaches outside means the name is already covered — leave it.
        if any((h.start < r.start or h.end > whole_end) and talc.spec(h.entity_type).phi
               for h in inside):
            continue
        for h in inside:
            out.remove(h)
        # Detections inside the name are covered by the new hit; ones that
        # also take in the kind word ("Supico Bakery") are explained here.
        ledger.mark_within(r.start, whole_end, talc.NOT_PHI,
                           f"{r.entity.lower()} name redacted; kind word '{r.role}' kept")
        new.append(PHIHit(r.entity, r.start, r.end, text[r.start:r.end], 0.90, "role",
                          section="", evidence=(f"role:{r.role}",)))
        if getattr(trace, "held", None):
            trace.held = [x for x in trace.held
                          if not (x["start"] < whole_end and r.start < x["end"])]
    ledger.detect(new)
    return sorted(out + new, key=lambda h: h.start)


# Adjacent-token shape for shale's adjacent-token step: the loose name
# token, so a one-character CJK given name can join its surname.
_ADJACENT_TOKEN = re.compile(_NAME_TOKEN_LOOSE)


def scan(text: str, memory: set | None = None) -> list[PHIHit]:
    """Hits only. See scan_with_trace for the full account."""
    return scan_with_trace(text, memory=memory)[0]


def warmup() -> dict:
    """Force every lazily-built component into existence.

    Run at start-up so a missing language model or a broken vocabulary
    file is reported straight away, with what is missing, rather than
    surfacing as a failure on the first note.
    """
    get_analyzer()                      # loads spaCy + builds recognizers
    scan("Warmup: patient John Doe, MRN 1234567.")   # exercises the default layers
    status = {"analyzer": "ok", "scan": "ok"}

    # COMPONENT INVENTORY. Two components degrade SILENTLY if their
    # dependency is missing: without wordfreq every frequency signal reads
    # zero (common words look like rare surnames; precision collapses),
    # and without the multilingual model non-Latin names lose one of
    # their layers. Report both so the user knows what is running.
    try:
        from wordfreq import zipf_frequency
        status["wordfreq"] = "ok" if zipf_frequency("the", "en") > 5 else "degraded"
    except Exception:
        status["wordfreq"] = "MISSING (required: pip install wordfreq)"
    # A keep list naming a Safe Harbor identifier is ignored, loudly.
    from inkguard.talc import refused_keeps
    refused = refused_keeps(settings.keep_entities)
    if refused:
        status["keep_entities_ignored"] = (
            ", ".join(refused) + " — Safe Harbor identifiers are always removed")
    status["multilingual_ner"] = ("ok" if _get_multilingual() is not None else
                                  "absent (optional: python -m spacy download xx_ent_wiki_sm)")
    return status
