"""The clinician settles the batch and the clean copies leave.

What the service holds here:
  1. nothing is exported while anything is still undecided;
  2. the clinician cannot overrule two reviewers on a certain removal;
  3. the small-numbers warnings must be answered before anything leaves;
  4. the pool is emptied only after every shape is written and verified,
     and a failed export destroys nothing.
"""
from __future__ import annotations

import zipfile

import pytest

from inkguard import granite, halite, opal
from tests.test_granite import a_folder, finish, link_phones, with_people
from tests.test_opal import GOOD_PW, call, code, make, ok, ready
from tests.test_review import work_through


@pytest.fixture
def for_doctor(tmp_path):
    """A batch both reviewers have signed off, waiting for the clinician."""
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = a_folder(tmp_path)
    b = ok(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                confirmed=[k for k, _ in opal.Service.CARE_POINTS]))
    finish(svc, b["id"])
    one = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    two = ok(call(svc, "sign_in", name="Casey Park", password=GOOD_PW))["session"]
    work_through(svc, one, b["id"])
    work_through(svc, two, b["id"])
    yield svc, admin, b["id"], folder, tmp_path / "finished"
    svc.close()


def settle_everything(svc, tok, bid) -> dict:
    """The clinician's whole pass: decide every disagreement."""
    opened = ok(call(svc, "settle_open", tok, batch=bid))
    for row in opened["notes"]:
        if row["status"] != "ready":
            continue
        n = ok(call(svc, "settle_note", tok, batch=bid, note=row["note"]))
        if n.get("bucket"):
            ok(call(svc, "settle_rule", tok, batch=bid, note=row["note"],
                    rulings={b["key"]: "remove" for b in n["bucket"]}, seconds=30))
    return opened


def answer_warnings(svc, tok, bid) -> dict:
    check = ok(call(svc, "settle_check", tok, batch=bid))
    ruling = {}
    for row in check["odd_one_out"]:
        for nid in row["notes"]:
            ruling[nid] = "accept"
    if ruling:
        ok(call(svc, "settle_small", tok, batch=bid, rulings=ruling))
    return check


def run_export(svc, tok, bid, out, shapes=("text",), arrangement="shuffled", **extra):
    ok(call(svc, "export", tok, batch=bid, shapes=list(shapes), arrangement=arrangement,
            out=str(out), attest=True, **extra))
    t = svc._export_thread
    if t is not None:
        t.join(300)
    return svc.doc["batches"].get(bid)


# ---------------------------------------------------------------------------
# Settling
# ---------------------------------------------------------------------------

def test_only_a_doctor_or_admin_can_settle(for_doctor):
    svc, admin, bid, _folder, _out = for_doctor
    one = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    assert code(call(svc, "settle_open", one, batch=bid)) == "ROLE-DENIED"


def test_the_clinician_sees_what_is_left_to_decide(for_doctor):
    svc, admin, bid, _folder, _out = for_doctor
    opened = ok(call(svc, "settle_open", admin, batch=bid))
    assert opened["batch"] == bid and len(opened["notes"]) == 3
    assert {s["id"] for s in opened["shapes"]} == set(halite.SHAPES)
    assert svc.doc["batches"][bid]["stage"] == "settling"


def test_the_clinician_reads_the_note_and_sees_both_reviewers_answers(for_doctor):
    svc, admin, bid, _folder, _out = for_doctor
    one = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    nid = ok(call(svc, "review_open", one, batch=bid))["notes"][0] \
        if False else ok(call(svc, "settle_open", admin, batch=bid))["notes"][0]["note"]
    n = ok(call(svc, "settle_note", admin, batch=bid, note=nid))
    assert n["text"] and n["status"] == "ready"
    for b in n.get("bucket", []):
        assert set(b["votes"]) == {"R1", "R2"} and b["word"]


def test_nothing_is_exported_while_something_is_undecided(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    one = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    # One reviewer's "unsure" puts an item in the clinician's bucket.
    opened = ok(call(svc, "settle_open", admin, batch=bid))
    undecided = [r for r in opened["notes"] if r["to_decide"]]
    if not undecided:
        pytest.skip("this batch produced no disagreements to decide")
    assert code(call(svc, "export", admin, batch=bid, shapes=["text"], arrangement="shuffled",
                     out=str(out), attest=True)) == "EXPORT-NOT-SETTLED"


def test_the_clinician_cannot_keep_what_both_reviewers_removed(for_doctor):
    svc, admin, bid, _folder, _out = for_doctor
    opened = ok(call(svc, "settle_open", admin, batch=bid))
    for row in opened["notes"]:
        if row["status"] != "ready":
            continue
        n = ok(call(svc, "settle_note", admin, batch=bid, note=row["note"]))
        locked = [b for b in n.get("bucket", []) if b["locked"]]
        if locked:
            assert code(call(svc, "settle_rule", admin, batch=bid, note=row["note"],
                             rulings={locked[0]["key"]: "keep"})) == "EXPORT-LOCKED"
            return


def test_a_ruling_for_an_item_that_does_not_exist_is_refused(for_doctor):
    svc, admin, bid, _folder, _out = for_doctor
    nid = ok(call(svc, "settle_open", admin, batch=bid))["notes"][0]["note"]
    assert code(call(svc, "settle_rule", admin, batch=bid, note=nid,
                     rulings={"not-a-key": "remove"})) == "APP-BAD-REQUEST"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def test_export_needs_the_signed_statement(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    assert code(call(svc, "export", admin, batch=bid, shapes=["text"],
                     arrangement="shuffled", out=str(out))) == "EXPORT-NOT-ATTESTED"


def test_export_needs_at_least_one_shape(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    assert code(call(svc, "export", admin, batch=bid, shapes=[], arrangement="shuffled",
                     out=str(out), attest=True)) == "EXPORT-NO-SHAPE"


def test_an_unanswered_small_numbers_warning_stops_the_export(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    check = ok(call(svc, "settle_check", admin, batch=bid))
    if not check["odd_one_out"]:
        pytest.skip("no odd-one-out warning in this batch")
    assert code(call(svc, "export", admin, batch=bid, shapes=["text"], arrangement="shuffled",
                     out=str(out), attest=True)) == "EXPORT-SMALL-NUMBERS"


def test_a_finished_export_leaves_zips_and_empties_the_pool(for_doctor):
    svc, admin, bid, folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    rec = run_export(svc, admin, bid, out, shapes=("text", "research"))
    assert (out / "Plain text copy.zip").exists(), (rec or {}).get("export")
    assert (out / "Research copy.zip").exists()
    # The pool is gone: key destroyed, work folder deleted. What stays is a
    # closed record — the dates, the counts, who signed — and nothing of the
    # patients'.
    assert not (svc.dir / "work" / bid).exists()
    closed = svc.doc["batches"][bid]
    assert closed["closed"] is True and closed["stage"] == "signed_off"
    assert closed["export"]["stage"] == "done"
    assert "folder" not in closed and "rulings" not in closed


def test_the_report_travels_with_every_shape(for_doctor):
    svc, admin, bid, folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    run_export(svc, admin, bid, out, shapes=("text",))
    with zipfile.ZipFile(out / "Plain text copy.zip") as z:
        report = next(z.read(i).decode() for i in z.namelist() if i.endswith("Evidence report.md"))
    assert "Jordan Lee" in report and "Casey Park" in report
    assert "Still inside the pool: **0**" in report
    assert "45 CFR 164.514(b)(2)" in report


def test_the_practices_own_files_are_untouched_by_an_export(for_doctor):
    svc, admin, bid, folder, out = for_doctor
    before = {p.name: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    run_export(svc, admin, bid, out)
    assert {p.name: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before


def test_an_export_that_cannot_be_written_destroys_nothing(for_doctor):
    svc, admin, bid, folder, _out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    # A folder inside the one the files came from is refused outright.
    assert code(call(svc, "export", admin, batch=bid, shapes=["text"], arrangement="shuffled",
                     out=str(folder / "finished"), attest=True)) in ("RUN-FOLDER-OVERLAP",
                                                                    "EXPORT-WRITE")
    assert not svc.doc["batches"][bid].get("closed"), "the batch must still be live"
    assert (svc.dir / "work" / bid).exists(), "the pool must not have been emptied"


def test_the_export_runs_in_the_background_and_reports_its_stage(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    ok(call(svc, "export", admin, batch=bid, shapes=["text"], arrangement="shuffled",
            out=str(out), attest=True))
    st = ok(call(svc, "export_status", admin, batch=bid))
    assert st["export"]["stage"] in ("working", "done")
    assert "by" not in st["export"]
    if svc._export_thread:
        svc._export_thread.join(300)


def test_every_shape_holds_the_same_words(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    run_export(svc, admin, bid, out, shapes=("text", "office"), arrangement="original")
    reports = ("Evidence report.md", "Accuracy report.md")
    with zipfile.ZipFile(out / "Plain text copy.zip") as z:
        text = " ".join(z.read(i).decode() for i in z.namelist()
                        if i.endswith(".txt") and not i.endswith(reports))
    with zipfile.ZipFile(out / "Office copy.zip") as z:
        office = " ".join(z.read(i).decode() for i in z.namelist()
                          if i.endswith((".txt", ".md")) and not i.endswith(reports))
        # The practice's own folder names hold patient names; the Office
        # copy must not carry them out.
        assert not any("Ellison" in i or "Okonkwo" in i or "Martha" in i
                       for i in z.namelist()), z.namelist()
    assert sorted(halite._words_of(text)) == sorted(halite._words_of(office))
    for word in ("Martha", "Ellison", "Okonkwo", "4481203"):
        assert word not in text and word not in office


# ---------------------------------------------------------------------------
# Feedback, after the files are released
# ---------------------------------------------------------------------------

def finished_batch(svc, admin, bid, out):
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    run_export(svc, admin, bid, out)
    return bid


def test_the_doctor_is_asked_for_feedback_and_the_report_carries_no_patient_text(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    finished_batch(svc, admin, bid, out)
    opened = ok(call(svc, "feedback_open", admin, batch=bid))
    assert opened["asked"] is False and "run_shape" in opened
    r = ok(call(svc, "feedback_save", admin, batch=bid,
                note="it took out a drug name it should have left in",
                suggestion="let me jump to the next unsure word with the keyboard"))
    assert r["saved"] is True
    text = (out / "..").resolve()
    body = "\n".join(p.read_text() for p in out.rglob("feedback_report_*.md"))
    assert "drug name" in body
    for word in ("Martha", "Ellison", "Okonkwo", "4481203", "555-0147"):
        assert word not in body
    assert svc.doc["batches"][bid]["feedback"]["report_id"]
    # The text handed back for the doctor's own email program is the same
    # checked report, word for word, so nothing unchecked can be sent.
    assert r["text"].strip() in body and "drug name" in r["text"]
    for word in ("Martha", "Ellison", "Okonkwo", "4481203", "555-0147"):
        assert word not in r["text"]


def test_a_doctor_who_types_patient_details_is_refused_not_sent(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    finished_batch(svc, admin, bid, out)
    r = ok(call(svc, "feedback_save", admin, batch=bid,
                note="it missed Martha Ellison's phone number 415-555-0199 on the second page"))
    saved = list(out.rglob("feedback_report_*.md"))
    if r["saved"]:
        # Scrubbed rather than blocked: either way nothing identifying may
        # reach the file.
        body = "\n".join(p.read_text() for p in saved)
        assert "Ellison" not in body and "415-555-0199" not in body
        assert "Ellison" not in r["text"] and "415-555-0199" not in r["text"]
    else:
        assert r["blocked"] and not saved and "text" not in r


def test_not_this_time_is_recorded_as_an_answer(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    finished_batch(svc, admin, bid, out)
    ok(call(svc, "feedback_skip", admin, batch=bid))
    assert svc.doc["batches"][bid]["feedback"]["declined"] is True
    assert not list(out.rglob("feedback_report_*"))


# ---------------------------------------------------------------------------
# Regression cases from a review of the export, each one a test
# ---------------------------------------------------------------------------

def test_a_batch_being_exported_cannot_be_cancelled_under_the_export(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    svc._export_running = bid                      # as if the thread were working
    assert code(call(svc, "batch_cancel", admin, batch=bid)) == "BATCH-BUSY"
    svc._export_running = None


def test_a_batch_being_exported_is_not_erased_when_its_time_runs_out(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    svc.doc["batches"][bid]["due"] = "2000-01-01"
    svc._export_running = bid
    svc._expire_batches()
    assert bid in svc.doc["batches"], "an export in flight holds the batch"
    svc._export_running = None
    svc._expire_batches()
    assert bid not in svc.doc["batches"]


def test_an_export_the_app_did_not_live_through_goes_back_to_the_clinician(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = a_folder(tmp_path)
    b = ok(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                confirmed=[k for k, _ in opal.Service.CARE_POINTS]))
    bid = b["id"]
    finish(svc, bid)                                # let the preparing worker finish first
    svc.doc["batches"][bid]["stage"] = "exporting"  # as if the power went out mid-release
    svc.doc["batches"][bid]["export"] = {"stage": "working"}
    svc.doc["batches"][bid]["signed_off_by_doctor"] = {"name": "Dr Sam Rivera"}
    svc._save()
    svc.close()
    again = make(tmp_path)
    rec = again.doc["batches"][bid]
    assert rec["stage"] == "settling"
    assert rec["export"]["error"] == "EXPORT-INTERRUPTED"
    assert "signed_off_by_doctor" not in rec, "the signature goes with the release that happened"
    again.close()


def test_a_reviewer_cannot_read_the_release_record(for_doctor):
    svc, admin, bid, _folder, _out = for_doctor
    one = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    assert code(call(svc, "export_status", one, batch=bid)) == "ROLE-DENIED"


def test_holding_a_record_back_holds_every_note_the_condition_sits_in(for_doctor):
    svc, admin, bid, _folder, _out = for_doctor
    settle_everything(svc, admin, bid)
    check = ok(call(svc, "settle_check", admin, batch=bid))
    row = next((r for r in check["odd_one_out"] if len(r["notes"]) > 1), None)
    if row is None:
        pytest.skip("no condition in this batch sits in more than one note")
    ok(call(svc, "settle_small", admin, batch=bid, rulings={row["notes"][0]: "withhold"}))
    ruled = svc.doc["batches"][bid]["small_rulings"]
    assert all(ruled.get(nid) == "withhold" for nid in row["notes"])


def test_the_report_counts_a_withheld_record_as_the_clinicians_decision(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    check = ok(call(svc, "settle_check", admin, batch=bid))
    if not check["odd_one_out"]:
        pytest.skip("no odd-one-out warning in this batch")
    first = check["odd_one_out"][0]["notes"]
    ruling = {nid: "withhold" for nid in first}
    for row in check["odd_one_out"][1:]:
        for nid in row["notes"]:
            ruling.setdefault(nid, "accept")
    ok(call(svc, "settle_small", admin, batch=bid, rulings=ruling))
    run_export(svc, admin, bid, out)
    with zipfile.ZipFile(out / "Plain text copy.zip") as z:
        report = next(z.read(i).decode() for i in z.namelist() if i.endswith("Evidence report.md"))
    line = next(l for l in report.splitlines() if "Held back by the clinician" in l)
    assert line.strip().endswith(("1", "2", "3")), line
    tool = next(l for l in report.splitlines() if "Held by the tool" in l)
    assert tool.strip().endswith("0"), tool


def test_the_report_names_each_reviewers_badge(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    run_export(svc, admin, bid, out)
    with zipfile.ZipFile(out / "Plain text copy.zip") as z:
        report = next(z.read(i).decode() for i in z.namelist() if i.endswith("Evidence report.md"))
    assert "practice badge holder, passed this app's practice batch on" in report


def test_a_rare_flag_must_be_ruled_on_before_anything_leaves(for_doctor):
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    check = ok(call(svc, "settle_check", admin, batch=bid))
    if not check["rare_in_world"]:
        pytest.skip("no rare-condition flag survived review in this batch")
    # Answer only the odd-one-out warnings, leaving the rare flag open.
    ruling = {nid: "accept" for row in check["odd_one_out"] for nid in row["notes"]}
    if ruling:
        ok(call(svc, "settle_small", admin, batch=bid, rulings=ruling))
    assert code(call(svc, "export", admin, batch=bid, shapes=["text"], arrangement="shuffled",
                     out=str(out), attest=True)) == "EXPORT-SMALL-NUMBERS"


def test_a_ruling_on_a_rare_flag_covers_every_note_of_that_patient(for_doctor):
    svc, admin, bid, _folder, _out = for_doctor
    settle_everything(svc, admin, bid)
    check = ok(call(svc, "settle_check", admin, batch=bid))
    row = next((r for r in check["rare_in_world"] if len(r["notes"]) > 1), None)
    if row is None:
        pytest.skip("no patient with a rare flag and more than one note here")
    ok(call(svc, "settle_small", admin, batch=bid, rulings={row["notes"][0]: "withhold"}))
    ruled = svc.doc["batches"][bid]["small_rulings"]
    assert all(ruled.get(nid) == "withhold" for nid in row["notes"])


def test_phones_are_required_of_admins_and_optional_for_everyone_else(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = a_folder(tmp_path)
    points = [k for k, _ in opal.Service.CARE_POINTS]
    svc.doc["accounts"][reviewers[0]].pop("totp", None)      # a reviewer who never linked one
    svc._save()
    b = ok(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers, confirmed=points))
    ok(call(svc, "batch_cancel", admin, batch=b["id"]))
    me = next(u for u, x in svc.doc["accounts"].items() if "admin" in x["roles"])
    svc.doc["accounts"][me].pop("totp", None)                 # the admin starting it has none
    svc._save()
    assert code(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                     confirmed=points)) == "BATCH-NO-PHONE"
    svc.close()


def test_a_locked_pool_reopens_with_the_password_for_someone_with_no_phone(tmp_path):
    svc, admin = ready(tmp_path)
    reviewers = with_people(svc, admin)
    folder = a_folder(tmp_path)
    b = ok(call(svc, "batch_create", admin, folder=str(folder), reviewers=reviewers,
                confirmed=[k for k, _ in opal.Service.CARE_POINTS]))
    finish(svc, b["id"])
    ok(call(svc, "batch_status", admin, batch=b["id"]))      # patient work open on this screen
    ok(call(svc, "lock_now", admin))
    for acc in svc.doc["accounts"].values():                  # as if no phone had ever been linked
        acc.pop("totp", None)
    svc._save()
    ok(call(svc, "unlock", admin, password=GOOD_PW))
    svc.close()


def test_an_accuracy_report_travels_with_the_files(for_doctor):
    """The accuracy report travels with the files and states its limits."""
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    run_export(svc, admin, bid, out)
    with zipfile.ZipFile(out / "Plain text copy.zip") as z:
        names = z.namelist()
        assert any(n.endswith("Accuracy report.md") for n in names), names
        acc = next(z.read(n).decode() for n in names if n.endswith("Accuracy report.md"))
    assert "Jordan Lee" in acc and "Casey Park" in acc
    assert "not an absolute truth" in acc
    assert "Identifiers in the final version" in acc
    for word in ("Martha", "Ellison", "Okonkwo", "4481203"):
        assert word not in acc
    low = acc.lower()
    assert "100%" not in low or "upheld" in low        # never a bare perfect score


def test_the_doctor_may_release_with_opaque_markers(for_doctor):
    """In the final files only, every removal can read [REMOVED], so the
    page gives no hint of what was there."""
    import re
    svc, admin, bid, _folder, out = for_doctor
    settle_everything(svc, admin, bid)
    answer_warnings(svc, admin, bid)
    rec = run_export(svc, admin, bid, out, markers="opaque")
    assert rec["export"]["markers"] == "opaque"
    with zipfile.ZipFile(out / "Plain text copy.zip") as z:
        notes = [z.read(n).decode() for n in z.namelist()
                 if n.endswith(".txt") and "report" not in n.lower()]
        report = next(z.read(n).decode() for n in z.namelist() if n.endswith("Evidence report.md"))
    assert notes and any("[REMOVED]" in t for t in notes)
    assert not any(re.search(r"\[(?:NAME|MRN|PHONE|DATE)\]", t) for t in notes)
    assert "Markers** opaque" in report
