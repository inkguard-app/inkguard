# HIPAA Safe Harbor

## At a glance

| Item | Version 0.25.0 |
|---|---|
| Method supported | HIPAA Safe Harbor, 45 CFR 164.514(b)(2) |
| Identifier categories addressed | 16 of 18 in text; categories 16 (biometrics) and 17 (photographs) are outside a text tool |
| Identifiers removed, messy-notes benchmark (held-out half), before human review | 90.5% (95% CI 89.1–91.7%) |
| Generalisations applied | Dates keep the year; ZIP codes keep three digits or become 000; ages over 89 become "90 or older" |
| Hidden content read | Document properties, comments, tracked changes, headers, footers, hidden sheets, file names |
| Identification-risk flags | Rare conditions, small populations, newsworthy events |
| Other profiles | Limited Data Set (still PHI); date shifting (not Safe Harbor) |
| De-identification determination | Made by your organisation, not the software |

## The standard

The HIPAA Privacy Rule recognises two methods of de-identification
(45 CFR 164.514(b)):

- **Safe Harbor**: the 18 listed identifiers of the individual and of
  relatives, employers and household members are removed, and the covered
  entity has no actual knowledge that the remaining information could
  identify the individual.
- **Expert Determination**: a qualified expert determines and documents that
  the risk of identification is very small.

HHS's *Guidance Regarding Methods for De-identification of Protected Health
Information* (November 2012) explains both. Neither method requires a
particular tool.

## Inkguard's position, by identifier

Each row is what the software looks for, not a promise that every instance
is found; measured misses by type are in RESULTS.md (linked below the table).

| # | Identifier | What the software does with what it finds |
|---|---|---|
| 1 | Names | Removed, using context (titles, relations, roles, record headers) as well as name lists; a name confirmed in one note is removed from all of that patient's notes |
| 2 | Geographic units smaller than a state | Addresses, cities, towns and counties removed; ZIP codes keep three digits, or 000 for the areas HHS lists |
| 3 | Dates except year | Day and month removed; year kept; relative times kept |
| 4–5 | Telephone and fax numbers | Removed, or the note is held for a person |
| 6 | Email addresses | Removed |
| 7 | Social Security numbers | Removed, including stated last-four digits |
| 8 | Medical record numbers | Removed |
| 9 | Health plan beneficiary numbers | Removed |
| 10 | Account numbers | Removed |
| 11 | Certificate and licence numbers | Removed |
| 12 | Vehicle identifiers | Removed |
| 13 | Device identifiers and serial numbers | Removed; the device model is kept as clinical detail |
| 14–15 | Web and IP addresses | Removed |
| 16 | Biometric identifiers | Not detected |
| 17 | Full-face photographs and comparable images | Not read; images and scans are copied unread to `3_NOT-PROCESSED-MAY-CONTAIN-PHI` |
| 18 | Other unique numbers, characteristics or codes | Labelled codes and usernames removed; a word the software cannot account for holds the note |

Measured misses by type are published in
[benchmark/RESULTS.md](../../benchmark/RESULTS.md).

For "actual knowledge", the software flags combinations that can identify
someone even without a listed identifier (a rare condition, a small
population, a newsworthy event) for human review.

Every run writes an audit trail (method, version, code fingerprint, removals
by type, files flagged, files not processed) and a citations file with a
draft Methods paragraph.

## What it needs from you

- Human review of every output before use ([Human review](review.md)).
- Your organisation's de-identification determination, made by the people
  authorised to make it.
- A data use agreement if you use the Limited Data Set profile, and an
  Expert Determination if you use date shifting.
- Images, scans and audio handled by another process; the software sets
  them aside.

## What it does not do

- It does not remove every identifier. That is the reason for review.
- It is not an Expert Determination and not a certification.
- Typed English text only.
- The list of small ZIP areas is the HHS-published list; checking it against
  current census data is planned.
- Not yet validated on real clinical records.
