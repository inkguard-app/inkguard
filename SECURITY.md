# Security policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Email
**inkguard.project@gmail.com** with:

- what you found and how to reproduce it,
- the version (`inkguard --about`, or the window's top bar),
- whether you believe patient information could be exposed.

Never include real patient information in a report. Use invented text.

A fix, or a plan for one, is
published within 90 days, and you are credited in the release notes unless
you ask not to be.

## Supported versions

Only the latest release receives security fixes.

## What this software protects against, and what it does not

See [THREAT_MODEL.md](THREAT_MODEL.md). In short: it is designed so that its
own code sends no patient information off the computer. Version 0.25.0 writes
unencrypted files; keep them on an encrypted disk.
It does **not** protect a computer that is already compromised, and no
automated tool removes every identifier: the human review step exists for
that reason.
