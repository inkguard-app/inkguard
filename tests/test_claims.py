"""Honest claims: nothing the app or its documents say overstates what it does.

An overstated claim misleads the people who rely on the tool. This test reads every
string the app can show a doctor, a reviewer or an IT person — report text,
support messages, screen text, the review pages — and fails the build if a
claim we must never make appears there. Honest negatives ("not a guarantee",
"cannot be guaranteed") are allowed; layout values ("width:100%") are not
claims. Code comments and docstrings are not scanned here.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Claims that must never reach a user-facing surface.
FORBIDDEN = {
    "HIPAA compliant": r"hipaa[\s-]*complian",
    "HIPAA certified": r"hipaa[\s-]*certif",
    "guaranteed": r"\bguarantee",
    "100% <anything>": r"\b100\s*%\s*[a-z]",
    "never misses": r"never\s+miss",
    "anonymous / anonymised": r"\banonymi[sz]|\banonymous\b",
    "certified safe/compliant": r"\bcertified\s+(?:safe|compliant|de-?identified)",
    # Wordings that overstate the tool, its setting or the people who use it:
    "certified reviewer": r"\bcertified[\s-]+reviewer",
    "air-gapped": r"\bair[\s-]*gapped",
    "chain of custody": r"chain\s+of\s+custody",
    "zero leaks": r"\bzero\s+leak",
    "no network code at all": r"no\s+network\s+code\s+at\s+all",
    "removes the 18 identifiers": r"\bremoves?\s+(?:all\s+)?(?:the\s+)?(?:18|eighteen)\b",
    "never seen during development": r"never\s+seen\s+during\s+development",
    "output is not PHI": r"output\s+is\s+not\s+phi",
    # Wordings that compare with other tools or promise proof:
    "and proves it": r"\band\s+proves?\s+it\b",
    "comparison with other tools": r"other\s+tools\s+remove",
}

# Honest wordings that contain a forbidden word but say the opposite. They are
# removed before the search, so only an actual claim is caught.
APPROVED_NEGATIVES = [
    r"(?:not|no|never|cannot\s+be|can't\s+be|isn't|is\s+not)\s+(?:a\s+|be\s+)?guarantee\w*",
    r"not\s+(?:a\s+)?certifi\w*\s+of\s+hipaa\s+complian\w*",
    r"does\s+not\s+certify",
    r"not\s+anonymous",                                             # the GDPR line: an honest negative
    r"not\s+a\s+hipaa\s+certif\w*",
    r"(?:not|never)\s+(?:claim|say)\w*\s+[\"“']?hipaa[\s-]*complian\w*",
    r"(?:tagline\s+was|it\s+was)\s+[\"“']and\s+proves\s+it[\"”']",   # the dated history
]

# Data tables (names, vocabulary) are not user-facing text.
SKIP_PY = {"name_registry.py"}
SKIP_PREFIX = ("lexicon",)


def _docstring_ids(tree) -> set[int]:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(getattr(n.body[0], "value", None), ast.Constant):
            ids.add(id(n.body[0].value))
    return ids


def _surfaces():
    """(where, text) for every user-facing string in the app."""
    for p in sorted((ROOT / "inkguard").glob("*.py")):
        if p.name in SKIP_PY or p.name.startswith(SKIP_PREFIX):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        docs = _docstring_ids(tree)
        for n in ast.walk(tree):
            # A claim is a phrase; a single bare word in code ("anonymous" as an
            # author name to match, a principal label) is a data value, not text
            # anyone reads.
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs \
                    and re.search(r"\s", n.value.strip()):
                yield f"{p.relative_to(ROOT)}:{n.lineno}", n.value
    pages = list((ROOT / "inkguard").glob("*.html"))
    # The desktop app's screens. (An earlier version pointed only at ROOT/"ui",
    # which does not exist, so no screen text was checked.)
    for ui in (ROOT / "desktop" / "ui", ROOT / "ui"):
        if ui.exists():
            pages += [p for p in ui.rglob("*")
                      if p.suffix in {".html", ".js", ".ts", ".tsx", ".json", ".md"}
                      and "node_modules" not in p.parts and "vendor" not in p.parts]
    pages.append(ROOT / "SUPPORT_CODES.md")           # shipped to the IT person
    # The public documents make claims too, and are held to the same rule.
    pages += [ROOT / n for n in ("README.md", "THREAT_MODEL.md", "SECURITY.md", "CITATION.cff",
                                 "CHANGELOG.md", "CONTRIBUTING.md", "NOTICE",
                                 "THIRD_PARTY_NOTICES.md", "examples/README.md", "USE_NOTICE.md",
                                 "TRADEMARKS.md")]
    pages += sorted((ROOT / "docs").rglob("*.md"))     # guides included
    pages += sorted((ROOT / "benchmark").glob("*.md"))
    for p in pages:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            yield f"{p.relative_to(ROOT)}:{i}", line
    # What `python -m inkguard.cli --help` prints is read by every IT person.
    from inkguard import cli
    for i, line in enumerate(cli.build_parser().format_help().splitlines(), 1):
        yield f"cli --help:{i}", line
    # The welcome page and the Methods paragraph researchers will paste.
    from inkguard import fluorite
    for i, line in enumerate(fluorite.about().splitlines(), 1):
        yield f"cli --about:{i}", line
    for profile, shifted in (("safe_harbor", False), ("safe_harbor", True), ("limited_data_set", False)):
        yield f"methods:{profile}:{shifted}", fluorite.methods_text(
            version="0.0.0", profile=profile, date_shifted=shifted, fingerprint="0" * 64)


def find_claims():
    found = []
    for where, text in _surfaces():
        cleaned = text
        for neg in APPROVED_NEGATIVES:
            cleaned = re.sub(neg, " ", cleaned, flags=re.I)
        for label, pat in FORBIDDEN.items():
            if re.search(pat, cleaned, re.I):
                found.append(f"{where}: {label!r} in {text.strip()[:90]!r}")
    return found


def test_no_forbidden_claim_reaches_a_user():
    found = find_claims()
    assert not found, ("A claim we never make reached a user-facing surface "
                       ":\n  " + "\n  ".join(found))


def test_the_guard_catches_claims_and_lets_honest_negatives_through():
    def flagged(s):
        c = s
        for neg in APPROVED_NEGATIVES:
            c = re.sub(neg, " ", c, flags=re.I)
        return any(re.search(p, c, re.I) for p in FORBIDDEN.values())

    for claim in ("Fully HIPAA-compliant de-identification", "Guaranteed removal",
                  "100% accurate", "It never misses a name", "Your data is anonymised",
                  "HIPAA certified output"):
        assert flagged(claim), claim
    for honest in ("Measured accuracy is not a guarantee.", "cannot be guaranteed complete",
                   "This report is not a certification of HIPAA compliance.",
                   "width:100%;height:100%", "calc(100% - 32px)", "We never claim HIPAA compliance."):
        assert not flagged(honest), honest


#: Words that would say, in public, that real patient records were used to
#: build or test the tool. Every test note in this repository is invented,
#: so these may appear nowhere, comments included.
PROVENANCE_WORDS = re.compile(
    r"real[\s-]+notes?\s+review|real[\s-]+note\s+round|partner\s+review|partner\s+practice"
    r"|from\s+real\s+(?:notes|records|patients)", re.IGNORECASE)


def test_nothing_in_the_repository_says_real_records_were_used():
    hits = []
    this = Path(__file__).resolve()
    for p in ROOT.rglob("*"):
        if (not p.is_file() or ".git" in p.parts or "node_modules" in p.parts
                or "__pycache__" in p.parts or p.resolve() == this
                or p.suffix not in {".py", ".md", ".txt", ".js", ".html", ".rs", ".toml",
                                    ".yml", ".yaml", ".cff", ".json", ".sh"}):
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if PROVENANCE_WORDS.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:90]}")
    assert not hits, "\n".join(hits)
