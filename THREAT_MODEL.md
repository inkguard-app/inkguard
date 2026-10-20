# Threat model

> Most of this document describes the desktop app planned for 1.0.0. Version
> 0.25.0 is the command line and a small window: it has no accounts or
> encryption, and writes ordinary files that you keep on an encrypted disk.

What this software is built to resist, how, and what it deliberately does
not try to do. Every control named here has a test; the full list, with the
test names, is in [docs/SECURITY_STANDARDS.md](docs/SECURITY_STANDARDS.md).

## What is being protected

1. **Patient information in the files being redacted**, and in the
   app's working copies while two people review them.
2. **The record of who did what**: which identifiers were removed, who
   reviewed, who signed off. This record is what makes a de-identification
   defensible later.

## Where it runs

On one computer the practice controls (Windows 10 22H2+ or macOS 13+), used
by a small team with their own accounts. There is no server, no cloud
service and no account with anyone outside the practice.

## Threats it is designed to resist

| Threat | How it is resisted |
|---|---|
| Patient information leaving over the network | The app contains **no network code** (enforced by `tests/test_no_network.py`). The desktop window's content security policy forbids connections. It works with the Wi-Fi off. |
| Someone reading the app's files on the disk (a stolen laptop, a curious colleague, a backup left on a shared drive) | Records and backups are encrypted with AES-256-GCM. Work in progress is encrypted under a per-batch key held only in the operating system's protected store (Windows Credential Manager or macOS Keychain), never in the app's folder or a backup. |
| Guessing passwords | NIST SP 800-63B-4 rules: 15+ characters, context and pattern blocklist (a breached-password list is planned), throttling that survives a restart, disabling after 100 misses in a row. Passwords are stored with scrypt at OWASP settings. Unknown names behave exactly like real ones. |
| Someone walking up to an unlocked screen | Idle lock enforced by the core (1–15 minutes, both clocks, a sleeping laptop wakes locked). Copying, printing and saving are blocked in the review room, and the screen blurs when the window loses focus. (No app can stop a phone camera; the reviewers attest to how they worked.) |
| An admin account being taken over | Phone codes (RFC 6238, works offline) are required for admins before they can start a batch. A recovery passphrase, chosen by the person in charge, is the only way back in without the password; every use is logged under their name. |
| Records or logs edited, cut or swapped for an older copy | Hash-chained security log; records and log vouch for each other; a sealed counter in the OS protected store moves with every save. Any mismatch raises an integrity alert that only an admin can clear. |
| A computer being retired or replaced | Cryptographic erase (NIST SP 800-88): keys are destroyed first, so work becomes unreadable at once, then files are deleted. Records copied back afterwards can never be opened. |
| Hidden identifiers in documents | Word metadata, comments, tracked changes, headers, footers, hidden sheets and file names are read and cleaned; each output file is read back off the disk and checked for every word the review removed before anything is released. |
| A de-identification nobody can account for | Two reviewers work independently and cannot see each other's answers; a named clinician signs off; the review report records method, version, people, decisions and measured accuracy. |

## What it does not protect against

- **A computer that is already compromised.** Malware with the user's
  rights can read the screen and the keyboard. Keep the operating system
  updated and use full-disk encryption (BitLocker, FileVault).
- **Someone with the admin's password and phone**, or with the recovery
  passphrase. Those are the keys; keep them as such.
- **Every identifier, automatically.** On deliberately messy notes the
  engine alone removes about 90% (90.2%, held-out half, 0.25.0) of the identifiers Safe Harbor requires
  (see the README for the test sets). Human review is part of the method,
  not an optional extra.
- **Re-identification from what Safe Harbor allows to remain.** Rare
  conditions and small groups are flagged for the reviewers, but this is not
  a statistical (Expert Determination) assessment.
- **Images.** Scanned pages and photographs hold no typed text to read;
  in 0.25.0 such files are copied unread into
  `3_NOT-PROCESSED-MAY-CONTAIN-PHI`; in the desktop app they are listed and
  never exported.
- **Legal compliance on its own.** The software supports a documented
  Safe Harbor process; whether a dataset may be shared is still a decision
  for the people and institutions responsible for it.

## Reporting a problem

See [SECURITY.md](SECURITY.md).
