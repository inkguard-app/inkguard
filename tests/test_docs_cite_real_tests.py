"""Every test the security document cites as proof must exist.

docs/SECURITY_STANDARDS.md names, for each control, the tests that prove it.
A renamed or deleted test would leave a control with no proof behind it, so
the citations are checked against the test suite itself.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_cited_test_exists():
    doc = (ROOT / "docs" / "SECURITY_STANDARDS.md").read_text(encoding="utf-8")
    cited = set(re.findall(r"`(test_\w+)`", doc))
    defined = set()
    for p in (ROOT / "tests").glob("test_*.py"):
        defined |= set(re.findall(r"^def (test_\w+)", p.read_text(encoding="utf-8"), re.M))
    missing = sorted(cited - defined)
    assert cited and missing == [], f"cited but not found: {missing}"


def test_every_module_is_in_the_module_index():
    doc = (ROOT / "docs" / "MODULES.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^\| `(\w+)` \|", doc, re.M))
    modules = {p.stem for p in (ROOT / "inkguard").glob("*.py")} - {"__init__"}
    assert modules == listed, (sorted(modules - listed), sorted(listed - modules))
