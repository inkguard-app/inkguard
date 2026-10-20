"""
Tests for cross-patient contamination and template-residue detection.

The governing risk for this layer is FALSE ALARMS. A contamination
detector that fires on a clean note gets switched off by the person using it,
and a switched-off detector catches nothing. So roughly half of these
tests assert that it stays SILENT — on format variation, on repeated
mentions of the same record, on the legitimate multiplicity of provider
names and phone numbers.
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inkguard.contamination import (  # noqa: E402
    analyze, check_contamination, check_template_residue,
    normalize_identifier, normalize_date,
)


@dataclass
class FakeHit:
    entity_type: str
    text: str


# ---------------------------------------------------------------- normalize

def test_identifier_normalization_strips_labels_and_separators():
    forms = ["MRN: 4471982", "MRN 4471982", "4471982",
             "Medical Record Number: 4471982", "447-1982"]
    norms = {normalize_identifier(f) for f in forms}
    assert norms == {"4471982"}, f"labels/separators not normalized: {norms}"


def test_date_normalization_across_formats():
    same = ["03/14/1962", "1962-03-14", "March 14, 1962",
            "14 Mar 1962", "03.14.1962", "3/14/62"]
    norms = {normalize_date(f) for f in same}
    assert norms == {"1962-03-14"}, f"same date read differently: {norms}"


def test_partial_dates_return_none():
    """Guessing a century manufactures false contamination."""
    for junk in ["1962", "March", "03/14", "", "not a date"]:
        assert normalize_date(junk) is None


# ---------------------------------------------------------------- silence

def test_same_mrn_many_formats_is_not_contamination():
    """THE false-alarm case. One record, written five ways."""
    hits = [FakeHit("MRN", v) for v in
            ["MRN: 4471982", "4471982", "MRN 4471982", "447-1982"]]
    assert not check_contamination(hits).contaminated


def test_same_dob_different_formats_is_not_contamination():
    hits = [FakeHit("DOB", "03/14/1962"), FakeHit("DOB", "1962-03-14")]
    assert not check_contamination(hits).contaminated


def test_multiple_providers_and_phones_are_not_contamination():
    """Several people and numbers per note is normal, not a defect."""
    hits = [
        FakeHit("PERSON", "Aisha Haddad"), FakeHit("PERSON", "Dr. Ravi Krishnamurthy"),
        FakeHit("PERSON", "Tobias Mbeki"),
        FakeHit("PHONE_NUMBER", "555-555-0134"),
        FakeHit("PHONE_NUMBER", "555-555-0188"),
        FakeHit("NPI", "1234567890"), FakeHit("NPI", "9876543210"),
    ]
    assert not check_contamination(hits).contaminated


def test_clean_note_produces_no_findings():
    hits = [FakeHit("MRN", "MRN: 4471982"), FakeHit("US_SSN", "123-45-6789")]
    text = "Patient seen today for follow-up. Vitals stable. Plan: continue current regimen."
    assert analyze(text, hits).findings == []


def test_short_remnants_are_ignored():
    """A 2-character fragment is noise, not a second record."""
    hits = [FakeHit("MRN", "4471982"), FakeHit("MRN", "A1")]
    assert not check_contamination(hits).contaminated


# ---------------------------------------------------------------- detection

def test_two_distinct_mrns_is_cross_system_not_contamination():
    """DOMAIN RULE: an MRN is scoped to the issuing hospital system, not
    to the person. A patient seen at two systems carries two MRNs, and a
    merged system holds two for one patient. This must NOT read as two
    people or the detector fires on every long-history patient."""
    hits = [FakeHit("MRN", "MRN: 4471982"), FakeHit("MRN", "MRN: 9987651")]
    report = check_contamination(hits)
    assert not report.contaminated, "multiple MRNs wrongly flagged as two patients"
    assert report.cross_system
    assert report.findings[0].code == "CROSS_SYSTEM_MRN"
    assert report.findings[0].severity.value == "advise"


def test_multiple_plan_ids_is_cross_system():
    """Payer-scoped: changing insurer is normal."""
    hits = [FakeHit("HEALTH_PLAN_ID", "A12345678"),
            FakeHit("HEALTH_PLAN_ID", "B98765432")]
    report = check_contamination(hits)
    assert not report.contaminated
    assert report.cross_system


def test_two_distinct_ssns_is_contamination():
    """SSN is scoped to the PERSON, not to an institution, so the
    cross-system explanation that clears multiple MRNs does not apply.
    Two SSNs means two people."""
    hits = [FakeHit("US_SSN", "123-45-6789"), FakeHit("US_SSN", "987-65-4321")]
    report = check_contamination(hits)
    assert report.contaminated
    assert report.findings[0].code == "CROSS_PATIENT_US_SSN"


def test_two_distinct_dobs_is_advisory_only():
    """Family history legitimately carries other people's dates
    ("mother died 1998"), so a second DOB is too weak to assert that the
    note describes two people. It is reported, but does not set the
    contamination flag — only person-scoped identifiers do that."""
    hits = [FakeHit("DOB", "03/14/1962"), FakeHit("DOB", "07/22/1938")]
    report = check_contamination(hits)
    assert not report.contaminated, "DOB alone must not assert two patients"
    assert report.findings, "but it should still be reported"
    assert report.findings[0].severity.value == "advise"


def test_findings_never_echo_the_identifier():
    """A diagnostic that prints PHI back to the caller defeats the tool."""
    hits = [FakeHit("MRN", "MRN: 4471982"), FakeHit("MRN", "MRN: 9987651")]
    blob = str(check_contamination(hits).to_dict())
    assert "4471982" not in blob and "9987651" not in blob


# ---------------------------------------------------------------- template

def test_template_residue_detected():
    text = ("HPI: *** presents with chest pain. DOB: [DATE OF BIRTH]. "
            "Signed by @PROVIDER@ on ____.")
    findings = check_template_residue(text)
    assert findings and findings[0].code == "TEMPLATE_RESIDUE"
    assert findings[0].count >= 4


def test_clean_prose_has_no_template_residue():
    text = ("Patient presents with substernal chest pain radiating to the left arm. "
            "ECG shows ST elevation in V2-V4. Troponin 8.2 ng/mL. "
            "Plan: emergent PCI, dual antiplatelet therapy.")
    assert check_template_residue(text) == []


def test_clinical_text_with_punctuation_is_not_template_residue():
    """Blood pressure and ratios must not read as scaffolding."""
    text = "BP 180/120, HR 110. I/O 1200/800. Wt 82.4 kg. A1c 7.2%."
    assert check_template_residue(text) == []


# ---------------------------------------------------------------- realistic

def test_patient_with_outside_hospital_history_is_clean():
    """The common real case: a referral note citing the outside system's
    record number alongside the local one. One patient, two systems."""
    text = (
        "PROGRESS NOTE\n"
        "Patient: Aisha Haddad   MRN: 4471982   DOB: 03/14/1962\n\n"
        "HPI: Patient transferred from Lowmoor General, where she was followed "
        "under record 9987651 for the same condition.\n"
        "ASSESSMENT: CHF exacerbation, improving on IV diuresis.\n"
    )
    hits = [
        FakeHit("MRN", "MRN: 4471982"), FakeHit("MRN", "record 9987651"),
        FakeHit("DOB", "03/14/1962"),
        FakeHit("PERSON", "Aisha Haddad"),
    ]
    report = analyze(text, hits)
    assert not report.contaminated, "prior-system MRN wrongly read as a second patient"
    assert report.cross_system


def test_realistic_copy_forward_with_two_ssns():
    """A meticulous, fully-templated note that really does contain two
    people. The MRNs alone would not prove it — the SSNs do."""
    text = (
        "PROGRESS NOTE\n"
        "Patient: Aisha Haddad   MRN: 4471982   SSN: 123-45-6789\n\n"
        "SUBJECTIVE: Patient reports improved dyspnea since admission.\n"
        "ASSESSMENT: CHF exacerbation, improving on IV diuresis.\n\n"
        "--- carried forward from 03/02 ---\n"
        "Patient: Robert Chen   MRN: 9987651   SSN: 987-65-4321\n"
        "Admitted with COPD exacerbation, started on steroids.\n"
    )
    hits = [
        FakeHit("MRN", "MRN: 4471982"), FakeHit("MRN", "MRN: 9987651"),
        FakeHit("US_SSN", "123-45-6789"), FakeHit("US_SSN", "987-65-4321"),
        FakeHit("PERSON", "Aisha Haddad"), FakeHit("PERSON", "Robert Chen"),
    ]
    report = analyze(text, hits)
    assert report.contaminated
    codes = {f.code for f in report.findings}
    assert "CROSS_PATIENT_US_SSN" in codes
    # and the MRNs are reported as history, not as a defect
    assert "CROSS_SYSTEM_MRN" in codes


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
