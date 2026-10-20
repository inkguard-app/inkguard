"""Two people review every note, one at a time, neither seeing the other.

The review is what puts a person's judgement on every note, so the service
holds the rules: every word the tool could not name is answered, identifiers
that are never a judgement call cannot be kept, a signed review is sealed,
and the doctor's clock starts only when both reviewers are done.
"""
from __future__ import annotations

import datetime as dt
import time

import pytest

from inkguard import granite, opal
from tests.test_granite import NOTE_A, a_folder, finish, with_people
from tests.test_opal import GOOD_PW, TODAY, call, code, ok, phone_now, ready


@pytest.fixture
def reviewed(tmp_path):
    """A batch prepared and ready for its two reviewers."""
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = a_folder(tmp_path)
    b = ok(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                confirmed=[k for k, _ in opal.Service.CARE_POINTS]))
    finish(svc, b["id"])
    one = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    two = ok(call(svc, "sign_in", name="Casey Park", password=GOOD_PW))["session"]
    yield svc, admin, b["id"], one, two
    svc.close()


def work_through(svc, tok, bid, *, answer="remove", attest=True):
    """One reviewer's whole pass: every note, every unsure word answered."""
    opened = ok(call(svc, "review_open", tok, batch=bid))
    nid = opened["next"]
    seen = 0
    while nid:
        n = ok(call(svc, "review_note", tok, batch=bid, note=nid))
        decision = {"answers": {q["id"]: answer for q in n["questions"]},
                    "kinds": {q["id"]: "name" for q in n["questions"]}}
        r = ok(call(svc, "review_save", tok, batch=bid, note=nid, decision=decision, confirm=True))
        seen += 1
        nid = r["next"]
    if attest:
        return ok(call(svc, "review_sign_off", tok, batch=bid, attest=True)), seen
    return None, seen


def test_each_reviewer_works_through_every_note_and_signs_off(reviewed):
    svc, admin, bid, one, two = reviewed
    opened = ok(call(svc, "review_open", one, batch=bid))
    assert opened["slot"] == "R1" and opened["of"] == 3 and opened["done"] == 0
    assert opened["minutes_left"] > 0 and opened["next"]

    first = ok(call(svc, "review_note", one, batch=bid))
    assert first["place"] == 1 and first["of"] == 3
    assert first["text"] and first["removals"]                 # the whole note, and what is marked
    assert first["confirmed"] is False

    out1, seen1 = work_through(svc, one, bid)
    # Their own part only: nothing about the other reviewer comes back.
    assert seen1 == 3 and out1 == {"signed_off": True}
    assert svc.doc["batches"][bid]["stage"] == "in_review"     # still waiting for the second

    out2, seen2 = work_through(svc, two, bid)
    assert seen2 == 3 and out2 == {"signed_off": True}
    rec = svc.doc["batches"][bid]
    assert rec["stage"] == "ready_for_doctor"
    assert rec["due"] == (TODAY + dt.timedelta(days=opal.REVIEW_DAYS_DEFAULT)).isoformat()
    assert ok(call(svc, "batch_status", admin, batch=bid))["days_left"] == opal.REVIEW_DAYS_DEFAULT
    assert {x["name"] for x in rec["signed_off"].values()} == {"Jordan Lee", "Casey Park"}


def test_neither_reviewer_ever_sees_the_others_answers(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    keep = [r["id"] for r in n["removals"] if not r["certain"]][:1]
    ok(call(svc, "review_save", one, batch=bid, note=nid,
            decision={"keep": keep, "answers": {q["id"]: "fine" for q in n["questions"]}}))
    mine = ok(call(svc, "review_note", one, batch=bid, note=nid))
    assert mine["decision"].get("keep") == keep                # their own work comes back
    theirs = ok(call(svc, "review_note", two, batch=bid, note=nid))
    assert theirs["decision"] == {}                            # the other's is nowhere in it
    assert "R1" not in str(theirs) and keep[0] not in str(theirs.get("decision"))


def test_every_unsure_word_is_answered_before_moving_on(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    if not n["questions"]:
        pytest.skip("this note left the tool in no doubt")
    assert code(call(svc, "review_save", one, batch=bid, note=nid,
                     decision={}, confirm=True)) == "REVIEW-UNANSWERED"
    part = {q["id"]: "remove" for q in n["questions"][:-1]}
    assert code(call(svc, "review_save", one, batch=bid, note=nid,
                     decision={"answers": part}, confirm=True)) == "REVIEW-UNANSWERED"
    whole = {q["id"]: "remove" for q in n["questions"]}
    ok(call(svc, "review_save", one, batch=bid, note=nid, decision={"answers": whole}, confirm=True))


def test_the_unsure_word_shows_the_tools_guess_and_keeps_the_reviewers_own(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    if not n["questions"]:
        pytest.skip("this note left the tool in no doubt")
    q = n["questions"][0]
    assert q["entity"] and q["kind"] in ("not_understood", "tool_unsure")   # what the tool thinks
    assert [k["id"] for k in n["kinds"]] == [k for k, _ in opal.REVIEW_KINDS]
    assert set(n["answers"]) == set(opal.REVIEW_ANSWERS)
    ok(call(svc, "review_save", one, batch=bid, note=nid, decision={
        "answers": {x["id"]: "remove" for x in n["questions"]},
        "kinds": {q["id"]: "place"},
        "words": {q["id"]: "the name of their village"},
    }, confirm=True))
    back = ok(call(svc, "review_note", one, batch=bid, note=nid))
    assert back["decision"]["kinds"][q["id"]] == "place"
    assert back["decision"]["words"][q["id"]] == "the name of their village"
    assert back["confirmed"] is True


def test_an_identifier_that_is_never_a_judgement_call_cannot_be_kept(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    certain = [r["id"] for r in n["removals"] if r["certain"]]
    if not certain:
        pytest.skip("no certain identifier in this note")
    assert code(call(svc, "review_save", one, batch=bid, note=nid,
                     decision={"keep": certain})) == "REVIEW-CERTAIN"


def test_a_decision_the_screen_sends_is_checked_field_by_field(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    bad = [
        {"keep": ["r999"]},                                     # not in this note
        {"keep": "all of them"},
        {"answers": {"q999": "remove"}},
        {"kinds": {"q0": "not a kind"}},
        {"add": [{"start": 5, "end": 2, "type": "name"}]},
        {"add": [{"start": 0, "end": 3, "type": "whatever"}]},
        {"add": [{"start": 0, "end": 3, "type": "name"}] * (granite.MAX_ADDS + 1)},
        {"words": {"q0": "x" * (granite.MAX_WORDS + 1)}},
        {"fb": {"over": "yes please"}},
        "not a decision at all",
    ]
    for d in bad:
        assert code(call(svc, "review_save", one, batch=bid, note=nid, decision=d)) \
            in ("APP-BAD-REQUEST", "REVIEW-CERTAIN")


def test_a_signed_review_is_sealed_and_cannot_be_reopened(reviewed):
    svc, admin, bid, one, two = reviewed
    work_through(svc, one, bid)
    nid = ok(call(svc, "review_open", one, batch=bid))["done"] and \
        granite.load_items(svc._batch_work(bid))
    first = [i.note_id for i in nid if i.note_id][0]
    assert code(call(svc, "review_save", one, batch=bid, note=first,
                     decision={"keep": []})) == "REVIEW-SIGNED"
    assert code(call(svc, "review_sign_off", one, batch=bid, attest=True)) == "REVIEW-SIGNED"


def test_signing_off_needs_every_note_done_and_the_statement_read(reviewed):
    svc, admin, bid, one, two = reviewed
    assert code(call(svc, "review_sign_off", one, batch=bid, attest=True)) == "REVIEW-UNFINISHED"
    _out, seen = work_through(svc, one, bid, attest=False)
    assert seen == 3
    assert code(call(svc, "review_sign_off", one, batch=bid)) == "REVIEW-NOT-ATTESTED"
    assert code(call(svc, "review_sign_off", one, batch=bid, attest="sure")) == "REVIEW-NOT-ATTESTED"
    ok(call(svc, "review_sign_off", one, batch=bid, attest=True))


def test_only_this_batch_s_two_reviewers_may_open_it(reviewed):
    svc, admin, bid, one, two = reviewed
    assert code(call(svc, "review_open", admin, batch=bid)) == "ROLE-DENIED"   # the doctor settles, not reviews
    assert code(call(svc, "review_note", admin, batch=bid)) == "ROLE-DENIED"


def test_progress_is_saved_as_they_go_and_survives_a_restart(reviewed, tmp_path):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    ok(call(svc, "review_save", one, batch=bid, note=nid,
            decision={"answers": {q["id"]: "unsure" for q in n["questions"]}, "fb": {"over": True}},
            confirm=True))
    svc.close()
    from tests.test_opal import make
    again = make(tmp_path)
    tok = ok(call(again, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    opened = ok(call(again, "review_open", tok, batch=bid))
    assert opened["done"] == 1 and opened["next"] != nid        # carries on at the next one
    back = ok(call(again, "review_note", tok, batch=bid, note=nid))
    assert back["confirmed"] is True and back["decision"]["fb"] == {"over": True}
    again.close()


def test_the_time_each_note_took_is_measured_by_the_app_not_the_screen(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    ok(call(svc, "review_note", one, batch=bid, note=nid))
    svc._clock.t += 45                                          # three quarters of a minute reading
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    ok(call(svc, "review_save", one, batch=bid, note=nid,
            decision={"answers": {q["id"]: "remove" for q in n["questions"]},
                      "seconds": 99999}, confirm=True))
    st = granite.load_decision(svc._batch_work(bid), "R1", nid)
    assert 0 < st["seconds"] < 1000                             # the screen's number is ignored
    assert svc.doc["pace"][ok(call(svc, "me", one))["id"]]["pages"] >= 1


def test_the_estimate_comes_from_that_persons_own_pace(reviewed):
    svc, admin, bid, one, two = reviewed
    uid = ok(call(svc, "me", one))["id"]
    svc.doc["pace"] = {uid: {"pages": 10, "seconds": 1800}}     # three minutes a page
    opened = ok(call(svc, "review_open", one, batch=bid))
    assert opened["minutes_left"] == int(opened["pages_left"] * 180 / 60)


# ---- the assessment before a batch starts --------------------------------

FILLER = ("Blood pressure stable at 128/78. Continue metformin 500 mg twice daily. "
          "Reviewed bloods from last visit; renal function unchanged. ")


def big_folder(tmp_path, patients=8, per_patient=9_000):   # about five pages each
    """Ordinary clinical prose, so the engine runs at its real pace."""
    root = tmp_path / "Big practice"
    root.mkdir()
    for i in range(patients):
        d = root / f"Patient {i:02d}"
        d.mkdir()
        body = (FILLER * (per_patient // len(FILLER) + 1))[:per_patient]
        (d / "note.txt").write_text(NOTE_A + body, encoding="utf-8")
    return root


def test_the_doctor_is_told_the_size_and_the_time_before_anything_starts(tmp_path):
    svc, admin = ready(tmp_path)
    with_people(svc, admin)
    folder = big_folder(tmp_path)
    ok(call(svc, "settings_set", admin, pages_per_part=20))     # a short sitting, for the test
    got = ok(call(svc, "batch_assess", admin, folder=str(folder)))
    assert got["patients"] == 8 and got["files"] == 8
    assert got["pages"] >= 30
    assert got["at_least_minutes"] >= 1
    assert got["one_part"] is False and len(got["parts"]) >= 2
    for p in got["parts"]:
        assert p["from"] <= p["to"] and p["patients"] >= 1 and p["at_least_minutes"] >= 1
    # Contiguous whole patients, every one accounted for, none in two parts.
    covered = [n for p in got["parts"] for n in range(p["from"], p["to"] + 1)]
    assert covered == list(range(1, got["patients"] + 1))
    svc.close()


def test_the_assessment_quotes_the_accuracy_with_its_test_sets(tmp_path):
    svc, admin = ready(tmp_path)
    with_people(svc, admin)
    got = ok(call(svc, "batch_assess", admin, folder=str(a_folder(tmp_path))))
    acc = got["accuracy"]
    assert acc["as_of"] and acc["plain"]
    assert any("90.5%" in x["result"] and "never used for tuning" in x["test"] for x in acc["lines"])
    assert not any("98.7%" in x["result"] for x in acc["lines"])    # a retired figure, never shown again
    assert all(x["test"] and x["result"] for x in acc["lines"])   # never a figure without its set
    # A perfect score is never shown to a reviewer: it would teach them the
    # tool catches everything, which is the opposite of what a review is for.
    assert not any("100%" in x["result"] for x in acc["lines"])
    svc.close()


def test_a_small_folder_is_one_part_and_the_practice_sets_the_size(tmp_path):
    svc, admin = ready(tmp_path)
    with_people(svc, admin)
    small = ok(call(svc, "batch_assess", admin, folder=str(a_folder(tmp_path))))
    assert small["one_part"] is True and len(small["parts"]) == 1

    folder = big_folder(tmp_path, patients=8)
    wide = ok(call(svc, "batch_assess", admin, folder=str(folder)))
    ok(call(svc, "settings_set", admin, pages_per_part=20))
    narrow = ok(call(svc, "batch_assess", admin, folder=str(folder)))
    assert narrow["per_part"] == 20
    assert len(narrow["parts"]) >= len(wide["parts"])          # smaller sittings, more parts
    assert all(p["patients"] >= 1 for p in narrow["parts"])    # never smaller than one patient
    for bad in (0, 5, 2000, "lots"):
        assert code(call(svc, "settings_set", admin, pages_per_part=bad)) == "APP-BAD-REQUEST"
    svc.close()


def test_each_part_becomes_its_own_batch_covering_its_own_patients(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = big_folder(tmp_path, patients=6)
    ok(call(svc, "settings_set", admin, pages_per_part=20))
    plan = ok(call(svc, "batch_assess", admin, folder=str(folder)))
    assert len(plan["parts"]) >= 2
    made = []
    for i, part in enumerate(plan["parts"], 1):
        made.append(ok(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                            confirmed=[k for k, _ in opal.Service.CARE_POINTS],
                            part={"from": part["from"], "to": part["to"],
                                  "fingerprint": part["fingerprint"]},
                            part_label=f"Part {i} of {len(plan['parts'])}")))
    assert made[0]["queued"] is False and all(m["queued"] for m in made[1:])
    for m in made:
        finish(svc, m["id"], timeout=300)
    seen = 0
    for m, part in zip(made, plan["parts"]):
        st = ok(call(svc, "batch_status", admin, batch=m["id"]))
        assert st["stage"] == "ready_for_review"
        assert st["counts"]["patients"] == part["patients"]
        assert st["part_label"].startswith("Part ")
        seen += st["counts"]["patients"]
    assert seen == plan["patients"]                       # every patient, exactly once
    svc.close()


def test_the_reviewer_is_told_the_size_and_a_floor_for_the_time(reviewed):
    svc, admin, bid, one, two = reviewed
    opened = ok(call(svc, "review_open", one, batch=bid))
    assert opened["pages_left"] >= 1 and opened["at_least_minutes"] >= 1
    assert opened["at_least_minutes"] <= max(1, opened["minutes_left"])   # a floor, not a ceiling
    assert opened["accuracy"]["lines"] and opened["pages"] >= 1


# ---- review findings, each one a test ------------------------------------------

def test_a_confirmed_note_cannot_be_quietly_emptied_afterwards(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    ok(call(svc, "review_save", one, batch=bid, note=nid, confirm=True,
            decision={"answers": {q["id"]: "remove" for q in n["questions"]}}))
    # Saving again would let the answers be wiped while the note still counts
    # as done, and the sign-off statement would then be untrue.
    assert code(call(svc, "review_save", one, batch=bid, note=nid,
                     decision={})) == "REVIEW-NOTE-CONFIRMED"
    st = granite.load_decision(svc._batch_work(bid), "R1", nid)
    assert st["answers"] and st["confirmed_at"]


def test_sign_off_reads_every_note_again_rather_than_trusting_the_count(reviewed):
    svc, admin, bid, one, two = reviewed
    work_through(svc, one, bid, attest=False)
    # A decision emptied behind the app's back is caught at the moment of signing.
    work = svc._batch_work(bid)
    nid = [i.note_id for i in granite.load_items(work) if i.note_id][0]
    st = granite.load_decision(work, "R1", nid)
    if not ok(call(svc, "review_note", one, batch=bid, note=nid))["questions"]:
        pytest.skip("this note left the tool in no doubt")
    st["answers"] = {}
    granite.save_decision(work, "R1", nid, st)
    assert code(call(svc, "review_sign_off", one, batch=bid, attest=True)) == "REVIEW-UNANSWERED"


def test_a_reviewer_is_never_told_the_other_has_finished(reviewed):
    svc, admin, bid, one, two = reviewed
    work_through(svc, one, bid)
    seen = ok(call(svc, "batch_status", two, batch=bid))
    assert "signed_off" not in seen and "reviews_in" not in seen
    rows = ok(call(svc, "batch_list", two))["batches"]
    assert all("signed_off" not in r for r in rows)
    assert "Jordan" not in str(seen) or "Jordan" in str(seen.get("reviewers"))
    # The doctor, who settles the batch, does see it.
    mine = ok(call(svc, "batch_status", admin, batch=bid))
    assert mine["signed_off"]["R1"]["name"] == "Jordan Lee" and mine["reviews_in"] == 1


def test_a_lock_keeps_the_reading_time_already_spent(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    svc._clock.t += 50                                          # read for most of a minute
    ok(call(svc, "lock_now", one))                              # then the screen locks
    svc._clock.t += 600                                         # ten minutes away from the desk
    ok(call(svc, "unlock", one, password=GOOD_PW, code=phone_now(svc)))
    ok(call(svc, "review_save", one, batch=bid, note=nid, confirm=True,
            decision={"answers": {q["id"]: "remove" for q in n["questions"]}}))
    st = granite.load_decision(svc._batch_work(bid), "R1", nid)
    assert 45 <= st["seconds"] < 120                            # kept, and the time away left out


def test_one_reviewers_lock_does_not_touch_the_others_clock(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    ok(call(svc, "review_open", two, batch=bid))
    ok(call(svc, "review_note", two, batch=bid, note=nid))
    svc._clock.t += 40
    ok(call(svc, "lock_now", two))                              # the other reviewer walks away
    ok(call(svc, "review_save", one, batch=bid, note=nid, confirm=True,
            decision={"answers": {q["id"]: "remove" for q in n["questions"]}}))
    st = granite.load_decision(svc._batch_work(bid), "R1", nid)
    assert st["seconds"] >= 40                                  # their own reading still counted


def test_cancelling_one_batch_does_not_stop_the_ones_waiting(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = big_folder(tmp_path, patients=4)
    plan = ok(call(svc, "batch_assess", admin, folder=str(folder)))
    made = [ok(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                    confirmed=[k for k, _ in opal.Service.CARE_POINTS],
                    part={"from": p["from"], "to": p["to"], "fingerprint": p["fingerprint"]}))
            for p in plan["parts"]]
    if len(made) < 2:
        pytest.skip("this folder is one part")
    ok(call(svc, "batch_cancel", admin, batch=made[0]["id"]))
    for m in made[1:]:
        finish(svc, m["id"], timeout=300)               # the rest still run
        assert svc.doc["batches"][m["id"]]["stage"] == "ready_for_review"
    svc.close()


def test_a_part_is_refused_if_the_folder_changed_since_it_was_planned(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = big_folder(tmp_path, patients=4)
    plan = ok(call(svc, "batch_assess", admin, folder=str(folder)))
    newcomer = folder / "AAA new patient"
    newcomer.mkdir()
    (newcomer / "note.txt").write_text(NOTE_A, encoding="utf-8")   # sorts first, shifting everyone
    part = plan["parts"][0]
    b = ok(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                confirmed=[k for k, _ in opal.Service.CARE_POINTS],
                part={"from": part["from"], "to": part["to"], "fingerprint": part["fingerprint"]}))
    finish(svc, b["id"], timeout=300)
    rec = svc.doc["batches"][b["id"]]
    assert rec["stage"] == "failed" and rec["error"] == "BATCH-FOLDER-CHANGED"
    svc.close()


def test_a_patient_keeps_the_same_number_whichever_part_they_are_in(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = big_folder(tmp_path, patients=6)
    plan = ok(call(svc, "batch_assess", admin, folder=str(folder)))
    if len(plan["parts"]) < 2:
        pytest.skip("this folder is one part")
    seen = set()
    for p in plan["parts"]:
        b = ok(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                    confirmed=[k for k, _ in opal.Service.CARE_POINTS],
                    part={"from": p["from"], "to": p["to"], "fingerprint": p["fingerprint"]}))
        finish(svc, b["id"], timeout=300)
        nums = {i.patient for i in granite.load_items(svc._batch_work(b["id"]))}
        assert nums == set(range(p["from"], p["to"] + 1))        # their place in the whole folder
        assert not (nums & seen)                                 # and never two patients with one number
        seen |= nums
    svc.close()


def test_the_pace_counts_each_note_once(reviewed):
    svc, admin, bid, one, two = reviewed
    uid = ok(call(svc, "me", one))["id"]
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    answers = {"answers": {q["id"]: "remove" for q in n["questions"]}}
    ok(call(svc, "review_save", one, batch=bid, note=nid, decision=answers, confirm=True))
    first = dict(svc.doc["pace"][uid])
    for _ in range(5):
        call(svc, "review_save", one, batch=bid, note=nid, decision=answers, confirm=True)
    assert svc.doc["pace"][uid] == first


def test_a_reviewer_can_go_back_to_an_earlier_note_before_signing_off(reviewed):
    """Realising on note three that note one was answered wrongly is the
    normal case, not an edge case."""
    svc, admin, bid, one, two = reviewed
    opened = ok(call(svc, "review_open", one, batch=bid))
    order = [n["id"] for n in opened["notes"]]
    assert len(order) == 3 and all(n["done"] is False for n in opened["notes"])

    first = ok(call(svc, "review_note", one, batch=bid, note=order[0]))
    ok(call(svc, "review_save", one, batch=bid, note=order[0], confirm=True,
            decision={"answers": {q["id"]: "remove" for q in first["questions"]}}))
    second = ok(call(svc, "review_note", one, batch=bid, note=order[1]))
    ok(call(svc, "review_save", one, batch=bid, note=order[1], confirm=True,
            decision={"answers": {q["id"]: "remove" for q in second["questions"]}}))

    # Back to the first one: it is still theirs to change, and their answers
    # are there as they left them.
    again = ok(call(svc, "review_note", one, batch=bid, note=order[0]))
    assert again["confirmed"] is True
    # Confirmed notes are sealed against accidental change: taking one back
    # is deliberate, and counted.
    assert code(call(svc, "review_save", one, batch=bid, note=order[0], confirm=True,
                     decision={"answers": {}})) == "REVIEW-NOTE-CONFIRMED"
    if first["questions"]:
        assert again["decision"]["answers"]
        assert ok(call(svc, "review_reopen", one, batch=bid, note=order[0]))["times"] == 1
        changed = {q["id"]: "fine" for q in first["questions"]}
        ok(call(svc, "review_save", one, batch=bid, note=order[0], confirm=True,
                decision={"answers": changed}))
        after = ok(call(svc, "review_note", one, batch=bid, note=order[0]))
        assert set(after["decision"]["answers"].values()) == {"fine"}
        assert svc.doc["batches"][bid]["reopened"] == 1
        kinds = [e["event"] for e in svc.log.tail(80)]
        assert "review_reopened" in kinds
    listed = ok(call(svc, "review_open", one, batch=bid))["notes"]
    assert [n["done"] for n in listed] == [True, True, False]


def test_once_they_have_signed_off_nothing_can_be_changed(reviewed):
    svc, admin, bid, one, two = reviewed
    work_through(svc, one, bid)
    nid = ok(call(svc, "review_open", one, batch=bid))["notes"][0]["id"]
    assert code(call(svc, "review_reopen", one, batch=bid, note=nid)) == "REVIEW-SIGNED"
    assert code(call(svc, "review_save", one, batch=bid, note=nid, confirm=True,
                     decision={"answers": {}})) in ("REVIEW-SIGNED", "REVIEW-NOTE-CONFIRMED",
                                                    "REVIEW-NOT-READY")


# ---------------------------------------------------------------------------
# The reading room: a break, and the way out
# ---------------------------------------------------------------------------

def test_a_break_covers_the_screen_and_their_password_brings_it_back(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    ok(call(svc, "review_note", one, batch=bid, note=nid))
    out = ok(call(svc, "review_pause", one, batch=bid))
    assert out["paused"] is True and out["grace_minutes"] >= 1
    # While it is covered, the notes are not readable.
    assert code(call(svc, "review_note", one, batch=bid, note=nid)) == "SESSION-LOCKED"
    assert code(call(svc, "review_resume", one, batch=bid, password="wrong one")) == "AUTH-FAILED"
    assert ok(call(svc, "review_resume", one, batch=bid, password=GOOD_PW))["paused"] is False
    assert ok(call(svc, "review_note", one, batch=bid, note=nid))["text"]
    st = svc.doc["batches"][bid]["sitting"]["R1"]
    assert st["pauses"] == 1 and st["paused_s"] >= 0


def test_a_long_break_becomes_the_real_lock(reviewed):
    svc, admin, bid, one, two = reviewed
    ok(call(svc, "review_open", one, batch=bid))
    ok(call(svc, "review_pause", one, batch=bid))
    svc._clock.t += (opal.Service.PAUSE_GRACE_MIN * 60) + 5
    assert code(call(svc, "review_resume", one, batch=bid, password=GOOD_PW)) == "SESSION-LOCKED"


def test_leaving_part_way_closes_the_pool_signs_them_out_and_says_so(reviewed):
    svc, admin, bid, one, two = reviewed
    nid = ok(call(svc, "review_open", one, batch=bid))["next"]
    n = ok(call(svc, "review_note", one, batch=bid, note=nid))
    ok(call(svc, "review_save", one, batch=bid, note=nid, confirm=True,
            decision={"answers": {q["id"]: "remove" for q in n["questions"]}}))
    out = ok(call(svc, "review_leave", one, batch=bid))
    assert out["left"] is True and out["done"] == 1 and out["of"] == 3
    # Signed out, pool closed, and on the record.
    assert code(call(svc, "review_open", one, batch=bid)) == "SESSION-ENDED"
    assert not svc._pool_keys
    st = svc.doc["batches"][bid]["sitting"]["R1"]
    assert st["left"] == 1 and st["left_after"] == 1 and st["left_at"]
    assert "review_left" in [e["event"] for e in svc.log.tail(40)]
    # And nothing they decided was lost.
    again = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    assert ok(call(svc, "review_open", again, batch=bid))["done"] == 1


def test_a_blocked_copy_or_a_lost_window_is_counted_never_scolded(reviewed):
    svc, admin, bid, one, two = reviewed
    ok(call(svc, "review_open", one, batch=bid))
    ok(call(svc, "review_away", one, batch=bid, what="away"))
    ok(call(svc, "review_away", one, batch=bid, what="blocked"))
    ok(call(svc, "review_away", one, batch=bid, what="blocked"))
    assert code(call(svc, "review_away", one, batch=bid, what="screenshot")) == "APP-BAD-REQUEST"
    st = svc.doc["batches"][bid]["sitting"]["R1"]
    assert st["away"] == 1 and st["blocked"] == 2
