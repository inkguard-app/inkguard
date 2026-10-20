# Results: Inkguard 0.25.0 (public preview)

Measured 18 October 2026 on the 0.25.0 engine (after the pre-release fixes in
the changelog), by the tool alone,
**before any human review**. Human review comes after these figures and is
not measured here.

**Reproducibility.** These figures describe the software alone, so they are
reproducible: the same version (its code fingerprint is in every report) on
the same notes gives the same counts. Human review changes the result, in
both directions: a reviewer may remove more than the tool did or put back a
word the tool removed. Those changes are never folded into these numbers.

## Headline

| Test set | What is counted | Result |
|---|---|---|
| Messy-notes benchmark, **held-out half** (72 invented patients, 289 notes, never used for tuning) | identifiers Safe Harbor requires removing | **90.5%** (4,058 of 4,485; 95% CI 89.1–91.7%) |
| Messy-notes benchmark, tuning half (72 patients, 288 notes) | identifiers Safe Harbor requires removing | 92.9% (4,229 of 4,550; 91.8–94.1%) |
| [ASQ-PHI](https://doi.org/10.1016/j.dib.2026.112586), a published external benchmark | Safe Harbor identifiers | 97.1% on the odd half (never used for tuning), 96.8% on the even half (used for tuning); 8 of 219 harmless queries changed |

A figure for 1,000 surnames in none of the tool's name lists (98.7%) was
shown before 0.25.0. Its test inputs were not published, so no one else
could reproduce it, and it has been withdrawn until they are.

The held-out half is the figure to report: it was kept aside while the engine
was built, so the tool was never tuned to it.

**Scorer correction (18 October 2026).** The scorer now ignores letters
that fall inside the tool's own placeholders when it lines output up with
the answer key; before, a placeholder such as `[NAME REMOVED]` could be
matched against a marked word and count as "kept". Under the old scorer the
18 October build scored 89.7% on the held-out half (88.2–91.1%), so of the
change from the 89.2% measured on an earlier build, about half was the engine
and about half the correction. Intervals
resample whole patients, 2,000 times.

## Clinical detail kept: the preservation set

Removing identifiers is half the job; keeping the clinical detail a study
needs is the other half. The preservation set ([`neuro/`](neuro/README.md))
is invented neurosurgical and surrounding-care notes in which both are
marked: every identifier, and every clinical phrase that must survive
(instruments, devices, eponyms, scores, approaches, doses, imaging
findings). It was written in a separate session without access to the
tool's code or word lists. The held-out half (20 patients, 58 notes) was
not used for tuning; the tuning half (50 patients, 137 notes) was.

| Held-out half, by the software alone | Inkguard 0.25.0 | Presidio 2.2.364, untuned defaults (reference point only; a general library meant to be configured) |
|---|---|---|
| Identifiers removed (Safe Harbor types) | **97.9%** (327 of 334; 95% CI 96.2–99.4%) | 75.1% (251 of 334; 70.8–79.1%) |
| With places of care and organisations | 95.2% (355 of 373) | 74.8% (279 of 373) |
| Clinical phrases kept whole | **98.1%** (935 of 953; 97.1–99.1%) | 68.3% (651 of 953; 64.3–72.3%) |
| Clinical words kept | 99.1% (2,319 of 2,341) | 83.8% (1,962 of 2,341) |
| Notes held for a person | 32.8% (19 of 58) | (no review step) |

On the tuning half: identifiers 96.8% (720 of 744), clinical phrases 98.3%
(2,621 of 2,665), notes held 27.7%; Presidio 75.5% and 74.1%.

The reference is Microsoft Presidio's analyzer (presidio-analyzer 2.2.364,
spaCy 3.8 with en_core_web_lg 3.8.0) with all its default recognizers and
every finding replaced, untuned. It is a general-purpose library that users
are expected to configure; it is shown only as a reference point under the
same scorer, not as a measure of how a configured deployment of it would
score. A flagged note is scored on its draft, which is what the reviewer sees;
a stopped note produces no output, so its identifiers count as removed and its
clinical phrases as lost.
Intervals resample whole patients, 2,000 times.

Limits: 20 held-out patients is small; the notes are synthetic, from one
AI author, in English; and the same family of AI model helped write the
tool. Reproduce with `python tools/eval_preservation.py --half held-out`
(add `--baseline` for the reference).

## Test sets from other places

Measured on 18 October 2026, by the software alone, before human review.
Sources, licences and how to rerun are in [EXTERNAL.md](EXTERNAL.md).

| Set | First run | After fixes |
|---|---|---|
| ACI-Bench visit notes (87 notes; patient names, 84 occurrences) | 97.6% (82 of 84) | 84 of 84 |
| Generated mixed set (400 notes, 30 name origins; first run seed 20261018) | 89.7% (CI 88.4–91.1%); 141 notes held | 97.8% on a new seed, 77031 (CI 97.1–98.5%); 35 held |
| Microsoft presidio-research synthetic sentences (1,500; general text, many non-US formats) | 70.1% (CI 67.9–72.4%) | 70.3% |
| Taiwan synthetic notes, Chinese mixed with English (200) | 83.9%; 194 notes held | unchanged |

In the generated set, clinical phrases kept whole: 99.3% (first run), 99.4%
(new seed); ordinary words removed by mistake: 1.1%. Invented names, in none
of the tool's lists: 88.0% first run; 487 of 487 on the new seed. Faker names: 87.2%,
98.9%. Names in non-Latin script: 83.0%, 74.1%; those notes are mostly held.

**Read the "after fixes" column with care.** The first run found five shapes
the tool missed (unlabelled capitals headers with a record number, uncommon
street types, web addresses ending in a number, "is a pleasant 45-year-old",
a phone number after "update … at"), and they were fixed. The new seed changes
every name, number and date, but the generator's templates are the same, so
the second figure shows the fixes work rather than how the tool does on
writing it has never seen. The first-run figures are the independent ones.
Inkguard's lower score on the presidio-research set is mostly street
addresses and phone numbers in non-US formats; the tool is built for US records.

## Held-out half: what reaches `1_REDACTED-NO-FLAGS`

The folder a user may trust most is the one to measure. On the held-out
half, 34 of the 258 processed notes reached `1_REDACTED-NO-FLAGS`; 10 of
those 34 still held at least one required identifier, 12 identifiers in all
(mostly a name that is also an ordinary word, or a town the place list does
not know). The other 224 notes went to `2_REDACTED-FLAGGED` with the
uncertain words listed. Every file still needs a person to read it.

## Held-out half: what the tool still misses

Only identifiers the Safe Harbor rule requires are counted here. A miss is
an identifier left partly or wholly readable.

| Identifier | Missed | Of | Removed |
|---|---:|---:|---:|
| Dates (day and month) | 99 | 1,558 | 93.6% |
| Patient and family names | 90 | 1,367 | 93.4% |
| Usernames and handles | 52 | 118 | 55.9% |
| Other ID numbers | 51 | 140 | 63.6% |
| Medical record numbers | 31 | 254 | 87.8% |
| Street addresses | 18 | 112 | 83.9% |
| Cities | 18 | 162 | 88.9% |
| ZIP codes | 16 | 72 | 77.8% |
| Ages 90 and over | 15 | 61 | 75.4% |
| Device identifiers | 13 | 31 | 58.1% |
| Other locations | 10 | 13 | 23.1% |
| Phone numbers | 8 | 293 | 97.3% |
| Health plan numbers | 7 | 48 | 85.4% |
| Vehicle plates | 6 | 26 | 76.9% |
| Emails, accounts, fax, licence | 6 | 142 | 95.8% |

Web addresses (20 of 20), IP addresses (66 of 66) and Social Security numbers (2 of 2) were all removed.

**Held back for a person, not released:** 31 of the 289 held-out notes. When
the tool's final check finds something it cannot account for, it refuses to
release the note rather than guess (24 for a confirmed name still
present, 5 for a detection lost between stages, 2 for a date still
present). Those 31 notes hold 727 of the 5,212 required identifiers in the
half (13.9%). The 90.5% is measured on the other 258 notes, which the tool
wrote to folders 1 and 2; the 31 stopped notes are copied unredacted to folder
3 and are not counted either way. Flagging a note does not
remove its identifiers; a person must review it.

## What is not counted, on purpose

The benchmark marks more than Safe Harbor requires. Counting everything
marked, the tool removes 79.0% on the held-out half; the difference is
items Safe Harbor allows to stay:

- **Ages 89 and under**, bare years, weekdays and seasons.
- **States and countries.** Safe Harbor removes places smaller than a state.
- **Hospitals, clinics, departments and professions.** These are not
  patient identifiers under Safe Harbor. The tool removes many of them anyway
  (clinicians' names: 96.4%), but it is not scored on them.

## Limits

- The notes are synthetic, written to be hard. Real notes from one practice
  are usually tidier, and results there may be higher or lower.
- Scored automatically against marked spans; a person reading the output may
  judge some partial removals as safe and some full removals as unnecessary.
- One engine version, one run. The numbers will change as the engine does;
  each release reports its own.

## Reproduce

```bash
python -m pip install -r requirements.txt
python tools/eval_corpus.py --half held-out
python tools/eval_corpus.py --half tuning
```

ASQ-PHI must be downloaded separately from [Mendeley Data](https://data.mendeley.com/datasets/csz5dzp7nx/1);
`tools/eval_asq_phi.py <path to synthetic_clinical_queries.txt> --half=odd`.
