"""
Tests for interval-preserving date shifting.

The three properties that make this worth shipping, and the ways each
can silently fail:

  intervals preserved   If offsets differ within a patient, length of
                        stay and time-to-readmission become fiction and
                        the research built on them is wrong. This is the
                        property researchers are trusting.
  dates not recoverable If the offset is global, one known patient
                        unshifts the entire corpus.
  nothing else moves    A blood pressure is shaped like a date. Moving
                        one is a clinical-safety defect that buys no
                        privacy at all.
"""
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PHI_ROOT_KEY", "ab" * 32)

from inkguard.date_shift import (  # noqa: E402
    shift_text, derive_offset, DEFAULT_MAX_OFFSET_DAYS,
)
from inkguard.security import KeyStore  # noqa: E402

KS = KeyStore(root_key=b"k" * 32)


def _dates(text):
    import re
    return [date(int(y), int(m), int(d))
            for m, d, y in re.findall(r"\b(\d{2})/(\d{2})/(\d{4})\b", text)]


# ------------------------------------------------------- the core property

def test_intervals_are_preserved_exactly():
    """The property the research depends on."""
    t = "Admitted 03/14/2024. Discharged 03/19/2024. Readmitted 04/03/2024."
    before = _dates(t)
    after = _dates(shift_text(t, "patient-A", keystore=KS)[0])
    assert len(after) == 3
    for i in range(len(before) - 1):
        assert (after[i + 1] - after[i]) == (before[i + 1] - before[i])


def test_absolute_dates_actually_change():
    t = "Admitted 03/14/2024."
    out, _ = shift_text(t, "patient-A", keystore=KS)
    assert "03/14/2024" not in out


def test_age_at_encounter_survives():
    """DOB moves with everything else, so computed age is unchanged —
    the reason DOB must be shifted rather than left alone."""
    t = "DOB 1962-03-14. Seen 03/14/2024."
    out, _ = shift_text(t, "patient-A", keystore=KS)
    import re
    dob = re.search(r"(\d{4})-(\d{2})-(\d{2})", out)
    enc = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", out)
    d1 = date(int(dob.group(1)), int(dob.group(2)), int(dob.group(3)))
    d2 = date(int(enc.group(3)), int(enc.group(1)), int(enc.group(2)))
    assert (d2 - d1).days == (date(2024, 3, 14) - date(1962, 3, 14)).days


# ------------------------------------------------------- offset security

def test_offset_differs_per_patient():
    """A global offset means one known patient unshifts everyone."""
    offsets = {derive_offset(f"patient-{i}", keystore=KS) for i in range(60)}
    assert len(offsets) > 40, "offsets are not well distributed across patients"


def test_offset_is_deterministic_across_runs_and_documents():
    """Same patient, different note, same shift — otherwise intervals
    hold within a document but break across encounters."""
    a = derive_offset("patient-A", keystore=KS)
    b = derive_offset("patient-A", keystore=KS)
    assert a == b


def test_offset_depends_on_the_key():
    """Without this the shift is reversible by anyone who reads the code."""
    other = KeyStore(root_key=b"z" * 32)
    assert derive_offset("patient-A", keystore=KS) != \
        derive_offset("patient-A", keystore=other)


def test_offset_is_never_zero():
    """A zero offset leaves a record unshifted and indistinguishable
    from one that was never processed."""
    for i in range(500):
        assert derive_offset(f"p{i}", keystore=KS) != 0


def test_offset_stays_in_window():
    for i in range(200):
        assert abs(derive_offset(f"p{i}", keystore=KS)) <= DEFAULT_MAX_OFFSET_DAYS


def test_offset_is_never_reported():
    """Publishing the offset hands back the secret the shift relies on."""
    _, rep = shift_text("Admitted 03/14/2024.", "patient-A", keystore=KS)
    blob = str(rep.to_dict()).lower()
    assert "offset_days" not in blob
    assert str(abs(derive_offset("patient-A", keystore=KS))) not in blob


# ------------------------------------------------------- must not move

def test_vitals_and_ratios_are_untouched():
    t = "BP 120/80, I/O 1200/800, HR 72. Admitted 03/14/2024."
    out, _ = shift_text(t, "patient-A", keystore=KS)
    assert "BP 120/80" in out
    assert "I/O 1200/800" in out
    assert "HR 72" in out


def test_relative_time_is_untouched():
    """Already non-identifying, and correct only if left alone."""
    t = "Seen 3 days ago. Post-op day 2. Admitted 03/14/2024."
    out, _ = shift_text(t, "patient-A", keystore=KS)
    assert "3 days ago" in out
    assert "Post-op day 2" in out


def test_impossible_dates_are_not_shifted():
    """02/30 is shaped like a date and is not one."""
    t = "Reference 02/30/2024 and 13/45/1999."
    out, _ = shift_text(t, "patient-A", keystore=KS)
    assert "02/30/2024" in out and "13/45/1999" in out


# ------------------------------------------------------- formats

def test_format_is_preserved():
    t = "A 03/14/2024, B 2024-03-14, C March 14, 2024, D 14 Mar 2024."
    out, rep = shift_text(t, "patient-A", keystore=KS)
    assert rep.shifted == 4
    import re
    assert re.search(r"A \d{2}/\d{2}/\d{4}", out)
    assert re.search(r"B \d{4}-\d{2}-\d{2}", out)
    assert re.search(r"C [A-Z][a-z]+ \d{1,2}, \d{4}", out)
    assert re.search(r"D \d{1,2} [A-Z][a-z]{2} \d{4}", out)


def test_all_formats_shift_by_the_same_amount():
    """A patient's dates must move together regardless of how each was
    written, or intervals break between differently-formatted dates."""
    same_day = "03/14/2024 and 2024-03-14 and March 14, 2024 and 14 Mar 2024"
    out, _ = shift_text(same_day, "patient-A", keystore=KS)
    import re
    found = set()
    m = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", out)
    found.add(date(int(m.group(3)), int(m.group(1)), int(m.group(2))))
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", out)
    found.add(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    assert len(found) == 1, f"same date shifted inconsistently: {found}"


def test_offset_is_stable_across_separate_calls_without_explicit_keystore():
    """REGRESSION: found by running a large synthetic batch.

    derive_offset() constructed KeyStore() per call. With PHI_ROOT_KEY
    unset that generates a fresh random root each time, so every date in
    a batch received its own offset: intervals held inside one date pair
    and broke everywhere else. Invisible in any single file, total across
    the batch. The keystore is now process-shared.
    """
    import inkguard.date_shift as D
    a = D.derive_offset("patient-A")
    b = D.derive_offset("patient-A")
    c = D.derive_offset("patient-A")
    assert a == b == c, "same patient shifted differently across calls"


def test_same_patient_same_offset_across_documents():
    """The cross-encounter promise: two notes about one person must move
    together, or time-to-readmission is fiction."""
    import inkguard.date_shift as D
    note1, _ = D.shift_text("Admitted 03/14/2024.", "patient-A")
    note2, _ = D.shift_text("Follow-up 03/14/2024.", "patient-A")
    assert _dates(note1) == _dates(note2)


def test_ephemeral_key_is_refused():
    """An ephemeral key changes on restart, so a set of notes processed in two
    sessions gets two offset families and every cross-batch interval is
    silently wrong. Refused rather than warned about."""
    import importlib
    import inkguard.date_shift as D
    saved = os.environ.pop("PHI_ROOT_KEY", None)
    try:
        importlib.reload(D)
        try:
            D.derive_offset("patient-A")
            assert False, "ephemeral key was accepted"
        except D.EphemeralKeyError as exc:
            assert "PHI_ROOT_KEY" in str(exc)
    finally:
        if saved is not None:
            os.environ["PHI_ROOT_KEY"] = saved
        importlib.reload(D)


def test_no_patient_key_means_no_shift():
    t = "Admitted 03/14/2024."
    out, rep = shift_text(t, "", keystore=KS)
    assert out == t and rep.shifted == 0


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
