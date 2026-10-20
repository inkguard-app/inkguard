"""
Tests for residual linkage risk scoring.

Half of these assert SILENCE. A re-identification warning that fires on
routine notes trains the reviewer to dismiss it, and a dismissed warning
protects nobody — so the false-positive cases are as load-bearing as the
detection cases.

Several encode collisions found by running against a large set of
sample notes rather than by reasoning. They are kept as regression tests
because each one looked correct when written:

  band-aids     "aids" matched inside a hyphenated compound, flagging
                every wound-closure note as an HIV disclosure.
  bowel prep    PrEP vs surgical prep. In operative notes the surgical
                sense dominates by orders of magnitude.
  vasectomy     Included as "sensitive reproductive care". It is a
                routine elective procedure; this axis measures harm if
                exposed, not topical sensitivity.
  HPV           Near-universal and appears in routine screening
                results. A finding 80% of adults share does not
                discriminate.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inkguard.linkage import (  # noqa: E402
    assess, find_rare_conditions, find_sensitive_categories,
    profile_demographics,
)


# ------------------------------------------------------------ silence

def test_routine_note_is_clean():
    t = ("<PERSON> is a 54-year-old male with hypertension and type 2 "
         "diabetes. BP 138/84. Continue lisinopril.")
    r = assess(t)
    assert r.verdict == "ok", f"routine note flagged: {r.factors}"


def test_band_aids_is_not_an_hiv_disclosure():
    """REGRESSION: 'aids' matched inside 'band-aids' because the word
    guard excluded letters and digits but not hyphens."""
    t = "Closed in subcuticular fashion, Dermabond and band-aids applied."
    assert find_sensitive_categories(t) == []


def test_bowel_prep_is_not_prep():
    """REGRESSION: PrEP (HIV prophylaxis) vs surgical/bowel prep."""
    for t in ("Full bowel prep had been obtained prior to the procedure.",
              "We were preparing and draping his abdomen.",
              "Preoperative prep and drape in the usual sterile fashion."):
        assert find_sensitive_categories(t) == [], t


def test_routine_elective_procedures_are_not_sensitive():
    """REGRESSION: vasectomy/tubal ligation are ordinary procedures."""
    for t in ("Bilateral vasectomy for elective sterility.",
              "Tubal ligation performed without complication.",
              "Patient counseled on contraception options."):
        assert find_sensitive_categories(t) == [], t


def test_hpv_screening_result_is_not_flagged():
    """REGRESSION: near-universal findings do not discriminate."""
    t = "Pap smear shows HPV changes, repeat cytology in one year."
    assert find_sensitive_categories(t) == []


def test_common_condition_with_demographics_is_ok():
    """Age + sex + common disease is not a small population."""
    t = "<PERSON> is a 61-year-old female with osteoarthritis of the knee."
    assert assess(t).verdict == "ok"


# ------------------------------------------------------------ detection

def test_the_motivating_case():
    """Nothing on the Safe Harbor list remains, and it is still almost certainly one person."""
    t = ("<PERSON> is a 62-year-old Lebanese female, ZIP 021**, with "
         "Erdheim-Chester disease.")
    r = assess(t)
    assert r.verdict == "review"
    assert r.findability == "high"


def test_ultra_rare_condition_alone_is_high_findability():
    """At a few hundred cases worldwide, almost any extra attribute
    isolates a person — so the condition alone is sufficient."""
    r = assess("Biopsy confirms fibrodysplasia ossificans progressiva.")
    assert r.findability == "high"


def test_real_hiv_disclosure_is_flagged():
    t = "<PERSON> is a 41-year-old male on antiretroviral therapy, CD4 380."
    r = assess(t)
    assert "hiv_status" in r.sensitive_categories
    assert r.harm in ("elevated", "high")


def test_harm_axis_fires_without_rarity():
    """The case a pure k-anonymity score misses entirely: a common
    demographic, a common condition, and a disclosure that can cost
    someone their job."""
    t = "<PERSON>, 45-year-old male, HIV positive, adherent to therapy."
    r = assess(t)
    assert r.findability == "low"
    assert r.verdict != "ok"


def test_two_sensitive_categories_forces_review():
    t = ("<PERSON>, 33, opioid use disorder on buprenorphine, with "
         "documented intimate partner violence.")
    r = assess(t)
    assert r.harm == "high"
    assert r.verdict == "review"


def test_age_over_89_is_a_safe_harbor_finding():
    """§164.514(b)(2)(i)(C): ages over 89 must be banded."""
    r = assess("<PERSON> is a 94-year-old female admitted with pneumonia.")
    assert any(f.code == "AGE_OVER_89_RETAINED" for f in r.findings)


def test_age_89_is_not_flagged():
    """The boundary is 90, not 89: Safe Harbor allows an age of 89 to stay."""
    r = assess("<PERSON> is an 89-year-old female admitted with pneumonia.")
    assert not any(f.code == "AGE_OVER_89_RETAINED" for f in r.findings)


# ------------------------------------------------------------ contract

def test_never_reports_a_population_estimate():
    """Printing a count implies a precision the prevalence data does not
    support, and a wrong number manufactures false confidence."""
    t = ("<PERSON> is a 62-year-old Lebanese female, ZIP 021**, with "
         "Erdheim-Chester disease.")
    d = assess(t).to_dict()
    blob = str(d).lower()
    assert "population:" not in blob
    assert "k=" not in blob
    for key in ("estimated_population", "population_size", "k_anonymity"):
        assert key not in d


def test_disclaims_expert_determination():
    """It must never read as a §164.514(b)(1) determination."""
    d = assess("Patient with Erdheim-Chester disease, 62-year-old female.").to_dict()
    disclaimer = d["disclaimer"].lower()
    assert "not an expert determination" in disclaimer
    assert "164.514(b)(1)" in disclaimer
    assert "no population estimate" in disclaimer


def test_demographic_profiling_counts_retained_attributes():
    p = profile_demographics(
        "62-year-old Lebanese female, ZIP 021**, works as a firefighter.")
    assert p.exact_age == 62
    assert p.count >= 4


def test_rare_condition_tiers_resolve():
    assert ("ultra_rare", "erdheim-chester disease") in \
        find_rare_conditions("biopsy-confirmed Erdheim-Chester disease")
    assert any(t == "very_rare" for t, _ in find_rare_conditions("Wilson disease"))
    assert any(t == "rare" for t, _ in find_rare_conditions("cystic fibrosis"))


def test_a_small_population_narrows_the_record():
    """Rural areas and small states can narrow a record."""
    r = assess("62-year-old female rancher from a frontier county in Wyoming, seen at the "
               "only clinic in town.")
    assert r.verdict == "review"
    assert any("small-population" in f or "rural" in f for f in r.factors)
    r = assess("Lives on the reservation, seen at the IHS clinic. 45-year-old man.")
    assert r.verdict != "ok"
    assert assess("Pt from the city, 45 year old male, HTN.").verdict == "ok"


def test_a_small_place_alone_is_not_flagged():
    """A small state or rural area counts only together
    with something rare or an exact age and sex, so a rural dataset does not
    flag every record."""
    assert assess("Seen in Cheyenne, WY. Male, hypertension.").verdict == "ok"
    assert assess("Lives on a ranch in rural Montana. Hypertension.").verdict == "ok"
