"""
PATIENT FILE — read the whole file first, then redact every note.

Design: read all of one patient's notes first, learn the names they
contain, then redact every note with that knowledge, including names that
are also everyday words. Whatever is still uncertain goes to the review
list.

A single note is a keyhole. "Rose" in "Rose visited, upset" means nothing
alone; in the patient's file, the admission note says "daughter Rose
Alvarado", and from then on every Rose in every note is her. The same file
says "Pfannenstiel incision" in the op note, which explains a bare
"Pfannenstiel" in the discharge summary.

PASS 1 (learn) reads every note of one patient and collects:
  names        every name confirmed with strong evidence anywhere in the
               file (a label, a title, a relation, a sign-off, a role...):
               the patient, family, providers — everyday-word names too;
  places       employers and named facilities found anywhere in the file;
  understood   words the file itself uses clinically ("Pfannenstiel
               incision", "Foley removed") and NEVER beside a person cue.
PASS 2 (process) redacts each note with that knowledge: a name confirmed in
any note is removed wherever it appears — capitalised, possessive, or in
lowercase beside a person cue.

What is still not understood after both passes is the REVIEW LIST: those
notes are flagged for a person to review, with the words listed and sorted
into
  - words the tool does not yet recognise (a brand, an eponym, a rare
    term, a date or place it left in) — a gap in the tool, listed in the
    output on the user's computer;
  - writing the tool cannot interpret (an abbreviation never spelled out, a
    person named with no role, a bare number with no label) — listed with
    a tip, so notes can be written more clearly.
Nothing is sent anywhere; the user may choose to return the feedback
report.

Everything lives in RAM for one patient and is discarded before the next.
Nothing learned about one patient ever touches another patient's notes.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# Clinical bindings strong enough to explain a word for the whole file.
# "clinical_noun_after" and list siblings are left out: they are what read
# "asked by Cseh ortho" as an instrument.
_STRONG_BINDINGS = frozenset({
    "qualifier_after", "device_state", "sizing_near", "instrument_number", "grade_after",
    "score_after", "test_result", "procedure_history", "diagnostic_after", "dose_before",
    "dose_after", "route_in_span", "pharm_before", "hyphen_compound", "modifier_before",
    "anatomic_of",
})


@dataclass(frozen=True)
class FileContext:
    names: frozenset = frozenset()
    places: dict = field(default_factory=dict)       # "Supico" -> "EMPLOYER"
    understood: frozenset = frozenset()
    notes: int = 0
    reads: int = 1          # times the whole file was read before it settled

    def summary(self) -> dict:
        """Counts only — the context itself is patient information."""
        return {"notes_read": self.notes, "times_read": self.reads,
                "names_confirmed": len(self.names),
                "workplaces_and_facilities": len(self.places),
                "words_explained_by_the_file": len(self.understood)}


def learn(texts, *, patient_key=None, profile_name=None, check=None) -> FileContext:
    """PASS 1: read every note of ONE patient."""
    from inkguard import garnet, talc, basalt
    from inkguard.cli import patient_memory
    from inkguard.agate import _person_context
    from inkguard.policy import evaluate

    texts = list(texts)
    structural = patient_memory(texts)
    names: set[str] = set(structural)
    places: dict[str, str] = {}
    clinical: Counter = Counter()
    person_words: set[str] = set()
    for t in texts:
        if check is not None:
            check()
        r = evaluate(t, patient_key=patient_key, profile_name=profile_name,
                     memory=structural, redaction_mode="mask")
        if r.action.value == "blocked":
            continue
        if r.trace is not None:
            names |= set(r.trace.confirmed_names)
        for h in r.hits:
            c = talc.canonical(h.entity_type)
            if c in ("EMPLOYER", "FACILITY") and h.text.strip():
                places[h.text.strip()] = c
        st = basalt.diagnose(t).sanitized_text
        removed = [(h.start, h.end) for h in r.hits]
        for m in re.finditer(r"\b[A-Z][A-Za-z'’-]{2,}\b", st):
            s, e = m.start(), m.end()
            if any(a < e and s < b for a, b in removed):
                continue                      # a removed word is no evidence
            w = re.sub(r"['’]s$", "", m.group()).lower()
            if _person_context(st, s, e):
                person_words.add(w)
                continue
            why = garnet.clinically_bound(st, s, e)
            if why in _STRONG_BINDINGS:
                clinical[w] += 1
    from inkguard.garnet import plausible_name
    names = {n for n in names if plausible_name(n)}
    name_parts = {p.lower().strip(".") for n in names for p in n.split()}
    understood = frozenset(w for w in clinical if w not in person_words and w not in name_parts)
    return FileContext(frozenset(names), places, understood, len(texts))


#: Every file is read at least this many times, counting the first read,
#: whatever the earlier reads found. The first read learns; later reads
#: redact with what was learned.
READ_MINIMUM = 3

#: The maximum number of reads of the whole file, including the first.
#: Reading continues while a read turns up something the previous one did
#: not know. A file can hold several kinds of variation at once — a
#: maiden name in one note, a nickname in another, the surname in capitals
#: in a pasted fax — and each read can uncover what the next needs.
#: Once READ_MINIMUM is reached, reading stops as soon as a read adds
#: nothing.
REREAD_LIMIT = 5


def process(texts, *, patient_key=None, profile_name=None, redaction_mode=None,
            want_oplog=False, reread_limit: int | None = None, should_stop=None):
    """PASS 2 and on: redact every note with what the whole file taught,
    and read again while a read keeps teaching. Returns (results, context).

    Knowledge only grows from read to read (names and places are added,
    never removed), so a later read never removes less than an earlier
    one; the reads settle when one adds nothing (after at least
    READ_MINIMUM reads), or at the limit."""
    from inkguard.policy import evaluate
    texts = list(texts)
    limit = REREAD_LIMIT if reread_limit is None else max(1, int(reread_limit))
    def check():
        # A cancel, a lock or closing the app must not wait for every read
        # of a long file to finish: it is honoured between notes.
        if should_stop is not None and should_stop():
            from inkguard.contract import Stopped
            raise Stopped()
    check()
    ctx = learn(texts, patient_key=patient_key, profile_name=profile_name, check=check)
    reads = 1
    while True:
        results = []
        for t in texts:
            check()
            results.append(evaluate(t, patient_key=patient_key, profile_name=profile_name,
                                    memory=set(ctx.names), redaction_mode=redaction_mode,
                                    want_oplog=want_oplog, file_context=ctx))
        reads += 1
        grown = _grow(ctx, results)
        if reads >= max(limit, READ_MINIMUM) or (grown is None and reads >= READ_MINIMUM):
            break
        ctx = grown or ctx
    ctx = _with_reads(ctx, reads)
    return results, ctx


def _grow(ctx: FileContext, results) -> FileContext | None:
    """What this read confirmed that the file did not know yet, added to
    it — or None when it taught nothing new."""
    from inkguard import talc
    names = set(ctx.names)
    places = dict(ctx.places)
    for r in results:
        if r.action.value == "blocked":
            continue
        if r.trace is not None:
            names |= set(r.trace.confirmed_names)
        for h in r.hits:
            c = talc.canonical(h.entity_type)
            if c in ("EMPLOYER", "FACILITY") and h.text.strip():
                places.setdefault(h.text.strip(), c)
    from inkguard.garnet import plausible_name
    names = {n for n in names if plausible_name(n)}
    if names == set(ctx.names) and places == ctx.places:
        return None
    parts = {p.lower().strip(".") for n in names for p in n.split()}
    return FileContext(frozenset(names), places,
                       frozenset(w for w in ctx.understood if w not in parts), ctx.notes, ctx.reads)


def _with_reads(ctx: FileContext, reads: int) -> FileContext:
    return FileContext(ctx.names, ctx.places, ctx.understood, ctx.notes, reads)


# ---------------------------------------------------------------------
# THE REVIEW LIST
# ---------------------------------------------------------------------

# The review-list taxonomy and its markdown formatting moved to the shell
# (inkguard/cli.py: REVIEW_REASONS, review_list, review_side) — it is how held
# words are PRESENTED to a reviewer, not detection.
