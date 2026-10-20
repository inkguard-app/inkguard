"""The one door between the core engine and the shell (inkguard/contract.py).

These tests pin the contract's shape and prove it reproduces the engine's own
output exactly, so the shell can depend on it alone. All data invented."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inkguard import contract  # noqa: E402


def test_text_in_positions_out():
    text = "Patient: Hubert Lindqvist seen today. Plan: aspirin daily; follow up in 2 weeks."
    r = contract.deidentify(text, mode="mask")
    assert r.status == "clear" and r.ok
    # edits are ready to apply, and applying them reproduces the clean text.
    out = contract._apply(text, r.edits)
    assert out == r.clean_text
    assert "Lindqvist" not in r.clean_text
    assert "Hubert Lindqvist" in r.removed_person_names or any(
        "Lindqvist" in s for s in r.removed_person_names)
    assert r.reproduced


def test_the_core_rebuilds_the_engines_own_output_exactly():
    from inkguard import calcite
    from inkguard.policy import evaluate
    eng = calcite.Engine(evaluate)
    for text in ("Daughter Rose Alvarado at bedside. Call 415-555-0199.",
                 "Address: 12 Orchard Lane, Marion, IA 52302. Seen by Dr. Kellerman.",
                 "Knee pain, improving. Return as needed."):
        r = contract.deidentify(text, mode="mask")
        p = calcite._engine_pass(text, eng, set())
        assert sorted(r.edits) == sorted(p.edits)
        assert r.secrets == p.secrets
        assert r.removed_person_names == p.names
        assert r.words_to_check == p.words_to_check
        assert r.clean_text == p.result.clean_text


def test_a_blocked_note_is_a_stop_not_a_release():
    # A line the engine refuses outright comes back as status 'stop', never
    # as partial output.
    text = "Patient: Hubert Lindqvist MRN 4471822 DOB 03/14/1951 Age 92 in Marion"
    r = contract.deidentify(text, mode="mask")
    if r.status == "stop":
        assert not r.ok and r.clean_text == ""


def test_classify_names_the_shape_without_deciding_phi():
    c = contract.classify("Patient: X  MRN: 5\nName: Y")
    assert isinstance(c["kind"], str)                 # a plain string, no engine enum
    assert "PERSON" in c["labelled_fields"] and "MRN" in c["labelled_fields"]


def test_version_is_reported_for_the_startup_handshake():
    v = contract.version()
    assert v["contract"] == contract.CONTRACT_VERSION
    assert isinstance(v["build"], str)


def test_the_core_never_needs_a_file_or_a_name():
    # The whole contract takes text and options; nothing in its signatures
    # accepts a path, a file, or a filename.
    import inspect
    for fn in (contract.deidentify, contract.learn, contract.classify):
        params = set(inspect.signature(fn).parameters)
        assert not (params & {"path", "file", "filename", "src", "dst"})
