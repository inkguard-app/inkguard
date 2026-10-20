# Design decisions

The decisions that shape this software, with the date each was made and the
reason. Newest last. Each was made by the author, Alize Wajahat; the code
was written with an AI assistant (see "Acknowledgements and AI disclosure" in the README).
New decisions are added at the end; old entries are not rewritten.

Some entries describe features in development and not part of 0.25.0: the
three-person review workflow and the desktop app.

| Date | Decision | Why |
|---|---|---|
| 2026-09 | Everything runs on the user's own computer; files never leave it. | Patient notes should not have to be trusted to anyone else's server to be cleaned. |
| 2026-09 | Remove hidden content too: comments, tracked changes, document properties, hidden sheets, file names. | A document can be clean on the page and still carry names underneath. |
| 2026-09 | Words the engine is unsure about go to a person, never silently either way. | A wrong guess in either direction is worse than a question. |
| 2026-09-27 | Every batch ends with a review report: what was removed, by type, who reviewed and who signed. | A record of the work travels with the files. |
| 2026-10-05 | Each patient's notes are read together, so a name confirmed in one note is removed from all of them. | Real records repeat a nickname or a relative's name in one note and not another. |
| 2026-10-09 | Small-numbers warning by default when a combination of details fits a single patient; the threshold is adjustable. | In small research sets the common threshold of 11 flags nearly every row, and a warning that always fires stops being read. |
| 2026-10-10 | Two independent reviewers read every note; the signing clinician settles what they disagreed on. | One reviewer misses what the tool missed; two finding things independently also lets the misses be estimated. |
| 2026-10-10 | A word may stay only if the engine raised it, both reviewers agree, and the clinician agrees. | Leaving identifying text in should take deliberate, recorded agreement. |
| 2026-10-10 | Write, read back, verify, then erase the working copy. | Nothing is destroyed until the clean files are proven to be on disk and correct. |
| 2026-10-15 | A benchmark of invented patients with complex histories: several addresses, carers and clinics, uncommon names, changes of name or gender, and notes written in many styles. | To test varied, realistic writing. |
| 2026-10-16 | Every patient's notes are read at least three times, until a read learns nothing new. | Rereading catches what one pass misses; a file can hold several kinds of trouble at once. |
| 2026-10-16 | Released as open source under Apache-2.0, with the benchmark under CC BY 4.0. | So that anyone, including hospital IT departments, can use, check and improve it. |
| 2026-10-16 | No server, no online accounts and no network code of any kind; works with the Wi-Fi off. | Simplest to trust and to audit: there is nothing that could send data anywhere. |
| 2026-10-16 | The practice chooses two or three people per batch; phones are required for admins and optional for others. | Small practices need two-person review to be possible; larger ones may want a third. |
| 2026-10-16 | The first admin chooses a recovery passphrase, shown back to be written on paper and kept in a safe place. | The easiest recovery that needs no server and no outside party. |
| 2026-10-17 | Feedback is sent by the person from their own email program, never by the app. | Keeps the app free of network code while still letting users report problems. |
| 2026-10-17 | Problem reports by email only for the public preview; pull requests, issues and discussions are closed. | One private channel, read by the author, with no public place for anyone to paste patient information. |
| 2026-10-17 | No comparison with other tools in the project's own descriptions; another tool appears only in benchmark/RESULTS.md, as an untuned reference point run under the same scorer. | Say only what the measurements show. |
| 2026-10-17 | A clinical-preservation benchmark: invented neurosurgical and neurological notes, written independently of the tool's word lists, scoring the clinical terms kept beside the identifiers removed. | Keeping clinical detail matters to studies, so it is measured, not claimed. |
| 2026-10-17 | Inkguard is an academic, non-commercial open-source project, with no company behind it. | Free for every research team, with no conflict of interest. |
| 2026-10-18 | A standing notice on every screen, report and output guide: the tool is automated, can make mistakes, including misreading context, and a person must check every output. | It is a helper inside a human process, and it should say so wherever someone might forget. |
| 2026-10-18 | Newsworthy events (disasters, mass-casualty incidents) are flagged as a way to identify a patient. The details that pin the event down are suggested for removal unless both reviewers keep them; the event itself is a gentle reminder to the clinician. | A crash that made the news can identify the few people in it, but the clinical story still needs to say what happened. |
| 2026-10-18 | Small populations (small states, rural areas) count as narrowing, flagged only beside something rare or an exact age and sex. | A small place alone is not identifying; combined with a rare detail it can be. |
| 2026-10-18 | Spelling slips that look like a known word are listed and asked about; the record shows that both reviewers and the clinician saw them. | A misspelled name reads as a typo and slips past; a misspelled drug reads as a name and is removed. Either way a person should see it. |
| 2026-10-18 | Opaque markers ([REMOVED], with no type) only for the final files, after both reviews and the clinician's sign-off. Review always shows the type. | Reviewers need to know what was removed to check it; the published copy need not say. |
| 2026-10-18 | A batch can be wiped and started again, with a reason, and a restart report is kept. | Starting over should be possible, and should leave a record. |
| 2026-10-18 | Clinical vocabulary for neurosurgery and surrounding care, with capitalised eponyms counted as clinical only beside clinical words, and a given name before an eponym making it a surname. | The detail a study needs must survive, but a patient named Babinski is still a person. |
| 2026-10-18 | A large open vocabulary (MeSH, HL7 and others) was built, measured and not adopted. | It lowered identifier recall on the tuning half; more words is not better words. |
| 2026-10-18 | No patents. The code is published as open prior art. | Published openly so anyone may use and build on it. |
| 2026-10-18 | The name and logo are kept for the maintained version (TRADEMARKS.md); changed copies use another name. | The code is free to reuse; the name is how a reader knows which version a published result describes. |
| 2026-10-18 | Each new version is measured once on fresh invented notes written after the previous release; those notes are published afterwards. Earlier results stay tied to their own version. | Once a test set is public anyone can tune to it; a fresh set keeps every new figure a fair test. |
