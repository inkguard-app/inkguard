"""The practice batch and the badge it earns.

Nobody's first look at a real patient's notes should also be their first
look at this tool. What this file holds to:
  1. the practice notes are invented — nothing in them is a real person;
  2. a reviewer who only ticks what the tool marked does not pass;
  3. an identifier that would have left the building is a fail, always;
  4. a badge runs out, and a reviewer without a current one cannot be put
     on a real batch.
"""
from __future__ import annotations

import datetime as dt
import re

import pytest

from inkguard import granite, opal, pyrite
from tests.test_granite import a_folder, with_people
from tests.test_opal import GOOD_PW, TODAY, call, code, make, ok, ready


def released(keep_planted: bool = False, skip: int = 0) -> dict[str, str]:
    """What a reviewer's work would have released: every must-go word taken
    out, optionally leaving the planted ones in (a reviewer who only ticks
    what the tool marked)."""
    out = {}
    for n in pyrite.NOTES[skip:]:
        text = n.text
        for word in n.must_go:
            if keep_planted and word in n.planted:
                continue
            text = re.sub(re.escape(word), "[REMOVED]", text)
        out[n.id] = text
    return out


def by_them(keep_planted: bool = False, skip: int = 0) -> dict[str, list]:
    """What the reviewer took out THEMSELVES. A planted identifier counts as
    found only when it is in here: marking it by absence would credit the
    reviewer whenever the engine happened to catch it, which is the opposite
    of what the practice batch measures."""
    return {n.id: ([] if keep_planted else list(n.planted))
            for n in pyrite.NOTES[skip:]}


def attempt(keep_planted: bool = False, skip: int = 0, **kw):
    """One whole attempt, marked as the service marks it."""
    return pyrite.mark(released(keep_planted, skip), own=by_them(keep_planted, skip), **kw)


# ---------------------------------------------------------------------------
# The notes themselves
# ---------------------------------------------------------------------------

def test_the_practice_notes_are_invented_and_say_so():
    assert len(pyrite.NOTES) >= 4
    for n in pyrite.NOTES:
        assert n.text.strip() and n.must_go and n.must_stay
        assert n.why, "each note teaches something, and says what"
    assert len(pyrite.ALL_PLANTED) >= 2


def test_every_planted_identifier_must_also_come_out():
    for n in pyrite.NOTES:
        assert set(n.planted) <= set(n.must_go)


def test_the_engine_really_does_miss_the_planted_ones():
    """The exercise is only worth doing if these are genuinely invisible to
    the tool. If a future engine starts catching one, this test fails and
    the practice set needs a new one — which is the point."""
    from inkguard import contract
    still_missed = []
    for n in pyrite.NOTES:
        if not n.planted:
            continue
        clean = contract.deidentify(n.text, profile="safe_harbor", mode="mask").clean_text
        for word in n.planted:
            if word.lower() in clean.lower():
                still_missed.append(word)
    assert still_missed, ("the engine now catches every planted identifier — add a harder one "
                          "to inkguard/pyrite.py rather than deleting this test")


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------

def test_a_careful_reviewer_passes():
    m = attempt()
    assert m.passed and not m.wrongly_kept and len(m.planted_found) == len(pyrite.ALL_PLANTED)


def test_a_reviewer_who_only_ticks_what_the_tool_marked_does_not_pass():
    m = attempt(keep_planted=True)
    assert not m.passed
    assert "reading is the job" in " ".join(m.why)


def test_one_identifier_left_in_is_a_fail():
    work = released()
    work["p1"] = work["p1"].replace("[REMOVED]", "7781042", 1)
    m = pyrite.mark(work, own=by_them())
    assert not m.passed and m.wrongly_kept


def test_an_unanswered_word_is_a_fail():
    assert not attempt(unanswered=1).passed


def test_an_unfinished_attempt_is_not_marked_as_passed():
    m = attempt(skip=1)
    assert not m.passed and "of 4 practice notes" in " ".join(m.why)


def test_removing_the_medicine_is_marked_but_is_not_a_fail():
    work = released()
    work["p1"] = work["p1"].replace("hydroxychloroquine", "[REMOVED]")
    m = pyrite.mark(work, own=by_them())
    assert m.over_removed and m.passed, "over-removal costs the research, not the patient"


def test_a_mark_never_quotes_the_answer():
    m = attempt(keep_planted=True)
    said = str(m.public())
    for word in ("Tranche", "hardware shop", "Harriet", "7781042"):
        assert word not in said


# ---------------------------------------------------------------------------
# The badge
# ---------------------------------------------------------------------------

def test_a_badge_runs_out_after_a_year():
    b = pyrite.badge(attempt(), today=dt.date(2026, 1, 1))
    assert pyrite.badge_state(b, today=dt.date(2026, 6, 1))["certified"] is True
    assert pyrite.badge_state(b, today=dt.date(2027, 6, 1))["certified"] is False
    assert pyrite.badge_state(b, today=dt.date(2026, 12, 20))["due_soon"] is True


def test_no_badge_is_not_a_badge():
    assert pyrite.badge_state(None, today=TODAY)["certified"] is False
    assert pyrite.badge_state({"until": "not a date"}, today=TODAY)["certified"] is False


def test_a_failed_attempt_earns_nothing():
    with pytest.raises(pyrite.PracticeError):
        pyrite.badge(attempt(keep_planted=True), today=TODAY)


# ---------------------------------------------------------------------------
# Through the service
# ---------------------------------------------------------------------------

def test_a_reviewer_works_through_the_practice_notes_and_earns_the_badge(tmp_path):
    svc, admin = ready(tmp_path)
    ok(call(svc, "account_create", admin, name="Jordan Lee", roles=["reviewer"],
            password=GOOD_PW))
    tok = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    opened = ok(call(svc, "practice_open", tok))
    assert opened["of"] == len(pyrite.NOTES)
    assert opened["badge"]["certified"] is False
    assert "do not exist" in opened["about"]
    for note in opened["notes"]:
        assert note["text"] and "status" in note

    # Answer every unsure word, and mark the planted identifiers by hand —
    # which is the only way to find them.
    decisions = {}
    for note in opened["notes"]:
        d = {"answers": {q["id"]: "remove" for q in note["questions"]},
             "kinds": {q["id"]: "name" for q in note["questions"]}, "add": []}
        for word in next(n for n in pyrite.NOTES if n.id == note["note"]).planted:
            at = note["text"].find(word)
            if at >= 0:
                d["add"].append({"start": at, "end": at + len(word), "type": "other"})
        decisions[note["note"]] = d
    out = ok(call(svc, "practice_mark", tok, decisions=decisions))
    assert out["passed"] is True, out["why"]
    assert out["badge"]["certified"] is True and out["badge"]["until"]
    assert ok(call(svc, "me", tok))["certified"] is True
    svc.close()


def test_a_reviewer_without_a_badge_cannot_be_put_on_a_real_batch(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = a_folder(tmp_path)
    for uid in reviewers:
        svc.doc["accounts"][uid].pop("badge", None)
    svc._save()
    assert code(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                     confirmed=[k for k, _ in opal.Service.CARE_POINTS])) == "BATCH-NOT-CERTIFIED"
    svc.close()


def test_a_badge_that_has_run_out_stops_a_new_batch(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = a_folder(tmp_path)
    for uid in reviewers:
        svc.doc["accounts"][uid]["badge"] = {"earned": "2024-01-01", "until": "2025-01-01"}
        svc.doc["accounts"][uid]["certified"] = True        # the old flag means nothing
    svc._save()
    assert code(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                     confirmed=[k for k, _ in opal.Service.CARE_POINTS])) == "BATCH-NOT-CERTIFIED"
    assert ok(call(svc, "account_list", admin))["accounts"][1]["certified"] is False
    svc.close()


def test_a_failed_attempt_is_recorded_without_shaming_anyone(tmp_path):
    svc, admin = ready(tmp_path)
    ok(call(svc, "account_create", admin, name="Jordan Lee", roles=["reviewer"],
            password=GOOD_PW))
    tok = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    opened = ok(call(svc, "practice_open", tok))
    # Everything left exactly as the tool marked it: no added marks, and the
    # unsure words answered "leave it in".
    decisions = {n["note"]: {"answers": {q["id"]: "fine" for q in n["questions"]}}
                 for n in opened["notes"]}
    out = ok(call(svc, "practice_mark", tok, decisions=decisions))
    assert out["passed"] is False and out["why"]
    assert ok(call(svc, "me", tok))["certified"] is False
    events = [e["event"] for e in svc.log.tail(50)]
    assert "practice_not_passed" in events
    # The log keeps counts, never the words.
    for e in svc.log.tail(50):
        assert "Tranche" not in str(e) and "Harriet" not in str(e)
    svc.close()
