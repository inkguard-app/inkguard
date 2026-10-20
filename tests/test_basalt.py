"""
Intake (basalt) tests.

Two categories, and the first is security rather than ergonomics:

  EVASION — invisible characters and homoglyphs are working bypasses of
    the detection layers. A zero-width space inside an SSN defeats the
    regex outright. These tests are regression guards on a real hole.

  DIAGNOSTICS — quality findings that tell the user how to send
    better input. Every finding must carry an actionable fix; a
    complaint without a remedy is noise the user learns to ignore.
"""
import sys
import os
import pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inkguard.policy import evaluate, Action
from inkguard.basalt import diagnose, sanitize, Severity

ZWSP = "\u200b"
CYRILLIC_A = "\u0430"
CYRILLIC_O = "\u043e"


# ---------- Evasion: these are bypasses, not style issues ----------
def test_zero_width_space_does_not_defeat_ssn_detection():
    """One invisible character between digits used to break the regex
    completely and leave the SSN in the output."""
    r = evaluate(f"SSN 123-45-{ZWSP}6789 on file.")
    assert "6789" not in r.clean_text, "SSN leaked via zero-width character"
    assert "[SSN]" in r.clean_text


def test_zero_width_is_reported_to_client():
    d = diagnose(f"SSN 123-45-{ZWSP}6789")
    codes = [f.code for f in d.findings]
    assert "INVISIBLE_CHARACTERS" in codes
    finding = next(f for f in d.findings if f.code == "INVISIBLE_CHARACTERS")
    assert finding.fix, "finding must tell the user what to do"


def test_homoglyph_substitution_normalized():
    text = f"Patient J{CYRILLIC_O}hn {CYRILLIC_A}lvarez here."
    clean, actions, findings = sanitize(text)
    assert "John" in clean
    assert "alvarez" in clean.lower()
    assert any(f.code == "HOMOGLYPH_CHARACTERS" for f in findings)


def test_fullwidth_digits_normalized_to_ascii():
    r = evaluate("SSN \uff11\uff12\uff13-\uff14\uff15-\uff16\uff17\uff18\uff19 on file.")
    assert "[SSN]" in r.clean_text


def test_control_characters_stripped():
    clean, actions, findings = sanitize("SSN 123-45-67\x0089")
    assert "\x00" not in clean


def test_sanitization_leaves_clean_text_untouched():
    text = "CT head negative. Patient Sarah Wilson, MRN C445566."
    clean, actions, findings = sanitize(text)
    assert clean == text
    assert actions == []
    assert findings == []


# ---------- Silent-failure blockers ----------
def test_non_english_text_is_blocked_not_silently_passed():
    """The NER model is English-only. On other scripts it finds nothing
    and reports success — a silent false negative, which is worse than
    an error. Must block."""
    r = evaluate("Пациент Иван Петров, диагноз установлен, МРН А123456.")
    assert r.action is Action.BLOCKED
    assert "UNSUPPORTED_SCRIPT" in (r.error or "")


# ---------- Quality diagnostics ----------
def test_mojibake_detected():
    d = diagnose("Patient reported chest painâ€™s onset yesterday.")
    assert any(f.code == "ENCODING_CORRUPTION" for f in d.findings)


def test_base64_blob_detected():
    d = diagnose("Attachment: " + "QUJDREVG" * 40)
    assert any(f.code == "EMBEDDED_BINARY" for f in d.findings)


def test_truncation_detected():
    d = diagnose("The patient presented with " + "chest pain and dyspnea " * 12 + "and then")
    assert any(f.code == "POSSIBLE_TRUNCATION" for f in d.findings)


def test_clean_input_scores_one():
    d = diagnose("CT head negative. Patient Sarah Wilson, MRN C445566. BP 140/90.")
    assert d.quality_score == 1.0
    assert d.findings == []
    assert d.blocked is False


def test_quality_score_degrades_with_findings():
    clean = diagnose("Patient Sarah Wilson, MRN C445566.")
    dirty = diagnose(f"Patient S{ZWSP}arah Wilson, MRN C4{ZWSP}45566. painâ€™s")
    assert dirty.quality_score < clean.quality_score


def test_every_finding_carries_an_actionable_fix():
    """A complaint with no remedy is noise the user learns to ignore."""
    d = diagnose(f"S{ZWSP}SN 123-45-6789 painâ€™s " + "QUJDREVG" * 40)
    assert d.findings
    for f in d.findings:
        assert f.fix and len(f.fix) > 20, f"{f.code} has no actionable fix"


def test_unresolved_abbreviations_surface_as_advice():
    d = diagnose("Note: CT.", unresolved_tokens=["CT"])
    finding = next(f for f in d.findings if f.code == "AMBIGUOUS_ABBREVIATIONS")
    assert "CT" in finding.message
    assert finding.severity is Severity.ADVISE
