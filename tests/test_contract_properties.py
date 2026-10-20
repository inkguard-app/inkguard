"""Stress the one door (inkguard/contract.py) with many generated notes and hold
its invariants, in every mode and profile. The contract is now the single
chokepoint every de-identification flows through, so a hole here would reach
the whole tool. All generated content is invented.

Invariants, for every note x mode x profile:
  - status is one of clear / review / stop;
  - a non-stop result's edits are sorted, non-overlapping and in bounds;
  - applying the edits to the input reproduces the engine's output EXACTLY in
    mask mode (what the document path depends on), and `reproduced` says so;
  - clean_text is the engine's authoritative output and never contains a
    removable identifier that the engine itself removed;
  - secrets are exactly the strings the edits remove.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from inkguard import contract  # noqa: E402

# Invented building blocks. None of these are real people or numbers.
NAMES = ["Hubert Lindqvist", "Rose Alvarado", "Marta Quispe", "Thaddeus Winterbottom",
         "Gwendolyn Ashcroft", "Okonkwo Adaeze", "Zhou Wei", "Priya Raghunathan"]
BODIES = ["72M with chest pain, retired schoolteacher.",
          "Tolerating diet. Ambulating well. Vitals stable overnight.",
          "Plan: aspirin daily; follow up in clinic in two weeks.",
          "POD 2 s/p lap chole. No fever. Incision clean and dry.",
          "Assessment: improving. Continue current management."]
# (label line, the secret string, whether the Limited Data Set keeps it).
# The LDS deliberately keeps dates, ZIP codes, towns and ages, so those are
# not leaks under that profile.
IDENTIFIERS = [
    ("MRN 4471822", "4471822", False),
    ("SSN 123-45-6789", "123-45-6789", False),
    ("DOB 03/14/1951", "03/14/1951", True),
    ("phone 415-555-0199", "415-555-0199", False),
    ("email hubert.lindqvist@example.com", "hubert.lindqvist@example.com", False),
    ("12 Orchard Lane, Marion, IA 52302", "Orchard Lane", True),
]


def _note(rng: random.Random) -> tuple[str, list[tuple[str, bool]]]:
    """A synthetic note and the invented identifiers it contains, each with
    whether the Limited Data Set keeps it."""
    lines = []
    name = rng.choice(NAMES)
    lines.append(f"Patient: {name}")
    secrets: list[tuple[str, bool]] = [(name, False)]
    for _ in range(rng.randint(1, 3)):
        label, secret, lds_kept = rng.choice(IDENTIFIERS)
        lines.append(label)
        secrets.append((secret, lds_kept))
    rng.shuffle(BODIES)
    lines += BODIES[:rng.randint(1, 3)]
    if rng.random() < 0.5:
        lines.append(f"Seen by Dr. {rng.choice(NAMES).split()[0]} Kellerman.")
    return "\n".join(lines), secrets


def _check_invariants(text: str, r, mask: bool):
    assert r.status in ("clear", "review", "stop")
    if r.status == "stop":
        return
    es = sorted(r.edits)
    assert all(0 <= s <= e <= len(text) for s, e, _ in es), "edit out of bounds"
    assert all(es[i][1] <= es[i + 1][0] for i in range(len(es) - 1)), "edits overlap"
    assert r.secrets == {text[s:e] for s, e, _ in es}
    if mask:
        # In mask mode the document path relies on the edits rebuilding the
        # engine's output exactly.
        assert r.reproduced, "mask-mode edits did not reproduce the engine output"
        assert contract._apply(text, r.edits) == r.clean_text


def test_contract_holds_its_invariants_over_many_notes():
    rng = random.Random(2026)
    stops = clears = 0
    for _ in range(120):
        text, secrets = _note(rng)
        for mode in ("mask", "pseudonym"):
            for profile in ("safe_harbor", "limited_data_set"):
                r = contract.deidentify(text, mode=mode, profile=profile, patient_key="p")
                _check_invariants(text, r, mask=(mode == "mask"))
                if r.status == "stop":
                    stops += 1
                    continue
                clears += 1
                # Nothing the engine removed should still be readable. Under
                # the Limited Data Set profile, dates and towns are kept on
                # purpose, so those are not leaks there.
                for s, lds_kept in secrets:
                    if profile == "limited_data_set" and lds_kept:
                        continue
                    assert s not in r.clean_text, f"{s!r} left in ({mode}/{profile}): {r.clean_text!r}"
    assert clears > 0


def test_empty_and_tiny_inputs_do_not_crash():
    for text in ("", " ", "\n", "ok", "No PHI here at all."):
        r = contract.deidentify(text, mode="mask")
        assert r.status in ("clear", "review", "stop")
        if r.status != "stop":
            assert contract._apply(text, r.edits) == r.clean_text


def test_pseudonym_mode_is_honest_not_silently_masked():
    # The authoritative output in pseudonym mode is clean_text; the edits are
    # placeholder-form, so they are NOT expected to rebuild it. The contract
    # must not silently hand back masked text as if it were pseudonymised.
    text = "Patient: Hubert Lindqvist seen by Dr. Rose Alvarado. Plan: follow up."
    r = contract.deidentify(text, mode="pseudonym", profile="safe_harbor", patient_key="p1")
    if r.status != "stop":
        assert "Lindqvist" not in r.clean_text and "Alvarado" not in r.clean_text
        assert r.clean_text == r.raw.clean_text          # the engine's own output
