"""
Input classification tests.

The classifier earns its place in three ways, and each is tested here:

  1. FIELD ANCHORING — on labelled input, the label is stronger evidence
     than any model inference. "MRN: 4471" is certain; NER sees four
     digits and shrugs. Anchoring catches identifiers the other layers
     miss entirely, because it never has to recognize the value.

  2. PROFILE SELECTION — the same characters mean different things by
     class. Bare numbers are results in a lab report and account numbers
     in a claim. Getting it backwards either shreds lab values or leaks
     accounts.

  3. DENSITY ANOMALY — a form with zero identifiers is a detection
     failure, not a clean form. Nothing else in the pipeline can tell
     those apart, because "found nothing" and "nothing to find" look
     identical without an expectation to compare against.
"""
import sys
import os
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inkguard.policy import evaluate
from inkguard.input_classifier import (
    classify, InputClass, extract_anchored_fields, density_anomaly,
)

FORM = """Patient Name: Sarah Wilson
DOB: 03/14/1958
MRN: 4471
SSN: 123-45-6789
Phone: (555) 555-0134
Member ID: BCBS99881
Address: 4521 Maple St"""

LAB = """Na 128 mEq/L (L)
K 6.2 mEq/L (H)
Cr 2.1 mg/dL
Hgb 8.4 g/dL"""

MEDS = """Metoprolol 25 mg PO BID
Lisinopril 10 mg PO daily
Insulin glargine 20 units SQ QHS"""

SOAP = """S: 67yo M c/o chest pain x2 days.
O: BP 180/120, HR 110. CT chest neg for PE.
A: Hypertensive urgency.
P: Start amlodipine 5 mg daily."""

IMAGING = """CT ABDOMEN/PELVIS WITH CONTRAST
Technique: Axial images obtained.
Findings: Cholelithiasis. No free fluid.
Impression: Uncomplicated cholelithiasis."""


# ---------- Classification ----------
def test_structured_form_recognized():
    assert classify(FORM).input_class is InputClass.STRUCTURED_FORM


def test_lab_report_recognized():
    assert classify(LAB).input_class is InputClass.LAB_REPORT


def test_medication_list_recognized():
    assert classify(MEDS).input_class is InputClass.MEDICATION_LIST


def test_soap_note_recognized():
    assert classify(SOAP).input_class is InputClass.SOAP_NOTE


def test_imaging_report_recognized():
    assert classify(IMAGING).input_class is InputClass.IMAGING_REPORT


def test_reference_question_recognized():
    c = classify("What is the mechanism of action of metformin?")
    assert c.input_class is InputClass.CLINICAL_QUESTION
    assert c.profile.expects_phi is False


def test_classification_reports_its_evidence():
    """A classification the user cannot question is one they
    cannot correct."""
    c = classify(FORM)
    assert c.evidence
    assert "field" in c.evidence[0].lower()


def test_unknown_falls_back_to_strictest_profile():
    """A misread class must never loosen detection."""
    strict = classify("").profile
    assert strict.use_field_anchors is True
    assert strict.bare_numbers_suspect is True
    assert strict.expects_phi is True


def test_low_confidence_uses_strict_profile():
    from inkguard.input_classifier import Classification
    c = Classification(InputClass.MEDICATION_LIST, 0.1, [])
    assert c.profile.expects_phi is True  # not the med-list profile


# ---------- Profile differences that matter ----------
def test_lab_and_claim_treat_bare_numbers_differently():
    lab = classify(LAB).profile
    from inkguard.input_classifier import PROFILES
    claim = PROFILES[InputClass.BILLING_CLAIM]
    assert lab.bare_numbers_suspect is False, "lab values would be shredded"
    assert claim.bare_numbers_suspect is True, "account numbers would leak"


# ---------- Field anchoring ----------
def test_anchors_extract_labelled_values():
    fields = extract_anchored_fields(FORM)
    types = {f.entity_type for f in fields}
    assert "PERSON" in types
    assert "MRN" in types
    assert "US_SSN" in types
    assert "HEALTH_PLAN_ID" in types


def test_anchor_catches_short_mrn_ner_would_miss():
    """'MRN: 4471' is four characters — below every pattern threshold.
    The label is what makes it certain."""
    r = evaluate("MRN: 4471\nPatient Name: Sarah Wilson")
    assert "4471" not in r.clean_text


def test_anchor_catches_unusual_name_format():
    """Anchoring never has to recognize the value, so name formats NER
    was not trained on are still caught."""
    r = evaluate("Patient Name: Nguyễn Thị Hương\nMRN: 88231")
    assert "Hương" not in r.clean_text


def test_field_labels_are_never_redacted():
    """Redacting labels destroys the form's structure while protecting
    nothing — and 'Address' matches the loose plan-ID pattern."""
    r = evaluate(FORM)
    for label in ["Patient Name:", "DOB:", "MRN:", "SSN:", "Address:", "Member ID:"]:
        assert label in r.clean_text, f"label {label} was redacted"


def test_anchored_values_are_redacted():
    r = evaluate(FORM)
    for value in ["Sarah Wilson", "123-45-6789", "BCBS99881", "4521 Maple St"]:
        assert value not in r.clean_text, f"{value} leaked"


def test_a_name_ends_where_an_identifier_label_and_number_begin():
    """A PDF line loses the double space between fields. The name used to
    run on to the record number, the engine could not settle the span and
    stopped the whole file."""
    f = [x for x in extract_anchored_fields("Patient: Hubert Lindqvist MRN 4471822")
         if x.entity_type == "PERSON"]
    assert [x.value for x in f] == ["Hubert Lindqvist"]
    r = evaluate("Patient: Hubert Lindqvist MRN 4471822")
    assert r.action.value != "blocked", r.error
    assert "Lindqvist" not in r.clean_text and "4471822" not in r.clean_text
    # A name followed by an ordinary word is untouched by this rule.
    f = [x for x in extract_anchored_fields("Patient: Ida Rose") if x.entity_type == "PERSON"]
    assert [x.value for x in f] == ["Ida Rose"]


def test_placeholder_values_are_not_treated_as_identifiers():
    fields = extract_anchored_fields("Patient Name: N/A\nMRN: none")
    assert fields == []


# ---------- Density anomaly ----------
def test_form_with_no_identifiers_is_flagged():
    c = classify(FORM)
    msg = density_anomaly(c, identifier_count=0)
    assert msg is not None
    assert "detection miss" in msg.lower() or "verify" in msg.lower()


def test_form_with_identifiers_is_not_flagged():
    assert density_anomaly(classify(FORM), identifier_count=6) is None


def test_reference_question_with_many_identifiers_is_flagged():
    """The reverse anomaly: patient data showing up where none belongs."""
    c = classify("What is the mechanism of action of metformin?")
    msg = density_anomaly(c, identifier_count=4)
    assert msg is not None


def test_density_anomaly_surfaces_in_diagnosis():
    from inkguard.basalt import diagnose
    d = diagnose("Patient Name: \nDOB: \nMRN: \nSSN: ", identifier_count=0)
    assert any(f.code == "IDENTIFIER_DENSITY_ANOMALY" for f in d.findings)


# ---------- Classification must not weaken detection ----------
def test_medication_list_still_redacts_names():
    r = evaluate("Patient Sarah Wilson\nMetoprolol 25 mg PO BID\nLisinopril 10 mg daily")
    assert "Sarah Wilson" not in r.clean_text
    assert "Metoprolol 25 mg PO BID" in r.clean_text


def test_lab_values_survive_classification():
    r = evaluate("Patient Sarah Wilson, MRN 4471\n" + LAB)
    for v in ["Na 128", "K 6.2", "Cr 2.1", "Hgb 8.4"]:
        assert v in r.clean_text, f"lab value {v} destroyed"
    assert "Sarah Wilson" not in r.clean_text
