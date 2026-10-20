# Support codes — a field guide for IT support

When the app stops or sets something aside, it shows the doctor a short message and a
**code**. The doctor can share the support log (which contains codes, counts and times —
**never file names or patient information**) with whoever supports the software. Look the
code up here: it says what the code means and how to resolve it. You do not need access
to any patient file to use this guide. Entries marked "(desktop app, in development)" or "(review workflow, in development)" belong to parts that are not in 0.25.0.

Codes that start with `ZC-` come from the intake guard; they are described at the end, not listed one by one.

## Starting the app

### `APP-START`
- **The doctor sees:** The app could not start. Please try again; if it keeps happening, reinstall it.
- **What it means:** (desktop app, in development) A component failed to load, or the install is incomplete or damaged.
- **How to fix it:** Reinstall the software from the project's official release. Confirm the computer meets the supported operating-system version. Check the installer's own log if one was written.

### `APP-OS-UNSUPPORTED`
- **The doctor sees:** This version of the operating system is older than the app supports.
- **What it means:** (desktop app, in development) The OS is below the supported minimum.
- **How to fix it:** Update the operating system, or run the app on a supported machine. The supported versions are listed in the install guide.

### `APP-CORE-SIGNATURE`
- **The doctor sees:** The app found that one of its own parts had changed and stopped, to be safe.
- **What it means:** (desktop app, in development) The app's core component failed its integrity check — it was modified or is damaged.
- **How to fix it:** Reinstall the software from the project's official release. Do not run a modified copy. This is a safety stop, not a data problem.

### `APP-OCR-MISSING`
- **The doctor sees:** The page-reading check (OCR) is not installed, so PDFs are held for a person to look at rather than passed.
- **What it means:** (desktop app, in development) The text-recognition component (Tesseract) is not present.
- **How to fix it:** Reinstall the full package, which includes it, or install Tesseract. Until then PDFs are held for a person to check, not passed unchecked.

### `APP-UPDATE`
- **The doctor sees:** An update could not be applied. The app is still usable; please try the update again later.
- **What it means:** (desktop app, in development) Installing a new version failed (a permission problem or a locked file). The app does not fetch updates itself; a new version is installed by hand.
- **How to fix it:** Close the app so no file is locked, then install the new version again. If it still fails, check that the account may write to the install folder.

## Signing in

### `AUTH-FAILED`
- **The doctor sees:** That name and password don't match. Please try again.
- **What it means:** (desktop app, in development) A sign-in or unlock used the wrong password or PIN (or an unknown name — the app deliberately does not say which).
- **How to fix it:** Check Caps Lock and the keyboard language. If the person has forgotten it, the practice admin sets a new password for them.

### `AUTH-THROTTLED`
- **The doctor sees:** Too many tries. Please wait a few minutes, then try again.
- **What it means:** (desktop app, in development) Several wrong passwords in a row for this account; sign-in is paused for a while, and the pause grows with each further miss.
- **How to fix it:** Wait, then try again. If it keeps happening and the person did not cause it, someone may be guessing — the practice admin should reset the password and check the security log.

### `PASSWORD-WEAK`
- **The doctor sees:** Please choose a different password — at least 15 characters. A short sentence of your own works well, like "the blue kettle sings at seven".
- **What it means:** (desktop app, in development) The new password is under 15 characters, is on the list of breached or common passwords, contains the person's or practice's name or the app's name, or is a repeated or sequential pattern (NIST SP 800-63B-4).
- **How to fix it:** Use a passphrase of a few unrelated words. There are no other rules — no required symbols or numbers, and no forced changes.

### `AUTH-DISABLED`
- **The doctor sees:** This password was turned off after too many wrong tries. Ask your practice admin to set a new one — your saved work is safe.
- **What it means:** (desktop app, in development) 100 wrong passwords in a row for this name, so the password was disabled, as NIST SP 800-63B-4 requires.
- **How to fix it:** The practice admin sets a new password for the person. If the admin is the one locked out, they use the recovery code printed at setup. Check the security log: someone may have been guessing.

### `SESSION-LOCKED`
- **The doctor sees:** The screen locked to protect patient information. Sign back in to carry on — your work is saved.
- **What it means:** (desktop app, in development) The session was idle longer than the practice's lock time, or someone locked it.
- **How to fix it:** Expected behaviour. Enter the password or PIN to continue. The lock time is set by the practice admin (1–15 minutes).

### `SESSION-ENDED`
- **The doctor sees:** You were signed out. Please sign in again — your saved work is still here.
- **What it means:** (desktop app, in development) The session ended: signed out, too many wrong PINs at the lock screen, or the app was restarted.
- **How to fix it:** Sign in again with the full password. Nothing that was saved is lost.

### `ROLE-DENIED`
- **The doctor sees:** Your account can't do that. Ask your practice admin if you need it.
- **What it means:** (desktop app, in development) The signed-in account does not have the role this action needs (admin, doctor or reviewer).
- **How to fix it:** The practice admin can change the account's role.

## The app's own records

### `APP-STORE`
- **The doctor sees:** The app's local records could not be opened safely, so it stopped. Your files were not touched.
- **What it means:** (desktop app, in development) The practice root key in use does not open the app's records or its security log — almost always the wrong root key (a damaged records file is set aside by the app itself and leads to Restore from backup instead).
- **How to fix it:** Put the practice's backed-up root key back in place and open the app again. Nothing was changed while the key was wrong. Never edit or copy the app's data files by hand.

### `APP-ALREADY-RUNNING`
- **The doctor sees:** The app is already open on this computer. Please use the window that is open.
- **What it means:** (desktop app, in development) A second copy of the app tried to start while one was running; two copies would overwrite each other's records.
- **How to fix it:** Close the extra window. If no window is visible, restart the computer.

### `APP-INTEGRITY`
- **The doctor sees:** The app noticed that its records were changed outside the app, so only the practice admin can sign in until they have looked at it.
- **What it means:** (desktop app, in development) The app's records, its security log and this computer's tamper counter no longer agree: an older copy was put back by hand, the log was shortened, replaced or edited, the counter is missing or unreachable, or the records came from another computer.
- **How to fix it:** The practice admin signs in, reads the alert, checks the security log and the accounts, and acknowledges it. If nobody restored a backup, treat it as a possible break-in and keep a copy of the app's data folder before anything else.

### `BACKUP-FAILED`
- **The doctor sees:** The backup could not be made. Nothing was changed. Please choose another folder or drive and try again.
- **What it means:** (desktop app, in development) The chosen folder does not exist, is not writable, is inside the app's own folder, or the drive ran out of space.
- **How to fix it:** Choose a folder on an external or network drive the account can write to (not inside the app's own folder), with free space, and try again.

### `BACKUP-INVALID`
- **The doctor sees:** This backup could not be checked. Keep your other backups and make a fresh one.
- **What it means:** (desktop app, in development) The backup file is damaged, incomplete, edited, not a backup, or was made under a different practice root key.
- **How to fix it:** Make a new backup and check it right away. Keep backups on two separate drives. If no backup checks, the root key in use may not be the one that made them — find the practice's backed-up root key.

### `RESTORE-NOT-NEEDED`
- **The doctor sees:** The app's records are sound, so there is nothing to restore. Restoring is only for lost or damaged records, or a new computer.
- **What it means:** (desktop app, in development) A restore was asked for while the app's records (or the code table) open and pass their checks. Restoring over sound records would undo recent changes.
- **How to fix it:** Nothing to do. Never copy backup or data files back by hand: the app's tamper counter notices it and raises an integrity alert. If records look wrong, give this code to whoever helps you with the software.

### `RESTORE-EXPIRED`
- **The doctor sees:** The restore was not confirmed in time, or the backup file changed. Nothing was changed. Please start the restore again.
- **What it means:** (desktop app, in development) A restore must be confirmed within five minutes of its preview, once, with the same unchanged backup file.
- **How to fix it:** Start again from Restore from backup and confirm when the preview appears.

### `PHONE-CODE-NEEDED`
- **The doctor sees:** Enter the 6-digit code from the authenticator app on your phone.
- **What it means:** (desktop app, in development) This account has a phone linked, so sign-in and restore need its current code as well as the password.
- **How to fix it:** Open the authenticator app, find this app's entry and type the code shown. Lost the phone? An admin removes the phone link from your account; an admin who lost theirs uses the printed recovery code.

### `PHONE-LINK-EXPIRED`
- **The doctor sees:** Linking your phone timed out. Please start again.
- **What it means:** (desktop app, in development) The first code from the phone must be entered within ten minutes of starting.
- **How to fix it:** Start linking again, scan the new QR code, and enter the code the phone shows.

### `DEVICE-RETIRED`
- **The doctor sees:** This computer has been retired. Its work in progress was erased. Please use the practice's current computer.
- **What it means:** (desktop app, in development) An admin retired it: the work key was destroyed (so in-progress work is unreadable), then the work files and the app's records were deleted. The security log was kept.
- **How to fix it:** Nothing to recover on this computer. To use the app here again, restore from the practice's latest backup.

### `BATCH-NO-PHONE`
- **The doctor sees:** An admin on this batch needs their phone linked first. Admins can change everything in the app, so their accounts need a code from their own phone as well as a password.
- **What it means:** (desktop app, in development) An admin (starting the batch, or chosen as a reviewer) has no authenticator linked; the batch was refused before anything was read.
- **How to fix it:** Each person signs in and links their phone under My account (it takes a minute, and any authenticator app will do). Then start the batch again.

### `BATCH-NOT-CONFIRMED`
- **The doctor sees:** Please read and tick each point before the batch starts.
- **What it means:** (desktop app, in development) The points cover who may see these notes, naming anyone who helps, working in one sitting, and the practice's right to use these records. Each is recorded with the batch and printed in the review report, so the record shows what was agreed.
- **How to fix it:** Tick each point. If one of them is not true — someone else will be helping, or the screen is shared — put that right first, or name the helper once the batch starts.

### `REVIEW-NOTE-CONFIRMED`
- **The doctor sees:** You have already confirmed this note, so it cannot be changed.
- **What it means:** (desktop app, in development) A confirmed note is closed: changing answers afterwards would make the sign-off statement untrue.
- **How to fix it:** If something needs to change, tell the doctor settling the batch — they can send it back for another look.

### `BATCH-FOLDER-CHANGED`
- **The doctor sees:** The folder has changed since this batch was planned, so it was not read.
- **What it means:** (desktop app, in development) Files or patient folders were added or removed between planning the parts and reading them. Carrying on would mean a part covering different people than it said.
- **How to fix it:** Cancel this batch and start again from the folder as it stands now. Nothing in your own files was touched.

### `REVIEW-NOT-READY`
- **The doctor sees:** This batch is not ready for review yet.
- **What it means:** (desktop app, in development) It is still being prepared, or it has already gone to the doctor.
- **How to fix it:** The Home screen shows what each batch is waiting for.

### `REVIEW-NOTE-GONE`
- **The doctor sees:** That note is not part of this batch.
- **What it means:** (desktop app, in development) The batch was changed or erased while it was open on this screen.
- **How to fix it:** Go back to the batch and start again from the note it offers.

### `REVIEW-UNANSWERED`
- **The doctor sees:** Please answer every word the tool was unsure about before moving on.
- **What it means:** (desktop app, in development) A word the tool could not name is exactly where a person is needed, so each one is answered rather than left.
- **How to fix it:** Work down the unsure list: for each word say whether it is removed or left in, and what you think it is.

### `REVIEW-CERTAIN`
- **The doctor sees:** That one cannot be left in. It is a record number, a contact detail or a word under a patient label.
- **What it means:** (desktop app, in development) Identifiers of this kind are never a judgement call, so neither the screens nor the app allow them to be kept.
- **How to fix it:** If it is genuinely not an identifier, mark the note as 'removed too much' and the doctor will see it when settling the batch.

### `REVIEW-UNFINISHED`
- **The doctor sees:** There are still notes to work through in this batch.
- **What it means:** (desktop app, in development) Signing off says you read every note yourself, so it is offered only when each one is confirmed.
- **How to fix it:** Carry on from the note the screen offers. Your progress is saved as you go.

### `REVIEW-NOT-ATTESTED`
- **The doctor sees:** Please read and tick the statement before signing off.
- **What it means:** (desktop app, in development) The sign-off is what makes the determination a human one, and it is printed in the review report with your name.
- **How to fix it:** Read the statement and tick it, or go back and finish the notes first.

### `REVIEW-SIGNED`
- **The doctor sees:** You have already signed off this batch, so it cannot be changed.
- **What it means:** (desktop app, in development) A signed review is closed: changing it afterwards would make the review report untrue.
- **How to fix it:** If something needs to change, tell the doctor settling the batch; they can send it back for another look.

### `BATCH-FOLDER`
- **The doctor sees:** That folder could not be opened. Please choose the folder your files are in.
- **What it means:** (desktop app, in development) The path is not a folder on this computer, or this account cannot read it (it may be on a drive that is not connected).
- **How to fix it:** Choose the folder again. For a network or external drive, make sure it is connected and the person signed in can open it.

### `BATCH-EMPTY`
- **The doctor sees:** That folder has no files in it. Please choose the folder your documents are in.
- **What it means:** (desktop app, in development) The chosen folder is empty (sub-folders were looked in too).
- **How to fix it:** Choose the folder that holds the documents. One folder per patient is the usual arrangement; loose files are treated as one patient each.

### `BATCH-TOO-BIG`
- **The doctor sees:** That is more than the app takes in one batch. Please split it into smaller folders.
- **What it means:** (desktop app, in development) A batch is capped at 20,000 files and 20 GB so that a batch stays reviewable by two people and a doctor within the time allowed.
- **How to fix it:** Split the work into several folders — by month or by clinic, say — and run them as separate batches.

### `BATCH-NOTHING-READ`
- **The doctor sees:** Nothing in that folder could be read as a document, so there is nothing to review.
- **What it means:** (desktop app, in development) Every file was an image, a scan, an old-format document, a system file, or was set aside by the intake guard. The app reads typed Word, PDF, Excel, CSV and text files.
- **How to fix it:** Check the file types. Scans and photographs hold no typed text to read; save old .doc or .xls files as .docx or .xlsx. The batch's list shows every file and why it was not read.

### `BATCH-REVIEWERS`
- **The doctor sees:** A batch needs two different reviewers. If your practice works with three people, the doctor who signs it off cannot be one of them.
- **What it means:** (desktop app, in development) Two independent reviewers are what makes the determination a human one (and what the review report records). Settings › Review says whether the practice works with two people or three.
- **How to fix it:** Choose two different active people with the reviewer or doctor role. The practice admin can add a person in Settings › People.

### `BATCH-BUSY`
- **The doctor sees:** The app is still preparing another batch. Please wait for it to finish.
- **What it means:** (desktop app, in development) One batch is prepared at a time, so the work is predictable and the computer stays responsive.
- **How to fix it:** Wait for the batch on the Home screen to finish, then start the next one. A batch that was stopped finishes its current patient before it lets go.

### `BATCH-GONE`
- **The doctor sees:** That batch is no longer here. Its work was erased.
- **What it means:** (desktop app, in development) The batch was cancelled, signed off and exported, or its time ran out — in each case the app destroys the batch's key and deletes its working copies.
- **How to fix it:** The practice's own files were never touched: start the batch again from the same folder if the work still needs doing.

### `BATCH-UNREADABLE`
- **The doctor sees:** This batch's work could not be opened, so it cannot go on. Start it again from your files.
- **What it means:** (desktop app, in development) The key that opens this batch's working copies is gone or does not fit (the computer was retired, the records were restored from a backup made before the batch, or the protected key store was cleared).
- **How to fix it:** Cancel the batch and start it again from the same folder. Your own files are untouched; nothing of the patients' is lost.

### `BATCH-NO-EXTENSION`
- **The doctor sees:** This batch has already had its one extension, or its clock has not started yet.
- **What it means:** (desktop app, in development) Each batch may be extended once, after the reviewers have finished, so the extra time is a considered decision rather than a habit.
- **How to fix it:** Finish the sign-off within the time left. If the practice needs longer as a rule, the admin can raise the review time in Settings › Security (7 to 30 days).

### `BATCH-FAILED`
- **The doctor sees:** The app stopped while preparing this batch. Nothing was changed in your files.
- **What it means:** (desktop app, in development) An unexpected problem while reading the folder or running the engine. The batch is left where it stopped, with everything it had prepared.
- **How to fix it:** Choose Resume to carry on from where it stopped. If it stops again, cancel the batch and give this code to whoever helps you with the software.

### `BATCH-FILE-ENCODED`
- **The doctor sees:** One file holds a very long run of characters with no spaces, so it was set aside.
- **What it means:** (desktop app, in development) Writing has spaces in it. A run of thousands of characters without one is almost always an embedded image, encoded data or a damaged export rather than a note, and reading it as text would hold up the batch for no benefit.
- **How to fix it:** If the file really is a note, open it and save it again from the program that made it, then run the batch again. The batch's list names it.

### `BATCH-FILE-TOO-LONG`
- **The doctor sees:** One file holds more text than a single note should, so it was set aside.
- **What it means:** (desktop app, in development) A file with more than four million characters is treated as a set of records rather than one note; reviewing it as a single note would not be a real review.
- **How to fix it:** Split that file into one document per note or per encounter and run the batch again. The batch's list names it.

### `APP-SERVICE-STOPPED`
- **The doctor sees:** The app's background service stopped. Please close the app and open it again; your saved work is still here.
- **What it means:** (desktop app, in development) The part of the app that holds the records stopped answering, answered out of order, or did not answer within its time limit, so the window ended it.
- **How to fix it:** Close and reopen the app. If it happens again, restart the computer; if it keeps happening, give this code to whoever helps you with the software.

### `FEEDBACK-OPEN`
- **The doctor sees:** Your email program or browser could not be opened to send the feedback report.
- **What it means:** (desktop app, in development) Sending a report hands it to the computer's own email program or browser; none is set as the default, or this copy of the app was built without a feedback address.
- **How to fix it:** The report is already saved beside the released files: attach it to an email by hand, or set a default email program and try again.

### `APP-BAD-REQUEST`
- **The doctor sees:** Something went wrong inside the app. Please close it and open it again.
- **What it means:** (desktop app, in development) The screen sent the service a request it does not understand — a version mismatch or a damaged install.
- **How to fix it:** Reinstall the software from the project's official release so the screens and the service are the same version.

## A file the app could not use

### `FILE-UNREADABLE`
- **The doctor sees:** A file could not be opened and was set aside, not changed.
- **What it means:** The file could not be read from disk.
- **How to fix it:** Check the file is not open in another program and is not corrupt. Copy it somewhere local and try again. It was set aside, never altered.

### `FILE-PASSWORD`
- **The doctor sees:** A file is password-protected, so it was set aside without being opened.
- **What it means:** The file is encrypted or in the old binary Office format.
- **How to fix it:** Open it in Word/Excel, save an unprotected modern copy (.docx/.xlsx), and add that.

### `FILE-OLD-FORMAT`
- **The doctor sees:** A file is in an older format the app does not read; it was set aside.
- **What it means:** An old binary Office file (.doc/.xls) or similar.
- **How to fix it:** Save it as .docx, .xlsx or a text PDF and add that copy.

### `FILE-UNSUPPORTED`
- **The doctor sees:** A file is a type the app does not read; it was set aside, not exported.
- **What it means:** The file type is not one the tool processes (not Word, Excel, typed PDF, CSV or text).
- **How to fix it:** Convert it to a supported type if it holds typed text; otherwise it stays set aside, untouched, and is never exported.

### `FILE-IMAGE`
- **The doctor sees:** An image or scan was set aside — the app reads typed text only, so it does not read pictures.
- **What it means:** An image, scan, or fax PDF with no text layer.
- **How to fix it:** Expected behaviour: images are kept apart, flagged, and never exported. Reading scanned pages as text is not supported; OCR is used only to check typed PDF pages.

## Cleaning a document

### `DOC-PDF-SCANNED`
- **The doctor sees:** A PDF looks like a scan (mostly a picture); it was set aside, not read.
- **What it means:** A page is mostly image, so there is no reliable text to redact.
- **How to fix it:** Expected: scans are set aside. A typed PDF or the original document is needed.

### `DOC-PAGE-HELD`
- **The doctor sees:** A PDF page shows text the app could not read, so the file is held for a person to check.
- **What it means:** The page has text drawn as shapes, in a form picture, or too faint for OCR — the page-check caught a mismatch between what is shown and what was read.
- **How to fix it:** A person should open the held file and confirm what is on that page. This is a safety hold, not a failure.

### `DOC-STOP`
- **The doctor sees:** The app stopped on a file as a precaution and set it aside, rather than risk an incomplete clean. Your other files carried on. You can try that file again in a new batch; if it stops again, share the run's support log — never the file — with whoever helps you with the software.
- **What it means:** The engine declined the file (fail-closed). The file was set aside, never passed on.
- **How to fix it:** 1) Run that one file again in a new batch. 2) If it stops again, it is usually the file's content or formatting: save a fresh copy (Word: Save As .docx) and try that. 3) If several files stop, collect the support log (codes and counts only) and share it with whoever helps you with the software, or with the project maintainers. Never send the file itself.

## The practice batch

### `BATCH-NOT-CERTIFIED`
- **The doctor sees:** One of the people chosen as a reviewer has not passed the practice batch, or their badge has run out. They can do it now on their own account; it takes about half an hour and uses invented notes.
- **What it means:** (desktop app, in development) A reviewer had no current practice badge. Nobody's first look at a real patient's notes is also their first look at this tool.
- **How to fix it:** Ask that person to sign in and open "Practice batch" from their home screen. Passing records the badge against their account; it lasts a year and the date is printed on every review report they sign.

### `PRACTICE-NOT-PASSED`
- **The doctor sees:** That attempt did not pass, and the screen says why. Nothing is held against anyone — it can be done again straight away.
- **What it means:** (desktop app, in development) The attempt failed its marking (an identifier would have been released, a word was left unanswered, or the planted identifiers were not found).
- **How to fix it:** Read the reasons on the screen and try again. The notes are invented, so there is nothing to lose by practising.

## The tidy-up tool

### `TIDY-STILL-IDENTIFIED`
- **The doctor sees:** These files still look identified, so they were not reorganized. A tidy folder must never be mistaken for a redacted one. Run them through a batch first.
- **What it means:** (desktop app, in development) The check found identifiers in at least one file; the tool refused to reorder them.
- **How to fix it:** The screen says how many files and what kind of thing was found. Either redact them with a batch, or take the identified files out of the folder and tidy the rest.

### `TIDY-CANNOT-CHECK`
- **The doctor sees:** Some of those files cannot be read by this tool, so it will not reorder the folder. A file nobody has checked must never be filed as if it had been.
- **What it means:** (desktop app, in development) At least one file had no readable text (a picture, a scan with no text layer, an archive) or would not open. Unchecked files stop the folder, by design.
- **How to fix it:** Take those files out of the folder and tidy the rest, or put them through a batch first — a scanned page needs the page check, not this tool.

### `TIDY-UNCHECKABLE`
- **The doctor sees:** This tool cannot read that kind of file.
- **What it means:** (desktop app, in development) The file is not a text or office document, so there is nothing to check.
- **How to fix it:** Only text files and Word, Excel, PDF and CSV files can be checked here.

### `TIDY-FOLDER`
- **The doctor sees:** That folder could not be opened.
- **What it means:** (desktop app, in development) The path is not a folder, or the account may not read it.
- **How to fix it:** Choose a folder on this computer the user can open.

### `TIDY-EMPTY`
- **The doctor sees:** There are no files in that folder to reorganize.
- **What it means:** (desktop app, in development) The folder held no readable files.
- **How to fix it:** Choose the folder the files are actually in; the tool looks in subfolders too.

### `TIDY-TOO-BIG`
- **The doctor sees:** That folder holds more files than the tidy-up tool will do at once.
- **What it means:** (desktop app, in development) The folder exceeded the file limit; refused rather than half-done.
- **How to fix it:** Split it into smaller folders and tidy them one at a time.

### `TIDY-ORDER`
- **The doctor sees:** That order is not one the tool offers.
- **What it means:** (desktop app, in development) An unknown order was requested.
- **How to fix it:** Choose a random order, or order by file name.

### `TIDY-OVERLAP`
- **The doctor sees:** Please choose a folder for the tidied copies that is not inside the folder they come from.
- **What it means:** (desktop app, in development) Input and output overlap, which would mix the copies with the originals.
- **How to fix it:** Pick a separate folder, for example beside it rather than inside it.

### `TIDY-WRITE`
- **The doctor sees:** The tidied copies could not be written. Your own files were not changed.
- **What it means:** (desktop app, in development) Writing to the chosen folder failed.
- **How to fix it:** Check free space and that the account may write there, then try again.

## Finishing a batch

### `EXPORT-NO-SHAPE`
- **The doctor sees:** Choose at least one shape for the finished files before exporting.
- **What it means:** (desktop app, in development) No export shape was selected.
- **How to fix it:** Tick Research copy, Office copy or Plain text copy — or all three.

### `EXPORT-ARRANGEMENT`
- **The doctor sees:** That arrangement is not one the app offers.
- **What it means:** (desktop app, in development) An unknown arrangement was requested.
- **How to fix it:** Choose shuffled, grouped by patient, or the original arrangement.

### `EXPORT-RULING`
- **The doctor sees:** A small-numbers warning was answered with something the app does not recognise.
- **What it means:** (desktop app, in development) An unknown ruling was sent for a small-numbers warning.
- **How to fix it:** For each warning choose generalize, hold the record back, or accept it on the record.

### `EXPORT-NOTHING`
- **The doctor sees:** Nothing in this batch came through ready to release, so there is nothing to export. The batch and its report are unchanged.
- **What it means:** (desktop app, in development) Every note was blocked, held or withheld.
- **How to fix it:** Open the batch and look at the held and set-aside lists with their reasons. Files set aside were never read; files held still showed the shape of an identifier.

### `EXPORT-EMPTY`
- **The doctor sees:** One of the chosen shapes came out with no files in it, so nothing was exported and nothing was destroyed.
- **What it means:** (desktop app, in development) A shape's folder was empty after building; export stopped before the pool was emptied.
- **How to fix it:** Try the export again. If it happens again, choose a different shape and share the batch's support codes — never the files — with whoever helps you with the software.

### `EXPORT-WRITE`
- **The doctor sees:** The finished files could not be written, so nothing was exported and nothing was destroyed. Your batch is still here.
- **What it means:** (desktop app, in development) Writing a shape failed; export stopped before the pool was emptied.
- **How to fix it:** Check free space and that the account may write to the chosen folder, then export again. Choose a local folder if it is on a network or removable drive.

### `EXPORT-VERIFY`
- **The doctor sees:** A finished zip did not read back correctly, so it was deleted and nothing was destroyed. Your batch is still here.
- **What it means:** (desktop app, in development) A zip failed its read-back check (missing file or wrong size).
- **How to fix it:** Export again, preferably to a local disk. If it fails twice, the disk or the anti-virus software is interfering with writes.

### `EXPORT-LOCKED`
- **The doctor sees:** That cannot be left in: both reviewers removed it, or the tool is certain of it. Nobody can put it back, and that is on purpose.
- **What it means:** (desktop app, in development) A ruling tried to keep an item marked locked (both reviewers removed it, or the detection is a certain one).
- **How to fix it:** If it truly is not patient information, the batch has to be reviewed again. The clinician cannot overrule two reviewers and a certain detection on their own.

### `EXPORT-NOT-ATTESTED`
- **The doctor sees:** Read and sign the statement before the files are released.
- **What it means:** (desktop app, in development) Export was attempted without the clinician's attestation.
- **How to fix it:** Tick the statement on the sign-off screen. It is printed on the review report.

### `EXPORT-SMALL-NUMBERS`
- **The doctor sees:** There are small-numbers warnings nobody has answered yet, so nothing was exported.
- **What it means:** (desktop app, in development) An odd-one-out warning had no ruling (generalize, hold back, or accept).
- **How to fix it:** Open the small-numbers check and answer each warning. Each answer is printed on the review report.

### `EXPORT-REPORT`
- **The doctor sees:** The review report for this batch could not be built, so nothing was exported and nothing was destroyed. Your batch is still here.
- **What it means:** (desktop app, in development) Report rendering failed; export stopped before the pool was emptied. The report is always built once before anything is destroyed, exactly so this is survivable.
- **How to fix it:** Export again. If it fails again, share the batch's support codes — never the files — with whoever helps you with the software; the batch can be exported once the report is fixed.

### `EXPORT-NOT-SETTLED`
- **The doctor sees:** Something in this batch has not been decided yet, so it cannot be exported.
- **What it means:** (desktop app, in development) Export was attempted while items were still undecided or a sign-off was missing.
- **How to fix it:** Both reviewers must sign off, and the clinician must decide every item they disagreed on and every small-numbers warning.

## Finishing a run

### `RUN-SUMMARY`
- **The doctor sees:** 
- **What it means:** Not a problem — the normal end-of-run line: how many files were found, cleaned, held, set aside, failed, and refused by the guard. Counts only, no names.
- **How to fix it:** Use it to see the shape of a run at a glance. If 'failed' or 'guard_refused' is high, look for the matching codes in the same log.

### `RUN-INPUT-MISSING`
- **The doctor sees:** The input folder was not found.
- **What it means:** The folder given as input does not exist, or the path is misspelt.
- **How to fix it:** Check the path and run again.

### `RUN-CANCELLED`
- **The doctor sees:** Stopped by you. Nothing is finished.
- **What it means:** The run was interrupted (Ctrl-C or the window was closed) before it finished.
- **How to fix it:** Run again into the same results folder: what was written is deleted and the run starts over from your originals, which were only read.

### `RUN-OUTPUT-WRITE`
- **The doctor sees:** The clean copies could not be written to the chosen folder.
- **What it means:** Writing to the output folder failed.
- **How to fix it:** Check there is free disk space and that the account may write to that folder. Choose a local folder if it is on a network or removable drive.

### `RUN-DISK-FULL`
- **The doctor sees:** There is not enough free space to write the results.
- **What it means:** The disk holding the output folder is full.
- **How to fix it:** Free up space or choose a drive with room, then run again.

### `RUN-PERMISSION`
- **The doctor sees:** The app is not allowed to write to the chosen folder.
- **What it means:** The account lacks write permission on the output folder.
- **How to fix it:** Pick a folder the user owns (for example under their Documents), or grant write permission.

### `RUN-FOLDER-OVERLAP`
- **The doctor sees:** The output folder is inside the input folder. Please choose a separate output folder.
- **What it means:** Input and output overlap, which would mix originals with clean copies.
- **How to fix it:** Choose an output folder that is not the input folder and not inside it.

### `RUN-RESUME-SETTINGS`
- **The doctor sees:** This output folder was made with different settings, so the run did not start. Your files were not touched.
- **What it means:** --resume was given, but the profile, name mode or date shifting differs from the run that made this folder; mixing them would label files one way and clean them another.
- **How to fix it:** Run again with the same settings as before, or choose a new, empty output folder.

### `RUN-INPUT-CHANGED`
- **The doctor sees:** A file in the input folder changed while the app was working; please run again.
- **What it means:** The input folder was edited mid-run, so the count no longer matches.
- **How to fix it:** Make sure nothing else writes to the input folder during a run (sync clients, the user). Run again on a settled folder.

### `RUN-PSEUDONYM-SETUP`
- **The doctor sees:** Patient codes could not be set up, so the run did not start. Your files were not touched.
- **What it means:** Pseudonym mode needs two things kept together: the practice's persistent key and its code table. One is missing, unreadable, damaged, or does not belong with the other.
- **How to fix it:** Restore the key and the code table together from the practice's backup (never one without the other), or set the practice's chosen code-table location. Never edit the code table by hand — a changed table is refused on purpose. To run without codes, choose the default [NAME] mode.

### `RUN-UNACCOUNTED`
- **The doctor sees:** The app could not account for every file and held the result for checking.
- **What it means:** An output was missing, or two outputs collided, when the run was verified.
- **How to fix it:** Run again. If it persists, share the log and the run's report so the accounting can be looked at; no file is ever dropped silently.

## The review workflow

### `REVIEW-CERTAIN-KEPT`
- **The doctor sees:** A fresh review is needed before release.
- **What it means:** (review workflow, in development) A reviewer's file kept an identifier the tool marks as certain, which the page does not allow — the file was changed outside the app.
- **How to fix it:** Have the reviewer redo the review inside the app and save again. Do not edit the saved review file by hand.

### `REVIEW-FILE-MISMATCH`
- **The doctor sees:** A fresh review is needed before release.
- **What it means:** (review workflow, in development) A review file does not match the file its page saved (edited outside the app, unsealed, or saved by the other reviewer's page).
- **How to fix it:** Re-open the correct page and save the review again from it. Each page accepts only the file it produced.

### `REVIEW-RUSHED`
- **The doctor sees:** A fresh review is needed before release.
- **What it means:** (review workflow, in development) More than a quarter of a reviewer's notes were confirmed faster than they could be read.
- **How to fix it:** Ask the reviewer to go through the notes again at a normal reading pace, then save.

## The intake guard (`ZC-…`)

These appear in `intake_guard_log.md`. The guard refuses a file that is not what its
name claims, or that carries something that runs or launches, before the app opens it.
Each refusal's message already says what to do (save an unprotected copy, unpack the
archive, open an attachment separately, and so on). A file the guard refuses is
never opened and never exported.
