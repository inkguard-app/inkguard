# How it works

> The engine (the core, a patient's notes read together, files, measuring)
> is the one in version 0.25.0, which is the command line and a small window
> and writes ordinary, unencrypted files that you keep on an encrypted disk.
> The sections "A batch, the review and the release" and "The app around it"
> describe the review workflow and desktop app, in development and not part
> of 0.25.0.

A tour of the code for people who want to read it, check it or extend it.
Each part of the engine has a short neutral name (minerals) so it can be
referred to precisely. [MODULES.md](MODULES.md) lists every module.

## Two halves

The code is split into a **core** that finds and redacts identifiers and a **shell**
around it (files, batches, people, screens). The shell reaches the core
through one door, `inkguard/contract.py`, and never imports the core's
internals; `tests/test_boundary.py` enforces this. That keeps the part that
decides what is an identifier small, testable and replaceable.

```
 files ──► calcite (read Word/PDF/Excel/CSV/text, incl. hidden parts)
             │
             ▼
        contract.py  ── the only door into the core
             │
             ▼
   ┌─────────────── core: policy.evaluate() ───────────────┐
   │ basalt → quartz → phi_scanner (with jasper)            │
   │   → clinical guard → garnet → shale → talc             │
   │   → hipaa → obsidian → agate                           │
   └────────────────────────────────────────────────────────┘
             │
             ▼
   pumice: each patient's notes read together, ≥3 times
             │
             ▼
   0.25.0: results folder (1 no flags, 2 flagged, 3 not processed)
   desktop app, in development: granite (batch) → review → halite
```

## The core, in the order it runs

| Stage | Module | What it does |
|---|---|---|
| Intake | `basalt` | Normalises the text and undoes tricks that hide identifiers from a scanner: zero-width characters, look-alike letters from other alphabets. |
| Sections | `quartz` | Reads the note's structure (header, history, medication list, signature) so evidence can be weighed by where a word sits. |
| Candidates | `phi_scanner` | Detection layers: patterns for structured identifiers (phones, dates, record numbers, addresses, plates, logins, web addresses), labelled form fields, sign-offs, and statistical name recognition (spaCy via Presidio). A multilingual model runs only when non-Latin script is present and the optional model is installed. |
| Clinical guard | `clinical_lexicon`, `clinical_context` | Removes candidates that are medicine: eponyms ("Graves disease"), drugs, lab values, vital signs, scores, ages under 90, relative times. Removing clinical content protects nobody. |
| Evidence | `garnet` | Weighs each remaining name candidate by its signals (a title, a role, a relation word, a known name list, where it sits) and decides remove, keep or ask a person. |
| Spreading | `shale` | A name confirmed once is removed everywhere in the note, including in capitals and without accents. |
| Roles and relations | `jasper` | Runs with the detection layers. Finds people by what the text says about them: "attending: …", "daughter Wren", "PIKE, GIDEON H  MRN …", "goes by JB", towns before a state and ZIP. |
| Overlaps and the ledger | `phi_scanner`, `talc` | Resolves overlapping detections and keeps a ledger: every detection must end removed or explained, or the note is flagged for a person. |
| Re-check | `obsidian` | Re-reads the output for any confirmed name or structured identifier that survived. If one did, the file is stopped and copied, unredacted, to `3_NOT-PROCESSED-MAY-CONTAIN-PHI`. |
| Final check | `agate` | Checks that every word left in the note is accounted for (an English or clinical word, a number, a known code or a placeholder). Words it cannot account for are listed and the note is flagged. |

Safe Harbor generalisations are applied after redaction and before the
re-check (`hipaa.py`): dates keep their year, ZIP codes keep three digits
(or 000 for small areas), ages over 89 become "90 or older".

## A patient's notes are read together

`pumice` reads all of one patient's notes first, collecting every name it is
sure of (the patient, family, clinicians), then redacts each note with that
knowledge — and repeats the whole read while it keeps learning, **at least
three times and up to five**. A nickname confirmed in the third note is then
removed from the first. Nothing learned about one patient touches another's
notes.

## Files

`calcite` opens Word, Excel, typed PDF, CSV and text files and passes
everything a reader could see through the core: body text, headers,
footers, comments, tracked changes, document properties, hidden sheets,
cell comments, and file names. What it cannot check is dropped and counted.
`zircon` checks the folder before any of this: scans, images, old formats
and system files are listed and not read (in 0.25.0 they are copied unread
to `3_NOT-PROCESSED-MAY-CONTAIN-PHI`).

## A batch, the review and the release

- `granite` takes stock of a folder without changing it, prepares each
  patient's notes and seals the work in an encrypted pool (its own key, held
  in the operating system's protected store).
- Two reviewers go through every note in a distraction-free reading room
  (`desktop/ui/js/room.js`): copying and printing are blocked, losing focus
  veils the note, breaks keep their place. Every word the engine was unsure
  about is put to them. Neither can see the other's answers.
- `halite` is the clinician's step: points where the reviewers disagreed are
  settled, small-number risks are ruled on, and the clean copies are written,
  **read back off the disk and checked** for every removed word, before the
  pool's key is destroyed. The review report and an accuracy report travel
  with the files.
- The practice chooses whether this takes two people (the signing clinician
  may be one of the reviewers) or three.

## The app around it

`opal` is the secure core every screen talks to: local accounts, roles, the
idle lock, phone codes, the hash-chained security log, the tamper counter,
backups and the one restore door, and retiring a computer. The screens
(`desktop/ui/`, plain HTML and JavaScript) hold no keys and no patient data;
the desktop wrapper (`desktop/src-tauri/`, Tauri) relays one JSON line per
request over a pipe and opens no network port.

Every error carries a support code (`topaz`, listed in
[SUPPORT_CODES.md](../SUPPORT_CODES.md)) so problems can be diagnosed without
seeing anyone's files.

## Measuring it

- `benchmark/` holds 577 synthetic notes for 144 invented patients with
  every identifier marked, split into a tuning half and a held-out half.
  `python tools/eval_corpus.py --half held-out` scores the engine on what a
  reader would actually receive.
- `tools/eval_asq_phi.py` scores the published ASQ-PHI benchmark.
- `tools/independent_names.py` measures names that are in none of the
  engine's own name lists.
