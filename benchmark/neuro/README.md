# Clinical-preservation set (neurosurgery and surrounding care)

Invented clinical notes for measuring two things at once: that a
de-identification tool **removes the identifiers**, and that it **keeps the
clinical detail** a study needs (instruments, devices, eponyms, scores,
approaches, doses, imaging findings). Over-removal is silent: a removed word
leaves no trace. This set makes it visible.

## How it was written

By an AI assistant (Anthropic's Claude) working from general medical
knowledge, in a separate session that was told not to open the tool's code,
tests or word lists, so the notes are not built from the vocabulary the
tool uses. Everything is invented: patients, clinicians, facilities,
addresses, phone numbers (555), emails (example.org/.com/.net), record
numbers, and dates (years 2060 to 2080). Real commercial device and drug
names appear as clinical content.

Coverage: cranial tumour and neuro-oncology, neurovascular and
interventional neuroradiology, trauma and neurocritical care,
hydrocephalus, spine, functional and epilepsy surgery, paediatric
neurosurgery, peripheral nerve, neurology, neuroradiology, neuroanaesthesia
and monitoring, pain and rehabilitation, skull-base ENT and plastics,
obstetric and older-adult care. Note types: operative notes, clinic letters,
discharge summaries, consults, radiology reports, ICU notes, portal messages,
pasted fragments. Hard cases on purpose: patients and clinicians whose
surnames are also eponyms (a patient named Weber beside Weber syndrome),
device model names beside patient-specific serial numbers, numbers beside
clinical units.

## Markup (the answer key)

- `{{TYPE|text}}` an identifier. TYPE is one of NAME, DATE, AGE (only ages
  over 89), MRN, PHONE, EMAIL, ADDRESS, ZIP, FACILITY, ORG, ID, URL.
- `[[text]]` clinical content that must survive.
- Notes within a patient's file are separated by a line `=====NOTE=====`.

`check_markup.py` validates the markup and prints the counts.

## The two halves

| | Tuning (`tuning/`, p001–p050) | Held-out (`held-out/`, p051–p070) |
|---|---|---|
| Patients | 50 | 20 |
| Notes | 137 | 58 |
| Identifiers marked | 832 | 373 |
| Clinical phrases marked | 2,665 | 953 |

The held-out half was planned at 50 patients; its writer stopped after 20,
and it was not completed by anyone who had seen the tool's code. It is used
as written. The tuning half was written to a wider brief (more settings:
neuroanaesthesia, pain and rehabilitation, ENT, plastics, radiology-heavy,
medico-legal and billing notes, all ages); the held-out half to the
first brief (the neurosurgical subspecialties and neurology).

**The held-out half is never used for tuning.** Its misses are never
printed (the scorer refuses), and nothing in the tool's code or tests is
taken from it. Tuning uses the tuning half only.

## Scoring

`tools/eval_preservation.py` scores identifiers removed (Safe Harbor types,
and separately with places of care and organisations, which Inkguard
removes by policy) and clinical content kept (whole phrases intact, and the
share of their words), on each half. `--baseline` scores Microsoft
Presidio's analyzer in its default configuration on the same notes, as a
reference point.

## Limits

Synthetic notes, one author (an AI model), English only, not real records.
The same family of AI model also helped write the tool, so they may share
blind spots. A result here is evidence about these notes, not a guarantee
about anyone's records.

Released under CC BY 4.0 as part of the Inkguard benchmark.
