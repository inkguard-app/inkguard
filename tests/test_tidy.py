"""The tidy-up tool.

The whole point of this file: a folder that has merely been tidied must
never be mistaken for a de-identified one. So the tool checks first, refuses
anything that still looks identified, never touches the originals, and never
claims the files are clean.
"""
from __future__ import annotations

import pytest

from inkguard import dolomite, opal
from tests.test_opal import GOOD_PW, call, code, ok, ready

CLEAN = "Patient has hypertension. Reviewed in clinic and continued on treatment.\n"
STILL = "Martha Ellison, MRN 4481203, telephone 555-0147, seen 4 March 2026.\n"


def folder_of(tmp_path, *texts, name="records"):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(texts, start=1):
        (d / f"note-{i}.txt").write_text(t, encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def test_a_clean_folder_is_safe_to_tidy(tmp_path):
    found = dolomite.look(folder_of(tmp_path, CLEAN, CLEAN))
    assert found.files == 2 and found.with_identifiers == 0 and found.safe_to_tidy


def test_one_identified_file_stops_the_whole_folder(tmp_path):
    found = dolomite.look(folder_of(tmp_path, CLEAN, STILL))
    assert found.with_identifiers == 1 and not found.safe_to_tidy
    with pytest.raises(dolomite.TidyError) as e:
        dolomite.tidy(folder_of(tmp_path, CLEAN, STILL), tmp_path / "out")
    assert e.value.code == "TIDY-STILL-IDENTIFIED"


def test_the_check_says_what_kind_of_thing_it_found_and_never_the_word(tmp_path):
    found = dolomite.look(folder_of(tmp_path, STILL))
    said = " ".join(found.kinds)
    assert "a person's name" in said or "a record number" in said
    for word in ("Martha", "Ellison", "4481203", "555-0147"):
        assert word not in said and word not in str(found.public())


def test_the_check_runs_again_when_the_files_are_copied(tmp_path, monkeypatch):
    src = folder_of(tmp_path, CLEAN)
    calls = []
    real = dolomite.look
    monkeypatch.setattr(dolomite, "look", lambda f: (calls.append(f), real(f))[1])
    dolomite.tidy(src, tmp_path / "out")
    assert calls, "tidy must check the folder itself, not trust an earlier look"


def test_a_folder_that_changed_after_the_check_is_caught(tmp_path):
    src = folder_of(tmp_path, CLEAN)
    found = dolomite.look(src)
    assert found.safe_to_tidy
    (src / "slipped-in.txt").write_text(STILL, encoding="utf-8")
    with pytest.raises(dolomite.TidyError) as e:
        dolomite.tidy(src, tmp_path / "out", checked=found)
    assert e.value.code == "TIDY-STILL-IDENTIFIED"


# ---------------------------------------------------------------------------
# What it does
# ---------------------------------------------------------------------------

def test_the_copies_come_out_in_a_new_order_under_new_names(tmp_path):
    src = folder_of(tmp_path, *[f"{CLEAN}{i}\n" for i in range(12)])
    out = dolomite.tidy(src, tmp_path / "out", seed="a-seed")
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert out["files"] == 12 and len(names) == 13          # the copies and the note
    for p in (tmp_path / "out").iterdir():
        assert "note-" not in p.name or p.name.endswith(".md")
    bodies = [p.read_text() for p in sorted((tmp_path / "out").iterdir())
              if p.suffix == ".txt"]
    assert sorted(bodies) == sorted(f"{CLEAN}{i}\n" for i in range(12))


def test_the_originals_are_never_touched(tmp_path):
    src = folder_of(tmp_path, CLEAN, CLEAN)
    before = {p.name: p.read_bytes() for p in src.iterdir()}
    dolomite.tidy(src, tmp_path / "out")
    assert {p.name: p.read_bytes() for p in src.iterdir()} == before


def test_the_note_says_nothing_was_removed(tmp_path):
    dolomite.tidy(folder_of(tmp_path, CLEAN), tmp_path / "out")
    note = " ".join((tmp_path / "out" / "What this folder is.md").read_text().split())
    assert "Nothing was removed from these files" in note
    assert "does not de-identify anything" in note
    assert "not evidence that these files are de-identified" in note
    low = note.lower()
    assert "100%" not in low and "hipaa" not in low


def test_a_folder_inside_the_other_is_refused(tmp_path):
    src = folder_of(tmp_path, CLEAN)
    with pytest.raises(dolomite.TidyError) as e:
        dolomite.tidy(src, src / "tidied")
    assert e.value.code == "TIDY-OVERLAP"


def test_an_empty_folder_is_said_plainly(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(dolomite.TidyError) as e:
        dolomite.look(tmp_path / "empty")
    assert e.value.code == "TIDY-EMPTY"


# ---------------------------------------------------------------------------
# Through the service, for a signed-in admin
# ---------------------------------------------------------------------------

def test_the_tidy_tool_works_with_no_licence_at_all(tmp_path):
    svc, admin = ready(tmp_path)
    svc.doc["licence"] = None                      # a field nothing reads any more
    svc._save()
    src = folder_of(tmp_path, CLEAN, CLEAN)
    looked = ok(call(svc, "tidy_look", admin, folder=str(src)))
    assert looked["safe_to_tidy"] is True
    out = ok(call(svc, "tidy_run", admin, folder=str(src), out=str(tmp_path / "out")))
    assert out["files"] == 2
    # A batch is a different request and is answered on its own terms.
    assert code(call(svc, "batch_create", admin, folder=str(src), reviewers=[],
                     confirmed=[])) not in (None,)
    svc.close()


def test_the_service_refuses_to_tidy_identified_files(tmp_path):
    svc, admin = ready(tmp_path)
    src = folder_of(tmp_path, STILL)
    assert code(call(svc, "tidy_run", admin, folder=str(src),
                     out=str(tmp_path / "out"))) == "TIDY-STILL-IDENTIFIED"
    assert not (tmp_path / "out").exists()
    svc.close()


def test_tidying_is_recorded_in_the_security_log(tmp_path):
    svc, admin = ready(tmp_path)
    src = folder_of(tmp_path, CLEAN)
    ok(call(svc, "tidy_run", admin, folder=str(src), out=str(tmp_path / "out")))
    kinds = [e["event"] for e in svc.log.tail(50)]
    assert "tidied" in kinds
    svc.close()


# ---------------------------------------------------------------------------
# Reachable before any account is set up
# ---------------------------------------------------------------------------

def test_the_free_tool_works_before_anything_is_set_up(tmp_path):
    """The case the tidy tool exists for: just downloaded, no accounts yet,
    nobody to sign in as."""
    from tests.test_opal import make
    svc = make(tmp_path)
    assert ok(call(svc, "status"))["tidy_available"] is True
    src = folder_of(tmp_path, CLEAN, CLEAN)
    looked = ok(call(svc, "tidy_look", folder=str(src)))
    assert looked["safe_to_tidy"] is True
    out = ok(call(svc, "tidy_run", folder=str(src), out=str(tmp_path / "out")))
    assert out["files"] == 2
    kinds = [e["event"] for e in svc.log.tail(50)]
    assert "tidied" in kinds, "it is still written to the security log"
    svc.close()


def test_before_setup_it_still_refuses_identified_files(tmp_path):
    from tests.test_opal import make
    svc = make(tmp_path)
    src = folder_of(tmp_path, STILL)
    assert code(call(svc, "tidy_run", folder=str(src),
                     out=str(tmp_path / "out"))) == "TIDY-STILL-IDENTIFIED"
    svc.close()


def test_once_there_are_accounts_it_needs_a_sign_in(tmp_path):
    """Before setup there is nobody to sign in as. Afterwards there is, so
    the tidy tool is limited to the practice's own signed-in people, like everything else."""
    svc, admin = ready(tmp_path)
    src = folder_of(tmp_path, CLEAN)
    assert code(call(svc, "tidy_look", folder=str(src))) == "SESSION-ENDED"
    assert ok(call(svc, "tidy_look", admin, folder=str(src)))["safe_to_tidy"] is True
    svc.close()
