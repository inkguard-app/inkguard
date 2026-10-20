"""The support-code system (inkguard/topaz.py): every problem shape has a code, a
plain PHI-free message for the doctor, and a codebook entry for IT. The log
the doctor shares carries codes and counts, never patient data."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inkguard import topaz  # noqa: E402


def test_every_code_has_a_message_a_meaning_and_a_fix():
    for code, (cat, msg, means, fix) in topaz.CODEBOOK.items():
        assert cat and means and fix, code
        # Every code is actionable: it tells IT what it means and how to fix
        # it. (RUN-SUMMARY is informational, so it alone may have no doctor
        # message.)
        if code != "RUN-SUMMARY":
            assert msg, code


def test_the_doctor_message_names_the_code_for_an_unknown_one():
    assert "NOT-A-REAL-CODE" in topaz.message("NOT-A-REAL-CODE")
    assert not topaz.is_known("NOT-A-REAL-CODE")
    assert topaz.is_known("APP-OCR-MISSING")


def test_the_log_is_phi_free_and_appends(tmp_path):
    log = tmp_path / "support_log.jsonl"
    topaz.record("RUN-SUMMARY", log, files=8, deidentified=5, held=0, set_aside=2, failed=1)
    topaz.record("RUN-UNACCOUNTED", log)
    lines = log.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["code"] == "RUN-SUMMARY" and first["known"] is True
    assert first["context"]["files"] == 8 and "app_version" in first
    # Only the fields we passed — no path, no file name, no text.
    assert set(first["context"]) <= {"files", "deidentified", "held", "set_aside", "failed",
                                     "guard_refused"}


def test_record_without_a_path_writes_nothing_but_returns_the_event():
    ev = topaz.record("APP-STORE")
    assert ev["code"] == "APP-STORE" and ev["known"] is True


def test_the_codebook_renders_for_the_it_person():
    md = topaz.codebook_markdown()
    assert "Support codes" in md
    # A handful of representative codes across categories appear with their fix.
    for code in ("APP-OCR-MISSING", "APP-STORE", "RUN-DISK-FULL", "REVIEW-FILE-MISMATCH"):
        assert f"`{code}`" in md
    assert "ZC-" in md                                  # the guard's codes are cross-referenced
    # The codebook never needs the source or patient data to be useful.
    assert "do not need the" in md.lower() or "you do not need" in md.lower()


def test_the_cli_writes_a_shareable_support_log(tmp_path, monkeypatch):
    from inkguard import cli
    inp = tmp_path / "in" / "p1"
    inp.mkdir(parents=True)
    (inp / "note.txt").write_text("Seen today for knee pain. Plan: rest.\n")
    stats = cli.RunStats()
    cli.process_directory(None, tmp_path / "in", tmp_path / "out", stats, None, False, True, mode="mask")
    log = tmp_path / "out" / ".inkguard" / "support_log.jsonl"
    assert log.exists()
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert any(e["code"] == "RUN-SUMMARY" for e in events)
    # Nothing in the shareable log is a path or a patient detail.
    blob = log.read_text().lower()
    assert "note.txt" not in blob and "knee" not in blob and str(tmp_path).lower() not in blob


def test_folder_overlap_is_refused_with_its_code(tmp_path):
    from inkguard import cli
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "note.txt").write_text("x")
    stats = cli.RunStats()
    try:
        cli.process_directory(None, tmp_path / "in", tmp_path / "in" / "out", stats,
                              None, False, True, mode="mask")
        assert False, "expected a refusal"
    except SystemExit as e:
        assert "RUN-FOLDER-OVERLAP" in str(e)
