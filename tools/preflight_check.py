#!/usr/bin/env python3
"""
PRE-UPLOAD CHECK — run before anything leaves this computer.

  python tools/preflight_check.py            check every file in the project
  python tools/preflight_check.py --staged   check only files staged for a
                                             git commit (used by the hook)

Refuses (exit code 1) when it finds:

  1. SECRETS        a root key, private key, API key or .env file
  2. RUN ARTIFACTS  operation logs, review pages, decision files, feedback
                    reports, audit logs, databases, notes held for review
                    (needs_review/) — local files that can contain
                    patient data or its shape
  3. PATIENT DATA   a data file (txt, csv, json, jsonl, html, md outside
                    the code folders) in which the engine itself finds
                    SSNs, emails, phone numbers or record numbers — or
                    several names and dates together

A file a human has checked (for example a document whose example names
are fictional) can be listed, one path per line, in .preflight-allow.
Only the PATIENT DATA check honours that list; secrets and artifacts are
never allowed.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Build output and caches: nothing here is written by hand, and a Rust
# build folder alone holds hundreds of thousands of files.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules",
             "target", "dist", "build", "gen", ".cargo"}
CODE_DIRS = {"inkguard", "tests", "tools"}          # code and generated word lists
DATA_SUFFIXES = {".txt", ".csv", ".json", ".jsonl", ".html", ".htm", ".md", ".tsv", ".xml"}

SECRET_PATTERNS = [
    ("root key assignment", re.compile(r"PHI_ROOT_KEY\s*[:=]\s*['\"]?[0-9a-fA-F]{32,}")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("API key assignment", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"][A-Za-z0-9_\-]{24,}['\"]")),
    ("Anthropic/OpenAI key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{24,}")),
]
# A real environment or key file. Templates (".env.example") are allowed as
# files; their CONTENTS are still checked for secrets below.
SECRET_FILES = re.compile(r"(?:^|/)(?:\.env(?:\.(?!example$|sample$|template$)[^/]*)?|.*\.pem|.*\.key)$")
ARTIFACT_FILES = re.compile(
    r"(?:^|/)(?:.*\.oplog|.*oplog.*\.jsonl|audit_log.*\.jsonl|.*\.db|review.*\.html|"
    r".*-review.*\.html|decisions.*\.json|feedback_report.*|manifest\.json|"
    r"needs_review/.*|.*\.check\.json|.*\.needs_review\.jsonl|"
    # Three-person release (inkguard/chert.py): the pages hold notes; the batch,
    # the map and the local report point to patients and name reviewers; the
    # other reports are run outputs of a clinic, not source.
    r"batch\.json|resolve.*\.html|resolution.*\.json|release_map.*\.csv|batch_report.*|"
    r"doctor.*\.html|doctor-.*\.json|integrity_alert.*\.json|accuracy_report.*|"
    r"learning_report.*|evidence_summary.*|released\.json|send_to_maintainers.*|intake_guard_log.*|"
    r"support_log.*|"
    # The batch tool: files set aside (not de-identified) and the local
    # manifest of every input file, and the not-for-export folder that holds
    # them beside the output.
    r"unprocessed/.*|file_manifest.*|\.work-.*|[^/]*-not-for-export/.*|"
    # The results folder (three tagged folders, the audit trail, the key).
    r"[0-9]_REDACTED-[A-Z-]+/.*|[0-9]_NOT-PROCESSED-[A-Z-]+/.*|_ORIGINAL-NAMES-KEY.*|_WORDS-TO-CHECK.*|"
    r"[0-9]_Audit_trail.*|[0-9]_Feedback_report.*|\.inkguard/.*)$")

def patient_like(text: str) -> str | None:
    from inkguard import obsidian
    structured = obsidian.residual_structured(text, check_dates=True)
    strong = {k: v for k, v in structured.items() if k in ("SSN", "EMAIL", "PHONE", "MRN")}
    if strong:
        return "identifiers found: " + ", ".join(f"{v} {k}" for k, v in strong.items())
    from inkguard.phi_scanner import scan
    hits = scan(text[:200_000])
    persons = sum(h.entity_type == "PERSON" for h in hits)
    dates = sum(h.entity_type.startswith("DATE") for h in hits)
    if persons >= 3 and dates >= 1:
        return f"{persons} names and {dates} dates found"
    return None


def candidate_files(staged: bool) -> list[Path]:
    if staged:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        return [ROOT / p for p in out.split() if p]
    files = []
    for p in ROOT.rglob("*"):
        if p.is_file() and not (set(p.relative_to(ROOT).parts) & SKIP_DIRS):
            files.append(p)
    return files


def main() -> int:
    staged = "--staged" in sys.argv
    allow = set()
    af = ROOT / ".preflight-allow"
    if af.exists():
        allow = {l.strip() for l in af.read_text().splitlines() if l.strip() and not l.startswith("#")}

    problems: list[tuple[str, str]] = []
    for p in candidate_files(staged):
        rel = str(p.relative_to(ROOT))
        if p.name == "preflight_check.py":
            continue
        if SECRET_FILES.search(rel):
            problems.append((rel, "secret file"))
            continue
        # The published benchmark's index is data we release on purpose.
        if ARTIFACT_FILES.search(rel) and rel != "benchmark/manifest.json":
            problems.append((rel, "run artifact"))
            continue
        if p.suffix == ".zip" or p.stat().st_size > 20_000_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeError):
            continue
        for label, rx in SECRET_PATTERNS:
            if rx.search(text):
                problems.append((rel, f"secret: {label}"))
                break
        top = p.relative_to(ROOT).parts[0]
        # A line ending in "/" allows a whole folder a person has checked (the
        # synthetic benchmark is patient-shaped by design).
        allowed = rel in allow or any(a.endswith("/") and rel.startswith(a) for a in allow)
        if (p.suffix.lower() in DATA_SUFFIXES and top not in CODE_DIRS and not allowed):
            why = patient_like(text)
            if why:
                problems.append((rel, f"possible patient data ({why})"))

    if not problems:
        print("preflight: clean — no secrets, run artifacts or patient data.")
        return 0
    print("preflight: REFUSED. Nothing should leave this computer until these are resolved:\n")
    for rel, why in problems:
        print(f"  {rel}: {why}")
    print("\nFictional examples in a document you have checked can be allowed by adding the "
          "path to .preflight-allow (patient-data check only).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
