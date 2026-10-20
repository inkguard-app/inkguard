"""Settling a batch and taking the clean copies out.

What this file holds to:
  1. every shape carries the SAME words the three people agreed;
  2. a word can only be left on the page when the tool itself asked about
     it and two reviewers AND the doctor said to leave it;
  3. nothing is destroyed until every shape is written and verified;
  4. the report states what was decided, what was held, and what was
     destroyed — and never a patient's words.
"""
from __future__ import annotations

import zipfile

import pytest

from inkguard import chert, contract, granite, halite

NOTE = """Discharge summary
Patient: Martha Ellison, MRN 4481203, date of birth 14 March 1952.
Seen at Lakeside Family Practice on 2 April 2026 by Dr. Sam Rivera.
Telephone 555-0147.
Plan: continue metformin, review in six weeks.
Walks by the Lakeside path most days.
"""
# The last line keeps one word the tool is unsure of, so there is always a
# question to answer (see NOTE_A in test_granite.py).


def prepared(text: str = NOTE, nid: str = "n00001", patient: int = 0, item: str = "P1-F1") -> dict:
    """One note as the pool holds it after preparation."""
    res = contract.deidentify(text, profile="safe_harbor", mode="mask")
    payload, sanitized = chert.note_payload(text, res.raw, False)
    return {"id": nid, "item": item, "patient": patient, "text": sanitized,
            "chars": len(text), "pages": granite.pages(len(text)), **payload}


def answers(note: dict, how: str = "remove") -> dict:
    """One reviewer's decisions: every question answered the same way."""
    return {"answers": {q["id"]: how for q in note["questions"]
                        + note.get("rare", [])}, "confirmed_at": "2026-10-10T00:00:00Z",
            "seconds": 300}


def doctor_for(note: dict, d1: dict, d2: dict, ruling: str = "remove") -> dict:
    return {"rulings": {it["key"]: ruling for it in halite.bucket(note, d1, d2)},
            "seconds": 120, "opened": True}


# ---------------------------------------------------------------------------
# Settling
# ---------------------------------------------------------------------------

def test_a_settled_note_removes_what_the_engine_found():
    n = prepared()
    d = answers(n)
    s = halite.settle_note(n, d, d, doctor_for(n, d, d), lds=False)
    assert s.status == "ready"
    assert "Martha" not in s.clean and "4481203" not in s.clean and "555-0147" not in s.clean
    assert "metformin" in s.clean            # the medicine is not an identifier


def test_the_doctor_must_decide_every_item_the_reviewers_disagreed_on():
    n = prepared()
    d1, d2 = answers(n, "remove"), answers(n, "unsure")
    assert halite.bucket(n, d1, d2), "an unsure answer belongs in the doctor's bucket"
    with pytest.raises(chert.ReviewError):
        halite.settle_note(n, d1, d2, {"rulings": {}}, lds=False)


def test_a_blocked_note_never_reaches_a_shape():
    n = prepared()
    n["status"], n["block_reason"] = "blocked", contract.BLOCKED_REASON
    s = halite.settle_note(n, {}, {}, {}, lds=False)
    assert s.status == "blocked" and s.clean == ""


def test_a_word_can_only_be_left_in_when_the_tool_itself_asked_about_it():
    n = prepared()
    d1, d2 = answers(n, "fine"), answers(n, "fine")
    s = halite.settle_note(n, d1, d2, doctor_for(n, d1, d2, "keep"), lds=False)
    # Everything in the unmask set must be a word the engine raised, never
    # a certain removal: the MRN and the phone number can never appear.
    assert "4481203" not in s.unmask and "555-0147" not in s.unmask
    for word in s.unmask:
        assert word in n["text"]


def test_a_certain_removal_cannot_be_kept_by_anyone():
    n = prepared()
    certain = [r for r in n["removals"] if r.get("certain")]
    assert certain, "the MRN and phone number should be certain removals"
    d = {"keep": [r["id"] for r in certain], "confirmed_at": "x", "seconds": 300}
    s = halite.settle_note(n, d, d, doctor_for(n, d, d), lds=False)
    for r in certain:
        assert n["text"][r["start"]:r["end"]] not in s.clean


def test_a_note_still_showing_an_identifier_shape_is_held_not_released():
    n = prepared()
    d = answers(n)
    s = halite.settle_note(n, d, d, doctor_for(n, d, d), lds=False)
    s.clean += "\nCall 415-555-0199 to confirm."      # as if something slipped through
    again = halite.Settled(id=s.id, item=s.item, patient=s.patient, status="ready", clean=s.clean)
    again.left = contract.shapes_left(again.clean, False)
    assert again.left, "the final inspection must see the phone shape"


# ---------------------------------------------------------------------------
# Small numbers
# ---------------------------------------------------------------------------

def mk(nid: str, patient: int, text: str) -> halite.Settled:
    return halite.Settled(id=nid, item=f"P{patient}-F1", patient=patient, status="ready",
                          clean=text, pages=1)


def test_the_odd_one_out_is_the_condition_only_one_patient_in_the_batch_carries():
    lex, _ = chert._rare_lexicon()
    rare = next(c for c in sorted(lex) if " " in c and len(c) > 12)
    rows = [mk("n1", 0, f"Patient has {rare}."),
            mk("n2", 1, "Patient has hypertension."),
            mk("n3", 2, "Patient has hypertension.")]
    out = halite.small_numbers(rows, warn_at=1)
    assert any(r["condition"] == rare.lower() for r in out["odd_one_out"])


def test_a_small_batch_is_told_the_count_means_little():
    out = halite.small_numbers([mk("n1", 0, "hypertension")], warn_at=1)
    assert out["small_set"] and "means little" in out["small_set_note"]


def test_the_warning_line_is_one_patient_by_default():
    assert halite.DEFAULT_WARN_AT == 1


def test_the_doctor_can_hold_a_record_back_and_it_never_leaves():
    rows = [mk("n1", 0, "one"), mk("n2", 1, "two")]
    halite.apply_small_numbers(rows, {"n1": "withhold"})
    assert rows[0].status == "held" and rows[1].status == "ready"
    assert [s.id for s in halite.arrange(rows, "grouped")] == ["n2"]


def test_an_unknown_ruling_is_refused():
    with pytest.raises(halite.ExportError):
        halite.apply_small_numbers([mk("n1", 0, "x")], {"n1": "ignore"})


# ---------------------------------------------------------------------------
# Arrangement
# ---------------------------------------------------------------------------

def test_shuffling_changes_the_order_and_keeps_every_record():
    rows = [mk(f"n{i}", i, f"record {i}") for i in range(30)]
    out = halite.arrange(rows, "shuffled", seed="a-seed")
    assert sorted(s.id for s in out) == sorted(s.id for s in rows)
    assert [s.id for s in out] != [s.id for s in rows]


def test_grouped_keeps_a_patients_records_together():
    rows = [mk("n1", 0, "a"), mk("n2", 1, "b"), mk("n3", 0, "c")]
    assert [s.patient for s in halite.arrange(rows, "grouped")] == [0, 0, 1]


def test_an_unknown_arrangement_is_refused():
    with pytest.raises(halite.ExportError):
        halite.arrange([], "alphabetical")


def test_record_codes_say_nothing_about_the_person():
    rows = [mk("n1", 0, "a"), mk("n2", 1, "b")]
    a = halite.record_codes(rows)
    b = halite.record_codes(rows)
    assert set(a) == {0, 1} and len(set(a.values())) == 2
    assert a != b, "codes are random per export, never derived from the person"


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

FACTS = {"batch": "b000001", "app": "Inkguard", "version": "0.25.0",
         "practice": "Test Practice", "verified_on": "2026-10-01",
         "doctor": "Dr. A. Winterhalter", "reviewers": {"R1": "Nurse One", "R2": "Biller Two"},
         "helpers": [], "points": ["only the people named will see these notes"],
         "reviewer_attestation": chert.ATTESTATION,
         "profile": "safe_harbor", "citation": "45 CFR 164.514(b)(2)",
         "counts": {"files": 1, "patients": 1, "notes": 1, "released": 1, "held_by_tool": 0,
                    "withheld": 0, "blocked": 0, "set_aside": 0},
         "decisions": {"engine_kept": 7, "bucket": 0, "bucket_removed": 0, "bucket_kept": 0,
                       "marks": 0, "questions": 2},
         "small_numbers": {"warn_at": 1, "patients": 1, "notes": 1, "odd_one_out": [],
                           "rare_in_world": [], "small_set": True,
                           "small_set_note": "This batch holds 1 patient(s). Below about 10 the "
                                             "odd-one-out count means little."},
         "accuracy": {"lines": ["example accuracy line for this test fixture (no measured figure)"]},
         "codes": {}}


def settled_one(tmp_path) -> tuple[list, list[halite.Settled], object]:
    folder = tmp_path / "notes"
    (folder / "patient-1").mkdir(parents=True)
    src = folder / "patient-1" / "note.txt"
    src.write_text(NOTE, encoding="utf-8")
    items, _ = granite.take_stock(folder)
    n = prepared(item=items[0].id)
    items[0].outcome, items[0].note_id, items[0].chars = "prepared", n["id"], len(NOTE)
    d = answers(n)
    s = halite.settle_note(n, d, d, doctor_for(n, d, d), lds=False)
    return items, [s], folder


def test_export_writes_every_chosen_shape_as_a_verified_zip(tmp_path):
    items, rows, folder = settled_one(tmp_path)
    out = halite.export(folder, items, rows, shapes=["research", "office", "text"],
                        arrangement="shuffled", out_dir=tmp_path / "out", facts=FACTS,
                        profile="safe_harbor", erase=lambda: {"files": 9, "key_destroyed": "yes"})
    for name in ("Research copy.zip", "Office copy.zip", "Plain text copy.zip"):
        z = tmp_path / "out" / name
        assert z.exists(), name
        with zipfile.ZipFile(z) as f:
            assert any(i.filename.endswith("Evidence report.md") for i in f.infolist())
    assert out["erased"]["files"] == 9


def test_every_shape_carries_the_same_words(tmp_path):
    items, rows, folder = settled_one(tmp_path)
    halite.export(folder, items, rows, shapes=["office", "text"], arrangement="original",
                  out_dir=tmp_path / "out", facts=FACTS, profile="safe_harbor")
    with zipfile.ZipFile(tmp_path / "out" / "Plain text copy.zip") as z:
        text = next(z.read(i).decode() for i in z.namelist() if i.endswith(".txt"))
    with zipfile.ZipFile(tmp_path / "out" / "Office copy.zip") as z:
        office = next(z.read(i).decode() for i in z.namelist() if i.endswith("note.txt"))
    assert halite._words_of(text) == halite._words_of(office)
    assert "Martha" not in text and "Martha" not in office


def test_nothing_is_destroyed_when_a_shape_cannot_be_written(tmp_path):
    items, rows, folder = settled_one(tmp_path)
    called = []
    rows[0].clean = ""                             # an empty record writes nothing readable
    with pytest.raises(halite.ExportError):
        halite.export(folder, items, rows, shapes=["nonsense"], arrangement="shuffled",
                      out_dir=tmp_path / "out", facts=FACTS, profile="safe_harbor",
                      erase=lambda: called.append(1) or {"files": 0, "key_destroyed": ""})
    assert not called, "the pool must not be emptied when export fails"


def test_export_refuses_a_folder_inside_the_one_the_files_came_from(tmp_path):
    items, rows, folder = settled_one(tmp_path)
    with pytest.raises(halite.ExportError) as e:
        halite.export(folder, items, rows, shapes=["text"], arrangement="shuffled",
                      out_dir=folder / "finished", facts=FACTS, profile="safe_harbor")
    assert e.value.code == "RUN-FOLDER-OVERLAP"


def test_export_refuses_when_nothing_is_ready(tmp_path):
    items, rows, folder = settled_one(tmp_path)
    rows[0].status = "held"
    with pytest.raises(halite.ExportError) as e:
        halite.export(folder, items, rows, shapes=["text"], arrangement="shuffled",
                      out_dir=tmp_path / "out", facts=FACTS, profile="safe_harbor")
    assert e.value.code == "EXPORT-NOTHING"


def test_the_originals_are_untouched_by_an_export(tmp_path):
    items, rows, folder = settled_one(tmp_path)
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    halite.export(folder, items, rows, shapes=["research"], arrangement="shuffled",
                  out_dir=tmp_path / "out", facts=FACTS, profile="safe_harbor")
    assert {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def report_text(tmp_path) -> str:
    items, rows, folder = settled_one(tmp_path)
    out = halite.export(folder, items, rows, shapes=["text"], arrangement="shuffled",
                        out_dir=tmp_path / "out", facts=FACTS, profile="safe_harbor",
                        erase=lambda: {"files": 4, "key_destroyed": "2026-10-10T00:00:00Z"})
    return out["report"]


def test_the_report_names_the_people_and_what_was_destroyed(tmp_path):
    r = report_text(tmp_path)
    assert "Dr. A. Winterhalter" in r and "Nurse One" in r and "Biller Two" in r
    assert "Still inside the pool: **0**" in r
    assert "4 destroyed" in r or "**4** destroyed" in r


def test_the_report_states_the_method_and_claims_nothing_more(tmp_path):
    r = report_text(tmp_path)
    assert "45 CFR 164.514(b)(2)" in r
    assert "qualified expert" in r
    low = r.lower()
    assert "hipaa compliant" not in low and "100%" not in low


def test_the_report_carries_no_patient_text(tmp_path):
    r = report_text(tmp_path)
    for word in ("Martha", "Ellison", "4481203", "555-0147", "Carlton"):
        assert word not in r


def test_the_report_states_the_arrangement_that_was_used(tmp_path):
    r = report_text(tmp_path)
    assert "random order" in r


# ---------------------------------------------------------------------------
# Regression cases from a review of the export, each with its own test
# ---------------------------------------------------------------------------

def test_nothing_is_destroyed_until_every_shape_has_been_read_back(tmp_path, monkeypatch):
    items, rows, folder = settled_one(tmp_path)
    called = []
    # A shape that verifies must still not trigger the erase if a later one
    # does not: the erase happens after ALL of them.
    monkeypatch.setattr(halite, "verify_shape",
                        lambda key, here, rws: (_ for _ in ()).throw(
                            halite.ExportError("EXPORT-VERIFY", "pretend the disk lied")))
    with pytest.raises(halite.ExportError) as e:
        halite.export(folder, items, rows, shapes=["text", "research"], arrangement="shuffled",
                      out_dir=tmp_path / "out", facts=FACTS, profile="safe_harbor",
                      erase=lambda: called.append(1) or {"files": 0, "key_destroyed": ""})
    assert e.value.code == "EXPORT-VERIFY" and not called
    assert not list((tmp_path / "out").glob("*.zip"))


def test_a_shape_short_of_a_record_is_caught_before_the_erase(tmp_path, monkeypatch):
    items, rows, folder = settled_one(tmp_path)
    called = []
    real = halite.plain_text_copy
    monkeypatch.setattr(halite, "plain_text_copy",
                        lambda rws, codes, where: real(rws[:0], codes, where) or
                        [(where / "only-one.txt").write_text("x", encoding="utf-8")])
    with pytest.raises(halite.ExportError):
        halite.export(folder, items, rows, shapes=["text"], arrangement="shuffled",
                      out_dir=tmp_path / "out", facts=FACTS, profile="safe_harbor",
                      erase=lambda: called.append(1) or {"files": 0, "key_destroyed": ""})
    assert not called


def test_each_file_is_cleaned_with_only_its_own_records_kept_words(tmp_path, monkeypatch):
    """A word two reviewers left in belongs to the note they were reading.

    Handing the whole batch's kept words to every file would mean a word
    kept as a place name in one note keeps a different patient's first name
    in another, so each file is cleaned with its own record's words only."""
    from inkguard import calcite
    seen = {}
    real = calcite.clean_file

    def spy(src, dst, eng):
        seen[src.name] = set(eng.keep)
        return real(src, dst, eng)

    monkeypatch.setattr(calcite, "clean_file", spy)
    a = halite.Settled(id="n1", item="P1-F1", patient=0, status="ready",
                       clean="Seen at Chelsea clinic.", unmask={"Chelsea"})
    b = halite.Settled(id="n2", item="P2-F1", patient=1, status="ready",
                       clean="[NAME] attended.")
    src = tmp_path / "files"
    src.mkdir()
    (src / "a.csv").write_text("note\nSeen at Chelsea clinic.\n", encoding="utf-8")
    (src / "b.csv").write_text("note\nChelsea Okonkwo attended.\n", encoding="utf-8")
    items = [type("I", (), {"id": "P1-F1", "source": "a.csv", "outcome": "prepared",
                            "patient": 0})(),
             type("I", (), {"id": "P2-F1", "source": "b.csv", "outcome": "prepared",
                            "patient": 1})()]
    halite.office_copy(src, items, [a, b], tmp_path / "office",
                       profile="safe_harbor", codes={0: "AAA111", 1: "BBB222"})
    assert seen.get("a.csv") == {"Chelsea"}
    assert seen.get("b.csv") == set(), "the other patient's file keeps nothing of this one's"


def test_a_file_that_still_shows_a_removed_word_is_held_not_released(tmp_path, monkeypatch):
    """The written file is read back and checked for the very words the
    three of them took out. One showing means it is deleted and held."""
    from inkguard import calcite
    rec = halite.Settled(id="n1", item="P1-F1", patient=0, status="ready",
                         clean="[NAME] attended.", removed_words={"Okonkwo"})
    src = tmp_path / "files"
    src.mkdir()
    (src / "a.csv").write_text("note\nOkonkwo attended.\n", encoding="utf-8")
    items = [type("I", (), {"id": "P1-F1", "source": "a.csv", "outcome": "prepared",
                            "patient": 0})()]
    # As if cleaning had quietly left the name on the page.
    monkeypatch.setattr(calcite, "read_text", lambda p: "Okonkwo attended.")
    out = halite.office_copy(src, items, [rec], tmp_path / "office",
                             profile="safe_harbor", codes={0: "AAA111"})
    assert out["files"] == [] and len(out["trouble"]) == 1
    assert "took out" in out["trouble"][0]["reason"]
    assert not [p for p in (tmp_path / "office").rglob("*") if p.is_file()]


def test_an_unsure_reviewer_is_not_an_agreement_to_leave_a_word_in():
    n = prepared()
    if not n["questions"]:
        pytest.skip("this note left the tool in no doubt")
    one = {"answers": {q["id"]: "fine" for q in n["questions"]}, "confirmed_at": "x", "seconds": 1}
    other = {"answers": {q["id"]: "unsure" for q in n["questions"]}, "confirmed_at": "x",
             "seconds": 1}
    s = halite.settle_note(n, one, other, doctor_for(n, one, other, "keep"), lds=False)
    assert s.unmask == set(), "one reviewer's 'not sure' is not agreement"


def test_a_record_the_clinician_held_back_is_counted_as_theirs_not_the_tools():
    rows = [mk("n1", 0, "one"), mk("n2", 1, "two")]
    halite.apply_small_numbers(rows, {"n1": "withhold"})
    assert rows[0].withheld_by_doctor is True
    assert "clinician" in rows[0].reason


def test_the_report_prints_a_ruling_for_every_note_a_condition_sits_in(tmp_path):
    facts = dict(FACTS)
    facts["small_numbers"] = {"warn_at": 1, "patients": 2, "notes": 3, "rare_in_world": [],
                              "small_set": False,
                              "odd_one_out": [{"condition": "a rare thing", "patients": 1,
                                               "notes": ["n1", "n2"]}]}
    facts["small_rulings"] = {"n1": "withhold", "n2": "withhold"}
    facts.update(erase={"files": 1, "key_destroyed": "x"}, shapes=["text"],
                 arrangement="shuffled", arrangement_said="one file, records in random order",
                 finished="2026-10-10T00:00:00Z")
    r = halite.evidence_report(facts)
    assert "withhold" in r


def test_a_rare_condition_warning_covers_every_note_of_that_patient():
    """A rare-condition flag is about the patient, not one note of theirs."""
    a = mk("n1", 0, "one")
    b = mk("n2", 0, "two")
    c = mk("n3", 1, "three")
    a.rare = [{"id": "f1", "start": 0, "end": 3, "also": ["an age"]}]
    out = halite.small_numbers([a, b, c])
    assert len(out["rare_in_world"]) == 1
    row = out["rare_in_world"][0]
    assert row["patient"] == 0 and row["notes"] == ["n1", "n2"], row
    assert row["also"] == ["an age"] and row["flags"] == 1


def test_the_report_shows_the_ruling_on_every_rare_flag(tmp_path):
    facts = dict(FACTS)
    facts["small_numbers"] = {"warn_at": 1, "patients": 2, "notes": 2, "odd_one_out": [],
                              "small_set": False,
                              "rare_in_world": [{"patient": 0, "flags": 2, "notes": ["n1", "n2"],
                                                 "also": ["an age", "a job or school"]}]}
    facts["small_rulings"] = {"n1": "withhold", "n2": "withhold"}
    facts.update(erase={"files": 1, "key_destroyed": "x"}, shapes=["text"],
                 arrangement="shuffled", arrangement_said="shuffled",
                 finished="2026-10-10T00:00:00Z")
    r = halite.evidence_report(facts)
    assert "rare in the world" in r and "withhold" in r
    assert "an age, a job or school" in r
