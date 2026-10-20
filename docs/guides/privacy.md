# Private by design

## At a glance

| Item | Version 0.25.0 |
|---|---|
| Where records are processed | On the computer it is installed on |
| Network connections made by Inkguard's own code | None (checked by test) |
| Internet needed | Once, at installation (about 600 MB of libraries) |
| Third parties that receive records | None |
| Business associate relationship with the project | None; the project receives no patient information |
| Original files | Read only; SHA-256 fingerprints re-checked after each run |
| Content of logs and the feedback report | Support codes, counts, timings, version; no note text, no file names (file ids and the computer name are in the audit trail only). Anything a person adds to the feedback report can be checked with `inkguard --check-feedback` before sending |
| Encryption at rest | Desktop app (planned for 1.0.0): AES-256-GCM. Command line: plain files |
| Requirements | 64-bit computer, Python 3.11–3.14, about 1 GB disk, 4 GB memory (2 GB minimum) |

## The standard

- **HIPAA Security Rule, technical safeguards** (45 CFR 164.312): access
  control, audit controls, integrity, person or entity authentication, and
  transmission security for electronic PHI.
- **Business associates** (45 CFR 160.103): a party that creates, receives,
  maintains or transmits PHI on behalf of a covered entity is a business
  associate and needs a business associate agreement. HHS's 2016 guidance on
  cloud computing applies this to cloud providers that store encrypted PHI,
  even without the key.
- **Storage encryption**: NIST SP 800-111 describes encryption of data at
  rest on end-user devices.

## Inkguard's position

- Processing is local. No record is sent to the project or to any service;
  there is no server, account or telemetry.
- `tests/test_no_network.py` checks the imports of every shipped module for
  network code and runs the engine with network connections refused.
  `python -m inkguard.cli --self-test` runs the engine on the installed copy,
  on invented text, with every network connection refused, and reports any
  attempt to connect; it does not re-check the code's imports.
- Originals are opened read-only. After each command-line run, each
  original's SHA-256 fingerprint is compared with the one taken before the
  run, and the result is reported.
- Support codes ([SUPPORT_CODES.md](../../SUPPORT_CODES.md)) describe
  problems without patient information, so a problem can be reported and
  diagnosed without anyone seeing records.
- Desktop app controls (encryption at rest, accounts, security log, erasure)
  are listed with the test behind each in
  [SECURITY_STANDARDS.md](../SECURITY_STANDARDS.md).

## What it needs from you

- A computer your organisation already permits to hold the source records,
  with full-disk encryption and up-to-date security software.
- For the command line: keeping the results folder on that encrypted disk,
  never sharing `3_NOT-PROCESSED-MAY-CONTAIN-PHI`, and deleting it when no
  longer needed.
- Your organisation's usual approval for installing software (Python and
  the listed libraries; [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md)).

## What it does not do

- It does not protect a computer that is already compromised, or stop
  someone with administrator rights from copying files or photographing the
  screen ([THREAT_MODEL.md](../../THREAT_MODEL.md)).
- The command line does not encrypt its output. Encryption at rest arrives
  with the desktop app in 1.0.0.
- No signed installers yet; installation is from source. Signed Windows and
  macOS installers are planned for 1.0.0.
- No shared server or multi-computer deployment, by design.
