"""A surname written in lowercase is still the surname.

Clinicians write "garcia says" and "mrs nguyen". Before 0.25.0 a lowercase
repeat of a confirmed name was removed only beside a cue word, and the
final word check let a lowercase name through when it was a frequent word
in English text, which common surnames are precisely because they are
common surnames. Three layers shared one assumption, so the holes lined up.
"""
from inkguard.contract import deidentify


def _clean(text: str):
    r = deidentify(text, patient_key="p1", mode="mask")
    return r.clean_text, r


def _flagged(r) -> set[str]:
    """The words the final check could not account for, as written."""
    return {w["word"].lower() for w in r.words_to_check}


def test_a_confirmed_surname_in_lowercase_is_removed():
    clean, _ = _clean("Patient: Maria Garcia, DOB 03/14/2071.\n"
                      "Seen for follow-up. garcia says the pain is better.")
    assert "Garcia" not in clean and "garcia" not in clean


def test_a_lowercase_surname_the_note_never_confirmed_flags_the_note():
    clean, r = _clean("Seen for follow-up. mrs nguyen (neighbour) drove her.")
    words = _flagged(r)
    assert r.status == "review" and "nguyen" in words


def test_a_lowercase_name_after_a_title_flags_even_a_frequent_one():
    _, r = _clean("Seen for follow-up. mrs lee asks about her meds.")
    words = _flagged(r)
    assert "lee" in words


def test_ordinary_words_that_are_also_rare_surnames_are_not_flagged():
    _, r = _clean("Pt asks about her meds. Daughter visits weekly. Walked the hall.")
    words = _flagged(r)
    assert not words & {"asks", "her", "visits", "walked", "hall"}


def test_a_name_that_is_also_a_word_still_needs_a_cue():
    clean, _ = _clean("Daughter Rose Alvarado at bedside. BP rose to 150 overnight.")
    assert "BP rose to 150" in clean and "Alvarado" not in clean
