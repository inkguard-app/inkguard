"""Key custody: derived keys are independent, repeatable, and never printed."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from inkguard.security import KeyStore

ROOT = bytes(range(32))


def test_derived_keys_are_independent():
    ks = KeyStore(root_key=ROOT)
    a = ks.derive("code-table-v1")
    b = ks.derive("date-shift-v1")
    assert a != b
    assert len(a) == 32 and len(b) == 32


def test_derivation_is_deterministic_across_instances():
    assert KeyStore(root_key=ROOT).derive("x") == KeyStore(root_key=ROOT).derive("x")


def test_short_root_key_rejected():
    with pytest.raises(ValueError):
        KeyStore(root_key=b"tooshort")


def test_keystore_never_prints_key_material():
    ks = KeyStore(root_key=ROOT)
    assert "REDACTED" in repr(ks)
    assert ROOT.hex() not in repr(ks)


def test_no_root_key_means_a_key_for_this_process_only():
    os.environ.pop("PHI_ROOT_KEY", None)
    a, b = KeyStore(), KeyStore()
    assert a.ephemeral and b.ephemeral
    assert a.derive("x") != b.derive("x")
