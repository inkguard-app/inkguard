"""Batches: files read where they are, work kept encrypted and erasable.

The two rules this file exists to hold:
  1. the practice's own files are never moved, changed or deleted;
  2. everything the app makes is encrypted under that batch's own key, and
     destroying the key makes it unreadable at once.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import zipfile

import pytest

from inkguard import granite, opal
from inkguard.security import KeyStore
from tests.test_opal import (GOOD_PW, TODAY, Clock, call, code, make, ok, phone_now, ready, SETUP)

CONFIRMED = [k for k, _ in opal.Service.CARE_POINTS]   # the points a doctor ticks before starting

NOTE_A = """Discharge summary
Patient: Martha Ellison, MRN 4481203, date of birth 14 March 1952.
Seen at Lakeside Family Practice on 2 April 2026 by Dr. Sam Rivera.
Telephone 555-0147. Lives at 18 Carlton Road, Ashford.
Plan: continue metformin, review in six weeks.
Walks by the Lakeside path most days.
"""
# The last line keeps one word the tool is unsure of (a place it cannot
# place), so the review tests always have a question to answer. ("Lakeside
# Family Practice" itself is removed with confidence as a named place of care.)
NOTE_B = """Follow-up note
Mrs Ellison returned on 14 May 2026. Blood sugar improved.
Her daughter Priya drove her to the appointment.
"""
NOTE_C = """Clinic letter
Mr Daniel Okonkwo, MRN 7782910, attended on 3 June 2026.
Referred by Dr. Amina Yusuf at Riverside Surgery.
"""


def a_folder(tmp_path):
    """One folder per patient, plus the kinds of file a real drop-in holds."""
    root = tmp_path / "Patient files"
    (root / "Ellison, Martha").mkdir(parents=True)
    (root / "Okonkwo, Daniel").mkdir(parents=True)
    (root / "Ellison, Martha" / "discharge.txt").write_text(NOTE_A, encoding="utf-8")
    (root / "Ellison, Martha" / "follow-up.md").write_text(NOTE_B, encoding="utf-8")
    (root / "Okonkwo, Daniel" / "letter.txt").write_text(NOTE_C, encoding="utf-8")
    (root / "Okonkwo, Daniel" / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 64)
    (root / "Ellison, Martha" / ".DS_Store").write_bytes(b"\0" * 16)
    (root / "old-record.doc").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    return root


def snapshot(folder):
    """Exactly what is in the practice's folder, to the byte."""
    return {str(p.relative_to(folder)): (p.stat().st_size, p.read_bytes())
            for p in sorted(folder.rglob("*")) if p.is_file()}


def with_people(svc, admin, *, certified=True):
    """Two reviewers, as a practice would have them: both have passed the
    practice batch, because a reviewer without a current badge cannot be put
    on a real one (test_practice.py holds that rule)."""
    ok(call(svc, "account_create", admin, name="Jordan Lee", roles=["reviewer"], password=GOOD_PW))
    ok(call(svc, "account_create", admin, name="Casey Park", roles=["reviewer"], password=GOOD_PW))
    accounts = {a["name"]: a["id"] for a in ok(call(svc, "account_list", admin))["accounts"]}
    out = [accounts["Jordan Lee"], accounts["Casey Park"]]
    if certified:
        for uid in out:
            svc.doc["accounts"][uid]["badge"] = {
                "earned": svc._today().isoformat(),
                "until": (svc._today() + dt.timedelta(days=200)).isoformat(),
                "planted_found": 3, "planted_of": 3, "over_removed": 0, "notes": 4}
            svc.doc["accounts"][uid]["certified"] = True
    link_phones(svc)
    return out


def link_phones(svc):
    """Give every account a linked phone, as a practice must before a batch:
    coming back to open patient work needs that person's own code, so nobody
    can be put on a batch without one (test_opal holds that rule). The
    record is written straight in, exactly as a confirmed link leaves it,
    rather than walking the authenticator flow in every fixture."""
    for acc in svc.doc["accounts"].values():
        acc.setdefault("totp", {"secret": ("ab" * 20), "last_step": -1,
                                "linked": "2026-10-01T00:00:00Z"})
    svc._save()


def finish(svc, bid, timeout=240):
    """Wait for the background preparation to end, as a screen would."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rec = svc.doc["batches"].get(bid)
        # Only this batch matters: with several parts queued, the next one
        # starts the moment this finishes.
        if rec and rec["stage"] in ("ready_for_review", "failed") and svc._working_on != bid:
            return rec
        time.sleep(0.2)
    raise AssertionError("the batch did not finish in time")


@pytest.fixture
def practice(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    yield svc, admin, reviewers, a_folder(tmp_path)
    svc.close()


# ---- the practice's own files -----------------------------------------------------------

def test_the_practice_files_are_read_where_they_are_and_never_touched(practice):
    svc, admin, reviewers, folder = practice
    before = snapshot(folder)
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    assert snapshot(folder) == before                      # byte for byte, nothing moved or added
    assert not (folder / "work").exists()


def test_every_file_is_accounted_for_with_its_reason(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    st = ok(call(svc, "batch_status", admin, batch=b["id"]))
    c = st["counts"]
    assert c["files"] == 6 and c["patients"] == 3          # the loose .doc is its own patient
    assert c["prepared"] == 3                              # three text notes
    assert c["system"] == 1                                # .DS_Store
    assert c["set_aside"] == 2                             # the image and the old .doc
    reasons = {r["id"]: r["reason"] for r in st["set_aside"]}
    assert any("image or scan" in r for r in reasons.values())
    assert any(".doc" in r for r in reasons.values())
    assert c["pages"] >= 1 and st["pages"] == c["pages"]


# ---- the encrypted, erasable work area --------------------------------------------------

def test_patient_text_is_only_ever_in_the_encrypted_work_area(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    secrets_in_notes = [b"Ellison", b"4481203", b"Carlton Road", b"Okonkwo", b"Patient files"]
    for p in (svc.dir).rglob("*"):
        if p.is_file():
            raw = p.read_bytes()
            for needle in secrets_in_notes:
                assert needle not in raw, f"{needle!r} readable in {p.name}"
    # and the app's own records carry no path, name or text at all
    rec = json.dumps(svc.doc["batches"][b["id"]])
    for needle in ("Ellison", "Okonkwo", "Patient files", str(folder)):
        assert needle not in rec


def test_destroying_the_batch_key_makes_its_work_unreadable_at_once(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    work = svc._batch_work(b["id"])
    assert work.get("folder")["path"] == str(folder)       # readable while the key exists
    svc._anchor.delete(f"{svc._anchor_acct}-batch-{b['id']}")
    svc._pool_keys.clear()                                 # as after a lock, or a restart
    with pytest.raises(opal.ServiceError) as e:
        svc._batch_work(b["id"])
    assert e.value.code == "BATCH-UNREADABLE"
    assert code(call(svc, "batch_status", admin, batch=b["id"])) == "BATCH-UNREADABLE"


def test_cancelling_erases_the_key_and_the_files_but_not_the_originals(practice):
    svc, admin, reviewers, folder = practice
    before = snapshot(folder)
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    area = svc.dir / "work" / b["id"]
    assert area.exists()
    out = ok(call(svc, "batch_cancel", admin, batch=b["id"]))
    assert out["key_destroyed"] and out["files_removed"] >= 1
    assert not area.exists()
    assert svc._anchor.read(f"{svc._anchor_acct}-batch-{b['id']}") is None
    assert code(call(svc, "batch_status", admin, batch=b["id"])) == "BATCH-GONE"
    assert snapshot(folder) == before
    events = [e["event"] for e in ok(call(svc, "security_log", admin, last=200))["events"]]
    assert "batch_erased" in events


def test_retiring_the_computer_erases_every_batch_key(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    ok(call(svc, "retire_here", admin, password=GOOD_PW, confirm="RETIRE",
            code=phone_now(svc)))
    assert svc._anchor.read(f"{svc._anchor_acct}-batch-{b['id']}") is None
    assert not (svc.dir / "work" / b["id"]).exists()


# ---- who may start one, and with whom ---------------------------------------------------

def test_a_batch_needs_two_reviewers_and_the_practice_chooses_two_or_three_people(practice):
    svc, admin, reviewers, folder = practice
    me = ok(call(svc, "me", admin))["id"]
    bad = [{"reviewers": [reviewers[0]]}, {"reviewers": [reviewers[0], reviewers[0]]},
           {"reviewers": [reviewers[0], "U000000"]}, {"reviewers": "both of them"}]
    for args in bad:
        assert code(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), **args)) == "BATCH-REVIEWERS"
    # Two people (the default): the clinician starting the batch may be one of
    # the reviewers. Three people: never.
    assert code(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder),
                     reviewers=[reviewers[0], me])) != "BATCH-REVIEWERS"
    ok(call(svc, "settings_set", admin, review_people=3))
    assert code(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder),
                     reviewers=[reviewers[0], me])) == "BATCH-REVIEWERS"
    assert code(call(svc, "settings_set", admin, review_people=4)) == "APP-BAD-REQUEST"
    rid = [a for a in ok(call(svc, "account_list", admin))["accounts"] if a["id"] == reviewers[1]][0]["id"]
    ok(call(svc, "account_disable", admin, id=rid))
    assert code(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder),
                     reviewers=reviewers)) == "BATCH-REVIEWERS"


def test_a_reviewer_cannot_start_a_batch_and_sees_only_their_own(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    rev = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    assert code(call(svc, "batch_create", rev, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers)) == "ROLE-DENIED"
    assert [x["id"] for x in ok(call(svc, "batch_list", rev))["batches"]] == [b["id"]]
    ok(call(svc, "account_create", admin, name="Robin Shah", roles=["reviewer"], password=GOOD_PW))
    stranger = ok(call(svc, "sign_in", name="Robin Shah", password=GOOD_PW))["session"]
    assert ok(call(svc, "batch_list", stranger))["batches"] == []
    assert code(call(svc, "batch_status", stranger, batch=b["id"])) == "ROLE-DENIED"


def test_a_folder_that_is_empty_wrong_or_unreadable_says_so_plainly(practice, tmp_path):
    svc, admin, reviewers, _folder = practice
    empty = tmp_path / "empty"; empty.mkdir()
    assert code(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(empty), reviewers=reviewers)) == "BATCH-EMPTY"
    assert code(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(tmp_path / "nope"),
                     reviewers=reviewers)) == "BATCH-FOLDER"
    assert code(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder="relative/path",
                     reviewers=reviewers)) == "BATCH-FOLDER"
    pics = tmp_path / "pics"; pics.mkdir()
    (pics / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert code(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(pics),
                     reviewers=reviewers)) == "BATCH-NOTHING-READ"


# ---- the intake guard, inside a batch ----------------------------------------------------

def test_the_guard_sets_aside_a_file_that_lies_about_its_type(practice, tmp_path):
    svc, admin, reviewers, folder = practice
    (folder / "Okonkwo, Daniel" / "report.pdf").write_bytes(b"MZ\x90\x00" + b"\0" * 200)
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    st = ok(call(svc, "batch_status", admin, batch=b["id"]))
    guarded = [r for r in st["set_aside"] if r["code"].startswith("ZC-")]
    assert guarded and "not a document" in guarded[0]["reason"]
    assert st["counts"]["prepared"] == 3                   # the ordinary notes still went through


# ---- saving progress, resuming, and time limits ------------------------------------------

def test_a_batch_interrupted_part_way_carries_on_where_it_stopped(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    svc._stop_work.set()                                    # as if the app were closed mid-batch
    if svc._worker:
        svc._worker.join(timeout=120)
    svc._working_on, svc._worker = None, None
    items = granite.load_items(svc._batch_work(b["id"]))
    done_before = sum(1 for i in items if i.outcome != "pending")
    ok(call(svc, "batch_resume", admin, batch=b["id"]))
    rec = finish(svc, b["id"])
    assert rec["stage"] == "ready_for_review"
    assert rec["counts"]["prepared"] == 3 and done_before <= rec["progress"]["done"]


def test_work_nobody_finished_in_time_is_erased_and_the_files_remain(practice):
    svc, admin, reviewers, folder = practice
    before = snapshot(folder)
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    rec = svc.doc["batches"][b["id"]]
    rec["due"] = (TODAY - dt.timedelta(days=1)).isoformat()   # the doctor's time ran out
    assert ok(call(svc, "batch_list", admin))["batches"] == []
    assert not (svc.dir / "work" / b["id"]).exists()
    assert snapshot(folder) == before
    why = [e for e in ok(call(svc, "security_log", admin, last=200))["events"]
           if e["event"] == "batch_erased"]
    assert "time ran out" in why[-1]["detail"]["why"]


def test_the_doctor_gets_one_extension_and_reminders_near_the_end(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    assert code(call(svc, "batch_extend", admin, batch=b["id"])) == "BATCH-NO-EXTENSION"
    svc.doc["batches"][b["id"]]["due"] = (TODAY + dt.timedelta(days=3)).isoformat()
    st = ok(call(svc, "batch_status", admin, batch=b["id"]))
    assert st["days_left"] == 3 and st["remind"] is True
    out = ok(call(svc, "batch_extend", admin, batch=b["id"]))
    assert out["days_left"] == 3 + opal.REVIEW_EXTENSION_DAYS and out["extended"] is True
    assert code(call(svc, "batch_extend", admin, batch=b["id"])) == "BATCH-NO-EXTENSION"


def test_the_backstop_catches_a_batch_the_reviewers_never_finished(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    rec = svc.doc["batches"][b["id"]]
    assert rec["due"] is None and rec["backstop"] == (
        TODAY + dt.timedelta(days=opal.BATCH_BACKSTOP_DAYS)).isoformat()
    rec["backstop"] = (TODAY - dt.timedelta(days=1)).isoformat()
    assert ok(call(svc, "batch_list", admin))["batches"] == []


def test_the_practice_sets_its_own_review_time_within_limits(practice):
    svc, admin, _reviewers, _folder = practice
    assert ok(call(svc, "batch_list", admin))["review_days"] == opal.REVIEW_DAYS_DEFAULT
    ok(call(svc, "settings_set", admin, review_days=21))
    assert ok(call(svc, "batch_list", admin))["review_days"] == 21
    for bad in (0, 6, 31, 365, True, "forever"):
        assert code(call(svc, "settings_set", admin, review_days=bad)) == "APP-BAD-REQUEST"
    assert ok(call(svc, "status", admin))["review_days"] == 21


# ---- one at a time, and the window never waits -------------------------------------------

def test_batches_are_prepared_one_at_a_time_and_the_rest_wait_their_turn(practice, tmp_path):
    svc, admin, reviewers, folder = practice
    second = tmp_path / "More files"; second.mkdir()
    (second / "note.txt").write_text(NOTE_C, encoding="utf-8")
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    # A second batch is not refused: it waits its turn and starts on its own
    # when the first finishes, which is how the parts of a big folder run.
    other = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(second),
                    reviewers=reviewers))
    assert other["queued"] is True and other["stage"] == "preparing"
    finish(svc, b["id"])
    finish(svc, other["id"])
    assert svc.doc["batches"][other["id"]]["stage"] == "ready_for_review"


def test_the_service_keeps_answering_while_a_batch_is_prepared(practice):
    svc, admin, reviewers, folder = practice
    ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    slowest = 0.0
    for _ in range(25):                                     # as the screens poll
        t = time.monotonic()
        ok(call(svc, "status", admin))
        slowest = max(slowest, time.monotonic() - t)
        time.sleep(0.05)
    assert slowest < 2.0, f"a status request waited {slowest:.1f}s on the batch"


# ---- the pieces on their own --------------------------------------------------------------

def test_the_work_area_refuses_a_changed_file_or_a_foreign_key(tmp_path):
    key = os.urandom(32)
    w = granite.WorkArea(tmp_path / "w", key)
    w.put("note-n00001", {"text": "Martha Ellison"})
    assert w.get("note-n00001")["text"] == "Martha Ellison"
    raw = bytearray((tmp_path / "w" / "note-n00001.bin").read_bytes())
    assert b"Ellison" not in bytes(raw)
    raw[-2] ^= 1
    (tmp_path / "w" / "note-n00001.bin").write_bytes(bytes(raw))
    for area in (w, granite.WorkArea(tmp_path / "w", os.urandom(32))):
        with pytest.raises(granite.BatchError) as e:
            area.get("note-n00001")
        assert e.value.code == "BATCH-UNREADABLE"
    for bad in ("../escape", "note/n1", "", "x" * 90):
        with pytest.raises(granite.BatchError):
            w.put(bad, {})


def test_pages_are_counted_the_way_a_person_would_judge_them():
    assert granite.pages(0) == 0 and granite.pages(1) == 1
    assert granite.pages(granite.CHARS_PER_PAGE * 10) == 10


def test_a_word_document_is_read_without_changing_it(practice):
    svc, admin, reviewers, folder = practice
    docx = folder / "Okonkwo, Daniel" / "clinic.docx"
    _write_docx(docx, "Mr Daniel Okonkwo attended on 3 June 2026 with Dr. Amina Yusuf.")
    before = docx.read_bytes()
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    assert docx.read_bytes() == before
    st = ok(call(svc, "batch_status", admin, batch=b["id"]))
    assert st["counts"]["prepared"] == 4                    # the Word file was read too


def _write_docx(path, text):
    body = ("<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    rels = ("<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rId1' Target='word/document.xml' "
            "Type='http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
            "officeDocument'/></Relationships>")
    ctypes = ("<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
              "<Default Extension='xml' ContentType='application/xml'/>"
              "<Override PartName='/word/document.xml' ContentType='application/vnd."
              "openxmlformats-officedocument.wordprocessingml.document.main+xml'/></Types>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", ctypes)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", body)


# ---- the pool: what went in, what came out, what is left ---------------------------------

def test_the_pool_keeps_a_running_account_of_what_is_inside(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    pool = ok(call(svc, "batch_status", admin, batch=b["id"]))["pool"]
    assert pool["files_in"] == 6 and pool["notes_in"] == 3
    assert pool["notes_out"] == 0 and pool["in_pool"] == 3     # nothing has come out yet
    led = granite.WorkArea.ledger(svc._batch_work(b["id"]))
    assert led["opened"] and [e["event"] for e in led["events"]][:2] == ["pool_opened", "files_in"]


def test_emptying_the_pool_is_recorded_in_the_security_log(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    ok(call(svc, "batch_cancel", admin, batch=b["id"]))
    erased = [e for e in ok(call(svc, "security_log", admin, last=200))["events"]
              if e["event"] == "batch_erased"][-1]
    assert erased["detail"]["pool"]["in_pool"] == 3            # what was destroyed, counted
    assert erased["detail"]["key_destroyed"] is True


# ---- review findings, each one a test ------------------------------------------

def test_closing_the_app_never_lets_go_while_the_work_is_still_writing(practice, monkeypatch):
    svc, admin, reviewers, folder = practice
    started, release = threading.Event(), threading.Event()
    real = granite.prepare_patient

    def slow(*args, **kw):
        started.set()
        release.wait(20)
        return real(*args, **kw)

    monkeypatch.setattr(granite, "prepare_patient", slow)
    ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    assert started.wait(60)
    worker = svc._worker
    closer = threading.Thread(target=svc.close)
    closer.start()
    time.sleep(0.5)
    assert worker.is_alive()                        # still writing: the folder is not handed over
    release.set()
    closer.join(timeout=120)
    assert not worker.is_alive()
    opal.Service(svc.dir, keystore=KeyStore(root_key=b"r" * 32),
                 anchor=opal.FolderAnchor(svc.dir.parent / "keychain"),
                 clock=Clock(), wall=Clock(), today=lambda: TODAY).close()   # free now


def test_the_doctors_clock_starts_when_the_reviewers_finish(practice):
    svc, admin, reviewers, folder = practice
    ok(call(svc, "settings_set", admin, review_days=10))
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    rec = svc.doc["batches"][b["id"]]
    assert rec["due"] is None
    st = ok(call(svc, "batch_status", admin, batch=b["id"]))
    assert st["remind"] is False and st["days_left"] == opal.BATCH_BACKSTOP_DAYS
    svc._set_stage(b["id"], rec, "ready_for_doctor")           # the reviewers are done
    assert rec["due"] == (TODAY + dt.timedelta(days=10)).isoformat()
    assert ok(call(svc, "batch_status", admin, batch=b["id"]))["days_left"] == 10
    out = ok(call(svc, "batch_extend", admin, batch=b["id"]))
    assert out["days_left"] == 10 + opal.REVIEW_EXTENSION_DAYS


def test_a_folder_refused_at_the_start_leaves_no_key_and_no_work_behind(practice, tmp_path,
                                                                        monkeypatch):
    svc, admin, reviewers, folder = practice
    monkeypatch.setattr(granite, "save_items", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    before = set((svc.dir / "work").glob("*")) if (svc.dir / "work").exists() else set()
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    assert svc.doc["batches"][b["id"]]["stage"] == "failed"
    monkeypatch.undo()
    ok(call(svc, "batch_cancel", admin, batch=b["id"]))
    after = set((svc.dir / "work").glob("*")) if (svc.dir / "work").exists() else set()
    assert after == before


def test_work_out_of_time_is_erased_even_if_nobody_signs_in(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    svc.doc["batches"][b["id"]]["backstop"] = (TODAY - dt.timedelta(days=1)).isoformat()
    svc._save()
    ok(call(svc, "status"))                                     # the first screen, nobody signed in
    assert svc.doc["batches"] == {}
    assert not (svc.dir / "work" / b["id"]).exists()


def test_taking_stock_of_a_big_folder_does_not_hold_up_the_window(practice, tmp_path):
    svc, admin, reviewers, _folder = practice
    big = tmp_path / "Big folder"
    big.mkdir()
    for i in range(400):
        (big / f"note{i:04d}.txt").write_text(NOTE_C + "x" * 20000, encoding="utf-8")
    t = time.monotonic()
    ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(big), reviewers=reviewers))
    assert time.monotonic() - t < 2.0                           # the hashing happens in the background
    svc._stop_work.set()
    if svc._worker:
        svc._worker.join(timeout=120)


def test_a_restore_does_not_bring_back_batches_whose_keys_are_gone(practice, tmp_path):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    drive = tmp_path / "usb"; drive.mkdir()
    backup = ok(call(svc, "backup_create", admin, folder=str(drive)))["file"]
    svc.close()
    (tmp_path / "appdata" / "app_store.bin").unlink()
    svc2 = make(tmp_path)
    pre = ok(call(svc2, "restore_preview", file=backup, name="Dr. Sam Rivera",
                  password=GOOD_PW, code=phone_now(svc2)))
    assert any("not restored" in n for n in pre["notes"])
    ok(call(svc2, "restore_confirm", confirm=pre["confirm"]))
    tok = ok(call(svc2, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    assert ok(call(svc2, "batch_list", tok))["batches"] == []
    svc2.close()


def test_a_file_changed_after_the_batch_started_is_not_quietly_used(practice):
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    svc._stop_work.set()
    if svc._worker:
        svc._worker.join(timeout=120)
    svc._working_on, svc._worker = None, None
    work = svc._batch_work(b["id"])
    items = granite.load_items(work)
    for it in items:
        it.outcome = "pending"
        it.note_id = ""
    granite.save_items(work, items)
    (folder / "Okonkwo, Daniel" / "letter.txt").write_text(NOTE_C + "\nAdded later.\n", encoding="utf-8")
    ok(call(svc, "batch_resume", admin, batch=b["id"]))
    finish(svc, b["id"])
    aside = ok(call(svc, "batch_status", admin, batch=b["id"]))["set_aside"]
    assert any("changed on disk" in r["reason"] for r in aside)


def test_another_doctor_cannot_reach_into_a_batch_but_an_admin_can(practice):
    svc, admin, reviewers, folder = practice
    ok(call(svc, "account_create", admin, name="Dr. Lee Nakamura", roles=["doctor"], password=GOOD_PW))
    link_phones(svc)                       # nobody starts a batch without their own phone
    other = ok(call(svc, "sign_in", name="Dr. Lee Nakamura", password=GOOD_PW))["session"]
    b = ok(call(svc, "batch_create", other, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    me = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    svc.doc["accounts"][ok(call(svc, "me", me))["id"]]["roles"] = ["doctor"]   # a plain doctor
    assert code(call(svc, "batch_cancel", me, batch=b["id"])) == "ROLE-DENIED"
    svc.doc["accounts"][ok(call(svc, "me", me))["id"]]["roles"] = ["doctor", "admin"]
    ok(call(svc, "batch_cancel", me, batch=b["id"]))


# ---- the pool closes when the screen locks -------------------------------

def test_locking_the_screen_closes_the_pool_and_pauses_the_work(practice, monkeypatch):
    svc, admin, reviewers, folder = practice
    started, release = threading.Event(), threading.Event()
    real = granite.prepare_patient

    def slow(*args, **kw):
        started.set()
        release.wait(30)
        return real(*args, **kw)

    monkeypatch.setattr(granite, "prepare_patient", slow)
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    assert started.wait(60)
    assert svc._pool_keys                                   # a key is held while someone is here
    ok(call(svc, "lock_now", admin))
    assert svc._pool_keys == {}                             # the app lets go of it at once
    assert svc._pool_paused
    release.set()
    time.sleep(1.0)
    assert svc.doc["batches"][b["id"]]["stage"] == "processing"      # waiting, not working
    ok(call(svc, "unlock", admin, password=GOOD_PW))
    assert svc._pool_paused is None
    finish(svc, b["id"])


def test_a_practice_may_let_long_batches_carry_on_while_locked(practice):
    svc, admin, reviewers, folder = practice
    ok(call(svc, "settings_set", admin, prepare_while_locked=True))
    assert ok(call(svc, "status", admin))["prepare_while_locked"] is True
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    ok(call(svc, "lock_now", admin))
    assert svc._pool_paused is None                         # the work goes on; the screen is still locked
    assert svc._pool_keys == {}                             # but the app holds no key for the screens
    finish(svc, b["id"])


def test_getting_back_to_patient_work_needs_that_persons_own_phone_code(tmp_path, browser=None):
    """The nurse or biller who was looking at patient notes is the one who
    gets back in — with the code on their own phone, not just a password."""
    from tests.test_restore import _link_phone, phone_code
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    reviewers = with_people(svc, admin)
    folder = a_folder(tmp_path)
    for acc in svc.doc["accounts"].values():
        acc.pop("totp", None)              # this test links one for real, through the flow
    secret = _link_phone(svc, admin, wall)
    link_phones(svc)                       # the reviewers get the fixture's stub phone
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    ok(call(svc, "batch_status", admin, batch=b["id"]))      # patient work was open on this screen
    ok(call(svc, "lock_now", admin))
    assert code(call(svc, "unlock", admin, password=GOOD_PW)) == "PHONE-CODE-NEEDED"
    wall.t += opal.TOTP_STEP_S
    ok(call(svc, "unlock", admin, password=GOOD_PW, code=phone_code(secret, wall.t)))
    svc.close()


def test_a_screen_with_no_patient_work_open_unlocks_with_the_password(tmp_path):
    from tests.test_restore import _link_phone
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    _link_phone(svc, admin, wall)
    ok(call(svc, "lock_now", admin))
    ok(call(svc, "unlock", admin, password=GOOD_PW))         # nothing of the patients' was open
    svc.close()


def test_a_file_that_is_really_an_encoded_blob_is_set_aside_not_laboured_over(practice):
    """Writing has spaces. A long unbroken run is an embedded image or
    encoded data, and the engine slows sharply on one, so it is set aside
    with a plain reason instead of holding up the batch."""
    svc, admin, reviewers, folder = practice
    (folder / "Ellison, Martha" / "scan-data.txt").write_text(
        "Attachment follows\n" + "QUJDRA" * 2000, encoding="utf-8")
    started = time.monotonic()
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder),
                reviewers=reviewers))
    finish(svc, b["id"])
    assert time.monotonic() - started < 120
    aside = ok(call(svc, "batch_status", admin, batch=b["id"]))["set_aside"]
    blob = [r for r in aside if r["code"] == "BATCH-FILE-ENCODED"]
    assert blob and "no spaces" in blob[0]["reason"]
    assert ok(call(svc, "batch_status", admin, batch=b["id"]))["counts"]["prepared"] == 3


def test_a_wiped_batch_leaves_a_restart_report(practice):
    """The doctor may wipe everything and start again, and the wipe is
    documented: who, when, why, and how far the work had got."""
    svc, admin, reviewers, folder = practice
    b = ok(call(svc, "batch_create", admin, confirmed=CONFIRMED, folder=str(folder), reviewers=reviewers))
    finish(svc, b["id"])
    out = ok(call(svc, "batch_cancel", admin, batch=b["id"], reason="Reviewers used the wrong profile"))
    rep = out["report"]
    assert "# Restart report" in rep and "Reviewers used the wrong profile" in rep
    assert "Reviewer R1" in rep and "notes confirmed" in rep
    for word in ("Martha", "Ellison", "4481203", "Okonkwo"):
        assert word not in rep
    listed = ok(call(svc, "restart_reports", admin))["reports"]
    assert listed and listed[-1]["batch"] == b["id"] and listed[-1]["report"] == rep
    events = [e["event"] for e in ok(call(svc, "security_log", admin, last=200))["events"]]
    assert "batch_restart_report" in events and "batch_erased" in events
