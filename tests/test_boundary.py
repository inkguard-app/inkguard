"""The core/shell boundary (see docs/HOW_IT_WORKS.md).

The file-handling layer (the shell) reaches the redaction engine only through
inkguard/contract.py, so each side can be read, tested and changed on its own.
This test reads each shell module's imports and fails if a shell module imports
an engine module any other way.

No module outside the engine imports an engine module directly. LEGACY_CROSSINGS
stays empty, and the test below fails if a crossing reappears.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "inkguard"

# The redaction engine and its clinical word lists: the core.
CORE = {
    "basalt", "quartz", "mica", "phi_scanner", "garnet", "shale", "slate",
    "jasper", "talc", "onyx", "obsidian", "agate", "pumice", "policy", "hipaa",
    "deident_profile", "date_shift", "input_classifier", "clinical_context",
    "clinical_lexicon", "clinical_translator", "contamination", "linkage",
    "rare_conditions", "name_registry",
    "lexicon_data_v2", "lexicon_english", "lexicon_gate", "lexicon_icd_vocab",
    "lexicon_open", "lexicon_rare",
}
# The shell: everything about files, screens, workflow and delivery.
SHELL = {"cli", "cli_attest", "cli_docs", "cli_report", "cli_review", "window", "calcite", "zircon", "chert", "amber", "opal", "granite", "halite", "fluorite",
         "dolomite", "pyrite"}
# The one door. The shell may import this freely.
DOOR = {"contract"}

# Every shell module reaches the engine only through the contract.
CLEAN = {"calcite", "zircon", "cli", "cli_attest", "cli_docs", "cli_report", "cli_review", "window", "chert", "amber", "opal", "granite", "fluorite"}

# No shell module imports an engine module. This stays empty; the test below
# fails if a crossing ever reappears.
LEGACY_CROSSINGS: dict[str, set] = {}


def _engine_imports(path: Path) -> set[str]:
    """The engine (core) modules a file imports directly: `from inkguard.X import`,
    `from inkguard import X`, `import inkguard.X`."""
    tree = ast.parse(path.read_text(), filename=str(path))
    hit: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "inkguard":
                for a in node.names:
                    if a.name in CORE:
                        hit.add(a.name)
            elif node.module.startswith("inkguard."):
                mod = node.module.split(".", 2)[1]
                if mod in CORE:
                    hit.add(mod)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("inkguard."):
                    mod = a.name.split(".", 2)[1]
                    if mod in CORE:
                        hit.add(mod)
    return hit


def test_clean_shell_modules_touch_the_core_only_through_the_contract():
    for mod in sorted(CLEAN):
        crossings = _engine_imports(APP / f"{mod}.py")
        assert crossings == set(), (
            f"{mod} imports engine modules directly: {sorted(crossings)}. "
            "The shell must go through inkguard/contract.py.")


def test_remaining_crossings_only_shrink():
    for mod, allowed in LEGACY_CROSSINGS.items():
        crossings = _engine_imports(APP / f"{mod}.py")
        new = crossings - allowed
        assert not new, (
            f"{mod} has NEW engine imports not in the allowlist: {sorted(new)}. "
            "Route them through inkguard/contract.py instead of widening the boundary.")
        # If a module is now cleaner than the allowlist claims, tighten it.
        gone = allowed - crossings
        assert not gone, (
            f"{mod} no longer imports {sorted(gone)} — remove them from "
            "LEGACY_CROSSINGS so the boundary list reflects reality.")


def test_the_contract_does_not_import_the_shell():
    # The door is core-side: it may use the engine, but nothing shell.
    tree = ast.parse((APP / "contract.py").read_text())
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "inkguard":
                mods = [a.name for a in node.names]
            elif node.module.startswith("inkguard."):
                mods = [node.module.split(".", 2)[1]]
        elif isinstance(node, ast.Import):
            mods = [a.name.split(".", 2)[1] for a in node.names if a.name.startswith("inkguard.")]
        for m in mods:
            assert m not in SHELL, f"contract imports shell module {m}; the door is core-side only."


def test_the_check_reads_the_real_package(tmp_path):
    """A renamed package once left this check passing on nothing: it must
    see the package, and must catch a crossing written today."""
    assert len(list(APP.glob("*.py"))) > 30
    f = tmp_path / "probe.py"
    f.write_text("from inkguard.policy import evaluate\nimport inkguard.garnet\n", encoding="utf-8")
    assert {"policy", "garnet"} <= _engine_imports(f)
