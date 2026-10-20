"""
pyrite — the practice batch, and the badge a reviewer earns by passing it.

Nobody's first look at a real patient's notes should also be their first
look at this tool. So before a reviewer can be put on a real batch they work
through a PRACTICE BATCH: notes that look exactly like the real thing, read
on the real review screen, with one difference — these people do not exist,
and the tool already knows every answer.

Four things make this worth doing rather than a formality.

  1. THE NOTES ARE INVENTED. Every name, number, address
     and date below was made up for this file. Nothing here came from a
     patient, a practice or any record, and the file is checked
     by the repository's own preflight scan like everything else.

  2. THE MARKING IS OBJECTIVE. Each note carries an answer key: what must
     come out, and what must stay. Passing is not an opinion.

  3. IT TESTS THE PERSON, NOT THE ENGINE. Two notes hold identifiers the
     engine is known to miss — a job only one person in a village holds, and
     a rare disease combined with the shop a patient runs. A
     reviewer who only ticks what the tool already marked does not pass,
     because that is exactly the reviewer a two-person review cannot carry.

  4. THE BADGE EXPIRES. Competence at this decays, the engine changes, and a
     badge from three years ago says nothing about today. It lasts a year,
     and the review report prints the date it was earned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

#: How long a badge lasts before it is practised again.
BADGE_MONTHS = 12
BADGE_DAYS = 365

#: What passing takes.
MUST_CATCH_PLANTED = 2          # of the identifiers the engine is known to miss
MAX_WRONGLY_KEPT = 0            # an identifier left in is a fail, always
MAX_OVER_REMOVED = 6            # taking out too much is a mark against, not a fail


class PracticeError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


@dataclass
class Note:
    """One invented note and its answer key."""
    id: str
    text: str
    #: Must not appear in the finished text. Exact words.
    must_go: tuple = ()
    #: Must still be there: clinical facts a nervous reviewer tends to
    #: remove. Taking these out is over-removal, which is marked but not a
    #: fail — it costs the research, not the patient.
    must_stay: tuple = ()
    #: Identifiers the engine is known NOT to mark. These are the point of
    #: the exercise: only a person reading the note finds them.
    planted: tuple = ()
    why: str = ""


# ---------------------------------------------------------------------------
# The notes. Invented people, invented places, invented numbers.
# ---------------------------------------------------------------------------

NOTES: tuple[Note, ...] = (
    Note(
        id="p1",
        text="""Clinic letter

Patient: Harriet Vance, MRN 7781042, date of birth 3 February 1961.
Seen at Brookmill Surgery on 14 May 2026 by Dr. Omar Haddad.
Telephone 555-0182. Lives at 41 Pargeter Lane, Welbury.

She reports three weeks of morning stiffness in both hands, improving
through the day. No rash, no fever. Rheumatoid factor negative.
Plan: start hydroxychloroquine 200 mg daily, review in eight weeks.
""",
        must_go=("Harriet", "Vance", "7781042", "555-0182", "Pargeter", "Welbury",
                 "Brookmill", "Haddad"),
        must_stay=("hydroxychloroquine", "morning stiffness", "Rheumatoid factor"),
        why="The straightforward one: everything that identifies is labelled or obvious. "
            "A reviewer should find nothing here the tool has not already marked.",
    ),
    Note(
        id="p2",
        text="""Follow-up note

Mrs Vance returned on 2 July 2026. Hands much improved on treatment.
She mentioned her daughter Tranche is getting married in the autumn.
Her husband is the village postmaster and drove her in.

Reassured. Continue hydroxychloroquine. Annual eye check arranged.
""",
        must_go=("Vance", "Tranche", "village postmaster"),
        must_stay=("hydroxychloroquine", "eye check"),
        planted=("village postmaster",),
        why="A job that only one person in a place holds. It is not a name, not a number "
            "and not an address, so no detector has anything to catch — and yet in a "
            "village it names the household exactly. (The daughter's name here is also an "
            "ordinary English word, which is the kind of thing the engine has missed "
            "before; it is on the answer key, but it is not what this note is testing, "
            "because an engine that learns the word stops teaching the lesson.)",
    ),
    Note(
        id="p3",
        text="""Discharge summary

Patient: Dennis Okoro, MRN 4420913. Admitted 9 June 2026, discharged 12 June 2026.
Referred by Dr. Priya Raghunathan at Eastfield Health Centre.

He is the only patient in our practice with Erdheim-Chester disease, and runs
the hardware shop on the high street in Little Ashby. Presented with bone pain
and fatigue. Started on interferon alfa; follow-up with haematology.
""",
        must_go=("Dennis", "Okoro", "4420913", "Raghunathan", "Eastfield", "Little Ashby",
                 "only patient in our practice", "hardware shop"),
        must_stay=("Erdheim-Chester", "interferon alfa", "bone pain"),
        planted=("only patient in our practice", "hardware shop"),
        why="Nothing here is a name or a number, and that is the lesson: \"the only patient "
            "in our practice with\" a rare disease, plus the shop he runs, identifies him in "
            "his own town more surely than his MRN does. The tool flags the rare disease; "
            "what it cannot do is read the sentence around it.",
    ),
    Note(
        id="p4",
        text="""Telephone note

Call from the patient's wife at 14:20. Husband is 94 years old and has become
confused since starting the new tablet. Lives in ZIP 02139.

Advised to stop the tablet and bring him to the surgery tomorrow morning.
""",
        must_go=("94", "02139"),
        must_stay=("confused", "stop the tablet"),
        why="The two Safe Harbor details people forget are details at all: an age over 89 "
            "and a full ZIP code. Both must be made less exact, and the tool does it — but "
            "a reviewer who does not know why will put one back.",
    ),
)

#: Every planted identifier across the practice set. Each one is in its
#: note's `must_go` as well: a planted identifier is still an identifier, so
#: leaving it in is both a miss and a fail.
ALL_PLANTED = tuple(w for n in NOTES for w in n.planted)
for _n in NOTES:
    assert set(_n.planted) <= set(_n.must_go), f"{_n.id}: a planted word must also be must_go"


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------

def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", text or "")}


def _present(needle: str, haystack: str) -> bool:
    """Is this word or phrase still in the text, as a whole word?"""
    if " " in needle:
        return needle.lower() in (haystack or "").lower()
    return needle.lower() in _words(haystack)


@dataclass
class Mark:
    """How one reviewer did on the practice batch."""
    attempted: str = ""
    wrongly_kept: list = field(default_factory=list)      # an identifier left in
    over_removed: list = field(default_factory=list)       # clinical facts taken out
    planted_found: list = field(default_factory=list)      # the ones only a person finds
    planted_missed: list = field(default_factory=list)
    unanswered: int = 0
    notes: int = 0
    passed: bool = False
    why: list = field(default_factory=list)                # plain reasons, shown to them

    def public(self) -> dict:
        return {"passed": self.passed, "notes": self.notes,
                "wrongly_kept": len(self.wrongly_kept),
                "over_removed": len(self.over_removed),
                "planted_found": len(self.planted_found),
                "planted_of": len(ALL_PLANTED),
                "unanswered": self.unanswered, "why": list(self.why),
                "attempted": self.attempted}


def mark(finished: dict[str, str], unanswered: int = 0,
         own: dict[str, list] | None = None) -> Mark:
    """Mark a practice attempt.

    `finished` maps each practice note's id to the text the reviewer's own
    decisions produced — exactly what would have left the building had these
    been patients' notes. That is the thing worth marking: not which boxes they
    ticked, but what their work would have released.

    `own` maps a note id to the pieces of text THE REVIEWER THEMSELVES took
    out (their own marks and their own answers, not the engine's removals).
    A planted identifier counts as found only when it is in there. Scoring
    it by mere absence would credit the reviewer whenever the engine happened
    to catch it — which is exactly the opposite of what this exercise
    measures."""
    m = Mark(attempted=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             notes=len(finished), unanswered=int(unanswered))
    own = own or {}
    for note in NOTES:
        out = finished.get(note.id)
        if out is None:
            continue
        mine = " \n ".join(own.get(note.id) or ()).lower()
        for word in note.must_go:
            if _present(word, out):
                m.wrongly_kept.append({"note": note.id, "kind": _kind_of(word, note)})
        for word in note.must_stay:
            if not _present(word, out):
                m.over_removed.append({"note": note.id, "what": word})
        for word in note.planted:
            # Found by this person, or not found at all.
            by_them = word.lower() in mine
            (m.planted_found if by_them else m.planted_missed).append({"note": note.id})

    need = min(MUST_CATCH_PLANTED, len(ALL_PLANTED))
    if len(finished) < len(NOTES):
        m.why.append(f"Only {len(finished)} of {len(NOTES)} practice notes were finished.")
    if m.unanswered:
        m.why.append(f"{m.unanswered} word the tool was unsure about "
                     f"{'was' if m.unanswered == 1 else 'were'} left unanswered. Every one has "
                     "to be decided — that decision is what makes this a human determination.")
    if m.wrongly_kept:
        kinds = sorted({r["kind"] for r in m.wrongly_kept})
        m.why.append(f"{len(m.wrongly_kept)} identifier(s) would have left the building: "
                     f"{', '.join(kinds)}. One is one too many.")
    if len(m.planted_found) < need:
        m.why.append(f"{len(m.planted_found)} of the {len(ALL_PLANTED)} identifiers the tool "
                     "does not mark were found by your own reading, and at least "
                     f"{need} are needed. Ticking what the tool has already marked is not a "
                     "review — the reading is the job.")
    if len(m.over_removed) > MAX_OVER_REMOVED:
        m.why.append(f"{len(m.over_removed)} clinical details were removed that should have "
                     "stayed — a medicine, a finding, the reason for the visit. Taking out "
                     "everything is not a safe answer: it leaves the records useless, and it is "
                     "not what a reviewer is for.")
    m.passed = (len(finished) == len(NOTES)
                and m.unanswered == 0
                and len(m.wrongly_kept) <= MAX_WRONGLY_KEPT
                and len(m.planted_found) >= need
                # Blanking the notes removes every identifier too. A pass has
                # to mean the records are still worth something afterwards.
                and len(m.over_removed) <= MAX_OVER_REMOVED)
    if m.passed and not m.why:
        m.why.append("Every identifier was taken out, every uncertain word was answered, the "
                     "clinical facts were left alone, and the ones the tool does not mark were "
                     "found by reading.")
    return m


def _kind_of(word: str, note: Note) -> str:
    """What sort of identifier a missed word was, in plain words. The word
    itself is never put in a result: these are invented people, but a mark
    that quotes the answer teaches the answer."""
    if word in note.planted:
        return "something that identifies by what the sentence says, not by being a name"
    if word.isdigit() and len(word) >= 5:
        return "a record number"
    if word.isdigit():
        return "an age or a code"
    if "-" in word or word.replace("-", "").isdigit():
        return "a telephone number"
    return "a name or a place"


# ---------------------------------------------------------------------------
# The badge
# ---------------------------------------------------------------------------

def badge(m: Mark, *, today: date) -> dict:
    """What is recorded against the person when they pass. No answers, no
    note text — the score, the date, and when it runs out."""
    if not m.passed:
        raise PracticeError("PRACTICE-NOT-PASSED")
    return {"earned": today.isoformat(),
            "until": (today + timedelta(days=BADGE_DAYS)).isoformat(),
            "planted_found": len(m.planted_found), "planted_of": len(ALL_PLANTED),
            "over_removed": len(m.over_removed), "notes": m.notes}


def badge_state(b: dict | None, *, today: date) -> dict:
    """Where a person's badge stands. A badge that has run out is not a
    badge: the practice batch is done again."""
    if not b or not b.get("until"):
        return {"certified": False, "state": "never", "days_left": None}
    try:
        until = date.fromisoformat(b["until"])
    except (ValueError, TypeError):
        return {"certified": False, "state": "unreadable", "days_left": None}
    left = (until - today).days
    return {"certified": left >= 0, "state": "current" if left >= 0 else "run out",
            "earned": b.get("earned"), "until": b["until"], "days_left": left,
            "due_soon": 0 <= left <= 30}
