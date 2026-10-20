# Human review

> **In development, not part of 0.25.0.** In version 0.25.0, review is done
> by your own team: a person reads every file in `1_REDACTED-NO-FLAGS` and
> `2_REDACTED-FLAGGED`, using `_WORDS-TO-CHECK.md` in folder 2, as the guide
> in each results folder explains. The rest of this page describes the
> three-person review workflow, which is included in the code (the
> `inkguard-review` command) but has not been validated and is not ready
> for use with patient records.

## At a glance

| Item | Review workflow (in development) |
|---|---|
| People per batch | Three (two reviewers and a clinician), or two when the clinician also reviews |
| Reviewer independence | Each reviewer works alone and cannot see the other's answers |
| Final decision | The responsible clinician, who signs |
| Notes held for a person, preservation set | 32.8% (19 of 58) |
| Words the software may never be overruled on | Identifiers found by label or exact format |
| Record kept | Every decision, by person, in the review report |
| Status | In development; installed as `inkguard-review` but not ready for use. Desktop app planned for 1.0.0 |

## The standard

- HIPAA does not prescribe a review process; it places the
  de-identification determination with the covered entity (45 CFR
  164.514(b)).
- NIST SP 800-188, *De-Identifying Government Datasets* (September 2023),
  recommends governance for de-identification, including documented
  procedures and review of what is released.
- Published evaluations of automated de-identification report that no
  system removes every identifier, which is why human review of output is
  common practice.

## Inkguard's position

| Step | Who | What happens |
|---|---|---|
| 1. Mark | Software | Removes what it finds; holds any note with a word it cannot account for; lists every word it was unsure about |
| 2. Review | Two reviewers, independently | Each reads every note twice (removals highlighted, then exactly as it will leave); removes more, restores mistaken removals, answers the software's questions, sends doubts to an "unsure" list |
| 3. Decide and sign | Responsible clinician | Sees both reviews and the unsure list; settles every disagreement; may remove more; signs |
| 4. Release | Software | Writes clean copies, reads each back from disk to check nothing removed is still visible, then erases the working copy |

Rules applied by the software:

- Two reviewers removing a word is final.
- A word stays only if the software asked about it, both reviewers kept it,
  and the clinician agreed.
- An identifier found by its label or exact format cannot be kept; the
  clinician may withhold the note instead.
- Spelling slips, and names that also appear as clinical terms in the same
  note, are put to both reviewers.

## What it needs from you

- People your organisation authorises to see the source records, named in
  the batch.
- Time: every note is read by each reviewer.
- A clinician accountable for the release decision.

Commands (command line):

```bash
inkguard-review prepare NOTES_DIR --out BATCHES_DIR \
    --authorized-by "Dr. Name" --reviewer "Name One" --reviewer "Name Two"
inkguard-review doctor BATCH_DIR --decisions R1.json R2.json --out doctor.html
inkguard-review release BATCH_DIR --decisions R1.json R2.json \
    --doctor DOCTOR.json --out CLEAN_DIR
```

## What it does not do

- On the command line, review pages are plain local files containing
  patient information; they must stay on the encrypted computer. The desktop
  app (accounts, encrypted batches, a reading room that blocks copying,
  phone codes for admins) is planned for 1.0.0.
- It does not replace the reviewers' judgement or the clinician's
  accountability.
- The record of a batch is kept on the computer it ran on.
