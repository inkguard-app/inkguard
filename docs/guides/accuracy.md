# Accuracy

## At a glance

All figures are for the software alone, before human review, on held-out
halves never used for tuning.

| Test set | n | Identifiers removed | Clinical phrases kept whole |
|---|---|---|---|
| Clinical-preservation set (neurosurgical and surrounding-care notes) | 20 patients, 58 notes | 97.9% (95% CI 96.2–99.4%) | 98.1% (95% CI 97.1–99.1%) |
| Messy-notes benchmark (deliberately difficult synthetic notes) | 72 patients, 289 notes | 90.5% of Safe Harbor identifiers (95% CI 89.1–91.7%) | not marked |
| ASQ-PHI (published external benchmark) | odd (held-out) half | 97.1% | not marked |

On the messy-notes held-out half, 34 of the 258 processed notes reached `1_REDACTED-NO-FLAGS`, and 10 of those still held at least one identifier; every file still needs a person to read it. Notes held for a person: 31 of 289 (messy notes), 19 of 58 (preservation
set). Misses by identifier type: [benchmark/RESULTS.md](../../benchmark/RESULTS.md).

## The standard

- The most widely used benchmark for clinical-text de-identification is the
  2014 i2b2/UTHealth shared task (now distributed by n2c2 under a data use
  agreement). Results are usually reported as recall, precision and F1, per
  identifier type, at entity or token level.
- Recall (identifiers removed) is the measure that matters most for privacy;
  precision (how much ordinary text is removed by mistake) affects how
  useful the data remain.

## Inkguard's position

- **Recall** is reported per identifier type, with 95% intervals that
  resample whole patients (2,000 resamples), on held-out halves.
- **Clinical preservation** is reported directly: the clinical-preservation
  set marks every clinical phrase that must survive (instruments, devices,
  eponyms, scores, approaches, doses, imaging findings) as well as every
  identifier.
- **Scoring is strict**: an identifier counts as removed only if none of it
  remains readable; a clinical phrase counts as kept only if all of it
  survives. A flagged note is scored on its draft. On the messy-notes set,
  the 31 notes the tool stopped (copied unredacted to folder 3) are not
  counted; the 90.5% is measured on the other 258.
- **Disclosed correction**: on 18 October 2026 the scorer was changed to
  ignore letters inside the tool's own placeholders. Under the previous
  scorer, the 18 October build scored 89.7% on the messy-notes held-out half.

## What it needs from you

- Human review of every output; the figures above are before review.
- If you need a figure on your own records, a local evaluation on a sample
  your organisation approves.

## What it does not do

- No result on the i2b2/n2c2 2014 corpus is published yet.
- Precision (over-removal of ordinary text) is not yet published as a
  single figure; clinical-phrase preservation is published instead.
- All test notes are synthetic; the clinical-preservation held-out half is
  small (20 patients); the notes and the engine were written with the help
  of the same AI assistant and may share blind spots.
- Not yet validated on real clinical records.
