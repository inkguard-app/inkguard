"""
Name spreading — adjacent tokens, whole-note propagation, the clinical
guard, and the per-patient name set.

Detection layers find names one occurrence at a time. But a name in a
clinical note is not an isolated event: it is introduced once with
strong evidence ("Patient: Aisha Haddad") and then referred to again and
again with none ("Haddad tolerated the procedure"). Every layer that asks
"is THIS token a name?" meets the later mentions cold.

So once a name is confirmed with sufficient confidence, it is not
handled locally — it is carried everywhere the name appears. What stops
that spreading from removing clinical words is the clinical guard:
confirming "Mrs. Wilson" must never result in redacting "Wilson disease".

FOUR MECHANISMS
---------------
ADJACENT EXPANSION. When one token of a name is confirmed, the
neighbouring name-shaped tokens join the span: this is what turns
"Todd <PERSON>" into "<PERSON>" when only the surname was detected.

PROPAGATION. Names confirmed with strong evidence are searched for
throughout the document and every further occurrence is claimed.

SEED RULE. Only names confirmed by structure (a label, an age frame), by
a tight cue beside the name (a title, a relation, an attribution) or by
two independent detectors become seed names and are spread blind. A name
found by one statistical model alone is not spread blind, because if that
model was wrong, the error would be copied into every occurrence of the
word; it is spread only to later mentions with a person cue beside them
(weak_seed_names_from, cue_only).

PER-PATIENT NAME SET. Clinicians often write the structured note once
and then unstructured follow-ups. The structured note is where the name
appears with evidence; the follow-ups are where it appears without. So
seed names are collected across all of one patient's notes first (see
pumice.py) and carried into each note.

Names carried between one patient's notes (here and in pumice.FileContext)
are held in RAM for one patient at a time and are never written anywhere —
not to disk, not to a log, not to a cache. They are discarded when that
patient's notes are done.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Callable, Iterable

from inkguard.garnet import NEVER_NAMES, TITLES, clinically_bound, _zipf

# Evidence strong enough to make a name a seed name.
_SEED_LAYERS = frozenset({"anchor", "context", "patient_names"})
_SEED_SIGNALS = ("corroborated", "title_adjacent", "relation_adjacent",
                    "attribution_adjacent", "quorum")

from inkguard.talc import CASELESS_RE as _CASELESS  # shared vocabulary
_SENTENCE_START = re.compile(r"(?:^|[.!?\n]\s*|:\s*)$")
# One definition, shared with garnet. A shorter separate copy here once
# missed "Sunny expressed concern" because "expressed" was only in
# garnet's list.
from inkguard.garnet import _PREDICATE_AFTER  # noqa: E402


def _is_caseless(tok: str) -> bool:
    return bool(_CASELESS.search(tok))


def _usable_component(tok: str) -> bool:
    """Can this single token be propagated on its own?"""
    low = tok.lower().strip(".")
    if low in NEVER_NAMES or low in TITLES:
        return False
    from inkguard.garnet import not_a_name_part
    if not_a_name_part(tok):
        return False
    if _is_caseless(tok):
        return len(tok) >= 2
    return len(tok) >= 3 and tok[:1].isupper()


# =====================================================================
# Seed names
# =====================================================================

def seed_names_from(hits: Iterable) -> set[str]:
    """Names confirmed strongly enough to propagate (seed rule)."""
    out: set[str] = set()
    for h in hits:
        if h.entity_type != "PERSON":
            continue
        ev = getattr(h, "evidence", ()) or ()
        strong = (h.layer in _SEED_LAYERS
                  or any(e.startswith(_SEED_SIGNALS) for e in ev))
        if strong:
            # The same trims dedupe applies: "Barbara Fischer PhD" confirms
            # "Barbara Fischer"; the credential is not part of the name.
            from inkguard.talc import LEADING_ROLE, TRAILING_CREDENTIAL
            name = h.text.strip()
            m = LEADING_ROLE.match(name)
            if m and m.end() < len(name):
                name = name[m.end():]
            t = TRAILING_CREDENTIAL.search(name)
            if t and t.start() > 0:
                name = name[:t.start()]
            name = name.strip(" ,")
            from inkguard.garnet import plausible_name
            if name and name.lower() not in NEVER_NAMES and plausible_name(name):
                out.add(name)
    return out


def weak_seed_names_from(hits: Iterable) -> set[str]:
    """Every person name the note removes, however it was found. Used only
    for SECOND MENTIONS with a person cue beside them (propagate,
    cue_only=True) — never propagated blind."""
    from inkguard.talc import LEADING_ROLE, TRAILING_CREDENTIAL
    out: set[str] = set()
    for h in hits:
        if h.entity_type != "PERSON" or getattr(h, "layer", "") in ("uncertain",):
            continue
        name = h.text.strip()
        m = LEADING_ROLE.match(name)
        if m and m.end() < len(name):
            name = name[m.end():]
        t = TRAILING_CREDENTIAL.search(name)
        if t and t.start() > 0:
            name = name[:t.start()]
        name = name.strip(" ,")
        if name and name.lower() not in NEVER_NAMES and any(
                _usable_component(p) for p in name.split()):
            out.add(name)
    return out


def collect_seed_names(text: str, structural_scan: Callable[[str], list]) -> set[str]:
    """Cheap pre-pass for the per-patient name set.

    `structural_scan` must run ONLY the structural layers (labels and
    frames). They are regular expressions — no model — so reading every
    note of a patient this way costs almost nothing, and their hits are
    trusted as seeds on their own.
    """
    return seed_names_from(structural_scan(text))


# =====================================================================
# Clinical guard
# =====================================================================

def make_clinical_guard(is_clinical_in_context: Callable) -> Callable:
    """Build the check that stops name spreading from removing clinical
    vocabulary. True means: this occurrence is clinical, leave it alone."""
    def guarded(text: str, start: int, end: int, probe) -> bool:
        if clinically_bound(text, start, end):
            return True
        try:
            return bool(is_clinical_in_context(text, probe))
        except Exception:
            return False
    return guarded


# =====================================================================
# Adjacent expansion
# =====================================================================

def expand_adjacent(text: str, hits: list, name_token: re.Pattern,
                       guarded: Callable, max_extra: int = 2) -> tuple[list, int]:
    """Extend confirmed PERSON spans over adjacent name-shaped tokens.

    Adjacency is strict: tokens separated only by spaces. Punctuation, a
    newline, a title word, a clinical term or a very common English word
    stops the expansion. Returns (hits, number_of_tokens_added).
    """
    added = 0
    out: list = []
    for h in hits:
        if h.entity_type != "PERSON":
            out.append(h)
            continue
        start, end = h.start, h.end

        for _ in range(max_extra):                       # leftward
            m = re.search(r"([^\s.,;:()]+)( +)$", text[max(0, start - 40):start])
            if not m:
                break
            tok = m.group(1)
            tok_start = start - len(m.group(0))
            if not _joinable(text, tok, tok_start, name_token, guarded, h):
                break
            start = tok_start
            added += 1

        for _ in range(max_extra):                       # rightward
            m = re.match(r"( +)([^\s.,;:()]+)", text[end:end + 40])
            if not m:
                break
            tok = m.group(2)
            tok_start = end + len(m.group(1))
            if not _joinable(text, tok, tok_start, name_token, guarded, h):
                break
            end = tok_start + len(tok)
            added += 1

        if (start, end) != (h.start, h.end):
            ev = tuple(getattr(h, "evidence", ()) or ()) + ("adjacent_expansion",)
            h = dataclasses.replace(h, start=start, end=end,
                                    text=text[start:end], evidence=ev)
        out.append(h)
    return out, added


def _joinable(text: str, tok: str, tok_start: int, name_token: re.Pattern,
              guarded: Callable, like) -> bool:
    if not name_token.fullmatch(tok):
        return False
    low = tok.lower()
    if low in TITLES or low in NEVER_NAMES:
        return False
    if not _is_caseless(tok) and _zipf(low) >= 5.0:
        return False                        # "The", "With", "And"
    probe = dataclasses.replace(like, start=tok_start, end=tok_start + len(tok),
                                text=tok)
    if guarded(text, tok_start, tok_start + len(tok), probe):
        return False
    return True


# =====================================================================
# Propagation (within a note and across a patient's notes)
# =====================================================================

def _name_not_a_word(low: str) -> bool:
    """A lowercase word that is a listed name and not an English or clinical
    word: "garcia", "nguyen", "haddad" (not "rose", "will", "brown")."""
    from inkguard.name_registry import NAMES
    from inkguard.lexicon_english import ENGLISH_WORDS, NAME_ALSO_WORDS
    from inkguard.clinical_lexicon import is_clinical_term
    return (low in NAMES and low not in NAME_ALSO_WORDS and low not in ENGLISH_WORDS
            and not is_clinical_term(low) and _zipf(low) < 4.5)


def _fold(s: str) -> str:
    import unicodedata
    out = unicodedata.normalize("NFKD", s)
    return "".join(c for c in out if not unicodedata.combining(c)).replace("ł", "l").replace("Ł", "L")


def propagate(text: str, hits: list, seeds: set[str], guarded: Callable,
              make_hit: Callable, layer: str = "propagated",
              cue_only: bool = False) -> tuple[list, int]:
    """Claim every further occurrence of each seed name, subject to the
    clinical guard. Returns (new_hits, count).

    `make_hit(entity, start, end, text, score, layer, evidence)` builds a
    hit. An explicit factory rather than copying an existing hit as a
    template: an earlier draft did the latter, and in an unstructured
    follow-up note with NO detections there was no template to copy — so
    the per-patient name set silently did nothing in precisely the case
    it was built for.
    """
    if not seeds:
        return [], 0

    person_spans = [(h.start, h.end) for h in hits if h.entity_type == "PERSON"]
    new: list = []
    tag = "propagated" if layer == "propagated" else "patient_names"

    targets: set[str] = set()
    for name in seeds:
        targets.add(name)
        for part in name.split():
            if _usable_component(part):
                targets.add(part)
    # The same name as other systems print it: legacy all-caps headers
    # ("WIECZOREK, TADEUSZ") and keyboards without the accent ("Wisniewska"
    # for "Wiśniewska"). In testing, both were left in notes whose own
    # file had already confirmed the name.
    for t in list(targets):
        for form in (t.upper(), _fold(t), _fold(t).upper()):
            if form != t and len(form) >= 3:
                targets.add(form)

    # Longest first, so "Aisha Haddad" is claimed before "Haddad".
    for target in sorted(targets, key=len, reverse=True):
        if _is_caseless(target):
            finder = re.finditer(re.escape(target), text)
        else:
            # A hyphen after a letter joins a double-barrelled name
            # ("Mary-Deann"); a hyphen after a space or at a line start is a
            # list bullet ("-Deann (the Lanning in 716 ...)").
            # A possessive is still the name: "Hubert's daughter".
            # A specimen or part label is still the name: "Laws-A sigmoid".
            finder = re.finditer(
                rf"(?<!\w)(?<!\w')(?<![\w']-){re.escape(target)}(?=['’]s\b|-[A-Z0-9]\b|[^\w'’\-]|$)", text)
        for m in finder:
            s, e = m.start(), m.end()
            if any(s < pe and ps < e for ps, pe in person_spans):
                continue
            # SECOND MENTION. A name this note removed once, with evidence
            # too weak to make it a seed name (a model's reading, garnet's),
            # is claimed again only where a person cue stands
            # beside it: "Archil notes resting tremor", "Stone returned to
            # modified duty", "Savage to see at 2 weeks".
            if cue_only and not _person_cue(text, s, e):
                continue

            # A common English word that is also a CONFIRMED name ("John",
            # "Blank", "Curry", "Rose") at the start of a sentence. It used
            # to be claimed only before a listed person-verb, and "Doe woke",
            # "Blank served", "Curry informed", "John 52m" were left in notes
            # whose patient file had confirmed the name. Rule: once the
            # patient file confirms the name, every mention is the person
            # — unless what follows makes it the
            # ordinary word: a modal ("Will return"), or a function word or
            # number ("Rose to 38.5", "Mark the site", "Bill 3 units").
            if (" " not in target and not _is_caseless(target)
                    and _zipf(target.lower()) >= 3.6
                    and _SENTENCE_START.search(text[max(0, s - 3):s])
                    and not _PREDICATE_AFTER.match(text[e:e + 20])
                    and not _ERRAND_AFTER.match(text[e:e + 30])
                    and (target.lower() in _MODALS or _WORD_READING_AFTER.match(text[e:e + 24]))):
                continue

            probe = make_hit("PERSON", s, e, target, 0.80, layer, (tag,))
            if cue_only:
                # The person cue already outweighs a rare vocabulary sense
                # ("archil" is a lichen dye; "Archil notes resting tremor"
                # is the patient). Only a true clinical term is spared.
                from inkguard.clinical_lexicon import is_clinical_term
                from inkguard.garnet import clinically_bound
                if is_clinical_term(target) or clinically_bound(text, s, e):
                    continue
            elif guarded(text, s, e, probe):
                continue
            new.append(probe)
            person_spans.append((s, e))

        # A confirmed name written in lowercase ("garcia says", "spoke w
        # hubert", "mrs nguyen") is the same person. It is claimed outright
        # when the word is a name and not an ordinary English word; a name
        # that is also a word ("rose", "will", "mark") is claimed only where
        # a person cue stands beside it, so "BP rose to 150" stays.
        if (" " not in target and not _is_caseless(target) and target[:1].isupper()
                and len(target) >= 3):
            low = target.lower()
            plain_name = _name_not_a_word(low)
            for m in re.finditer(rf"(?<![\w'])(?<![\w']-){re.escape(low)}(?=['’]s\b|[^\w'’\-]|$)", text):
                s, e = m.start(), m.end()
                if any(s < pe and ps < e for ps, pe in person_spans):
                    continue
                before = text[max(0, s - 30):s]
                if not plain_name and not (
                        _LOWER_PERSON_BEFORE.search(before) or _PREDICATE_AFTER.match(text[e:e + 20])
                        or _LOWER_RELATION_AFTER.match(text[e:e + 40])):
                    continue
                probe = make_hit("PERSON", s, e, text[s:e], 0.75, layer, (tag, "lowercase"))
                if guarded(text, s, e, probe):
                    continue
                new.append(probe)
                person_spans.append((s, e))
    return new, len(new)


_MODALS = frozenset("will may can must shall might should could would".split())
# "Savage to see at 2 weeks", "Lindqvist to reassess": a person's errand.
_ERRAND_AFTER = re.compile(
    r"^\s+(?:to|will|can|should|may)\s+(?:see|call|follow|evaluate|re-?assess|re-?evaluate|"
    r"review|discuss|check|contact|speak|talk|meet|visit|round|examine|consult|decide|"
    r"come|return|arrive|stop\s+by|touch\s+base|update|let)\b"
    # "Okafor to floor", "Savage to OR": a patient being moved.
    r"|^\s+(?:to|back\s+to|transferred\s+to|sent\s+to)\s+(?:the\s+)?(?:floor|OR|PACU|ICU|MICU|SICU|"
    r"NICU|PICU|recovery|rehab|SNF|home|step-?down|tele(?:metry)?)\b", re.IGNORECASE)
_TITLE_RIGHT_BEFORE = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Mx|Dr|Prof)\.?\s*$")


def _person_cue(text: str, s: int, e: int) -> bool:
    return bool(_PREDICATE_AFTER.match(text[e:e + 20]) or _ERRAND_AFTER.match(text[e:e + 30])
                or _TITLE_RIGHT_BEFORE.search(text[max(0, s - 8):s]))
# What follows an everyday word used AS the word: "Rose to 38.5", "Mark the
# site", "Bill insurance", "Hope is to...", "Grace period".
_WORD_READING_AFTER = re.compile(
    r"^(?!\s+(?:to|will|can|should|may)\s+(?:see|call|follow|evaluate|re-?assess|review|discuss|"
    r"check|contact|speak|talk|meet|visit|round|examine|consult|update)\b)"
    r"(?:\s+(?:the|a|an|to|up|down|in|on|at|off|over|under|by|with|for|from|and|or|as|of|"
    r"than|into|onto|out|back|period|insurance|dry|well)\b|\s*\d(?![\dA-Za-z]*[mfMF]\b))",
    re.IGNORECASE)
_LOWER_PERSON_BEFORE = re.compile(
    r"\b(?:wife|husband|son|daughter|mother|father|mom|dad|brother|sister|spouse|partner|"
    r"friend|caregiver|guardian|aunt|uncle|niece|nephew|grand(?:son|daughter|mother|father)|"
    r"mr|mrs|ms|dr|pt|patient|w/|with|by|per|called|spoke\s+(?:with|to|w/?)|"
    # Self-introduction in a portal message: "hi this is rose", "its rose".
    r"this\s+is|it['’]?s|i\s+am|i['’]?m|im|my\s+name\s+is|name\s+is)\.?\s+$",
    re.IGNORECASE)
# "rose, hubert's daughter" / "rose (daughter)": the role follows the name.
_LOWER_RELATION_AFTER = re.compile(
    r"^\s*[,(]\s*(?:[a-z]+['’]s\s+|his\s+|her\s+|their\s+|the\s+)?"
    r"(?:wife|husband|son|daughter|mother|father|mom|dad|brother|sister|spouse|partner|"
    r"friend|caregiver|guardian|aunt|uncle|niece|nephew|grand(?:son|daughter|mother|father))\b",
    re.IGNORECASE)
