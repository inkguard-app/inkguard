"""
DE-IDENTIFICATION PROFILES — Safe Harbor vs Limited Data Set.

HIPAA offers three routes to sharing clinical data, and they are not
degrees of the same thing. They are different legal objects with
different obligations, and conflating them can lead a user to share
data under the wrong rules while believing they are covered.

  SAFE HARBOR         45 CFR 164.514(b)(2)
      Remove all eighteen identifiers. If all eighteen are removed and
      the covered entity has no actual knowledge that what remains could
      identify the person (164.514(b)(2)(ii)), the data is not PHI. Dates
      are stripped to year, ZIP to three digits, ages over 89 banded.

  LIMITED DATA SET    45 CFR 164.514(e)
      Remove sixteen DIRECT identifiers. Full dates, city, state, full
      five-digit ZIP, county and all ages are RETAINED. The result is
      STILL PHI. It may be disclosed only for research, public health
      or health-care operations, and only under a Data Use Agreement.

  EXPERT DETERMINATION  45 CFR 164.514(b)(1)
      A person with appropriate knowledge of and experience with
      statistical and scientific methods determines, and documents, that
      the risk of re-identification is very small.
      Out of scope here — this software does not make determinations.

WHY AN LDS IS THE HIGHER-RISK ARTEFACT, NOT THE LOWER ONE
---------------------------------------------------------
It looks safer because it is "less than full PHI". It is not. An LDS
legally retains date of birth, sex and full ZIP, and the combination of
those three uniquely identifies the large majority of the US population
(Sweeney 1997/2000; ~63% on 2000-census data per Golle 2006). An LDS is
protected by CONTRACT — the Data Use Agreement — not by any property of
the data itself.

Two consequences the code must honour. Residual linkage scoring matters
MORE here, not less, because the retained fields are precisely the
matching tuple. And date shifting is the single highest-value transform
available for an LDS, because it removes the strongest quasi-identifier
while leaving every interval the research depends on intact.

THE ONE THING THIS MODULE MUST NEVER DO
---------------------------------------
Describe an LDS as "de-identified". It is PHI. Any output that implies
otherwise would lead a user to share it without a DUA, which is a
disclosure violation caused by our own wording.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class Profile(str, Enum):
    SAFE_HARBOR = "safe_harbor"
    LIMITED_DATA_SET = "limited_data_set"


# =====================================================================
# THE SIXTEEN DIRECT IDENTIFIERS — 45 CFR 164.514(e)(2)
#
# An LDS must exclude these, for the individual AND for their relatives,
# employers and household members. Note what is ABSENT from this list
# relative to Safe Harbor's eighteen: dates, town or city, state and ZIP
# code (a street address is still removed), and Safe Harbor's catch-all
# (R) "any other unique identifying number, characteristic, or code".
#
# This set and entities_to_redact below are not used by the redaction
# path; what each profile removes is decided by talc.removed_under.
# =====================================================================
LDS_MUST_REMOVE: frozenset[str] = frozenset({
    "PERSON",              # 1  names
    "LOCATION_STREET",     # 2  street address (city/state/ZIP retained)
    "PHONE_NUMBER",        # 3  telephone numbers
    "FAX_NUMBER",          # 4  fax numbers
    "EMAIL_ADDRESS",       # 5  email addresses
    "US_SSN",              # 6  social security numbers
    "MRN",                 # 7  medical record numbers
    "HEALTH_PLAN_ID",      # 8  health plan beneficiary numbers
    "US_BANK_NUMBER",      # 9  account numbers
    "CREDIT_CARD",         # 9  account numbers
    "US_DRIVER_LICENSE",   # 10 certificate/license numbers
    "MEDICAL_LICENSE",     # 10 certificate/license numbers
    "NPI",                 # 10 certificate/license numbers
    "VEHICLE_ID",          # 11 vehicle identifiers and plates
    "DEVICE_ID",           # 12 device identifiers and serials
    "URL",                 # 13 web URLs
    "IP_ADDRESS",          # 14 IP addresses
    "BIOMETRIC_ID",        # 15 biometric identifiers
    "FULL_FACE_PHOTO",     # 16 full-face photographs
})

# Retained under an LDS and REMOVED or generalized under Safe Harbor.
# This set is the whole practical difference between the profiles.
# DERIVED from the shared vocabulary (inkguard/talc.py), not maintained by
# hand: a hand-kept list could let a subtype such as DATE_MONTHNAME fall
# out of a filter. Every subtype whose canonical family an LDS may keep is
# listed.
from inkguard.talc import REGISTRY as _REGISTRY  # noqa: E402

LDS_RETAINS: frozenset[str] = frozenset(
    k for k, v in _REGISTRY.items() if v.phi and v.lds_retains)


@dataclass
class ProfileConfig:
    profile: Profile
    redact_entities: frozenset[str]
    apply_safe_harbor_generalization: bool   # age banding + ZIP truncation
    is_still_phi: bool
    requires_dua: bool
    citation: str
    description: str
    obligations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "profile": self.profile.value,
            "citation": self.citation,
            "description": self.description,
            "output_is_still_phi": self.is_still_phi,
            "requires_data_use_agreement": self.requires_dua,
            "retains_full_dates": not self.apply_safe_harbor_generalization,
            "obligations": self.obligations,
        }


SAFE_HARBOR_CONFIG = ProfileConfig(
    profile=Profile.SAFE_HARBOR,
    # Whatever the deployment counts as PHI; resolved at call time from
    # settings so a user's entity list stays the single source.
    redact_entities=frozenset(),
    apply_safe_harbor_generalization=True,
    is_still_phi=False,
    requires_dua=False,
    citation="45 CFR 164.514(b)(2)",
    description=(
        "Safe Harbor method: the tool looks for the sixteen kinds of Safe "
        "Harbor identifier that can appear in typed text and replaces what it "
        "finds; a person reviews the result. "
        "Output that a person has reviewed, with no actual knowledge that "
        "what remains could identify anyone, meets the Safe Harbor standard."
    ),
    obligations=[
        "No actual knowledge that the remaining information could identify "
        "the individual (164.514(b)(2)(ii)).",
    ],
)

LDS_CONFIG = ProfileConfig(
    profile=Profile.LIMITED_DATA_SET,
    redact_entities=LDS_MUST_REMOVE,
    apply_safe_harbor_generalization=False,   # dates and ZIP stay intact
    is_still_phi=True,
    requires_dua=True,
    citation="45 CFR 164.514(e)",
    description=(
        "Sixteen direct identifiers removed; full dates, city, state, ZIP "
        "and all ages retained. Output REMAINS PHI and is not "
        "de-identified."
    ),
    obligations=[
        "A Data Use Agreement must be in place before disclosure "
        "(164.514(e)(4)).",
        "Permitted only for research, public health, or health care "
        "operations (164.514(e)(3)).",
        "The recipient must not re-identify or contact individuals.",
        "Retained DOB, sex and full ZIP are a re-identifying combination; "
        "date shifting and residual-linkage review are strongly advised.",
    ],
)


def get_profile(name: str | None = None) -> ProfileConfig:
    """Resolve the active profile. Safe Harbor is the default because it
    is the only one of the two that can yield non-PHI output —
    defaulting to LDS would silently hand a user PHI without saying so."""
    raw = (name or os.getenv("PHI_DEIDENT_PROFILE", "safe_harbor")).strip().lower()
    if raw in ("lds", "limited_data_set", "limited-data-set"):
        return LDS_CONFIG
    return SAFE_HARBOR_CONFIG


def entities_to_redact(config: ProfileConfig, configured: list[str]) -> list[str]:
    """Which entity types this profile removes.

    Safe Harbor uses the deployment's configured PHI list unchanged.
    LDS intersects it with the sixteen direct identifiers, so dates and
    geography survive — and uses set logic rather than a hardcoded list
    so that a user who adds a custom recognizer still gets the
    correct LDS behaviour for it.
    """
    if config.profile is Profile.SAFE_HARBOR:
        return list(configured)
    return [e for e in configured if e in config.redact_entities]
