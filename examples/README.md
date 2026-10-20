# Sample notes to try

A small folder to practise on: 21 typed notes and one Word letter for five
invented patients, one folder each. **No real person is in them.** They are
taken from the tuning half of the [messy-notes benchmark](../benchmark/) and
are just as messy: a fax cover sheet pasted into a note, speech-recognition
errors, an all-capitals legacy system, long dictated operative notes, a
social-work case file.

`patient-A/note-04-letter.docx` also hides things a reader would not see on
the page: a comment ("Call Imogen Tarrow back…") and an author in the file's
properties. Both are invented, and both should be gone from the clean copy.

## Try it

In the Inkguard window (`inkguard-window`), choose this `sample-notes`
folder. Or, from the repository folder:

```bash
inkguard examples/sample-notes -o clean/
```

What happens (measured with 0.25.0 on 2026-10-19; about 30 seconds on a
2-core machine):

- All 22 files are processed; none is stopped. 8 go to
  `clean/1_REDACTED-NO-FLAGS/` and 14 to `clean/2_REDACTED-FLAGGED/`, each
  under its patient's folder. `3_NOT-PROCESSED-MAY-CONTAIN-PHI/` stays
  empty.
- For the 14 flagged notes, the words the tool was unsure about are listed,
  with line numbers, in a `_WORDS-TO-CHECK.md` inside each patient's folder
  of `2_REDACTED-FLAGGED/`. An unsure word goes to a person, never
  silently either way.
- Every note, including the 8 with no flags, still needs a person to read
  it. One known gap shows here: in `patient-A/note-04.txt` a spelled-out
  pronunciation of the patient's surname ("Vee-eh-CHOR-ek") is left in,
  and the note is not flagged.
- The audit trail, `clean/4_Audit_trail.pdf`, gives the files per folder,
  what hidden content was removed from the Word letter, and every file by id
  in its appendix.

Named hospitals and clinics are removed as `[FACILITY]` (Inkguard's own
policy; Safe Harbor does not require it). Some organisation names, such as
"Sound Crossing Endocrine Associates" and an all-capitals letterhead, stay
in; check them.
