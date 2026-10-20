"""Shapes found by the synthetic longitudinal test set (invented notes, waves 1-3).

Each case is a way clinical notes are written that the engine used to let
through, paired with the clinical text beside it that must survive. The
strings are invented; none comes from that set itself.
"""

from __future__ import annotations

import pytest

from inkguard.phi_scanner import warmup
from inkguard.policy import evaluate


@pytest.fixture(scope="module", autouse=True)
def _ready():
    warmup()


def clean(text: str, **kw) -> str:
    r = evaluate(text, **kw)
    assert r.action.value != "blocked", r.error
    return r.clean_text


# ---------------------------------------------------------------- gone
GONE = [
    # ages over 89, whoever they belong to
    ("Her mother Ada Quell, 93, lives with them.", "93"),
    ("GREAT-GRANDMOTHER IS 96. NO CONCERNS.", "96"),
    ("Grandfather passed away at 97 last spring.", "97"),
    ("husband is ninety-one years old", "ninety-one"),
    # names in capitals and by relation
    ("SOCIAL: DAUGHTER VESNA KORLOVIC IN SPLIT.", "KORLOVIC"),
    ("NEIGHBOR ABELARD QUINCEY AGREES TO CHECK IN.", "QUINCEY"),
    ("Arrived with kids Sunny (4) and Jaxtyn (2).", "Sunny"),
    ("TAVERNER, OSWIN H          MRN 0091-7730          DOB 01/02/2041", "OSWIN"),
    ("RE: MARCHETTI, LUCIA   DOB 11/03/2075", "MARCHETTI"),
    # structured identifiers
    ("Portal: https://mychart.lowmoor-gen.example/q.aldane ok", "lowmoor"),
    ("PRINTED 09/27/2073 15:02 BY qaldane FROM 10.44.2.188", "qaldane"),
    ("Car: grey sedan plate BKZ 6215, other driver left.", "BKZ 6215"),
    ("METER PAIRS TO PHONE AS QUELL_M_METER.", "QUELL_M_METER"),
    ("Member ID LMQ 4471 90238 W   DOB 05/22/2003", "90238"),
    ("RE: MARCHETTI, LUCIA   DOB 11/03/2075   MRN 00588-212   AGE 17", "00588"),
    ("report # TPQ-74-118820. Neck pain.", "118820"),
    ("my pin is four four zero eight, audio file", "four four zero eight"),
    ("Ortho consult, page pager 5527. NPO.", "5527"),
    # dates
    ("NO MEDS EXCEPT OCP SINCE DEC 2090.", "DEC"),
    ("Next well visit 12/2066, bring the shot record.", "12/2066"),
    ("C/O COUGH X THE 15TH, SOB.", "15TH"),
    ("Walk-in on Christmas Day.", "Christmas"),
    ("Pt lost job 4/17 so insurance may change.", "4/17"),
    # places
    ("LIVES AT 77 TAMARIND COURT UNIT 6, DULUTH MN 55805.", "TAMARIND"),
    ("SON FERGUS IN MELBOURNE WILL CALL.", "MELBOURNE"),
    ("he lives at ul. Długa 12/4, 80-827 Gdańsk, Poland", "Długa"),
    ("TORONTO ON M9C 5K1, CANADA", "M9C"),
]


@pytest.mark.parametrize("text,leak", GONE)
def test_identifier_is_removed(text, leak):
    assert leak not in clean(text)


# -------------------------------------------------------------- stays
STAYS = [
    ("FAMILY HX: MOTHER BREAST CA, FATHER MI AT 50, SISTER HEALTHY.", ["BREAST CA", "MI AT 50", "HEALTHY"]),
    ("Father had Parkinson's disease, uncle Hodgkin lymphoma.", ["Parkinson", "Hodgkin lymphoma"]),
    ("SpO2 94% on RA. BP 108/90, HR 95, glucose 110 mg/dL.", ["94%", "108/90", "95", "110 mg/dL"]),
    ("Pain 4/10. Strength 5/5. Take 1/2 tab.", ["4/10", "5/5", "1/2 tab"]),
    ("PT EVAL TWICE WEEKLY. CALLED BACK. PATIENT ALERT.", ["EVAL", "BACK", "ALERT"]),
    ("the third time; the 5th digit; on the 2nd dose", ["third time", "5th digit", "2nd dose"]),
    ("walked 3 blocks to the park; took 2 tabs by mouth", ["3 blocks", "2 tabs"]),
    ("Documented by nurse on the floor. HbA1c (T2DM) 7.2.", ["nurse", "T2DM"]),
    ("ASSIST IN BATH. IN READING THE CHART.", ["BATH", "READING"]),
    ("Tobin Farrow, 54, presents with cough.", ["54"]),
]


@pytest.mark.parametrize("text,keep", STAYS)
def test_clinical_text_survives(text, keep):
    out = clean(text)
    for k in keep:
        assert k in out, (k, out)


def test_age_over_89_reads_as_band():
    assert "90 or older" in clean("Her mother Ada Quell, 93, lives with them.")


def test_remembered_name_found_in_capitals_and_without_accents():
    out = clean("Signed QUELLWATER, TOMASZ. Pani Wiśniewska called; WISNIEWSKA chart.",
                memory={"Tomasz Quellwater", "Agata Wiśniewska"})
    for leak in ("QUELLWATER", "TOMASZ", "Wiśniewska", "WISNIEWSKA"):
        assert leak not in out


def test_clinic_words_do_not_spread_as_names():
    r = evaluate("To: Dr. Ilse Varga   Fax: (253) 555-0166\nFax cover. Varga Family Medicine.\n"
                 "CONFIDENTIALITY: this fax is for the addressee.")
    assert r.action.value != "blocked", r.error
    assert "fax is for the addressee" in r.clean_text


# ------------------------------------------------- reading the file again
def test_every_patient_file_is_read_at_least_three_times():
    from inkguard import contract, pumice
    _, ctx = contract.process_patient(["Seen today. BP 120/80."], patient_key="t-reads")
    assert ctx.reads >= pumice.READ_MINIMUM >= 3
    assert ctx.summary()["times_read"] == ctx.reads


def test_reads_stop_at_the_limit():
    from inkguard import contract, pumice
    _, ctx = contract.process_patient(["Seen today."], patient_key="t-limit")
    assert ctx.reads <= max(pumice.REREAD_LIMIT, pumice.READ_MINIMUM) + 1


def test_a_name_learned_in_one_note_is_removed_in_every_note():
    from inkguard import contract
    notes = ["ADMISSION. Patient: Quella Marsh-Odum. Daughter Junebug Odum (goes by JB) at bedside.",
             "Phone call: JB says mom fell again. Junebug will bring meds.",
             "QUELLA ODUM SEEN, JUNEBUG PRESENT."]
    results, _ = contract.process_patient(notes, patient_key="t-learn", mode="mask")
    for r in results:
        assert r.action.value != "blocked", r.error
        for leak in ("Quella", "QUELLA", "Junebug", "JUNEBUG", "JB", "Odum", "ODUM"):
            assert leak not in r.clean_text, (leak, r.clean_text)
    assert "fell again" in results[1].clean_text and "bring meds" in results[1].clean_text


def test_a_stop_is_honoured_between_notes_not_after_every_read():
    from inkguard import contract
    asked = []

    def stop():
        asked.append(1)
        return len(asked) > 2              # let two notes through, then stop
    notes = ["Seen today. BP 120/80."] * 4
    with pytest.raises(contract.Stopped):
        contract.process_patient(notes, patient_key="t-stop", should_stop=stop)
    assert len(asked) == 3                 # asked at each note, stopped at the third
