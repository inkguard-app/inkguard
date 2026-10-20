# Changelog

## 0.25.0 — 2026-10-19 — first public release: public preview

The first open-source release (earlier versions were not published). **Public preview:** tested against published and synthetic
benchmarks, not yet validated on real clinical records. Version 1.0.0 is
reserved for after real-world validation, under ethics approval, and the
desktop installers.

**Included in the code but not part of this release** (not validated, not
ready for use): a three-person review workflow (the `inkguard-review`
command) and the desktop app. Entries below that mention reviewers or a
clinician describe that workflow.

Highlights:

- **Redaction engine** for typed clinical documents (Word, typed PDF,
  Excel, CSV, text), including hidden content and file names.
- **Each patient's notes are read together at least three times**, until a
  read finds nothing new, so a name confirmed in one note is also removed
  from the others.
- **No network code**, enforced by a test. Works with the Wi-Fi off.
- **The messy-notes benchmark**: 577 synthetic notes for 144 invented
  patients, every identifier marked, with a scorer.
- Measured on the benchmark's held-out half: 90.2% of the identifiers Safe
  Harbor requires are removed by the tool alone, before human review
  (89.7% under the scorer used before the correction noted below). On the
  preservation set's held-out half: 97.9% of identifiers removed and 98.1%
  of clinical phrases kept whole.

Fixed while checking the package before release:

- A middle initial "T" in front of a Social Security number ("WYATT T
  900-14-7708") was read as a temperature, and the number was kept.
- A local seven-digit phone number standing on its own ("Called HR
  555-0147") was released; it now holds the note for review.
- A date run into "...no" ("12/18/2074...no") was read as a web address and
  stopped the whole file.
- The `tldextract` library, used by Presidio's email check, downloaded the
  public list of web-address suffixes from the internet on first use. It now
  uses the copy bundled with the library, and a test runs the engine with the
  network switched off.
- Resuming a run cleaned Word, Excel and PDF files a second time under a new
  name and reported the first copy as a leftover. They now keep their output.

Added before release, so the person using the tool always knows where things
stand:

- Every results folder opens with `0_READ_ME_FIRST_(Guide).txt`,
  a plain-language guide: what happened, what each file is, what to do next,
  what not to do, and an optional acknowledgement that approves nothing. The
  report and the screen use the same status words (incomplete review,
  pending review, not processed) and say that nothing is ready to share.
- `--dry-run` lists what would be read, set aside or ignored, and where the
  output would go, without writing anything or opening any file.
- The audit trail records a SHA-256 fingerprint of every file written,
  listed by id; a folder name is shown only when it holds no identifier.
- After every run, each original file's fingerprint is checked again, and the
  guide, report and screen say whether the originals are unchanged.
- Every text and Word file written starts with one provenance line naming the
  version, the method, its review status (incomplete review or pending review,
  not ready to share) and the run id that ties it to the manifest and report.
  Spreadsheets, CSV files and flattened PDF pages are not stamped. Turn it off
  with `--no-header`. The tool skips its own line when it reads a file back.
- The guide includes how to cite the version that produced the files.

Also added before publication (each recorded in
[docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md)):

- **A standing notice** (reports, guide, `--about`, the working screen):
  the tool is automated, can make mistakes, including misreading context, and
  a person must check every output.
- **Neurosurgical and surrounding-care vocabulary** (neuroanaesthesia, pain
  and rehabilitation, spine, ENT and skull base, plastics, dentistry and oral
  surgery, critical care, radiology, care settings, referrals and insurance)
  so instruments, devices, eponyms, scores and approaches are kept. Capitalised
  eponyms count as clinical only beside clinical words, so a patient named
  like an instrument is still removed.
- **The clinical-preservation set** (`benchmark/neuro/`, CC BY 4.0): notes in
  which both the identifiers and the clinical phrases that must survive are
  marked, with a scorer (`tools/eval_preservation.py`).
- **Newsworthy events** (mass-casualty incidents, disasters) are flagged as a
  way to identify patients. The details that pin an event down are suggested
  for removal.
- **Small populations** (small states, rural areas) count as narrowing in the
  identification screening, alongside a rare condition or an exact age and sex.
- **Spelling slips** that look like a known word are listed for review.
- **Wipe and start again** (desktop app, in development): a batch can be
  wiped with a reason, and a restart report is kept.
- **`--about` and `--self-test`**, a code fingerprint in every report, and a
  draft Methods paragraph.
- Names written with initials ("Dr. J. Valdéz", "BRANNIGAN, T."), ages written
  as "94 y" or "(96 M)", and named places of care ("Harrowmere Level I Trauma
  Center") are now removed; kinds of place ("walk-in clinic") are kept.
- A patient whose surname is
  also an eponym ("Pt: Lars Babinski", or in lower case) is removed; names
  in capitals in record headers ("SMITH, JOHN  MRN") are removed; a
  five-digit record number is no longer cut short as a ZIP code; phone
  numbers written "555 0147", "Called her at …" or with a leading 0
  ("07700 900123"), the last four digits of an SSN and labelled encounter
  numbers are removed or held; named scales without a grading word
  ("Lenke 1A", "Knosp 4", "House-Brackmann IV") are kept.
- When a name removed from a note is also kept there as a clinical word
  ("Weber syndrome" in a note about Ms Weber), it is listed for review. It
  does not hold the note.
- **Scorer correction:** letters inside the tool's own placeholders are no
  longer matched against the answer key. Disclosed in
  [benchmark/RESULTS.md](benchmark/RESULTS.md).

Packaging, before publication:

- The package is named `inkguard`; `pip install .` installs it with an
  `inkguard` command.
  `requirements.txt` keeps the exact pins.
- The generated word lists are compressed data files in `inkguard/data/`
  instead of Python source (9.7 MB of code became 2.4 MB of data; the values
  are identical).
- The command-line report and review list moved to their own modules.
- `docs/MODULES.md` lists every module; a test keeps it complete.
- **One results folder, three tagged folders.** Every file received comes
  out in exactly one: `1_REDACTED-NO-FLAGS`, `2_REDACTED-FLAGGED` (with a
  `_WORDS-TO-CHECK.md` per patient giving each word's line) or
  `3_NOT-PROCESSED-MAY-CONTAIN-PHI` (copies of the originals and the name
  key). Every file starts with its folder's tag, so a search finds them all,
  and the counts are checked to add up.
- **Four documents beside them:** `0_READ_ME_FIRST_(Guide).txt` (the guide, also
  shipped as `GUIDE.txt`), `4_Audit_trail.pdf` (a machine-written log of facts:
  who entered what before the run, when it ran, the exact code, files per
  folder, and an appendix of every file by id with its folder and
  fingerprint), `5_Citations.txt`, and
  `6_PLEASE-RETURN-to-inkguard.project@gmail.com_Feedback-report.docx` (codes and counts, and one open box; to
  email to the project, if the user wants).
- **Who is running it (optional):** the operator, role, principal
  investigator, institution and protocol can be entered before a run. Every
  field is optional; whatever is entered is recorded as typed in the audit
  trail, and the software cannot confirm who typed it.
- **A small window** (`inkguard-window`), four screens: welcome (the licence
  line), set up (folders, an optional local audit trail, two options),
  working (progress and a notice), done (files received, the results folder,
  the review status). No terms to accept: the Apache License 2.0 carries the
  warranty and liability terms.
- **Works on copies, and starts over after an interruption.** Each run copies
  the input to a hidden working folder, checks every copy against its original
  by SHA-256, and redacts the copies. If a run is interrupted, the next run
  deletes only what Inkguard wrote and starts again from the originals.
- **`--scramble-names`** gives every patient folder and file a random code;
  files stay grouped by patient and the key in the third folder links back.
- **A feedback report in every results folder** (`6_PLEASE-RETURN-to-inkguard.project@gmail.com_Feedback-report.docx`):
  counts, codes and the reasons files were flagged, written by the tool; one
  open box for anything the user wants to say; and
  `inkguard --check-feedback` to scan anything a person adds before they
  email it.
- **Measured on test sets from other places**: ACI-Bench visit notes,
  Microsoft presidio-research synthetic data, Taiwan synthetic notes, and a
  new generated set (`tools/make_mixed_set.py`, `tools/eval_external.py`,
  `benchmark/EXTERNAL.md`). What they showed was fixed: names and record
  numbers in unlabelled capitals headers, all USPS street suffixes, web
  addresses ending in a number, "X is a pleasant 45-year-old", and a phone
  number after "update … at".
- Five guides in `docs/guides/`: privacy, Safe Harbor, human review,
  accuracy, reproducibility.

Not adopted: a large open vocabulary built from MeSH, HL7 and other open
sources lowered identifier recall on the tuning half and was left out.
