"""How the command-line tool presents the words it flagged for review: the
reason for each, and whose side of the line it falls on (the tool's gap or
the writing)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:                       # annotations only
    pass


# Why a word was flagged, and whose side of the line it falls
# on — the tool's own gap, the writing, or either. This is how the review
# list is PRESENTED (shell), so it lives here; the engine only produces the
# reason codes.
REVIEW_REASONS = {
    "unknown_word": ("tool", "A word the tool does not know yet — usually a device or drug "
                     "brand, an eponym or a rare term; sometimes a misspelling."),
    "date_left_in": ("tool", "A day and month the tool left in."),
    "place_left_in": ("tool", "A city, town or county the tool left in."),
    "other_script": ("tool", "Text in another script."),
    "unknown_acronym": ("either", "An abbreviation the tool could not find in any list it "
                        "holds. If it is standard (VCUG), the tool is still learning it; if it "
                        "is local shorthand, spelling it out once helps."),
    "name_left_in": ("writing", "A person named with no role. The software reads a name more "
                     "reliably with its role beside it ('daughter Rose', 'Dr. Lee, cardiology')."),
    "possible_person": ("writing", "A word used like a person's name with no role. The "
                        "software reads a name more reliably with its role and full name."),
    "lowercase_name": ("writing", "A name written in lowercase, which the software reads less reliably."),
    "initial_by_name": ("writing", "An initial standing beside a name."),
    "unknown_code": ("writing", "Letters and digits with no label. The software reads "
                     "labelled numbers more reliably ('MRN:', 'Acct:', 'Lot:')."),
    "long_number": ("writing", "A long number with no label."),
    "gate_error": ("tool", "The check itself failed; flagged as a precaution."),
    "spelling_slip": ("writing", "A word one keystroke from a known word: a slip, or a "
                      "misspelled name. It does not flag the file on its own; check it."),
    "name_also_clinical": ("either", "A word kept as clinical (a sign, a syndrome, an "
                           "instrument) that is also a name removed from this note. Usually "
                           "right; check that it is not the person."),
    "newsworthy_event": ("either", "An event that may have been in the news (a crash with many "
                         "hurt, a disaster, a shooting). With a date and a hospital it can "
                         "identify everyone in it. A reminder, not a rule: the kind of event may "
                         "stay; consider removing its name and how many were hurt."),
}


def review_side(reason: str) -> str:
    return REVIEW_REASONS.get(reason, ("tool", ""))[0]


def review_list(patient: str, held: list[dict]) -> str:
    """Markdown review list for one patient's flagged notes. `held` rows:
    {"file": rel, "words_to_check": [{"word", "reason", "holds_the_note"}, ...]}.
    {..., "lines": [n, ...]} gives the lines of the flagged copy each is on.
    Stays in the flagged folder on this computer."""
    from collections import Counter
    tool: Counter = Counter()
    writing: Counter = Counter()
    either: Counter = Counter()
    L = [f"# Words to check — {patient}", "",
         f"{len(held)} file(s) of this patient were redacted by the software, which was "
         "unsure about the words below. A person must decide each one before any of these "
         "files leaves this computer. The line is the line of the flagged copy (for Word "
         "files, the paragraph; for a PDF, the .txt beside it); Find (Ctrl+F or Cmd+F) "
         "works too.", ""]
    for row in held:
        words = [w for w in row["words_to_check"] if w.get("holds_the_note", True)]
        reminders = [w for w in row["words_to_check"] if not w.get("holds_the_note", True)
                     and w.get("suggestion")]
        L += [f"## {row['file']}", "", "| Line | Word | Why the software was unsure |", "|---|---|---|"]

        def where(w):
            ls = w.get("lines") or []
            return (", ".join(map(str, ls)) + (f" (+{w['more_lines']})" if w.get("more_lines") else "")
                    ) if ls else "not found: use Find"

        def cell(t):
            return str(t).replace("|", "\\|").replace("\n", " ")
        for w in reminders:
            L.append(f"| {where(w)} | `{cell(w['word'])[:120]}` | Reminder: {cell(w['suggestion'])} |")
        for w in words:
            side, _ = REVIEW_REASONS.get(w["reason"], ("tool", ""))
            {"tool": tool, "writing": writing}.get(side, either)[w["reason"]] += 1
            L.append(f"| {where(w)} | `{cell(w['word'])}` | {w['reason'].replace('_', ' ')}"
                     + (f". Suggested: {cell(w['suggestion'])}" if w.get("suggestion") else "") + " |")
        L.append("")
    if tool:
        L += ["## Words the tool is still learning", "",
              "These are the tool's gap, not the writer's. They are counted in the feedback "
              "report, which you may choose to email, so the vocabulary can grow.", ""]
        L += [f"- {REVIEW_REASONS[k][1]} ({n})" for k, n in tool.most_common()]
        L.append("")
    if either:
        L += ["## Could be either", ""]
        L += [f"- {REVIEW_REASONS[k][1]} ({n})" for k, n in either.most_common()]
        L.append("")
    if writing:
        L += ["## Writing the tool could not interpret", "",
              "These depend on how a note is written; the software is still learning them.", ""]
        L += [f"- {REVIEW_REASONS[k][1]} ({n})" for k, n in writing.most_common()]
        L.append("")
    return "\n".join(L)
