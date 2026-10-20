"""
Detection checks. These are written to FAIL loudly if detection quality
regresses: a new failure here is a real regression in what the tool removes,
not a flaky test to skip.

Categories:
  1. Structured PHI (the pattern detectors should catch these cold)
  2. Narrative/embedded PHI (requires the NER layer)
  3. Edge cases (text that defeats a simple pattern match)
  4. Fail-closed behavior under scanner failure
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inkguard.policy import evaluate, Action


# ---------- 1. Structured PHI ----------
def test_ssn_detected():
    r = evaluate("Patient SSN is 123-45-6789 for verification.")
    assert r.action == Action.REDACTED
    assert "123-45-6789" not in r.clean_text
    assert any(h.entity_type == "US_SSN" for h in r.hits)


def test_phone_detected():
    r = evaluate("Call the patient back at (555) 555-0123 tomorrow.")
    assert r.action == Action.REDACTED
    assert "555" not in r.clean_text


def test_mrn_detected():
    r = evaluate("MRN: A1234567, admitted for evaluation.")
    assert r.action == Action.REDACTED
    assert "A1234567" not in r.clean_text


def test_email_detected():
    r = evaluate("Send results to jane.doe@example.com please.")
    assert r.action == Action.REDACTED


# ---------- 2. Narrative PHI (NER layer) ----------
def test_name_in_narrative():
    r = evaluate("The patient, John Alvarez, presented with chest pain.")
    assert r.action == Action.REDACTED
    assert "John Alvarez" not in r.clean_text


def test_address_in_narrative():
    r = evaluate("Patient resides at 4521 Maple Street, Denver, and reports fatigue.")
    assert r.action == Action.REDACTED


# ---------- 3. Adversarial / edge cases ----------
def test_multiple_entities_same_sentence():
    text = "John Smith, DOB 04/12/1985, SSN 987-65-4321, MRN: B7788990, phone 555-555-0144."
    r = evaluate(text)
    assert r.action == Action.REDACTED
    entity_types = {h.entity_type for h in r.hits}
    assert "US_SSN" in entity_types
    assert "MRN" in entity_types
    for leaked in ["987-65-4321", "B7788990", "555-555-0144"]:
        assert leaked not in r.clean_text


def test_clean_text_passes_through_unchanged():
    r = evaluate("What are common side effects of ibuprofen?")
    assert r.action == Action.ALLOW
    assert r.clean_text == "What are common side effects of ibuprofen?"


def test_empty_and_whitespace_input():
    assert evaluate("").action == Action.ALLOW
    assert evaluate("   ").action == Action.ALLOW


def test_lowercase_label_variants_still_caught():
    # Regex layer is case-insensitive on labels by design.
    r = evaluate("mrn: c1122334, patient stable.")
    assert r.action == Action.REDACTED


def test_no_double_redaction_on_overlapping_spans():
    r = evaluate("Contact: john@example.com")
    # exactly one hit should cover the email span, not multiple stacked tokens
    email_hits = [h for h in r.hits if h.entity_type == "EMAIL_ADDRESS"]
    assert len(email_hits) == 1


# ---------- 4. Fail-closed behavior ----------
def test_fail_closed_on_scanner_exception(monkeypatch):
    import inkguard.policy as policy_mod

    def broken_scan(text, *a, **k):
        raise RuntimeError("simulated NER engine crash")

    # evaluate() enters the scanner through scan_with_trace (the traced
    # pipeline); patch every entry point so the test cannot pass by
    # patching a function the policy no longer calls.
    monkeypatch.setattr(policy_mod, "scan", broken_scan, raising=False)
    monkeypatch.setattr(policy_mod, "scan_with_trace", broken_scan, raising=False)
    r = evaluate("this would normally be scanned")
    assert r.action == Action.BLOCKED
    assert r.error is not None


