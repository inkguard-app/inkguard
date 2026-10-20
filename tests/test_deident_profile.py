"""
Tests for the Safe Harbor / Limited Data Set profiles.

The failure this file exists to prevent is not a crash. It is a
researcher who receives an LDS, is told it is "de-identified", shares it
without a Data Use Agreement, and commits a disclosure violation caused
by our wording. Several tests below assert on LANGUAGE for that reason.

The substantive difference between the profiles is narrow and easy to
get backwards: an LDS RETAINS full dates, city, state, full ZIP and all
ages. Applying Safe Harbor's age banding or ZIP truncation to an LDS
destroys exactly what 164.514(e) permits keeping, and is the reason a
researcher asked for an LDS instead of a de-identified set.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("PHI_ROOT_KEY", "ab" * 32)

from inkguard.deident_profile import (  # noqa: E402
    Profile, get_profile, entities_to_redact, LDS_MUST_REMOVE,
)

CONFIGURED = [
    "PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "LOCATION", "DATE_TIME",
    "US_SSN", "MRN", "HEALTH_PLAN_ID", "NPI", "DEVICE_ID", "IP_ADDRESS",
    "US_DRIVER_LICENSE", "CREDIT_CARD", "URL",
]


# ------------------------------------------------------------ resolution

def test_default_is_safe_harbor():
    """Defaulting to LDS would hand users PHI they think is safe."""
    assert get_profile(None).profile is Profile.SAFE_HARBOR
    assert get_profile("").profile is Profile.SAFE_HARBOR
    assert get_profile("nonsense").profile is Profile.SAFE_HARBOR


def test_lds_aliases_resolve():
    for alias in ("lds", "LDS", "limited_data_set", "limited-data-set"):
        assert get_profile(alias).profile is Profile.LIMITED_DATA_SET


# ------------------------------------------------------------ the difference

def test_lds_retains_dates_and_geography():
    """THE defining property. If these are redacted it is not an LDS."""
    removed = set(entities_to_redact(get_profile("lds"), CONFIGURED))
    assert "DATE_TIME" not in removed, "LDS must retain full dates"
    assert "LOCATION" not in removed, "LDS must retain city/state/ZIP"


def test_lds_still_removes_the_direct_identifiers():
    removed = set(entities_to_redact(get_profile("lds"), CONFIGURED))
    for direct in ("PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS", "US_SSN",
                   "MRN", "HEALTH_PLAN_ID", "NPI", "DEVICE_ID",
                   "IP_ADDRESS", "URL", "CREDIT_CARD"):
        assert direct in removed, f"LDS must remove {direct}"


def test_safe_harbor_removes_everything_configured():
    removed = set(entities_to_redact(get_profile("safe_harbor"), CONFIGURED))
    assert removed == set(CONFIGURED)


def test_only_safe_harbor_generalizes():
    """Age banding and ZIP truncation under an LDS would strip the very
    fields 164.514(e) exists to retain."""
    assert get_profile("safe_harbor").apply_safe_harbor_generalization is True
    assert get_profile("lds").apply_safe_harbor_generalization is False


def test_custom_entities_are_handled_by_set_logic():
    """A user's own recognizer must not silently become retained
    under LDS just because it was not hardcoded anywhere."""
    removed = set(entities_to_redact(get_profile("lds"),
                                     CONFIGURED + ["CUSTOM_BIOBANK_ID"]))
    assert "CUSTOM_BIOBANK_ID" not in removed  # not a listed direct identifier
    assert "CUSTOM_BIOBANK_ID" not in LDS_MUST_REMOVE


# ------------------------------------------------------------ legal status

def test_lds_output_is_still_phi():
    """The single most consequential fact about an LDS."""
    c = get_profile("lds")
    assert c.is_still_phi is True
    assert c.requires_dua is True


def test_safe_harbor_output_is_not_phi():
    """The profile's legal label only; whether a given output meets Safe
    Harbor depends on review."""
    c = get_profile("safe_harbor")
    assert c.is_still_phi is False
    assert c.requires_dua is False


def test_lds_never_described_as_deidentified():
    """Wording test. A researcher who reads 'de-identified' may share
    without a DUA — a disclosure violation we would have caused."""
    d = get_profile("lds").to_dict()
    blob = (d["description"] + " " + " ".join(d["obligations"])).lower()
    assert "remains phi" in blob or "not de-identified" in blob
    assert d["output_is_still_phi"] is True
    assert d["requires_data_use_agreement"] is True


def test_lds_obligations_name_the_dua_and_citation():
    d = get_profile("lds").to_dict()
    assert "164.514(e)" in d["citation"]
    joined = " ".join(d["obligations"])
    assert "Data Use Agreement" in joined
    assert "research, public health" in joined


def test_lds_warns_about_the_reidentifying_combination():
    """DOB + sex + full ZIP is the classic re-identifying tuple, and an
    LDS retains all three. Saying so is the point of the profile."""
    joined = " ".join(get_profile("lds").obligations).lower()
    assert "re-identifying" in joined
    assert "date shifting" in joined


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
