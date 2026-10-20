"""amber — the voluntary, PHI-free feedback report (inkguard/amber.py).

The one rule amber cannot break: nothing it marks sendable may carry patient
information. These tests push patient details into every field a doctor can
fill and assert they never survive into the sealed report.
"""
from __future__ import annotations

import json

from inkguard import amber


# ---- what went wrong: built only from the support log -----------------

def test_summary_counts_codes_and_skips_the_normal_end_line():
    events = [
        {"code": "DOC-STOP"}, {"code": "DOC-STOP"}, {"code": "FILE-IMAGE"},
        {"code": "RUN-SUMMARY"},                       # not a problem — skipped
    ]
    out = amber.summarize_support_log(events)
    codes = {w["code"]: w["count"] for w in out}
    assert codes == {"DOC-STOP": 2, "FILE-IMAGE": 1}
    assert all(w["code"] != "RUN-SUMMARY" for w in out)
    # Each carries the codebook explanation, not raw internals.
    assert all(w["means"] for w in out)


def test_summary_ignores_any_context_a_caller_attached():
    # Even if a caller wrongly put text in a support event, the summary takes
    # only code + count — never the context.
    events = [{"code": "DOC-STOP", "context": {"note": "Mr Okafor chart"}}]
    out = amber.summarize_support_log(events)
    assert "Okafor" not in json.dumps(out)


# ---- scrubbing the doctor's free text ---------------------------------

def test_scrub_masks_a_name_in_free_text():
    cleaned, n = amber.scrub("Mr. Okafor's chart would not open.")
    assert "Okafor" not in cleaned
    assert n >= 1


def test_scrub_masks_contact_shaped_identifiers():
    cleaned, n = amber.scrub(
        "reach me at jane.doe@example.org or 555-555-0123, record 00482213")
    assert "@example.org" not in cleaned
    assert "555-555-0123" not in cleaned
    assert "00482213" not in cleaned
    assert n >= 3


def test_scrub_empty_is_empty():
    assert amber.scrub("") == ("", 0)
    assert amber.scrub("   ") == ("", 0)


# ---- building the sealed report ---------------------------------------

def test_build_is_phi_free_and_ready_when_text_is_clean():
    fr = amber.build(
        support_events=[{"code": "DOC-STOP"}],
        run_shape={"found": 40, "deidentified": 38, "failed": 1, "guard_refused": 1},
        note="A few PDFs would not open; the rest worked well.",
        suggestion="Maybe show which page is the problem.",
        contact={"name": "Dr. A. Smith", "email": "a.smith@example.org"},
        os_name="Windows 11",
    )
    assert fr.ready is True
    assert fr.blocked_fields == []
    # The doctor's OWN contact is kept verbatim — it is not patient data.
    assert fr.contact["email"] == "a.smith@example.org"
    blob = amber.to_json(fr)
    assert "DOC-STOP" in blob
    assert fr.run_shape == {"found": 40, "deidentified": 38, "failed": 1, "guard_refused": 1}


def test_build_blanks_and_flags_a_note_that_still_holds_phi():
    # A note dense with identifiers that survive scrubbing must not be sent.
    fr = amber.build(note="Patient Okafor, MRN 00482213, DOB 03/14/1951, kept crashing it.")
    # Either the field is fully scrubbed clean, or it is blanked and flagged —
    # never passed through with the identifier intact.
    assert "Okafor" not in amber.to_json(fr)
    assert "00482213" not in amber.to_json(fr)
    if fr.blocked_fields:
        assert fr.ready is False
        assert "note" in fr.blocked_fields


def test_run_shape_only_lets_known_numeric_counts_through():
    fr = amber.build(run_shape={
        "found": 10, "deidentified": 9,
        "secret_path": "/home/dr/patients/okafor.docx",   # dropped
        "verified": True,                                  # bool dropped
    })
    assert fr.run_shape == {"found": 10, "deidentified": 9}
    assert "okafor" not in amber.to_json(fr).lower()


def test_report_id_is_random_not_derived():
    a = amber.build(note="x")
    b = amber.build(note="x")
    assert a.report_id != b.report_id


def test_markdown_shows_exactly_what_leaves_and_warns_when_not_ready():
    fr = amber.build(note="Patient Okafor MRN 00482213 DOB 03/14/1951 phone 555-555-0123 everywhere")
    md = fr.render if hasattr(fr, "render") else amber.render_markdown(fr)
    assert "Okafor" not in md and "00482213" not in md
    if not fr.ready:
        assert "not ready to send" in md.lower()


def test_whole_serialized_report_carries_no_injected_identifier():
    fr = amber.build(
        support_events=[{"code": "FILE-IMAGE"}],
        note="Okafor's scan jane@example.org 5555550123 00482213",
        suggestion="call 555-555-0187, patient Rodriguez",
        contact={"name": "Dr Smith"},
    )
    blob = amber.to_json(fr)
    for leak in ("Okafor", "Rodriguez", "jane@example.org", "5555550123",
                 "00482213", "555-555-0187"):
        assert leak not in blob, f"{leak} leaked into the sealed report"


# ---- delivering the report ---------------------------------------------

def test_save_writes_both_forms_and_refuses_an_unready_report(tmp_path):
    fr = amber.build(note="A few PDFs would not open.")
    out = amber.save(fr, tmp_path)
    assert json.loads(open(out["json"]).read())["report_id"] == fr.report_id
    bad = amber.build(note="x")
    bad.ready = False
    import pytest
    with pytest.raises(amber.NotSendable):
        amber.save(bad, tmp_path)


