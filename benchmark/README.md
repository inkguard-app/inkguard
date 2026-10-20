# The messy-notes benchmark

577 synthetic clinical notes for 144 invented patients, written over
(fictional) years, with **every identifier marked**. Built to test
de-identification software on the notes it finds hardest.

**No real patient is in this data.** Clinical histories come from
[Synthea](https://github.com/synthetichealth/synthea) (synthetic patients;
its output is free of restrictions). Every name, number, address, date and
institution was invented for this benchmark, and dates are shifted into the
years 2060–2095. Real city, state and country names are used; any other
resemblance to real people or places is coincidental.

## What makes it hard

The notes are AI-generated synthetic data. Each patient's notes lean on
one kind of trouble:

- **Messy lives:** records abandoned or transferred abroad, name changes
  (marriage, divorce, legal), gender transitions with former names in older
  notes, patients whose stated history contradicts the chart.
- **Who is writing:** medical assistants and high-school volunteers adding
  odd descriptors, decorative formatting and essay-like "improvements";
  students; locums; dictation software errors.
- **Names that do not look like names:** Sky, Blue, Story, Justice, x183474,
  Mi4, A1, Twelve, usernames and gamer tags, surnames that are ordinary words
  (Page, Frost, Will, Rich), with the same words also used as ordinary words.
- **Era, region and specialty styles:** all-capital legacy systems, British
  spelling, day-first dates, foreign addresses and postcodes.
- Throughout: typos, template errors, unfilled placeholders, whole old notes
  pasted into new ones, wrong-patient fragments, and 541 catalogued
  "oddities" ([ODDITIES.md](ODDITIES.md)) such as a Wi-Fi network named after
  the family or a dog tag read on a chest film.

How the notes were written is in [WRITING_BRIEF.md](WRITING_BRIEF.md).

## Format

- `records/NNN-RR.xml`: one note each, in the i2b2/n2c2 2014
  de-identification format (`<TEXT>` plus `<TAGS>` with each identifier's
  type and exact character span). NNN is the patient, RR the note in date
  order.
- `manifest.json`: one row per note: patient, **half** (`tuning` or
  `held-out`), setting, writing voice.
- Identifier types are the 2014 scheme's (PATIENT, DOCTOR, DATE, AGE, PHONE,
  MEDICALRECORD, STREET, CITY, ZIP, USERNAME, …).

The halves are split by patient, so no patient appears in both.

## Scoring

```bash
python tools/eval_corpus.py --half held-out
```

The scorer compares what a reader would actually receive with the marked
spans, word by word. It scores separately:

- **all marked identifiers**, and
- **what Safe Harbor requires** (a bare year, an age of 89 or under, a
  weekday or a season may stay; clinicians' and hospitals' names are not
  Safe Harbor identifiers).

Please tune only on the `tuning` half and report the `held-out` half.
Inkguard's own scores, with every miss by type, are in [RESULTS.md](RESULTS.md).

## Licence and citation

The benchmark is released under the
[Creative Commons Attribution 4.0 International licence (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/):
use it for anything, including commercially, with credit. Cite it with the
software (see the repository's CITATION.cff).
