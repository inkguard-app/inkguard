"""
Clinical translator tests.

The failure this layer exists to prevent runs in BOTH directions within
a single sentence, which is what makes it distinct from the lexicon:

  over-redaction : "LA enlargement on echo"  → left atrium destroyed
  under-redaction: "Patient resides in CT"   → Connecticut leaked

A lexicon cannot fix either, because the same token is correct in both
readings. Only context separates them.
"""
import sys
import os
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inkguard.policy import evaluate
from inkguard.clinical_translator import (
    resolve, Sense, complexity_score, needs_translation, clarify,
)


def _sense(text, token):
    """First resolution for `token` in `text`."""
    for r in resolve(text):
        if r.token == token:
            return r.sense
    return None


# ---------- Clinical sense recognized ----------
def test_ct_scan_is_clinical():
    assert _sense("CT head negative for bleed.", "CT") is Sense.CLINICAL


def test_subcutaneous_is_clinical():
    assert _sense("Insulin 5 units SC qhs.", "SC") is Sense.CLINICAL


def test_left_atrium_is_clinical():
    assert _sense("LA enlargement on echo.", "LA") is Sense.CLINICAL


def test_ultrasound_is_clinical():
    assert _sense("US abdomen showed cholelithiasis.", "US") is Sense.CLINICAL


# ---------- Geographic sense recognized ----------
def test_resides_in_ct_is_identifier():
    assert _sense("Patient resides in CT.", "CT") is Sense.IDENTIFIER


def test_lives_in_mi_is_identifier():
    assert _sense("Lives in MI.", "MI") is Sense.IDENTIFIER


def test_address_frame_is_identifier():
    assert _sense("Springfield, MA 01103", "MA") is Sense.IDENTIFIER


# ---------- Directional context (the bugs that were found) ----------
def test_geo_cue_does_not_leak_backward_across_clause():
    """'Patient resides in CT' at the END of a sentence must not make
    'LA' at the START into Louisiana."""
    text = "MS exacerbation, LA enlargement on echo. Patient resides in CT."
    assert _sense(text, "LA") is Sense.CLINICAL
    assert _sense(text, "CT") is Sense.IDENTIFIER


def test_geo_cue_does_not_cross_sentence_boundary():
    """'transferred from Springfield...' must not make the NEXT
    sentence's 'US abdomen' into United States."""
    text = "Pt transferred from Springfield, MA 01103. US abdomen showed stones."
    assert _sense(text, "US") is Sense.CLINICAL


def test_same_token_both_senses_in_one_note():
    text = "s/p MI. CT chest neg for PE. Lives in MI."
    senses = [r.sense for r in resolve(text) if r.token == "MI"]
    assert Sense.CLINICAL in senses
    assert Sense.IDENTIFIER in senses


# ---------- Fail-closed on ambiguity ----------
def test_unplaceable_token_defaults_to_redaction():
    """No disambiguating context means redact, not assume safe."""
    assert _sense("Note: CT.", "CT") is Sense.UNRESOLVED


# ---------- Complexity gating ----------
def test_plain_prose_skips_translation():
    assert needs_translation("What are the side effects of ibuprofen?") is False


def test_dense_shorthand_triggers_translation():
    assert needs_translation("Pt s/p MI, CT chest neg, 5 units SC qhs, r/o PE") is True


def test_complexity_score_ordering():
    plain = complexity_score("The patient reports feeling better today.")
    dense = complexity_score("Pt c/o SOB. CT chest neg. MI r/o. 5u SC bid.")
    assert dense > plain


# ---------- End-to-end through the tool ----------
def test_clinical_abbreviations_survive():
    r = evaluate("CT head negative. MI ruled out. Insulin 5 units SC qhs.")
    for term in ["CT head", "MI ruled out", "SC qhs"]:
        assert term in r.clean_text, f"{term} destroyed by redaction"


def test_state_code_is_redacted():
    r = evaluate("MS exacerbation, LA enlargement on echo. Patient resides in CT.")
    assert "resides in CT" not in r.clean_text, "state code leaked"
    assert "LA enlargement" in r.clean_text, "left atrium destroyed"


def test_short_mrn_is_caught():
    """A 5-character MRN was slipping through the old 6-10 window."""
    r = evaluate("Pt Diane Vasquez, MRN F9988, s/p MI.")
    assert "F9988" not in r.clean_text


# ---------- clarify() is opt-in and conservative ----------
def test_clarify_expands_only_confident_clinical():
    out, res = clarify("CT head negative. Patient resides in CT.")
    assert "computed tomography" in out
    # The geographic one must never be expanded into a clinical reading.
    assert out.count("computed tomography") == 1


def test_clarify_does_not_run_in_redaction_path():
    """Redaction must operate on the original text, not an expansion —
    a wrong expansion would silently alter clinical meaning."""
    original = "CT head negative."
    r = evaluate(original)
    assert "computed tomography" not in r.clean_text
