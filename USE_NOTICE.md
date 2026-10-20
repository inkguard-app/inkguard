# Before you use Inkguard with patient records

Plain-language guidance for using this software. It adds no conditions to
the [Apache License 2.0](LICENSE), which alone governs your rights. This is
not legal advice. Your privacy officer, IRB or
ethics board, and counsel decide what your organisation requires.

## What Inkguard is

A tool that helps a person remove patient identifiers from typed clinical
documents, on that person's own computer. It looks for the identifiers
the HIPAA Safe Harbor method lists that can appear in typed text (16 of the
18 kinds), flags what it is unsure of,
marks every redacted file as pending human review, and writes an audit trail
of the run.

## What it is not

- **Not a de-identification determination.** The software finds most
  identifiers, not all ([measured results](benchmark/RESULTS.md)). Output is
  a draft until a person has read every note. Under HIPAA, the covered
  entity or its business associate decides whether data are de-identified;
  using this tool does not make that decision for you.
- **Not validated on real clinical records yet.** Version 0.25.0 is a public
  preview, measured on synthetic notes and a published external benchmark.
- **Not an Expert Determination** (45 CFR 164.514(b)(1)) and not a
  certification of any kind. Safe Harbor also requires that you have no actual
  knowledge that the remaining information could identify a person; the
  software cannot judge that.
- **Outside the US:** under the GDPR and UK GDPR, redacted output is usually
  pseudonymised, not anonymous, and remains personal data while it can be
  re-linked (for example, through the name key).
- **Not a medical device.** It is not intended for diagnosis, treatment or any
  clinical decision.
- **Not a service.** Nobody receives your files. The author is not your
  business associate and does not sign business associate agreements.

## Using it responsibly

1. **A person reviews every output** before it is shared, published, or
   given to anyone outside the people already allowed to see the originals,
   including files in `1_REDACTED-NO-FLAGS`.
2. **You keep the files safe.** The results folder stays on an encrypted
   disk on the computer that made it, and is deleted when no longer needed.
   `3_NOT-PROCESSED-MAY-CONTAIN-PHI` (copies of the originals and the
   name key) is never shared, nor is the hidden `.inkguard` folder while it
   holds working copies. Keep `4_Audit_trail.pdf` with your study records.
3. **You follow your own rules.** Your organisation's policies, your IRB or
   ethics approval, data use agreements, and the law where you work apply to
   your use of the software and its output.
4. **You never send patient information to the project.** Problem reports
   use made-up text and the `6_PLEASE-RETURN-to-inkguard.project@gmail.com_Feedback-report.docx` file, whose system record holds none
   and can be checked with `inkguard --check-feedback` before sending.
   Patient information sent to the project is unwanted and will be deleted when
   found, and sending it may be a disclosure your organisation must assess.
5. **You check the label.** Each run records the version and the code
   fingerprint (SHA-256) of the software that made it. Results apply to that
   version only.

## No warranty

The software is provided "as is", without warranty of any kind, and the
authors are not liable for any claim or damage arising from its use, as set
out in sections 7 and 8 of the Apache License 2.0. You use it at your own
risk and are responsible for the decisions you make with it.

## Contact

inkguard.project@gmail.com, for problems and questions. Never include
patient information.
