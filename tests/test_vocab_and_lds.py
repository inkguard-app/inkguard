"""Vocabulary across clinical subspecialties, and the Limited Data Set
profile's conflicts (LDS is offered beside full removal and needs closer
manual review)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PHI_ROOT_KEY", "ab" * 32)

from inkguard import agate  # noqa: E402
from inkguard.policy import evaluate  # noqa: E402

LDS = "limited_data_set"


def reasons(text, **kw):
    c = agate.assess(text, [], **kw)
    return [(text[n.start:n.end], n.reason) for n in c.not_understood]


# ------------------------------------------------------------ vocabulary

def test_abbreviations_from_the_meta_inventory_are_understood():
    assert reasons("Plan VCUG and RPOC evaluation.") == []
    assert reasons("Line placed. UVC in good position.") == []


def test_the_inventory_never_vouches_for_initials_where_initials_stand():
    # UVC is in the inventory; as a sign-off or after "discussed with" it
    # is someone's initials until a person says otherwise.
    assert "UVC" in agate._extra()[2]
    assert reasons("Note reviewed. -- UVC") == [("UVC", "unknown_acronym")]
    assert reasons("Discussed with UVC.") == [("UVC", "unknown_acronym")]


def test_instrument_eponyms_are_things_in_an_operation_but_people_elsewhere():
    assert reasons("Foramen widened with Kerrison and dura retracted.") == []
    assert reasons("Dr. Kerrison assisted.")[0][1] in ("possible_person", "name_left_in")
    assert reasons("Kerrison family at bedside.") != []


def test_a_rare_term_from_the_ontologies_is_understood():
    assert "matruchotii" in agate._extra()[0]
    assert ("matruchotii", "unknown_word") not in reasons("Culture grew Corynebacterium matruchotii.")


def test_an_eponym_the_ontologies_use_as_a_surname_needs_a_clinical_word():
    assert "darier" in agate._extra()[1]
    assert reasons("Darier disease confirmed on biopsy.") == []
    assert reasons("Discussed with Darier today.") != []


def test_two_rare_terms_run_together_do_not_make_a_word():
    # The compound rule reads only the core vocabulary, not 90,000 terms.
    assert not agate._derives("xqzvkitam", agate._known_word)


def test_an_inflected_common_word_at_a_sentence_start_is_not_a_name():
    r = evaluate("KUB ok. Tolerating diet. Improving daily.", redaction_mode="mask")
    assert "Tolerating diet" in r.clean_text and "Improving daily" in r.clean_text


# -------------------------------------------------------- limited data set

def test_lds_keeps_a_town_the_note_plainly_uses_as_a_place():
    r = evaluate("Pt from Marion, IA. Admitted March 14, 2024 for SBO.", profile_name=LDS,
                 redaction_mode="mask")
    assert r.action.value == "redacted" or r.action.value == "allow"
    assert "Marion, IA" in r.clean_text and "March 14, 2024" in r.clean_text


def test_lds_removes_a_town_spelled_like_the_patients_people_instead_of_blocking():
    r = evaluate("Transferred from Austin. Daughter Austin at bedside.", profile_name=LDS,
                 redaction_mode="mask")
    assert r.action.value != "blocked"
    assert "Austin" not in r.clean_text


def test_lds_removes_a_town_it_cannot_tell_from_a_person():
    r = evaluate("Received call from Austin re: labs. Pt lives in Ashland, OR.",
                 profile_name=LDS, redaction_mode="mask")
    assert "Austin" not in r.clean_text and "Ashland, OR" in r.clean_text


def test_lds_person_typed_as_a_place_is_removed_not_blocked():
    r = evaluate("Discussed w/ primary team (Doctolero). We've scheduled Jhosua for Oct 7th.",
                 profile_name=LDS, redaction_mode="mask")
    assert r.action.value != "blocked"
    assert "Doctolero" not in r.clean_text and "Jhosua" not in r.clean_text
    assert "Oct 7th" in r.clean_text


def test_a_date_field_ends_where_the_next_label_begins():
    t = "Collected: Apr 18, 2023   Submitting MD: Ardis Gaton\nDOB: 1977-06-28   MRN: 57559703"
    r = evaluate(t, profile_name=LDS, redaction_mode="mask")
    assert r.action.value != "blocked"
    assert "Ardis" not in r.clean_text and "Gaton" not in r.clean_text
    assert "Apr 18, 2023" in r.clean_text and "1977-06-28" in r.clean_text
    assert "57559703" not in r.clean_text
    sh = evaluate(t, redaction_mode="mask")
    assert "Ardis" not in sh.clean_text and "1977-06-28" not in sh.clean_text


def test_lds_months_in_kept_dates_do_not_hold_the_note():
    assert reasons("Seen Jun 9 and on 14 Sep.", lds=True) == []
    assert [r for _, r in reasons("Seen Jun 9 and on 14 Sep.")] == ["date_left_in"] * 2


def test_lds_report_says_it_needs_closer_review(tmp_path):
    from inkguard import cli, contract
    stats = cli.RunStats()
    rep = cli.build_report(stats, contract.profile_info(LDS), False)
    assert "Closer manual review" in rep["deidentification"]["review_guidance"]
    from inkguard import cli_docs
    cli_docs.audit_pdf(rep, tmp_path / "a.pdf")
    md = " ".join(cli_docs.document_text(tmp_path / "a.pdf").split())
    assert "Limited Data Set" in md and "remains PHI" in md
    sh = cli.build_report(stats, contract.profile_info("safe_harbor"), False)
    assert "review_guidance" not in sh["deidentification"]


# ------------------------------------- held-out template leaks
# Each of these left an identifier in a released note. A gate that held
# some of those notes only because of an unrelated unknown abbreviation
# stopped holding them once the vocabulary grew, which is how they surfaced.

import pytest  # noqa: E402


@pytest.mark.parametrize("text,gone,kept", [
    # A name removed once is removed again where a person cue stands by it.
    ("Thank you for seeing Archil Pinckney (DOB 05/05/1959). Archil notes resting tremor.",
     ["Archil"], ["resting tremor"]),
    ("Fakhar Stone employee of Joustra Dental Group.\nStone returned to modified duty.",
     ["Stone returned"], ["modified duty"]),
    ("Surgeon: Yijing Savage\n\nDischarged same day. Savage to see at 2 weeks.",
     ["Savage"], ["to see at 2 weeks"]),
    ("Pt: Anselm Bockhardt, 5 wk M\nParents updated. Anselm to floor.", ["Anselm"], ["to floor"]),
    # A specimen label carries the surname.
    ("Patient: Eileen Laws   MRN: MR672228\nContainer labeled 'Laws-A sigmoid'.",
     ["Laws"], ["-A sigmoid"]),
    # A possessive with a relation after it.
    ("- d/c POD1 if tolerating diet\n\nDomingo's wife (Sofica) at bedside.", ["Domingo", "Sofica"],
     ["wife", "at bedside"]),
    # A clinician in brackets after a service.
    ("Plan: continue as above, endocrine (Boatswain) following.", ["Boatswain"], ["endocrine"]),
    # A first name and initial opening a sign-out line.
    ("POD0 check\nLucky S. s/p lap chole. VSS.", ["Lucky"], ["s/p lap chole"]),
    # A note's own date after its title; a dose date.
    ("NIGHT SIGNOUT 7/24 - 7 WEST\nPEDS WARDS PROGRESS NOTE 2/10\nPain 2/10. Strength 5/5.",
     ["7/24", "NOTE 2/10"], ["Pain 2/10", "Strength 5/5"]),
    ("on dose-dense AC (cycle 3 given 8/1), presents with T 38.6", ["8/1"], ["cycle 3 given"]),
    ("Marlowe (WOCN) to reassess 2/11.", ["2/11", "Marlowe"], ["WOCN"]),
])
def test_held_out_template_leaks(text, gone, kept):
    out = evaluate(text, redaction_mode="mask").clean_text
    for g in gone:
        assert g not in out, (g, out)
    for k in kept:
        assert k in out, (k, out)


def test_hyphenated_clinical_words_and_credentials_are_not_names():
    out = evaluate("Braden 12. Pre-albumin 11. Non-reassuring tracing.\n-Kiliam Parson, LCSW",
                   redaction_mode="mask").clean_text
    assert "Pre-albumin 11" in out and "Non-reassuring" in out and ", LCSW" in out
    assert "Kiliam" not in out and "Parson" not in out


def test_everyday_word_names_keep_their_word_readings():
    out = evaluate("Pt Rose Alvarado. BP rose to 150. Rose to 38.5 overnight. Will return.",
                   redaction_mode="mask").clean_text
    assert "BP rose to 150" in out and "Will return" in out
