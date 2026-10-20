# Writing brief: the longitudinal test corpus

You are writing realistic clinical notes for SYNTHETIC patients. They are used
to measure software that removes patient identifiers from notes, so two things
matter above everything: the notes must read like real clinical writing, and
every identifier in them must be marked exactly.

## What you are given, per case

- `contexts/case-NNN.json` — the patient's clinical substance, from Synthea
  (synthetic, public domain): sex, age, conditions with onset dates,
  medications, visits with reasons, labs and vitals, procedures. Dates are
  already shifted into the future (2060-2095). **Keep the clinical facts and
  the intervals between dates true to this file** — that is what makes the
  record coherent over time.
- `matrix.json` — the scenario for that case: the setting, which note types to
  write (one note per type), the writing voice, and which identifier shapes to
  lean on. Follow it.

## Never

- Never use a real person, real patient, real record or real dataset. Invent
  every name, number, address, phone, email and institution. No famous names,
  no real hospitals or clinics. Real cities, states and countries are fine.
- Never mark medicines, diseases, lab tests, values, scores, or eponymous
  conditions and signs (Parkinson's disease, Bell's palsy, Hodgkin lymphoma).
- Never look at or run the de-identification software. This corpus is
  written blind to the software it tests.

## First, an identity card per case

Before writing a case's notes, invent its identity and save it as
`records/NNN-identity.json` in your output folder:
`{"patient": "...", "dob": "...", "mrn": "...", "address": {...}, "phone": "...",
"family": [{"relation": "...", "name": "..."}], "employer": "...",
"clinicians": [...], "facilities": [...], "other_ids": {...},
"variants": {"<correct spelling>": ["<each misspelling you used>"]},
"oddities": ["<one short line per new kind of absurdity you invented>"],
"former_names": [...], "unsure": [...]}`.
Then use exactly those details in every note of that case where they would
appear. The same patient has the same name, MRN, address and family in every
note — that consistency is the point of a longitudinal corpus. (A date of
birth should fit the age in the context file.)

## Human errors — write the way people actually write

Real notes are full of mistakes, and the mistakes are exactly where
de-identification software leaks: a misspelled name is no longer the name
the software was looking for. So put real human error into every note, at a
realistic rate — about one error in every 30-60 words for most voices, and
noticeably more for the "speech-recognition errors" and "first language is
not English" voices. Mix kinds, including:

- **Typing slips** (the four classic kinds): a wrong letter ("pateint"), a
  missing letter ("hypertnsion"), an extra letter ("diabettes"), two letters
  swapped ("teh"); and keyboard-neighbour slips ("bloos pressure").
- **Spelling and sound-alikes**: "seperate", "definately", "their/there",
  "affect/effect", "complaint/compliant", "ilium/ileum", "perineal/peroneal".
- **Autocorrect and speech recognition**: "patient was seen by Dr. Ozone"
  for Osei, "metformin" heard as "met for men", "hypo" for "hyper",
  "15" heard as "50", a whole sentence that makes no sense.
- **Scanning/OCR confusion**: rn read as m, l as 1, O as 0, cl as d.
- **Spacing and punctuation**: "Mr.Smith", "pt'sdaughter", missing full
  stops, double spaces, a stray comma, run-on sentences, a sentence cut off.
- **Capitalisation**: names in lower case ("john ferraro"), ALL CAPS
  fragments, Random Capitals.
- **Grammar**: wrong tense, missing words, subject-verb disagreement,
  dangling clauses, telegraphic fragments.
- **Copy-forward**: a paragraph repeated from the previous note, sometimes
  with a stale date or a stale medication still in it.
- **Inconsistent abbreviations**: "HTN" and "hypertension" and "high bp" in
  the same note.

**Errors in the identifiers themselves** — do this in roughly one identifier
mention in eight: the patient's name misspelled in one note ("Ferarro"),
a relative's name spelled two ways, two digits of an MRN or phone swapped,
a date with an impossible day ("4/31/71"), a ZIP with a digit missing, an
email with a typo. **Mark the identifier exactly as it is written, error and
all** — a misspelled name is still the patient's name. Add every misspelling
you used to the identity card under "variants": `{"Rosalind Ferraro":
["Ferarro", "rosalind ferraro"]}`.

**Wrong-patient fragments** — in about one case in ten, copy-forward drags in
a line about a DIFFERENT patient (invent them: a name, sometimes an MRN or
date of birth). This is a real way identifiers leak. Mark it the same way
(the other patient's name is PATIENT).

Keep the clinical meaning recoverable: errors should look like a busy
clinician's, not like noise.

## Template errors — what electronic records do to notes

Most notes are written inside an electronic record's templates, and templates
fail in their own ways. Put several of these into every case:

- **Placeholders never filled in**: `***`, `[NAME]`, `<<PATIENT_NAME>>`,
  `{FIRST_NAME}`, `@AGE@`, `Patient: ______`, `DOB: **/**/****`. (An empty
  placeholder holds no identifier — do not mark it. A half-filled one does:
  mark the part that is real.)
- **Shortcut phrases half-expanded**: ".HPI", "@NAME@ is a @AGE@ y.o. female"
  where some fields expanded and some did not, or expanded with the WRONG
  patient's details (mark those as PATIENT/AGE etc. — they are still
  identifiers, just someone else's).
- **Page headers and footers** repeated through the note:
  "FERRARO, ROSALIND  MRN 0071-4482  DOB 03/14/2001  Page 2 of 3".
- **Electronic signature and audit lines**: "Electronically signed by Kwame
  Osei, MD on 03/14/2071 14:02", "Last edited by jtwhite on 3/15/2071 09:14
  from 10.20.4.55", "Note routed to: ...".
- **Default values nobody changed**: "DOB: 01/01/1900", "Phone: (000)
  000-0000", "Age: 0", "Emergency contact: SAME". Mark the date, phone or
  age as its type anyway — software cannot tell a default from a real one —
  and list it under "oddities".
- **Truncated fields**: "Patient: Rosalind Ferr", "Address: 41 Pargeter La".
  Mark the truncated text as it stands.
- **Checkbox lists rendered as text**:
  "[x] Lives alone  [ ] Lives with spouse (name: ________)".
- **Markup leaking through**: "&lt;b&gt;Ferraro&lt;/b&gt;", "\\par",
  "&nbsp;", stray RTF or HTML tags around names and dates.
- **The wrong item picked from a list**: the wrong attending, department or
  clinic selected, then contradicted in the free text.
- **Field labels that do not match their contents**: "Emergency contact:
  hypertension", "Allergies: Rosalind Ferraro".
- **cc lists and routing blocks** with names, fax numbers and emails.
- **The same section twice**: the template's version and a free-text version
  that disagree.

## Carry-forward: whole old notes pasted into new ones

At handover and for quick retrieval, nurses and doctors paste entire earlier
notes — sometimes several — into the new one, ignoring the template and its
headings altogether. In at least one note per case (more in nursing,
inpatient and handover settings), do this properly:

- a handover or progress note that is mostly earlier notes pasted end to end,
  each still carrying its OWN old header, date, signature line and author;
- "Pasted from 0700 note:", "see below", "per prior note:", ">>>" or nothing
  at all to mark where the paste begins — and often no mark where it ends;
- the new note's own template abandoned halfway, so its headings never
  appear, or appear once at the top and never again;
- pasted text that contradicts the new text (an old medication still listed,
  an old room number, a discharged problem still "active");
- the same identifiers appearing again and again in different forms across
  the pasted blocks (full name in one header, initials in another, a
  misspelling in a third);
- now and then, a pasted block from the WRONG chart: another invented
  patient's note dragged in whole.

Mark every identifier in pasted text exactly as in any other text: a pasted
name is still a name.

## Stay curious about the absurd

The lists above are where to start, not where to stop. Real records are full
of identifiers in places nobody would think to look, put there by tired,
rushed, distracted or careless people. Be inventive about that, and keep
inventing new kinds. A few to get you going:

- the dictating doctor says "scratch that, her real name is..." and it is
  transcribed anyway;
- a template placeholder never filled in, or filled in wrongly
  ("Patient: <<insert name>>", "Dear [PATIENT], Dear Mrs Ferraro");
- a whole email or text-message thread pasted in, with From/To/Date lines,
  a phone signature, an IP address;
- a file name mentioned in the note ("see Ferraro_R_MRI_2071.pdf");
- an identifier written out in words ("five five five, oh one four seven"),
  or split across a line break;
- a patient named after a drug, a disease or a place (a child called
  "Mercy", a man called "Ambien" by his friends), or a pet with the
  family's surname, or a business named after the patient
  ("Ferraro & Sons Plumbing");
- a tattoo of a child's name and birth date, described on exam;
- a licence plate from an accident report, a Wi-Fi network name, a social
  media handle, GPS coordinates from a fall alarm, a pharmacy rx number;
- a credit-card or account number pasted in by mistake;
- a patient who is themselves a doctor, so "Dr." does not mean the clinician.

Do at least one genuinely new kind of absurdity per case that is not on any
list here, and describe it in the identity card under "oddities" (one short
line each). Mark everything identifying in it as usual. These are the cases
that teach us what we have not thought of.

## Messy lives — records are not tidy stories

Real records are incomplete, contradictory and written by many hands. Build
these into cases (your scenario's `team` focus says which to lean on):

- **Abandoned and broken records.** The patient moves abroad, changes
  provider, or simply stops coming. The last note may be a no-show, a
  records-transfer request to another clinic (with that clinic's name, fax,
  and a release form number), or nothing at all. Gaps of years. A note that
  starts "pt returns after 6 yrs, was living in Manila".
- **Name changes.** Marriage, divorce, legal change, adoption, an anglicised
  or re-ethnicised name. Older notes keep the old name; newer ones the new;
  some carry both ("Jane Doe (formerly Jane Smith)", "AKA", "née"). Every
  name the person has ever used is PATIENT. List them in the identity card
  under "former_names".
- **Gender changes.** Earlier notes may use a former name and other pronouns;
  later notes the current ones; some notes inconsistently mix both, or a
  copy-pasted old section keeps the old name. The former name is still
  PATIENT and must be marked wherever it appears. Pronouns are not marked.
  Write respectfully and clinically, never as a joke.
- **Unreliable histories.** The patient denies a condition the chart shows,
  claims surgeries that never happened, gives a different birth date or
  address at different visits, a relative "corrects" the history. Contradict
  the context file in what the patient SAYS, never in what the record shows.
  Every version of a date of birth or address is still marked.
- **Notes that make no sense.** Half-finished sentences, a paragraph about
  the wrong patient (that other patient is PATIENT too), orders for a
  medicine never mentioned again. Leave them in.

## Who is writing — style changes everything except the rules

Vary the writer between notes of the same case: attending, resident, nurse,
scribe, medical assistant, a student, a locum from another country, an
older doctor from a different era, a specialist with heavy jargon. Let era,
region, specialty and whatever was trending that week leak into the voice
(slang, emoji in portal messages, all-caps legacy systems, British spelling,
abbreviations nobody else uses).

At least one note per case in teams B and D is partly written by a **medical
assistant or high-school volunteer**: odd descriptors ("pt was super chill",
"vibes off"), decorative formatting (bullets made of hyphens and arrows,
random bold markers like **, headings in quotes), essay-like
"improvements" ("In conclusion, the patient's journey..."), rewording a
name or date in a strange way ("the twenty-third of March", "Mr. J.
(Jonathan)"), or inventing a heading. Identifiers inside all of this are
marked exactly as usual.

## Names that do not look like names

Software misses names that are also ordinary words, numbers or symbols. Use
plenty, especially in team C:

- Word names: Sky, Blue, River, Story, Justice, Royal, Legend, Halo, Nova,
  Saint, Bless, Kash, Miracle, Wednesday, Apple, Lake, Ocean.
- Letters, numerals and stylised spellings: X, X Æ A-12 style, Kxng, Jaxxon,
  Ny'Asia, Rhaegal, A1, Seven, Tre, Nyne, J'Lynn, "x183474" as a chosen name,
  a name with a digit in it (Mi4), lowercase names, names written in
  emoji-adjacent ways in a portal message.
- Usernames used as names ("goes by skyblu_99"), gamer tags, a nickname that
  is a number ("Twelve").
- A surname that is a common word (Page, Frost, Will, May, Hope, Rich).

All of these are PATIENT (or DOCTOR for staff) when they refer to a person,
and must be marked; the same word used as an ordinary word is NOT marked.
If you genuinely cannot tell whether a token is a name, mark it and list
it in the identity card under "unsure".

## Marking identifiers

Write each note as plain text with every identifier wrapped like this:

    {{TYPE:exact text}}

    Patient: {{PATIENT:Rosalind Ferraro}}  MRN {{MEDICALRECORD:0071-4482}}
    Seen {{DATE:03/14/2071}} by Dr. {{DOCTOR:Kwame Osei}} at {{HOSPITAL:Lakeview General}}.

TYPEs (use exactly these):

    PATIENT DOCTOR USERNAME PROFESSION
    ROOM DEPARTMENT HOSPITAL ORGANIZATION STREET CITY STATE COUNTRY ZIP LOCATION-OTHER
    AGE DATE
    PHONE FAX EMAIL URL IPADDR
    SSN MEDICALRECORD HEALTHPLAN ACCOUNT LICENSE VEHICLE DEVICE BIOID IDNUM

- Relatives', friends' and neighbours' names are **PATIENT** (as in the 2014
  scheme). Clinicians and staff are **DOCTOR**. Employers, schools and named
  companies are **ORGANIZATION**. A specific job is **PROFESSION**.
- Anyone who is not clinical staff (relatives, partners, other patients,
  interpreters' names, the neighbour) is **PATIENT**. Anyone on staff
  (doctors, nurses, scribes, MAs, therapists, clerks) is **DOCTOR**.
- Do **not** mark relative dates or durations ("3 days ago", "for 2 weeks",
  "last month", "POD 2"). Do **not** mark clock times alone ("14:30").
  Do mark named days ("Tuesday", "Christmas") and anything that fixes a
  calendar point.
- Mark **every** date and part of a date: full dates, month and year, a bare
  year, a weekday, a holiday, "the 14th". Mark **every** age.
- An address across lines is marked in pieces (STREET, CITY, STATE, ZIP).
- The marked text must be exactly what is in the note, no spaces at its ends.

## Files

- Notes: `<your folder>/notes/NNN-RR.txt` — NNN is the case number, RR the
  record number in date order (01, 02, ...).
- Then run, and fix everything it refuses until it reports 0 refused:

      python3 make_record.py <your folder>/notes <your folder>/records

## When you are done

Reply briefly: cases done, notes written, identifiers marked, a count per
TYPE, and anything you were unsure how to label.
