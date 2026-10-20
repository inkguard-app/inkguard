<p align="center"><img src="docs/brand/inkguard-logo.svg" alt="Inkguard logo" width="72"></p>

<h1 align="center">Inkguard</h1>

<p align="center">
Research software that helps redact patient identifiers from typed clinical documents before research use.<br>
For research use only. Free and open source. Works offline after installation.
</p>

<p align="center">
<a href="https://doi.org/10.5281/zenodo.23464777"><img alt="DOI 10.5281/zenodo.23464777" src="https://img.shields.io/badge/DOI-10.5281%2Fzenodo.23464777-1f2328"></a>
<a href="CHANGELOG.md"><img alt="version 0.25.0" src="https://img.shields.io/badge/version-0.25.0-0969da"></a>
<a href="LICENSE"><img alt="Apache License 2.0" src="https://img.shields.io/badge/licence-Apache%202.0-0969da"></a>
<a href="tests/test_no_network.py"><img alt="no network calls in its own code" src="https://img.shields.io/badge/network%20calls%20in%20its%20own%20code-none-1a7f37"></a>
</p>

> [!CAUTION]
> **FOR RESEARCH USE ONLY. PUBLIC PREVIEW, NOT VALIDATED ON REAL CLINICAL RECORDS. IT MISSES SOME IDENTIFIERS,
> INCLUDING IN FILES IT DOES NOT FLAG. A QUALIFIED PERSON MUST REVIEW EVERY OUTPUT BEFORE IT IS USED OR SHARED.
> PROVIDED AS IS, WITHOUT WARRANTY (APACHE LICENSE 2.0, SECTIONS 7 AND 8).** Read [USE_NOTICE.md](USE_NOTICE.md)
> before using it with patient records.
>
> **Inkguard is an automated tool. It can make mistakes, including misreading context. A person must check every output before it is used.**

## About

Inkguard is research software. Its only purpose is to assist and speed up one
step of clinical research: redacting patient identifiers from typed documents
(Word, text-based PDF, Excel, CSV and plain text) before they are reviewed and
used in a study that has the approvals it needs. It is a first pass that a
person checks. It is not a product, a service or a clinical tool, and it is not
for patient care, billing, legal disclosure or any decision about a patient.

It redacts copies on your computer, reads each patient's notes together so that
a name confirmed in one note is also redacted in the others, and sorts every
file it receives into one of three folders:

| Folder | Contents |
|---|---|
| `1_REDACTED-NO-FLAGS` | Redacted; no words flagged. Pending human review. |
| `2_REDACTED-FLAGGED` | Redacted; uncertain words listed with their line numbers. Pending human review. |
| `3_NOT-PROCESSED-MAY-CONTAIN-PHI` | Exact copies of files it could not read (photos, scans, other formats), files it stopped on as a precaution, and system files. |

It looks for the identifiers listed by the HIPAA Safe Harbor method that can
appear in typed text (16 of the 18 kinds), including in document properties,
comments, tracked changes, headers, hidden sheets and file names. It finds
most, not all (see [Accuracy](#accuracy)). A file with no flags can still
contain identifiers, so every file needs human review.

**Status: version 0.25.0, public preview.** An early version with known gaps;
work on it continues. Not yet validated on real clinical records. Known misses
include surnames that are also ordinary words and towns that are not in its
place list ([benchmark/RESULTS.md](benchmark/RESULTS.md)). Changes are announced
only when released, in the [changelog](CHANGELOG.md); no release dates are
promised. Not legal advice, not a certification, not an Expert Determination
and not a medical device. An independent, non-commercial project, not
affiliated with any university, medical school or hospital.

## Installation

Requires Python 3.11–3.14 (tested on 3.11 and 3.13) on a 64-bit computer with
4 GB of memory and about 1 GB of disk. Installation downloads about 600 MB of
libraries once; after that Inkguard runs offline. Checking PDF pages needs
[Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (`brew install tesseract`,
`apt install tesseract-ocr`, or the Windows installer); without it, PDFs are
not processed and are copied to folder 3.

```bash
git clone https://github.com/inkguard-app/inkguard.git
cd inkguard
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
python -m pip install --no-deps .
inkguard --self-test               # one-minute check, network blocked
```

`requirements.txt` pins the direct dependencies to exact versions. On Linux
the window needs the `python3-tk` package; the Python installers from
python.org include it. Release downloads come with `SHA256SUMS.txt`: compare it
with `shasum -a 256 <file>` (macOS), `sha256sum <file>` (Linux) or
`certutil -hashfile <file> SHA256` (Windows).

## Usage

**Window:** run `inkguard-window`. Four screens: welcome, set up (folders, an
optional local audit trail, options), working, done.

**Command line:**

```bash
inkguard NOTES_FOLDER -o RESULTS_FOLDER --dry-run    # preview; writes nothing
inkguard NOTES_FOLDER -o RESULTS_FOLDER
```

Try it on the invented notes in `examples/sample-notes`. Useful options:

| Option | Effect |
|---|---|
| `--scramble-names` | Give every redacted patient folder and file a random name (folder 3 has the original names) |
| `--profile lds` | Leave dates and towns unredacted (Limited Data Set; the output is still PHI) |
| `--operator`, `--pi`, `--institution`, `--protocol` | Record who ran it and for whom in the audit trail (optional) |
| `--check-feedback FILE` | Check a feedback report for patient information before sending |

`inkguard --help` lists every option. If a run is interrupted, running it
again into the same folder deletes what it wrote and starts over from the
originals, which are only ever read.

## Output

```
RESULTS_FOLDER/
├── 0_READ_ME_FIRST_(Guide).txt
├── 1_REDACTED-NO-FLAGS/
├── 2_REDACTED-FLAGGED/                  _WORDS-TO-CHECK.md in each patient folder
├── 3_NOT-PROCESSED-MAY-CONTAIN-PHI/     _ORIGINAL-NAMES-KEY.csv: neutral names → originals
├── 4_Audit_trail.pdf                    record of the run; appendix lists every file by id
├── 5_Citations.txt                      APA, AMA, Vancouver, BibTeX, Methods template
└── 6_PLEASE-RETURN-to-inkguard.project@gmail.com_Feedback-report.docx
```

Every file name starts with its folder's tag, so a search finds them all, and
the counts are checked to add up. Each redacted text and Word file begins with
a one-line watermark: software, version, review status and run. The guide is
the same as [GUIDE.txt](GUIDE.txt).

A hidden `.inkguard` folder holds the run's machine record. During a run it
also holds full working copies of your originals, deleted when the run ends.
**If a run is interrupted, those copies stay** until you run again into the same
folder or delete the whole results folder. Treat it like folder 3.

## Accuracy

Measured on the software alone, before human review:

| Test set | Result |
|---|---|
| Deliberately difficult synthetic notes, held-out half (72 patients, 289 notes, set aside and not used for tuning) | 90.5% of Safe Harbor identifiers redacted (95% CI 89.1–91.7%), measured on the 258 notes it released; 31 further notes (13.9% of identifiers) were held back unredacted for a person. Of 34 notes sorted into the no-flags folder, 10 still held an identifier |
| Clinical-preservation set, held-out half (20 patients, 58 notes) | 97.9% of identifiers redacted (95% CI 96.2–99.4%); 98.1% of marked clinical phrases left unredacted (95% CI 97.1–99.1%) |
| [ASQ-PHI](https://doi.org/10.1016/j.dib.2026.112586), a published benchmark, held-out half | 97.1% of identifiers redacted |

The test notes are AI-generated synthetic data. These figures describe these
test sets only and are not a prediction of performance on real records. The
benchmarks and the engine were written with the same AI assistant and may
share blind spots. Methods, data and the full results, including what the
tool still misses: [benchmark/RESULTS.md](benchmark/RESULTS.md).

## Privacy

- After installation, Inkguard's own code opens no network connections, and its tests run the
  engine with connections refused ([tests/test_no_network.py](tests/test_no_network.py)).
- Originals are only read. Each run works on checked copies and re-checks every
  original's SHA-256 fingerprint afterwards.
- The audit trail holds counts, codes, file ids, the computer name and the
  run details you enter. The citations and feedback report leave out the
  computer name and those details. All three are designed to hold no text
  from your files.
- Keep inputs and the results folder on an encrypted disk, outside
  cloud-synced folders (OneDrive, iCloud Drive, Dropbox, Google Drive). Never
  share `3_NOT-PROCESSED-MAY-CONTAIN-PHI` or the hidden `.inkguard` folder.
  See [THREAT_MODEL.md](THREAT_MODEL.md).
- Deleting a file does not wipe it: disks, backups, swap files and search
  indexes can retain copies. Full-disk encryption covers these. Reading PDFs
  uses the system temporary folder briefly; after a crash, delete any
  `tess_*` files there.

## Limitations

- Typed text only; photos and scans are copied unread.
- Built and measured for English-language notes.
- Not an Expert Determination. Safe Harbor also requires that no one has actual
  knowledge the remaining information could identify a person; the software
  cannot judge that.
- Not yet validated on real clinical records.

## Citation

> Wajahat, A. (2026). *Inkguard* (Version 0.25.0) [Computer software]. Zenodo. https://doi.org/10.5281/zenodo.23464777

See also **Cite this repository** on GitHub ([CITATION.cff](CITATION.cff)) and
`5_Citations.txt` in every results folder.

## Feedback and support

Each run writes a feedback report (Word): your optional comments, then a
record written by the software (counts and codes only). Please email it to
**inkguard.project@gmail.com**. Never send patient information. Security
issues: [SECURITY.md](SECURITY.md). Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).

## In development

The code also contains two features that are not part of the 0.25.0 release.
They are published so the work is open to inspection, have not been
validated, and are not ready for use with patient records:

- **A three-person review workflow** (two independent reviewers and a signing
  clinician). The `inkguard-review` command is installed with the package.
  See [docs/guides/review.md](docs/guides/review.md).
- **A desktop app** with local accounts and encrypted storage, planned for
  1.0.0. See [desktop/README.md](desktop/README.md).

## Development

```bash
python -m pip install pytest==9.1.1 pytest-timeout==2.4.0
python -m pytest -q --ignore=tests/test_desktop_ui.py      # about 1,000 tests, ~15 minutes
```

Design decisions are recorded in [docs/DESIGN_DECISIONS.md](docs/DESIGN_DECISIONS.md)
and security controls, each with its test, in
[docs/SECURITY_STANDARDS.md](docs/SECURITY_STANDARDS.md).


## Acknowledgements and AI disclosure

Designed and directed by Alize Wajahat. Most of the code, tests, documentation
and synthetic benchmark notes were written with Anthropic's Claude (Claude
Opus 5.5, via Claude Code, 2026) working to the author's direction; the author
reviewed the results and made the design decisions. Changes are tested, and accuracy is measured on held-out data never used for tuning.
There has been no independent human code review yet. This disclosure follows
the [Journal of Open Source Software AI usage policy](https://joss.readthedocs.io/en/latest/submitting.html#ai-usage-policy).

## Licence

[Apache License 2.0](LICENSE). The licence grants no permission to use the
name or logo to identify a product ([TRADEMARKS.md](TRADEMARKS.md)).
