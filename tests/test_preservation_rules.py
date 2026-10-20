"""Rules learned from the clinical-preservation set's tuning half.
Every sentence here is newly invented: none is copied from the
benchmark, so the benchmark's held-out half stays a fair test."""
import pytest

from inkguard import contract


def run(text):
    return contract.deidentify(text, mode="mask")


@pytest.mark.parametrize("text,gone", [
    ("Assistants: K. Wexley (spine), P. Ardmore-Lisle (vascular).", ["K.", "Wexley", "Ardmore"]),
    ("Plan per Dr. J. Valdéz: start lacosamide.", ["J.", "Valdéz"]),
    ("Circulating nurse Imogen Thrale; scrub tech present.", ["Imogen", "Thrale"]),
    ("BRANNIGAN, T.   MRN QX-5520193", ["BRANNIGAN", "T."]),
    ("CONSULT — 05/02/2068 — ELSPETH VANTREASE-OYO\nSeen for neck pain.", ["ELSPETH", "VANTREASE"]),
    ("Neuromonitoring O. Petrakis, CNIM, reported stable signals.", ["O.", "Petrakis"]),
])
def test_names_with_initials_and_in_headers_go_whole(text, gone):
    out = run(text).clean_text
    for g in gone:
        assert g not in out, (g, out)


@pytest.mark.parametrize("text,old", [
    ("Rosamund Teague, 94 y, MRN 2231 — fall at home.", "94"),
    ("CLINICAL: 92 F with acute back pain.", "92"),
    ("Admitted (96 M) with confusion.", "96"),
])
def test_shorthand_ages_over_89_are_generalised(text, old):
    out = run(text).clean_text
    assert "90 or older" in out and f"{old} " not in out


@pytest.mark.parametrize("text,kept", [
    ("Temp 99 F, HR 88, afebrile overnight.", "99 F"),
    ("16 F Foley placed; 62 M with sciatica.", "16 F Foley"),
    ("On arrival GCS 10 (E2V3M5), intubated later; GCS 7T (E2VTM5).", "E2VTM5"),
    ("Restart anticoagulation per the 1-3-6-12 day rule.", "1-3-6-12 day rule"),
    ("DBS settings 2.1 mA, 90 µs, 160 Hz bilaterally.", "90 µs"),
    ("Lumbar drain at 12 cmH2O; ICP 18 mmHg.", "12 cmH2O"),
    ("Frisén grade 3 papilloedema; Brown-Séquard pattern below T6.", "Frisén grade 3"),
    ("Engel class IB at two years after SEEG-guided resection.", "Engel class IB"),
])
def test_clinical_measurements_and_grades_survive(text, kept):
    r = run(text)
    assert r.status != "stop"
    assert kept in r.clean_text, r.clean_text


@pytest.mark.parametrize("text", [
    "Grip strength by Jamar dynamometer was symmetric.",
    "Two-level Ponte osteotomies were performed for kyphosis.",
    "Sensation intact to Semmes-Weinstein monofilament.",
    "Major curve with a Cobb angle of 42 degrees.",
    "Anti-GQ1b antibodies positive; AQP4-IgG negative.",
    "Coded M80.08XA and 64721-LT on the claim.",
    "Started cEEG; mJOA 13; iNPH work-up; AComm aneurysm coiled.",
])
def test_clinical_words_do_not_hold_the_note(text):
    r = run(text)
    assert r.status == "clear", r.words_to_check


@pytest.mark.parametrize("text,gone,kind", [
    ("Brindlewood Acute Rehabilitation Institute discharge summary.", "Brindlewood", "Rehabilitation Institute"),
    ("Transferred from Harrowmere Level I Trauma Center by air.", "Harrowmere", "Trauma Center"),
    ("MRI performed at Saltmarsh Open MRI last week.", "Saltmarsh", "Open MRI"),
])
def test_places_of_care_lose_their_name_and_keep_their_kind(text, gone, kind):
    out = run(text).clean_text
    assert gone not in out and kind in out, out


def test_vitamin_d_is_not_an_initial():
    assert "vitamin D" in run("Took vitamin D. Sleeping better.").clean_text


@pytest.mark.parametrize("text,kept", [
    ("Thoracic curve, Lenke 1A, Cobb 48 degrees.", "Lenke 1A"),
    ("Main curve classified Lenke 5C+.", "Lenke 5C+"),
    ("Macroadenoma Knosp 4; vestibular schwannoma Koos 3.", "Knosp 4"),
    ("Facial nerve House-Brackmann IV after resection.", "House-Brackmann IV"),
    ("Meyerding II slip with Pfirrmann 4 disc and Modic 1 change.", "Pfirrmann 4"),
    ("Modified Fisher 3 and Hunt and Hess 2 on arrival.", "Hunt and Hess 2"),
])
def test_named_scales_without_a_grading_word_survive(text, kept):
    r = run(text)
    assert r.status == "clear", r.words_to_check
    assert kept in r.clean_text, r.clean_text


@pytest.mark.parametrize("text,gone", [
    ("Pt: Oskar Babinski. Reflexes symmetric.", "Babinski"),
    ("Accompanied by Greta Romberg, sister.", "Romberg"),
    ("Pt: Ilse Wernicke, seen today.", "Wernicke"),
    ("Pt: Lars Babinski. Babinski sign present.", "Lars Babinski"),
    ("Mother Inga Charcot drove her in; Charcot foot noted.", "Inga Charcot"),
])
def test_a_given_name_makes_an_eponym_a_surname(text, gone):
    assert gone not in run(text).clean_text


def test_an_eponym_with_a_first_name_stays_clinical():
    assert "Charles Bonnet syndrome" in run("Suspected Charles Bonnet syndrome.").clean_text


@pytest.mark.parametrize("text,gone", [
    ("KOWALCZYK, MAREK  MRN 66120 — follow-up.", ["KOWALCZYK", "MAREK"]),
    ("ROMBERG, IDA   DOB 03/04/2061", ["ROMBERG", "IDA"]),
])
def test_capitalised_header_names_go_even_when_the_surname_is_an_eponym(text, gone):
    out = run(text).clean_text
    for g in gone:
        assert g not in out, out


@pytest.mark.parametrize("text,number", [
    ("MRN 66120 on the wristband.", "661"),
    ("Chart: 40417, reviewed.", "404"),
])
def test_a_five_digit_record_number_is_not_read_as_a_zip(text, number):
    assert number not in run(text).clean_text


def test_a_zip_after_a_state_is_still_a_zip():
    assert "558**" in run("Lives in Duluth, MN 55805.").clean_text


def test_a_lowercase_given_name_and_eponym_is_a_person():
    out = run("ingrid wernicke phoned about her results.").clean_text
    assert "wernicke" not in out, out


def test_an_everyday_word_before_an_eponym_is_not_a_given_name():
    assert "babinski" in run("mark babinski as present.").clean_text


@pytest.mark.parametrize("text,number", [
    ("Called her at 555 0163 after the scan.", "0163"),
    ("Rang his daughter on 555.0171.", "0171"),
    ("Text me on 07700 900456 if worse.", "900456"),
    ("SSN last 4: 4417 on the form.", "4417"),
    ("Confirmed the last four of her SSN is 3302.", "3302"),
    ("acct 551203 / enc 99871", "99871"),
])
def test_numbers_people_write_casually_are_never_released(text, number):
    r = run(text)
    assert number not in r.clean_text or r.status != "clear", r.clean_text


@pytest.mark.parametrize("text,kept", [
    ("BP 124 78 sitting.", "124 78"),
    ("Titrate 500 1000 mg as tolerated.", "500 1000 mg"),
])
def test_number_pairs_that_are_clinical_stay(text, kept):
    assert kept in run(text).clean_text


def test_a_patient_name_kept_as_a_clinical_word_is_a_question_not_a_hold():
    r = run("Patient: Ruth Fisher. Modified Fisher 3 SAH on CT.")
    assert r.status == "clear" and "Ruth" not in r.clean_text
    assert any(w["reason"] == "name_also_clinical" and not w["holds_the_note"]
               for w in r.words_to_check)


def test_both_reviewers_are_asked_about_a_name_kept_as_a_clinical_word():
    from inkguard import chert
    text = "Patient: Ruth Fisher. Modified Fisher 3 SAH on CT.\n"
    r = run(text)
    note, _ = chert.note_payload(text, r.raw, False)
    assert any(q["reason"] == "name_also_clinical" for q in note["questions"])


@pytest.mark.parametrize("text,kept", [
    ("MRN 5520917. L4-5 laminectomy, C5-6 ACDF, T12-L1 fusion.", "L4-5 laminectomy"),
    ("Chart 7781204: G2P1, E3V4M6 on arrival.", "E3V4M6"),
])
def test_short_clinical_codes_beside_a_record_number_stay(text, kept):
    r = run(text)
    assert kept in r.clean_text and "5520917" not in r.clean_text and "7781204" not in r.clean_text, r.clean_text


@pytest.mark.parametrize("text,gone", [
    ("Hana Corvell is a pleasant 52-year-old woman with migraine.", ["Hana", "Corvell"]),
    ("KESTOR, AMILA  41-889301\nSeen for follow-up.", ["KESTOR", "AMILA", "889301"]),
    ("Lives at 4410 Marigold Viaduct, Brenton.", ["Marigold Viaduct"]),
    ("Photos at https://orchard.example.com/u/73519 for review.", ["73519", "orchard.example"]),
    ("Asks us to update Pellam Druse at 555-0183 tonight.", ["0183"]),
])
def test_shapes_found_by_the_generated_set_are_removed(text, gone):
    """Shapes the first run of the generated mixed set showed.
    These sentences are new; the set itself is regenerated with a new seed
    for the next measurement."""
    r = run(text)
    for g in gone:
        assert g not in r.clean_text or r.status != "clear", (g, r.clean_text)
