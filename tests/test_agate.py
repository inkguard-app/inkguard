"""The unknown-word check (agate): a note goes out with no flags only
if every word left in it is one the tool can name; otherwise it is held for
a person. Plus every case from the second hand-written test review.
All names, numbers and notes here are invented."""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PHI_ROOT_KEY", "ab" * 32)

import pytest  # noqa: E402

from inkguard import agate  # noqa: E402
from inkguard.policy import evaluate  # noqa: E402


def reasons(text, hits=(), **kw):
    c = agate.assess(text, list(hits), **kw)
    return [(text[n.start:n.end], n.reason) for n in c.not_understood], c


# ------------------------------------------------- the unknown-word check

def test_a_note_it_fully_understands_is_cleared():
    r = evaluate("Patient seen for knee pain. Plan PT and ibuprofen 400 mg PO q6h prn.")
    assert r.cleared and r.action.value in ("allow", "redacted")


def test_a_name_left_in_holds_the_note():
    r = evaluate("Plan: Addiction medicine consult. Hubert to call brother.")
    assert r.action.value == "review" and not r.cleared
    words = [(n.reason) for n in r.comprehension.blocking]
    assert "name_left_in" in words


@pytest.mark.parametrize("text,word,reason", [
    ("cards consult asked by Cseh ortho re preop eval", "Cseh", "possible_person"),
    ("SW met with the patient and Hurt (hospitalist) to discuss discharge.", "Hurt", "name_left_in"),
    ("Lives in Waco with her son.", "Waco", "place_left_in"),
    ("Seen on 4/16 for f/u.", "4/16", "date_left_in"),
    ("Next visit March 14th.", "March 14th", "date_left_in"),
    ("Callback code XK4471 given.", "XK4471", "unknown_code"),
    ("Referred by JKD for eval.", "JKD", "unknown_acronym"),
    ("hi its ybarra, need a refill", "ybarra", "lowercase_name"),
    ("Account 12345678 on file.", "12345678", "long_number"),
])
def test_what_it_cannot_name(text, word, reason):
    found, c = reasons(text)
    assert (word, reason) in found, found
    assert not c.cleared


@pytest.mark.parametrize("text", [
    "Foley in place. Foley to gravity. Remove the Foley POD2.",
    "Neck: Supple. Mark the site. Will return in 2 weeks. Rose to 38.5 overnight.",
    "WBAT RLE. Shift 1900-0700. Pt's pain controlled. I'm told she is better.",
    "BRCA1 c.68_69delAG (p.Glu23fs); VUS in ATM c.2119T>C.",
    "Dx: SBO 2/2 adhesions. Hx HTN, HLD, CKD3. EF 55%. AVA 1.1.",
    "Fascia closed with 0 Vicryl. Steri-Strips applied. non-Hodgkin lymphoma.",
    "try Zyrtec instead of Allegra again",
    "Dense adhesions from the ligament of Treitz.",
])
def test_what_it_understands(text):
    found, c = reasons(text)
    assert c.cleared, found


def test_a_removal_it_was_unsure_of_is_listed_but_does_not_hold():
    # Over-removal of an uncertain word is listed for the reviewer but does not hold the note.
    @dataclass
    class H:
        start: int
        end: int
        layer: str
    t = "Seen with Mova today."
    c = agate.assess(t, [H(10, 14, "uncertain")])
    assert c.cleared and c.not_understood and not c.blocking


def test_limited_data_set_keeps_dates_and_towns():
    found, c = reasons("Seen on 4/16 in Waco.", lds=True)
    assert c.cleared, found


def test_the_gate_fails_closed(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("broken")
    monkeypatch.setattr(agate, "assess", boom)
    r = evaluate("Patient seen for knee pain.")
    assert r.action.value == "review"


def test_the_gate_never_logs_words():
    r = evaluate("Plan: Addiction medicine consult. Hubert to call brother.", want_oplog=True)
    blob = json.dumps(r.oplog)
    assert "Hubert" not in blob and r.oplog["agate"]["not_understood"]


def test_cli_puts_held_notes_apart(tmp_path):
    from inkguard import cli
    from inkguard.policy import evaluate as ev
    notes = tmp_path / "in" / "p1"
    notes.mkdir(parents=True)
    (notes / "a.txt").write_text("Patient seen for knee pain. Plan PT.\n")
    (notes / "b.txt").write_text("Plan: Addiction medicine consult. Hubert to call brother.\n")
    stats = cli.RunStats()
    cli.process_directory(ev, tmp_path / "in", tmp_path / "out", stats, None, False, True, mode="mask")
    out = tmp_path / "out"
    assert cli.done_path(out, "p1/a.txt").exists()
    assert not cli.done_path(out, "p1/b.txt").exists()
    assert cli.flagged_path(out, "p1/b.txt").exists()
    words = (cli.flagged_path(out, "p1/b.txt").parent / cli.WORDS_FILE).read_text()
    assert "`Hubert`" in words and "| 2 |" in words      # the word, on line 2 (after the watermark)
    assert stats.needs_review == 1


def test_preflight_refuses_held_notes():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import preflight_check as pc
    assert pc.ARTIFACT_FILES.search("out/2_REDACTED-FLAGGED/p1/REDACTED-FLAGGED__b.txt")
    assert pc.ARTIFACT_FILES.search("out/2_REDACTED-FLAGGED/p1/_WORDS-TO-CHECK.md")
    assert pc.ARTIFACT_FILES.search("out/3_NOT-PROCESSED-MAY-CONTAIN-PHI/p1/x.jpg")
    assert pc.ARTIFACT_FILES.search("out/.inkguard/run_settings.json")


# ------------------------------------- second hand-written test review

@pytest.mark.parametrize("text,gone,kept", [
    ("Adm Sep 26th\nAttg: Ashcombe\n\nDx: SBO 2/2 adhesions", ["Ashcombe"], ["Adm", "Attg:"]),
    ("Dense adhesions from prior Pfannenstiel. Bowel viable, Doppler signal good. Foley out POD1.",
     [], ["Pfannenstiel", "Doppler signal", "Foley out"]),
    ("Patient turned prone onto a Jackson table with Wilson frame. Foramina free with a "
     "ball-tip probe and Penfield No. 4.", [], ["Wilson frame", "Penfield No. 4"]),
    ("NIGHT SIGNOUT 10/10 - 7 WEST\n-Pellow-rm 712-CHF exac, recheck BMP\n"
     "-Jolene P.-rm 714-DKA resolving\n-Jolene (the Pellow in 716, NOT the one in 712!)-call",
     ["Pellow", "Jolene"], ["rm 712", "CHF exac", "DKA resolving"]),
    ("Referring physician: Dr. Haverty, Endocrinology", ["Haverty"], ["Endocrinology"]),
    ("no cavernous sinus invasion (Knosp 1).", [], ["Knosp 1"]),
    ("Critical K 6.1 called to Quintanar, RN at 0215.\nPt:Vasko,Teodor Acct/MRN:K5512083",
     ["Quintanar", "Vasko", "Teodor", "K5512083"], ["RN", "Acct"]),
    ("will sign out to day team. -Brusewitz 7/13 0340", ["Brusewitz"], ["sign out"]),
    ("cards consult\nasked by Valdespino ortho re preop eval", ["Valdespino"], ["ortho"]),
    ("relative adrenal insufficiency; Addison unlikely given AM cortisol", [], ["Addison unlikely"]),
    ("1mm rust ring central-inferior cornea, seidel neg. FB removed.", [], ["seidel neg"]),
    ("Ocampo returned to modified duty.\n-Nasrin Przybylsk Jun 9th", ["Nasrin", "Przybylsk"], []),
    ("Imms given. Next WCC 15 mo.", [], ["WCC"]),
    ("variant BRCA1 c.68_69delAG (p.Glu23fs).\n-Linh Halvorsen, MS, CGC", ["Halvorsen"],
     ["p.Glu23fs", "MS, CGC"]),
    ("Proband: Ottilie Marsh (II-2), 38F, dx L breast IDC", ["Ottilie", "Marsh"], ["Proband"]),
    ("PEDS NEURO CONSULT\n\nMerry Vu, 11 y.o., admitted for elective EEG monitoring.",
     ["Merry"], ["11 y.o."]),
    ("Return precautions given. F/u with PCP Dr. Ferrante in 1 wk.", ["Ferrante"], ["PCP"]),
    ("ph 319-555-0186. -Thibodeau np", ["Thibodeau", "319-555-0186"], []),
])
def test_hand_written_cases_round2(text, gone, kept):
    r = evaluate(text)
    assert r.action.value != "blocked", r.error
    for g in gone:
        assert g not in r.clean_text, (g, r.clean_text)
    for k in kept:
        assert k in r.clean_text, (k, r.clean_text)
