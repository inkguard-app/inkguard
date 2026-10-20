"""
basalt — intake: input diagnostics and sanitization.

WHAT IT DOES
------------
Three jobs at the point where input enters the engine:

  DIAGNOSE — recognize what is arriving and report on it: diagnostic
    checks that identify defects in the input and say, specifically,
    how to fix each one. A finding
    without a remedy is useless, so every finding carries an actionable
    fix, not just a complaint.

  CARRY FORWARD — findings do not stop at intake. They travel with
    the note into the policy engine (a blocker holds the note) and into
    the operation log.

  SANITIZE — neutralize adversarial and corrupt characters before they
    reach anything that matters.

WHY SANITIZATION IS SECURITY, NOT TIDINESS
-------------------------------------------
This is the part that matters most. A single zero-width space inside a
Social Security number defeats the regex completely:

    "SSN 123-45-<U+200B>6789"  →  no match, number left in the note

The text renders identically to a human. Homoglyphs do the same to the
NER layer: Cyrillic "а" (U+0430) and Latin "a" are visually identical
and lexically unrelated, so a substituted character can carry an
identifier past a name detector.

Neither is exotic. Both are one keystroke for anyone who knows the
trick, and both are indistinguishable from a copy-paste accident, which
is exactly why they must be neutralized rather than merely reported.

Sanitization therefore runs BEFORE detection, and the scanner operates
on the sanitized text. The report says what was changed.
"""
import re
import unicodedata

from inkguard import input_classifier as classifier
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    BLOCK = "block"      # detection cannot be trusted; hold the note
    DEGRADE = "degrade"  # detection quality reduced; proceed but warn loudly
    ADVISE = "advise"    # works fine, but the input could be better


@dataclass
class Finding:
    code: str
    severity: Severity
    message: str      # what is wrong, in plain terms
    fix: str          # what to do about it — always actionable
    count: int = 1
    sample: str = ""

    def to_dict(self) -> dict:
        d = {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "fix": self.fix,
            "occurrences": self.count,
        }
        if self.sample:
            d["sample"] = self.sample
        return d


@dataclass
class Diagnosis:
    findings: list[Finding] = field(default_factory=list)
    sanitized_text: str = ""
    sanitization_applied: list[str] = field(default_factory=list)
    classification: object | None = None  # input_classifier.Classification

    @property
    def blocked(self) -> bool:
        return any(f.severity is Severity.BLOCK for f in self.findings)

    @property
    def quality_score(self) -> float:
        """1.0 = clean input. Weighted by severity, floored at 0."""
        penalty = sum(
            {Severity.BLOCK: 0.5, Severity.DEGRADE: 0.2, Severity.ADVISE: 0.05}[f.severity]
            for f in self.findings
        )
        return round(max(0.0, 1.0 - penalty), 2)

    def to_dict(self) -> dict:
        return {
            "input_quality_score": self.quality_score,
            "blocked": self.blocked,
            "sanitization_applied": self.sanitization_applied,
            "classification": self.classification.to_dict() if self.classification else None,
            "findings": [f.to_dict() for f in self.findings],
        }


# =====================================================================
# SANITIZATION — neutralize adversarial and corrupt characters
# =====================================================================

# Invisible characters. Every one of these can sit inside an identifier
# without changing how it looks, while breaking any regex that expects
# contiguous digits.
INVISIBLE_CHARS = {
    "\u200b": "zero-width space",
    "\u200c": "zero-width non-joiner",
    "\u200d": "zero-width joiner",
    "\u2060": "word joiner",
    "\ufeff": "byte-order mark",
    "\u00ad": "soft hyphen",
    "\u180e": "Mongolian vowel separator",
}

# Confusable characters from non-Latin scripts. Visually identical to
# Latin letters; lexically unrelated, so they slip past name detection.
HOMOGLYPHS = {
    # Cyrillic → Latin
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p",
    "\u0441": "c", "\u0443": "y", "\u0445": "x", "\u0456": "i",
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K",
    "\u041c": "M", "\u041d": "H", "\u041e": "O", "\u0420": "P",
    "\u0421": "C", "\u0422": "T", "\u0425": "X",
    # Greek → Latin
    "\u03bf": "o", "\u0391": "A", "\u0392": "B", "\u0395": "E",
    "\u0396": "Z", "\u0397": "H", "\u0399": "I", "\u039a": "K",
    "\u039c": "M", "\u039d": "N", "\u039f": "O", "\u03a1": "P",
    "\u03a4": "T", "\u03a7": "X",
    # Fullwidth → ASCII digits
    "\uff10": "0", "\uff11": "1", "\uff12": "2", "\uff13": "3",
    "\uff14": "4", "\uff15": "5", "\uff16": "6", "\uff17": "7",
    "\uff18": "8", "\uff19": "9",
}

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Mojibake signatures — UTF-8 bytes decoded as Latin-1.
MOJIBAKE = re.compile(r"(?:â€™|â€œ|â€\x9d|Ã©|Ã¨|Ã¼|Ã±|â€“|â€”|Â\xa0)")


DIGIT_DASH = re.compile(r"(?<=\d)[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63](?=\d)")


def sanitize(text: str) -> tuple[str, list[str], list[Finding]]:
    """Neutralize junk before detection runs.

    Returns (clean_text, actions_taken, findings).

    Order: invisible characters first, then NFKC normalization (folds
    fullwidth and compatibility forms), then explicit homoglyph mapping
    for the cross-script confusables NFKC does not touch, then dashes
    between digits, then control characters.
    """
    actions: list[str] = []
    findings: list[Finding] = []
    original = text

    # 1. Invisible characters — the highest-severity case, because this
    #    is a working bypass rather than a quality issue.
    found_invisible = {c: text.count(c) for c in INVISIBLE_CHARS if c in text}
    if found_invisible:
        total = sum(found_invisible.values())
        names = ", ".join(INVISIBLE_CHARS[c] for c in found_invisible)
        for c in found_invisible:
            text = text.replace(c, "")
        actions.append(f"removed {total} invisible character(s)")
        findings.append(Finding(
            code="INVISIBLE_CHARACTERS",
            severity=Severity.DEGRADE,
            message=(
                f"Found {total} invisible character(s) ({names}) inside the text. "
                "These are indistinguishable on screen but split identifiers so "
                "that pattern matching misses them — a zero-width space inside a "
                "Social Security number prevents it being detected at all."
            ),
            fix=(
                "Usually copy-paste from a PDF or word processor. The tool "
                "removed them, so this note was scanned correctly, but the "
                "source file still carries them."
            ),
            count=total,
        ))

    # 2. Homoglyphs
    nfkc = unicodedata.normalize("NFKC", text)
    # NFKC turns the micro sign of a unit ("60 µs", "1.0 µC/cm2") into the
    # Greek letter mu, which then read as another script. In front of a
    # Latin unit it is put back.
    nfkc = re.sub(r"\u03bc(?=[A-Za-z]{1,3}(?![A-Za-z]))", "\u00b5", nfkc)
    if nfkc != text:
        actions.append("applied Unicode NFKC normalization")
        text = nfkc

    swapped = {c: text.count(c) for c in HOMOGLYPHS if c in text}
    if swapped:
        total = sum(swapped.values())
        for c, repl in HOMOGLYPHS.items():
            if c in text:
                text = text.replace(c, repl)
        actions.append(f"normalized {total} look-alike character(s)")
        findings.append(Finding(
            code="HOMOGLYPH_CHARACTERS",
            severity=Severity.DEGRADE,
            message=(
                f"Found {total} character(s) from other scripts that look "
                "identical to Latin letters (for example Cyrillic 'а' for 'a'). "
                "Name detection treats them as different words, so an identifier "
                "containing one can pass through undetected."
            ),
            fix=(
                "Check the source system's encoding, or the keyboard layout used "
                "for data entry. The tool normalized them before scanning."
            ),
            count=total,
        ))

    # 2b. Dashes between digits. Word autocorrect, PDF copy-paste and EHR
    #     exports write SSNs, phones and dates with typographic dashes
    #     (‐ ‑ ‒ – — ―, minus −) that every identifier pattern, and the final
    #     inspection, read as ordinary text, so "123–45–6789" was not
    #     matched in internal testing before this step was added. Between
    #     two digits a dash is always a separator, so it becomes "-"; dashes
    #     in prose are left alone.
    folded, n = DIGIT_DASH.subn("-", text)
    if n:
        text = folded
        actions.append(f"normalized {n} dash(es) between digits")

    # 3. Control characters
    ctrl = CONTROL_CHARS.findall(text)
    if ctrl:
        text = CONTROL_CHARS.sub("", text)
        actions.append(f"removed {len(ctrl)} control character(s)")
        findings.append(Finding(
            code="CONTROL_CHARACTERS",
            severity=Severity.ADVISE,
            message=f"Found {len(ctrl)} control character(s) in the text.",
            fix="Removed automatically; no action needed.",
            count=len(ctrl),
        ))

    if text != original and not actions:
        actions.append("normalized text")

    return text, actions, findings


# =====================================================================
# DIAGNOSTIC CHECKS — recognize input defects and say how to fix them
# =====================================================================

from inkguard.talc import NON_LATIN_RE as NON_LATIN_SCRIPT  # shared vocabulary

BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{200,}={0,2}")

REPEATED_BLOCK = re.compile(r"(.{80,}?)\1{2,}", re.DOTALL)


def _check_script(text: str) -> list[Finding]:
    """Non-English text is the quietest failure mode in the whole system.

    The NER model is English-only. Given Spanish or Arabic clinical
    text it does not error — it simply finds nothing, and would report
    a clean scan while every identifier stayed in the note. A
    silent false negative is worse than a loud failure, so this is a
    BLOCK rather than a warning.
    """
    non_latin = NON_LATIN_SCRIPT.findall(text)
    letters = sum(1 for ch in text if ch.isalpha())
    ratio = len(non_latin) / letters if letters else 0.0

    # RATIO, NOT COUNT.
    #
    # The old test was "5 or more non-Latin characters", which refused an
    # ENGLISH note containing one patient name in another script — a
    # two-word name in Arabic script is about ten characters. On a test set
    # this held many English notes that merely named a patient in Arabic or
    # Devanagari script, although their clinical content was entirely
    # English and scannable.
    #
    # Two different situations were being conflated:
    #
    #   a note written in another language — the English NER genuinely
    #   cannot scan it, finds nothing, and would report a clean pass. That
    #   must still BLOCK.
    #
    #   an English note naming a patient in another script — the common
    #   case in any US clinic. The clinical text is scannable and the name
    #   is caught structurally by the script-agnostic frames in
    #   phi_scanner, which do not depend on the alphabet. Refusing these
    #   protects nobody and makes the tool unusable for exactly the
    #   patients whose names are hardest to redact.
    #
    # A ratio separates them without needing to identify the language.
    if ratio >= 0.30 and len(non_latin) >= 20:
        return [Finding(
            code="UNSUPPORTED_SCRIPT",
            severity=Severity.BLOCK,
            message=(
                "The text contains substantial non-Latin script. Identifier "
                "detection in this tool is English-language only. It will not "
                "raise an error on other languages — it will find nothing and "
                "report a clean scan while identifiers pass through."
            ),
            fix=(
                "This tool reads English notes. Notes written mainly in "
                "another language are held, not cleaned; they need a tool "
                "built for that language."
            ),
            count=len(non_latin),
        )]

    # Present but not dominant: proceed, and say so. The note is
    # scannable and the structural name layers are script-agnostic, but
    # NER contributes nothing to these spans, so the user should know
    # the detection resting on them is thinner than elsewhere.
    if non_latin:
        return [Finding(
            code="MIXED_SCRIPT",
            severity=Severity.ADVISE,
            message=(
                f"{len(non_latin)} non-Latin characters present "
                f"({ratio:.0%} of letters). The document was scanned. "
                "Identifiers in non-Latin script are detected by "
                "structural position rather than by the English NER model, "
                "so confidence for those spans rests on fewer layers."
            ),
            fix=("No action required. Notes written mainly in another "
                 "language are held; this tool reads English notes."),
            count=len(non_latin),
        )]
    return []


def _check_encoding(text: str) -> list[Finding]:
    hits = MOJIBAKE.findall(text)
    if hits:
        return [Finding(
            code="ENCODING_CORRUPTION",
            severity=Severity.DEGRADE,
            message=(
                f"Found {len(hits)} sequence(s) typical of UTF-8 text decoded as "
                "Latin-1 (mojibake). Corrupted words do not match known clinical "
                "terms or identifier patterns."
            ),
            fix=(
                "Export the source as UTF-8 rather than Latin-1/Windows-1252 "
                "and run it again."
            ),
            count=len(hits),
            sample=hits[0][:20],
        )]
    return []


def _check_base64(text: str) -> list[Finding]:
    blobs = BASE64_BLOB.findall(text)
    if blobs:
        return [Finding(
            code="EMBEDDED_BINARY",
            severity=Severity.DEGRADE,
            message=(
                f"Found {len(blobs)} long encoded blob(s). Encoded content is not "
                "scanned — identifiers inside a base64 payload are invisible to "
                "every detection layer here."
            ),
            fix=(
                "Remove the encoded block or replace it with its text. Pictures "
                "are not scanned by this tool at all."
            ),
            count=len(blobs),
        )]
    return []


def _check_truncation(text: str) -> list[Finding]:
    stripped = text.rstrip()
    if len(stripped) > 200 and not re.search(r"[.!?:;)\]\"']$", stripped):
        return [Finding(
            code="POSSIBLE_TRUNCATION",
            severity=Severity.ADVISE,
            message=(
                "The text ends mid-sentence. If it was cut off, an identifier may "
                "be split across the boundary and only partly visible to the "
                "scanner."
            ),
            fix=(
                "Check whether the record was cut short when it was exported, "
                "and export it whole."
            ),
        )]
    return []


def _check_repetition(text: str) -> list[Finding]:
    if REPEATED_BLOCK.search(text):
        return [Finding(
            code="DUPLICATED_CONTENT",
            severity=Severity.ADVISE,
            message="Large blocks of text repeat verbatim.",
            fix=(
                "Often a template or copy-paste repeating the same text. It "
                "was scanned as it stands; removing the repeats makes review "
                "quicker."
            ),
        )]
    return []


def diagnose(text: str,
             unresolved_tokens: list[str] | None = None,
             identifier_count: int | None = None) -> Diagnosis:
    """Full input diagnosis: sanitize, classify, then run every check.

    Sanitization runs first so the checks and the scanner both see
    neutralized text — reporting a bypass while still scanning the
    unsanitized version would be pointless.
    """
    clean, actions, findings = sanitize(text)

    # Classify the shape of the input. This drives detection strategy
    # downstream (field anchoring, whether bare numbers are suspect)
    # and enables the density check below.
    cls = classifier.classify(clean)

    # Encoding corruption must be checked against the RAW text.
    # NFKC normalization rewrites the very characters that signal
    # mojibake (it folds U+2122 "™" into "TM"), so by the time
    # sanitization has run the evidence is gone. Diagnostics about how
    # the input was encoded have to look at what actually arrived.
    findings += _check_encoding(text)

    findings += _check_script(clean)
    findings += _check_base64(clean)
    findings += _check_truncation(clean)
    findings += _check_repetition(clean)

    if unresolved_tokens:
        uniq = sorted(set(unresolved_tokens))
        findings.append(Finding(
            code="AMBIGUOUS_ABBREVIATIONS",
            severity=Severity.ADVISE,
            message=(
                f"Could not determine whether {', '.join(uniq)} "
                f"{'is' if len(uniq) == 1 else 'are'} clinical shorthand or a "
                "place name. They were redacted, which is the safe default but "
                "may have removed clinical content."
            ),
            fix=(
                "Add context around the abbreviation, or write it out — "
                "\"CT of the head\" rather than a bare \"CT\"."
            ),
            count=len(uniq),
        ))

    # Density check — only possible once the class is known.
    # "Found nothing" and "there was nothing to find" are
    # indistinguishable without an expectation to compare against.
    if identifier_count is not None:
        anomaly = classifier.density_anomaly(cls, identifier_count)
        if anomaly:
            findings.append(Finding(
                code="IDENTIFIER_DENSITY_ANOMALY",
                severity=Severity.DEGRADE,
                message=anomaly,
                fix=(
                    "If the note was already redacted, no action is needed. "
                    "If not, read it closely: this is often a detection miss."
                ),
            ))

    return Diagnosis(
        findings=findings,
        sanitized_text=clean,
        sanitization_applied=actions,
        classification=cls,
    )
