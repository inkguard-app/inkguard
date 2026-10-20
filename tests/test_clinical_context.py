"""
Clinical safety tests.

These test the failure mode nobody instruments: OVER-redaction.
A test failing here does not mean PHI leaked — it means clinical
meaning was destroyed, which leaves an unsafe record for whoever reads it next
while the run still reports success.

Treat red tests here with the same seriousness as a leak.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inkguard.policy import evaluate, Action
from inkguard.clinical_lexicon import is_clinical_term
from inkguard.clinical_context import verify
from inkguard import hipaa


# ---------- Eponyms (diagnoses named after people) ----------
def test_unambiguous_eponyms_survive():
    """Wernicke/Korsakoff/Crohn/Guillain-Barre are diagnoses, not names."""
    text = "Wernicke encephalopathy with Korsakoff features. Also Crohn disease and Guillain-Barre."
    r = evaluate(text)
    for term in ["Wernicke", "Korsakoff", "Crohn", "Guillain-Barre"]:
        assert term in r.clean_text, f"{term} was wrongly redacted as PHI"


def test_eponymous_signs_survive():
    text = "Osler nodes, Janeway lesions, Roth spots. Duke criteria met."
    r = evaluate(text)
    for term in ["Osler", "Janeway", "Roth", "Duke"]:
        assert term in r.clean_text


def test_multiword_eponym_survives():
    r = evaluate("Torsades de pointes after ondansetron.")
    assert "Torsades de pointes" in r.clean_text


# ---------- Ambiguity: eponym vs. real patient name ----------
def test_ambiguous_eponym_alone_is_not_clinical():
    """Bare 'Wilson' could be a patient. Must NOT be auto-protected."""
    assert is_clinical_term("Wilson") is False
    assert is_clinical_term("Jones") is False
    assert is_clinical_term("Graves") is False


def test_ambiguous_eponym_with_qualifier_is_clinical():
    assert is_clinical_term("Wilson disease") is True
    assert is_clinical_term("Jones criteria") is True
    assert is_clinical_term("Bell palsy") is True


def test_patient_name_redacted_but_eponym_kept_same_sentence():
    """The hardest case: same surname as patient name AND as disease."""
    text = "Patient Sarah Wilson, 61yo, MRN C445566. Rule out Wilson disease."
    r = evaluate(text)
    assert "Sarah Wilson" not in r.clean_text, "patient name leaked"
    assert "Wilson disease" in r.clean_text, "diagnosis was destroyed"


def test_title_beats_qualifier():
    """'Mrs. Graves' is a person even though 'Graves disease' exists."""
    r = evaluate("Mrs. Graves has Graves disease. TSH 0.01.")
    assert "Mrs. Graves" not in r.clean_text
    assert "Graves disease" in r.clean_text


# ---------- Clinical values must never be redacted ----------
def test_vitals_preserved():
    r = evaluate("BP 180/120, HR 110, SpO2 88%. COPD exacerbation.")
    for v in ["BP 180/120", "HR 110", "SpO2 88%"]:
        assert v in r.clean_text, f"vital sign {v} was redacted"


def test_labs_preserved():
    r = evaluate("Patient Maria Lopez: K 6.2, Cr 2.1, BUN 45, Na 128.")
    for lab in ["K 6.2", "Cr 2.1", "BUN 45", "Na 128"]:
        assert lab in r.clean_text
    assert "Maria Lopez" not in r.clean_text  # name still goes


def test_dose_and_frequency_preserved():
    r = evaluate("Started prednisone 60 mg daily and lithium 900 mg qhs.")
    assert "60 mg daily" in r.clean_text
    assert "900 mg qhs" in r.clean_text


def test_negation_preserved():
    """Losing a negation inverts clinical meaning — the most dangerous
    possible redaction artifact."""
    r = evaluate("John Smith denies chest pain, no fever, no hemoptysis.")
    assert "denies chest pain" in r.clean_text
    assert "no fever" in r.clean_text


def test_relative_time_preserved():
    r = evaluate("Mr. Okafor: sudden onset 3 days ago, worsening over 2 weeks.")
    assert "3 days ago" in r.clean_text
    assert "2 weeks" in r.clean_text


# ---------- HIPAA Safe Harbor rules ----------
def test_age_under_90_retained():
    """Ages <90 are NOT Safe Harbor identifiers and are diagnostically
    essential. Redacting them is legally unnecessary and clinically harmful."""
    r = evaluate("67-year-old male with crushing substernal chest pain.")
    assert "67-year-old" in r.clean_text


def test_age_90_or_over_generalized():
    r = evaluate("94 years old with new confusion.")
    assert "90 or older" in r.clean_text
    assert "94" not in r.clean_text


def test_zip_truncated_to_three_digits():
    r = evaluate("Patient lives in ZIP 90210.")
    assert "902**" in r.clean_text
    assert "90210" not in r.clean_text


def test_restricted_zip_zeroed():
    """036 is on the HHS <=20,000 population list — must become 000."""
    r = evaluate("Address ZIP 03612 on file.")
    assert "000**" in r.clean_text
    assert "03612" not in r.clean_text


def test_safe_harbor_coverage_report_declares_gaps():
    report = hipaa.coverage_report()
    assert report["identifiers_total"] == 18
    gap_ids = [g["identifier"] for g in report["known_gaps"]]
    # Biometrics and photos are genuinely uncovered in a text tool;
    # the report must say so rather than claiming full coverage.
    assert "16_biometric_identifiers" in gap_ids
    assert "17_facial_photographs" in gap_ids
    assert "disclaimer" in report


# ---------- Integrity checker itself ----------
def test_integrity_detects_destroyed_clinical_content():
    original = "Patient with Guillain-Barre, K 6.2, no fever."
    mangled = "Patient with <PERSON>, <LAB>, <NEG>."
    report = verify(original, mangled)
    assert report.is_intact is False
    assert report.score < 1.0


def test_integrity_passes_on_clean_redaction():
    original = "John Smith has Guillain-Barre, K 6.2, no fever."
    redacted = "<PERSON> has Guillain-Barre, K 6.2, no fever."
    report = verify(original, redacted)
    assert report.is_intact is True


# ---------- Open vocabulary (ontology-derived; see lexicon_open.py) ----------
def test_mined_eponyms_survive():
    """Terms drawn from the open vocabulary (lexicon_open.py) that the
    hand-written lexicon never covered. A name detector alone would remove them."""
    text = ("Kartagener syndrome, Goodpasture disease, Krukenberg tumor, "
            "Wegener granulomatosis, Hirschsprung disease, Beckwith-Wiedemann.")
    r = evaluate(text)
    for term in ["Kartagener", "Goodpasture", "Krukenberg", "Wegener",
                 "Hirschsprung", "Beckwith"]:
        assert term in r.clean_text, f"{term} wrongly redacted"


def test_mined_conditions_survive():
    r = evaluate("Abetalipoproteinemia, bronchiectasis, hematuria, situs inversus noted.")
    for term in ["Abetalipoproteinemia", "bronchiectasis", "hematuria"]:
        assert term in r.clean_text


def test_mined_drugs_survive():
    r = evaluate("Started rituximab, cyclophosphamide, atezolizumab and plasmapheresis.")
    for term in ["rituximab", "cyclophosphamide", "atezolizumab"]:
        assert term in r.clean_text


def test_mined_ambiguous_names_still_require_qualifier():
    """The safety property of the mining pipeline: eponyms that are
    also common surnames must NOT be auto-protected, or a patient
    named Murphy or Gardner never gets redacted."""
    for name in ["Murphy", "Gardner", "Lynch", "Hansen", "Stevens", "Osler"]:
        assert is_clinical_term(name) is False, f"{name} is a plausible patient name"
    for concept in ["Murphy sign", "Gardner syndrome", "Osler nodes"]:
        assert is_clinical_term(concept) is True


def test_patient_named_after_eponym_is_redacted():
    """End-to-end version of the above — the failure that would leak."""
    r = evaluate("Patient Rebecca Murphy, MRN E112233, has a positive Murphy sign.")
    assert "Rebecca Murphy" not in r.clean_text
    assert "Murphy sign" in r.clean_text


def test_expanded_lexicon_did_not_become_a_bypass():
    """Growing the lexicon 3x must not create new escape routes for
    structured PHI sitting next to medical words."""
    text = ("SSN 123-45-6789, phone 555-555-0134, MRN A1234567 "
            "with Kartagener syndrome and bronchiectasis.")
    r = evaluate(text)
    for leak in ["123-45-6789", "555-555-0134", "A1234567"]:
        assert leak not in r.clean_text, f"PHI leaked next to clinical term: {leak}"
def test_phi_still_redacted_in_clinical_text():
    """The clinical layer must not become a PHI bypass."""
    text = ("Pt Robert Chen, MRN A1234567, SSN 123-45-6789, "
            "phone 555-555-0134, has Wernicke encephalopathy.")
    r = evaluate(text)
    assert r.action == Action.REDACTED
    for leak in ["Robert Chen", "A1234567", "123-45-6789", "555-555-0134"]:
        assert leak not in r.clean_text, f"PHI leaked: {leak}"
    assert "Wernicke" in r.clean_text
