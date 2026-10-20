"""
Tests for the engine's stages: note sections (quartz), evidence scoring
(garnet), name spreading and patient memory (shale), per-patient codes
(onyx), the type registry and the Ledger (talc), final inspection
(obsidian) and the operation log (beryl).

Every test here is a defect that actually happened, or the invariant that
would have caught it. The comment on each says which.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PHI_ROOT_KEY", "ab" * 32)

import pytest  # noqa: E402

from inkguard import quartz as C  # noqa: E402
from inkguard import garnet, talc, obsidian, hipaa  # noqa: E402
from inkguard.clinical_lexicon import is_clinical_term  # noqa: E402
from inkguard.phi_scanner import scan, scan_with_trace, PHIHit  # noqa: E402
from inkguard.policy import evaluate, Action  # noqa: E402


def texts(hits, kind=None):
    return [h.text for h in hits if kind is None or h.entity_type == kind]


# =====================================================================
# QUARTZ — note sections
# =====================================================================

def test_lazy_soap_is_recognised_by_quorum():
    p = C.profile("S: reports less dyspnea. O: BP 128/76. A: CHF improving. P: continue Lasix.")
    kinds = {s.kind for s in p.sections}
    assert C.ASSESSMENT_PLAN in kinds or C.MEASUREMENT in kinds


def test_pulse_value_is_not_a_plan_header():
    # "P: 72" is a pulse. Read as the Plan header, it re-typed the rest
    # of the note as a name-sparse section.
    p = C.profile("S: fine. O: BP 128/76, P: 72. A: stable. P: continue.")
    starts = [s.label for s in p.sections]
    assert starts.count("P") <= 1


def test_section_labels_never_serialised():
    # A label can be a name ("Haddad:") — summaries carry kinds only.
    p = C.profile("HPI: Mr. Haddad is well.\nMEDICATIONS: Lasix.")
    assert "Haddad" not in str(p.summary())


# =====================================================================
# GARNET — evidence scoring
# =====================================================================

@pytest.mark.parametrize("text", [
    "Follow-up: doing well. Foley removed.",
    "JP drain in place. Foley to gravity. Hemovac removed.",
    "He was given IV Toradol and was taken to recovery.",
    "15 mL of Toradol.",
    "There is a color Doppler suggestive of PFO.",
    "Hemostasis was obtained using the bipolar Bovie.",
    "Closed with 4-0 Monocryl.",
    "Please note that 18-French Foley was kept in place.",
    "Adenocarcinoma, Gleason 3 + 4 in 5% of tissue.",
])
def test_clinical_eponyms_and_brands_survive(text):
    assert texts(scan(text), "PERSON") == []


def test_document_level_clinical_use():
    # "Foley catheter placed" tells us what Foley means in this note.
    assert texts(scan("Foley catheter placed. Foley removed on day 2."), "PERSON") == []


def test_title_overrides_every_clinical_reading():
    hits = scan("Dr. Foley placed a Foley catheter. Foley removed on day 2.")
    person = [h for h in hits if h.entity_type == "PERSON"]
    assert len(person) == 1 and person[0].start == 4


@pytest.mark.parametrize("text,name", [
    ("Patient Mary Foley was seen today. Foley reports pain.", "Mary Foley"),
    ("Seen with wife Aisha Haddad. Haddad removed the drain.", "Aisha Haddad"),
    ("Patient Theo Foley, 54, presents with chest pain.", "Theo Foley"),
])
def test_names_that_are_also_devices_are_still_names(text, name):
    assert name in texts(scan(text), "PERSON")


def test_organisation_mistype_is_retyped_by_person_context():
    # NER typed "Smith, Jones" as ORGANIZATION; organisations are kept.
    r = evaluate("Seen by Smith, Jones and Wilson today.")
    for n in ("Smith", "Jones", "Wilson"):
        assert n not in r.clean_text


def test_organisation_with_institution_suffix_is_not_retyped():
    # Policy since the first hand-written test review: a named place of care is removed as a
    # FACILITY (never mistaken for a person), and the kind word stays.
    r = evaluate("Referred by Brightwater Clinic for evaluation.")
    assert "[FACILITY] Clinic" in r.clean_text and "[NAME]" not in r.clean_text


@pytest.mark.parametrize("word,expected", [
    ("Preoperative", True), ("Cystourethroscopy", True), ("Lymphadenectomy", True),
    ("Cystocele", True), ("Enterococcus", True), ("Endoscopically", True),
    ("Noncontributory", True),
    ("Overstreet", False), ("Underwood", False), ("Prescott", False),
    ("Gregory", False), ("Mallory", False), ("Yokoyama", False), ("Politis", False),
])
def test_medical_morphology_does_not_hit_surnames(word, expected):
    assert garnet.medical_morphology(word) is expected


def test_candidate_layer_cannot_vote_in_a_quorum():
    t = "Retzius was entered."
    hits, trace = scan_with_trace(t)
    for r in trace.held:
        assert not any(e.startswith("quorum") for e in r["evidence"])


def test_held_records_carry_no_text():
    _, trace = scan_with_trace("Neck: Supple without adenopathy.")
    for r in trace.held:
        assert "text" not in r


# =====================================================================
# SCRIPTS — the shared vocabulary
# =====================================================================

def test_japanese_iteration_mark_keeps_surname_whole():
    # 々 was missing from seven copies of the script class: 佐々木 split.
    assert "佐々木 浩" in texts(scan("佐々木 浩, 02/16/1977. NIHSS 14 on arrival."), "PERSON")


def test_two_character_cjk_name_is_a_candidate():
    assert "渡辺" in texts(scan("Follow-up: 渡辺 doing well."), "PERSON")


def test_script_family_is_resolved_centrally():
    from inkguard.onyx import script_family
    assert script_family("佐々木") == "cjk"
    assert script_family("김민준") == "hangul"
    assert script_family("أحمد") == "arabic"


# =====================================================================
# CLINICAL LEXICON — no name may be "always clinical"
# =====================================================================

def test_any_token_rule_no_longer_leaks_names():
    # "doe" (dyspnea on exertion) made "Patrick Doe" medical vocabulary.
    assert not is_clinical_term("Patrick Doe")
    assert "Patrick" not in evaluate("Patient Patrick Doe is here.").clean_text


def test_surname_collisions_are_demoted_to_ambiguous():
    assert not is_clinical_term("Hicks")
    assert is_clinical_term("Hicks catheter")


def test_acronym_collisions_keep_acronym_meaning():
    assert is_clinical_term("TIA")
    assert not is_clinical_term("Tia")


def test_drug_morphology_does_not_swallow_names():
    assert not is_clinical_term("Ahmed Tanvir")
    assert is_clinical_term("acyclovir")


def test_icd_vocabulary_yields_to_a_title():
    assert texts(scan("Urethra was identified at the tip."), "PERSON") == []
    assert "Fluke" in texts(scan("Seen by Dr. Fluke today."), "PERSON")


# =====================================================================
# DATES — elements smaller than a year, nothing else
# =====================================================================

@pytest.mark.parametrize("text", [
    "overweight for many years.", "for about a month and", "over an hour",
    "By the second postoperative day, was able", "A 16-month-old with history",
    "came in with 2-week history of pain", "for the past year.",
    "placed at the 1, 3, 5, 7, 9 and 11 o'clock positions",
    "Clinical stage Ta Nx Mx transitional cell carcinoma",
    "recurrence in the 1980s", "pulse of 83, respiratory rate of 18",
    "Foley removed on day 2.", "Cycle 2 day 1 of FOLFOX.",
])
def test_relative_time_and_non_dates_survive(text):
    assert [h for h in scan(text) if talc.canonical(h.entity_type) == "DATE_TIME"] == []


@pytest.mark.parametrize("text,value", [
    ("underwent cystoscopy on 4/13. On 4/14 the patient", "4/13"),
    ("Seen on 03/14/2024 and again today.", "03/14/2024"),
    ("DOS: 3/14 follow up", "3/14"),
])
def test_real_dates_are_removed(text, value):
    assert value not in evaluate(text).clean_text


@pytest.mark.parametrize("text", ["Motor 5/5 strength.", "Take 1/2 tab.", "2/6 murmur."])
def test_fractions_are_not_dates(text):
    assert evaluate(text).clean_text == text


def test_appositive_age_over_89_is_generalised():
    ages = hipaa.find_ages("Ms. Haddad (94) was seen.")
    assert ages and ages[0].replacement == "90 or older"


def test_vital_and_lab_patterns_cannot_shield_record_numbers():
    # Unbounded \d+ made "T2331823" a temperature and "K8055532" a potassium.
    for t in ("Record K8055532 was reviewed.", "Record T2331823 was reviewed."):
        r = evaluate(t)
        assert "8055532" not in r.clean_text and "2331823" not in r.clean_text


# =====================================================================
# TALC — type registry and the Ledger
# =====================================================================

def test_unregistered_entity_is_phi():
    assert talc.spec("SOMETHING_NEW").phi is True
    assert talc.removed_under("SOMETHING_NEW", profile_is_lds=False)


def test_subtypes_inherit_parent_policy():
    for sub in ("DATE_MONTHNAME", "DATE_MD"):
        assert talc.canonical(sub) == "DATE_TIME"
        assert not talc.removed_under(sub, profile_is_lds=True)
        assert talc.removed_under(sub, profile_is_lds=False)


def test_lds_retains_is_derived_from_the_registry():
    from inkguard.deident_profile import LDS_RETAINS
    assert "DATE_MD" in LDS_RETAINS and "DATE_MONTHNAME" in LDS_RETAINS


def test_placeholders_use_canonical_names():
    out = evaluate("underwent cystoscopy on 4/13.").clean_text
    assert "DATE_MD" not in out and "[DATE]" in out


def test_ledger_reports_lost_detection():
    led = talc.Ledger()
    h = PHIHit("PERSON", 0, 5, "Aisha", 0.9, "anchor")
    led.detect([h])
    lost = led.settle([], not_phi=lambda e: False, text="Aisha went home")
    assert len(lost) == 1 and lost[0].fate == talc.LOST


def test_ledger_trailing_whitespace_is_not_lost():
    led = talc.Ledger()
    led.detect([PHIHit("PERSON", 0, 8, "Aisha   ", 0.9, "anchor")])
    red = [PHIHit("PERSON", 0, 5, "Aisha", 0.9, "anchor")]
    assert led.settle(red, not_phi=lambda e: False, text="Aisha   went") == []


def test_ledger_title_only_detection_is_not_lost():
    # Dedupe trims "Dr." off a PERSON span; the ledger applies the same trim.
    r = evaluate("Patient: Dr. Stephanie Wood DDS\nDOB: August 09, 1992\nMRN: 1803264\n")
    assert r.action != Action.BLOCKED


def test_non_phi_label_cannot_own_a_name():
    # ORGANIZATION outscored a PERSON in dedupe and the surname was kept.
    r = evaluate("Babett Mentzel-Warmer, 1964-07-30. BP 180/110. Known CKD stage 3b.")
    assert r.action != Action.BLOCKED
    assert "Mentzel" not in r.clean_text and "Babett" not in r.clean_text


def test_model_spans_do_not_cross_lines():
    r = evaluate("Patient Sarah Wilson\nMetoprolol 25 mg PO BID\nLisinopril 10 mg daily")
    assert "Sarah Wilson" not in r.clean_text
    assert "Metoprolol 25 mg PO BID" in r.clean_text


def test_role_word_is_not_part_of_the_name():
    assert "Aisha Haddad" in texts(scan("Patient Aisha Haddad has hernia."), "PERSON")


def test_id_label_is_not_idaho():
    r = evaluate("Member ID: BCBS99881")
    assert "Member ID:" in r.clean_text and "BCBS99881" not in r.clean_text


# =====================================================================
# SHALE AND ONYX — patient memory and per-patient codes
# =====================================================================

def test_memory_carries_a_name_into_an_unlabelled_note():
    from inkguard.cli import patient_memory
    notes = ["Patient: Hani Sharbatly   MRN: 99812\nHPI: stable.",
             "Sharbatly seen again, stable."]
    mem = patient_memory(notes)
    r = evaluate(notes[1], memory=mem)
    assert "Sharbatly" not in r.clean_text


def test_pseudonym_is_stable_across_notes_and_opaque():
    from inkguard.cli import patient_memory
    notes = ["Patient: Hani Sharbatly   MRN: 99812\nHPI: Mr. Sharbatly is a 66-year-old male.",
             "S: Sharbatly reports less dyspnea. A: improving."]
    mem = patient_memory(notes)
    outs = [evaluate(n, patient_key="p001", memory=mem, redaction_mode="pseudonym").clean_text
            for n in notes]
    import re
    codes = [set(re.findall(r"\[PERSON-([A-Z0-9]{5})\]", o)) for o in outs]
    assert codes[0] and codes[0] == codes[1]
    # opaque: the code carries no script or language marker
    for o in outs:
        assert "ARABIC" not in o.upper() or "ARABIC" in notes[0].upper()


def test_pseudonyms_differ_between_patients():
    a = evaluate("Patient: Hani Sharbatly", patient_key="p001", redaction_mode="pseudonym")
    b = evaluate("Patient: Hani Sharbatly", patient_key="p002", redaction_mode="pseudonym")
    assert a.clean_text != b.clean_text


# =====================================================================
# OBSIDIAN — final inspection
# =====================================================================

def test_residual_confirmed_name_quarantines():
    rep = obsidian.inspect("Seen: Sharbatly stable.", {"Hani Sharbatly"}, check_dates=False)
    assert rep.grade == "F" and rep.quarantined


def test_residual_name_in_clinical_use_is_allowed():
    rep = obsidian.inspect("Wilson disease, copper studies.", {"Mary Wilson"},
                                check_dates=False)
    assert not rep.quarantined


def test_residual_ssn_quarantines():
    rep = obsidian.inspect("SSN 123-45-6789 on file.", set(), check_dates=False)
    assert rep.quarantined


def test_final_name_mask_does_not_run_in_procedure_sections():
    t = "PROCEDURE: The fascia was grasped with Kocher. Doyle splints were placed."
    assert "[NAME]" not in evaluate(t).clean_text


# =====================================================================
# BERYL — operation log
# =====================================================================

def test_oplog_holds_no_text_and_verifies():
    from inkguard import beryl
    t = "Patient: Hani Sharbatly   MRN: 99812\nHPI: Mr. Sharbatly is stable."
    r = evaluate(t, patient_key="p001", want_oplog=True, redaction_mode="pseudonym")
    log = r.oplog
    blob = str(log)
    assert "Sharbatly" not in blob and "99812" not in blob and "p001" not in blob
    res = beryl.verify(t, log, "p001")
    assert res["verified"], res


# =====================================================================
# VOCABULARY PROVENANCE — open sources only, names never claimed
# =====================================================================

def test_no_textbook_lexicon_ships():
    import importlib.util
    assert importlib.util.find_spec("inkguard.lexicon_data") is None


def test_every_open_term_names_its_source():
    from inkguard.lexicon_open import OPEN_VOCAB, OPEN_EPONYMS_AMBIGUOUS
    allowed = {"uberon", "doid", "mondo", "maxo", "ncit", "icd10cm", "wordnet", "compound"}
    for table in (OPEN_VOCAB, OPEN_EPONYMS_AMBIGUOUS):
        for term, src in table.items():
            tags = {t.replace("-def", "") for t in src.split(",")}
            assert tags and tags <= allowed, (term, src)


@pytest.mark.parametrize("surname", [
    # real surnames (2000 US Census) that ontologies also contain as words
    "khalifa", "soles", "piedra", "castleman", "fluke", "mantle", "thien",
    "takada", "brugada",
])
def test_weak_vocabulary_never_claims_a_census_surname(surname):
    from inkguard.clinical_lexicon import ICD_VOCAB
    assert surname not in ICD_VOCAB


def test_title_overrides_always_clinical_eponym():
    assert "Crohn" in texts(scan("Seen by Dr. Crohn today."), "PERSON")
    assert texts(scan("History of Crohn disease, on infliximab."), "PERSON") == []


@pytest.mark.parametrize("text,term", [
    ("Verumontanum was identified as a landmark.", "Verumontanum"),
    ("Enterococcus urosepsis, now on ampicillin.", "Enterococcus"),
    ("Beckwith-Wiedemann, followed by genetics.", "Beckwith-Wiedemann"),
])
def test_open_vocabulary_protects_clinical_words(text, term):
    assert term in evaluate(text).clean_text


# =====================================================================
# SPEC DEFAULTS — [NAME]-style placeholders; the year kept where allowed
# =====================================================================

def test_placeholders_are_plain_bracketed_words():
    r = evaluate("Patient: Aisha Haddad   MRN: 4471982   SSN: 123-45-6789")
    assert "[NAME]" in r.clean_text and "[MRN]" in r.clean_text and "[SSN]" in r.clean_text
    assert "<" not in r.clean_text


@pytest.mark.parametrize("text,expect", [
    ("Admitted 03/14/2024 for pneumonia.", "Admitted 2024 for pneumonia."),
    ("DOB: August 09, 1992", "DOB: 1992"),
    ("Seen on 4/13 for follow-up.", "Seen on [DATE] for follow-up."),
])
def test_dates_keep_only_the_year(text, expect):
    assert evaluate(text).clean_text == expect


def test_year_pointing_to_age_over_89_is_not_kept():
    from inkguard.policy import _date_placeholder
    assert _date_placeholder("03/14/1925", now_year=2026) == "[DATE]"
    assert _date_placeholder("03/14/1937", now_year=2026) == "1937"


def test_date_shift_actually_shifts(monkeypatch):
    # It used to run after the dates were masked and shifted nothing.
    monkeypatch.setenv("PHI_DATE_SHIFT", "true")
    r = evaluate("Admitted 03/14/2024, discharged 03/18/2024.", patient_key="p1")
    assert r.date_shift_report.shifted == 2
    assert "03/14/2024" not in r.clean_text and "[DATE]" not in r.clean_text


# =====================================================================
# REGRESSIONS FROM THE INDEPENDENT-NAME TEST (surnames outside every
# name list the tool holds). Every miss becomes a permanent test.
# =====================================================================

@pytest.mark.parametrize("text,name", [
    ("S: Meskin reports less dyspnea. O: BP 128/76. A: CHF improving. P: continue Lasix.", "Meskin"),
    ("Reason for visit: Wiegandt needs refill.", "Wiegandt"),
    ("Assessment: Vilallonga with decompensated HFrEF.", "Vilallonga"),
    ("Tilma, 67, presents with worsening edema.", "Tilma"),
    ("Follow-up: Hauschel doing well. Foley removed.", "Hauschel"),
    ("HPI: Melanio Schweigger is a 58-year-old male. Schweigger denies fever.", "Schweigger"),
    ("Per Cide, symptoms began Tuesday.", "Cide"),
])
def test_unlisted_surnames_are_removed(text, name):
    r = evaluate(text)
    assert r.action != Action.BLOCKED
    assert name not in r.clean_text


def test_oplog_verifies_with_date_shifting(monkeypatch):
    from inkguard import beryl
    monkeypatch.setenv("PHI_DATE_SHIFT", "true")
    t = "Patient: Hani Sharbatly   DOB: 03/14/1958\nAdmitted 03/14/2024."
    r = evaluate(t, patient_key="p001", want_oplog=True)
    assert r.date_shift_report.shifted >= 1
    assert beryl.verify(t, r.oplog, "p001")["verified"]


# =====================================================================
# REGRESSIONS FROM THE MACHINE-GENERATED TEMPLATE TEST (tuning half). The
# notes were written independently of this code; every miss becomes a test.
# =====================================================================

@pytest.mark.parametrize("text,value", [
    ("Counts correct x2.\nGault tolerated procedure well, extubated, to PACU.", "Gault"),
    ("Colonoscopy showed SES-CD 14. Pennig's employer has documented 22 missed days.", "Pennig"),
    ("Photos stored under 48315494. Mrs. Toran asks that results be sent.", "48315494"),
    ("Insurance on file: W6930-1983.", "W6930-1983"),
    ("workers comp claim per registration. -Marble", "Marble"),
    ("Shing, Iray\nMRN 4471982 | DOB 1981", "Shing"),
    ("COMPARISON: CT 4/3.\nFINDINGS: Dilated appendix.", "4/3"),
    ("Toran tolerated well. F/u June 6.", "June 6"),
    ("Plan: Clinic follow up with Kinney on 3/4/2024.", "Kinney"),
    ("Medicaid Managed Care | ID | UHC730475957", "UHC730475957"),
    ("Contraindications in CKD, John Willbrook, admitted on July 22nd, 2022, MRN 8765-4321.", "8765-4321"),
    ("pt with multiple myeloma, Mary Smith, last seen at clinic", "Mary Smith"),
])
def test_ai_template_regressions(text, value):
    r = evaluate(text)
    assert r.action != Action.BLOCKED
    assert value not in r.clean_text


# ---------------------------------------------------------------------
# HAND-WRITTEN TEST REVIEW, ROUND 1. Invented notes read by hand
# the way a clinician would read them. Every finding is pinned here; all
# names, numbers and notes are invented.
# ---------------------------------------------------------------------

@pytest.mark.parametrize("text,gone,kept", [
    # Half-caught names: the other half is chained in.
    ("Identifying data: Rollo Kestenbaum is a 24 y.o. single man.", ["Rollo", "Kestenbaum"], ["24 y.o."]),
    ("Plan: SICU, CIWA protocol, ortho for radius. Trauma attending Weiming Ashdown.",
     ["Weiming", "Ashdown"], ["Trauma attending", "CIWA"]),
    # Named clinic in the plan; services stay.
    ("Follow-up: Rutledge-Oyelaran clinic for Foley removal, PSA in 6 weeks.", ["Rutledge-Oyelaran"], ["clinic", "Foley removal"]),
    ("Follow-up: Coumadin clinic in 1 week. Hand clinic in 2 weeks.", [], ["Coumadin clinic", "Hand clinic"]),
    # Ordinary words that were removed.
    ("MSE: Disheveled, psychomotor agitation, speech rapid.", [], ["Disheveled"]),
    ("Preop Dx: Chiari I malformation\nPostop Dx: same", [], ["Postop Dx"]),
    ("Dura opened.\nGraft: bovine pericardium, running 4-0 Nurolon.", [], ["Graft:"]),
    ("Follow-up: Outpatient therapy intake within 7 days.", [], ["Outpatient therapy"]),
    ("feels foggy. neuro nonfocal, pupils equal, gait nl", [], ["neuro nonfocal"]),
    # Relations keep their role word; the person goes.
    ("0800 VSS. Daughter Shontelle at bedside. Paged Dr. Quan re: K 3.1.\nR. Ballesteros, RN",
     ["Shontelle", "Quan", "Ballesteros"], ["Daughter", "Paged Dr."]),
    ("1000 Villagomez (charge) aware.", ["Villagomez"], ["(charge) aware"]),
    # Sign-off and point of contact.
    ("-- Greta Wierzbicki, RN  pager 4-1987 / x4473", ["Wierzbicki", "4-1987", "4473"], ["RN", "pager"]),
    ("Plan: d/w Ashdown re: ex-lap.\n\nWeiming Ashdown, MD\nTrauma Surgery",
     ["Ashdown", "Weiming"], ["MD", "Trauma Surgery", "ex-lap"]),
    ("Resident Physician: see attached. Attending Physician note to follow.", [],
     ["Resident Physician", "Attending Physician"]),
    # Billing codes are not ZIP codes; addresses are.
    ("CPT 99213, 71046, 93000. Address verified: 8817 Larkspur Blvd/Wexford Falls/43992.",
     ["8817 Larkspur", "Wexford Falls", "43992"], ["99213", "71046", "93000", "439**"]),
    ("heparin 10000 units IV. WBC 12000.", [], ["10000 units", "WBC 12000"]),
    # A relative whose name is a common word, claimed again later.
    ("SW met with her son Sunny Truong.\nAssessment: Sunny expressed concern about stairs. "
     "Will return in 2 weeks.", ["Sunny", "Truong"], ["expressed concern", "Will return"]),
    # Businesses named with ordinary words.
    ("She may return to desk work at Hush Landscaping in one week.", ["Hush"], ["Landscaping"]),
    ("Referred to CPS. Works for Maple Foods Inc.", ["Maple"], ["Foods Inc."]),
    # A lowercase credential after a name stays; the name goes.
    ("pt cooperative. f/u 6wk. Pendergrast md 22 Feb 2071", ["Pendergrast"], ["md 2071"]),
    # The confirmed name carries the same trim, or final inspection reads
    # the kept credential as a surviving piece of the name and blocks.
    ("Winifred Fischer PhD, September 13, 2041. Stage IIIC ovarian adenocarcinoma.",
     ["Winifred", "Fischer"], ["PhD", "2041"]),
    ("Referred to Child Protective Services. Social Services consulted.", [],
     ["Child Protective Services", "Social Services"]),
])
def test_hand_written_cases_round1(text, gone, kept):
    r = evaluate(text)
    assert r.action.value != "blocked", r.error
    for g in gone:
        assert g not in r.clean_text, (g, r.clean_text)
    for k in kept:
        assert k in r.clean_text, (k, r.clean_text)


@pytest.mark.parametrize("text,placeholder,kind", [
    ("Social: truck driver with Elardo Plumbing, on disability.", "[EMPLOYER]", "Plumbing"),
    ("Son works nights at Novoselsky Elementary School.", "[EMPLOYER]", "Elementary School"),
    ("Employer: Goettsch Landscaping\nClaim: pending", "[EMPLOYER]", "Landscaping"),
    ("Grandmother works double shifts at Pulcino Logistics.", "[EMPLOYER]", "Logistics"),
    ("Referred from Lakeview Medical Center.", "[FACILITY]", "Medical Center"),
])
def test_employer_and_facility_keep_the_kind_of_place(text, placeholder, kind):
    # 45 CFR 164.514(b)(2)(i)(A): employers' names are Safe Harbor
    # identifiers. The kind of workplace is the clinical context and stays.
    r = evaluate(text)
    assert f"{placeholder} {kind}" in r.clean_text, r.clean_text


def test_read_the_bottom_first():
    from inkguard.jasper import read_signoff
    a = read_signoff("0800 pt resting.\nDaughter at bedside.\nMarisol Ybarra, RN")
    assert a.author_class == "nurse" and len(a.names) == 1
    a = read_signoff("hi dr, my bp is 150s again\nthanks\nMarisol Ybarra")
    assert a.note_kind == "portal" and len(a.names) == 1
    # A signed name is hunted through the body.
    r = evaluate("Pt asked for Ybarra twice overnight.\n\nMarisol Ybarra, RN")
    assert "Ybarra" not in r.clean_text


def test_site_cues_only_raise_scores(tmp_path, monkeypatch):
    from inkguard import jasper
    f = tmp_path / "cues.json"
    f.write_text(json.dumps({"learned_cues": {"trauma attending": 2, "seen once": 1,
                                              "Bad Cue!": 5}}))
    monkeypatch.setenv("PHI_SITE_CUES", str(f))
    assert jasper.site_cues() == ("trauma attending",)
    t = "Trauma attending Weiming Ashdown"
    assert jasper.site_cue_before(t, t.index("Ashdown")) == "trauma attending"
    assert jasper.site_cue_before("Seen once by Kim", 13) is None


def test_safe_harbor_identifiers_cannot_be_kept(monkeypatch):
    # Employer removal stays on, and no configuration may keep any of the
    # eighteen identifiers.
    from inkguard import talc
    from inkguard.config import settings
    for ent in ("EMPLOYER", "PERSON", "DATE_TIME", "MRN", "LOCATION", "SOME_NEW_TYPE"):
        assert talc.removed_under(ent, False, keep={ent, talc.canonical(ent)})
    assert not talc.removed_under("FACILITY", False, keep={"FACILITY"})
    assert talc.refused_keeps(["EMPLOYER", "FACILITY", "PERSON"]) == ["EMPLOYER", "PERSON"]
    monkeypatch.setattr(settings, "keep_entities", ["EMPLOYER", "PERSON"])
    r = evaluate("Mary Okafor works nights at Novoselsky Elementary School.")
    assert "Novoselsky" not in r.clean_text and "Okafor" not in r.clean_text
