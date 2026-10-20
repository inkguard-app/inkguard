# Security controls matrix

> Applies to the desktop app, in development and not part of 0.25.0. Version
> 0.25.0 is the command line and a small window; it writes ordinary, unencrypted
> files that you keep on an encrypted disk.

**As of release 0.25.0.** Each control the desktop app's secure core
implements, the standard it follows, where it lives, and the test that fails
if it breaks. Written by the project for anyone assessing the desktop app's
design; it has not been independently reviewed, and it is **not** a
certification of compliance with any standard or law.

## Sign-in and passwords — NIST SP 800-63B-4 (final, Aug 2025)

| Requirement | How the app meets it | Where | Proven by |
|---|---|---|---|
| Single-factor passwords at least 15 characters | `MIN_PASSWORD = 15` | `inkguard/opal.py` | `test_nist_password_rules` |
| Allow at least 64 characters | up to 256 | `inkguard/opal.py` | `test_nist_allows_long_passphrases_and_no_composition_rules` |
| No composition rules | none imposed | `inkguard/opal.py` | same |
| Blocklist: breached / common passwords | Not shipped in 0.25.0: public breach lists carry real people's identifiers. A hashed list is planned. | `inkguard/opal.py` (`_blocked`) | — |
| Blocklist: context-specific words | the person's name, the practice's name, the app's name (words of 4+ characters) | `inkguard/opal.py` | `test_nist_password_rules` |
| Blocklist: repetitive or sequential strings | repeated units, straight runs, three or fewer distinct characters (symbol-only passphrases are allowed) | `inkguard/opal.py` | `test_nist_password_rules`, `test_a_symbol_passphrase_is_allowed_and_a_reset_clears_the_pin` |
| At most 100 consecutive failures, then disable | a password is disabled at 100 misses in a row — counting sign-in and password re-entry inside a session; admin reset or the admin recovery code re-enables it; unknown names behave the same; a flood of made-up names cannot reset a real counter | `inkguard/opal.py` | `test_one_hundred_consecutive_misses_disable_the_password`, `test_a_disabled_admin_password_is_recovered_with_the_recovery_code`, `test_reentering_a_password_inside_a_session_is_limited_and_logged`, `test_a_flood_of_made_up_names_cannot_reset_a_real_counter` |
| Rate limiting (throttling) | per-name pauses from 30 s growing to 15 min; survive a restart; within a running session cannot be skipped by moving the clock (a restart combined with changing the computer's clock — which needs admin rights on the computer — can shorten a pause) | `inkguard/opal.py` | `test_guessing_is_paused_and_the_pause_survives_a_restart`, `test_a_pause_cannot_be_skipped_by_moving_the_wall_clock` |
| No periodic forced changes; force a change on evidence of compromise | no expiry; admin reset clears the password, PIN and pause | `inkguard/opal.py` | `test_one_hundred_consecutive_misses_disable_the_password`, `test_a_symbol_passphrase_is_allowed_and_a_reset_clears_the_pin` |
| Multi-factor (optional per account): something you have | an authenticator app on the person's phone (RFC 6238 one-time codes, works offline); once linked, sign-in and restore need its current code; a used code can never be replayed; wrong codes count toward the same guess limits; moving to a new phone needs the current phone's code; a lost phone is removed by an admin or by the admin's recovery code | `inkguard/opal.py` | `test_rfc6238_test_vector`, `test_a_linked_phone_is_needed_to_sign_in_and_codes_cannot_be_replayed`, `test_a_wrong_phone_code_counts_toward_the_same_guess_limits`, `test_moving_to_a_new_phone_needs_the_current_phones_code`, `test_a_lost_phone_is_handled_by_the_recovery_code_or_an_admin` |
| Do not reveal which account names exist | identical answers for unknown names, and the same hashing cost (equal timing, measured at production settings) | `inkguard/opal.py` | `test_wrong_name_and_wrong_password_look_the_same`, `test_unknown_names_are_paused_exactly_like_real_ones` |

## Password storage — OWASP Password Storage Cheat Sheet

| Requirement | How the app meets it | Where | Proven by |
|---|---|---|---|
| scrypt N=2^17, r=8, p=1 minimum | exactly these | `inkguard/opal.py` | `test_production_hashing_meets_owasp` |
| Store the algorithm and parameters with the hash | each hash records alg, N, r, p, salt | `inkguard/opal.py` | same |
| Upgrade the work factor at next use | passwords at the next sign-in, PINs at the next unlock; recovery codes are replaced on use | `inkguard/opal.py` | `test_an_older_hash_is_upgraded_at_the_next_sign_in` |
| Unique salt per hash | 16 random bytes | `inkguard/opal.py` | (by construction) |

## HIPAA Security Rule technical safeguards — 45 CFR 164.312

| Safeguard | How the app supports it | Proven by |
|---|---|---|
| (a)(2)(i) Unique user identification (Required) | every person gets their own local account with a unique name (sharing a login is a practice-policy matter the app cannot prevent) | `test_roles_are_enforced` |
| (a)(2)(ii) Emergency access procedure (Required) | a recovery passphrase chosen by the admin at setup (or a generated 96-bit code), shown back to be written on paper and kept in a safe place (not photographed: phone photos sync to the cloud), replaced on use; rate-limited but never disabled, so guessing cannot lock the admin out | `test_the_recovery_code_gets_the_admin_back_in_while_someone_keeps_guessing`, `test_guessing_recovery_codes_never_locks_out_the_admin`, `test_the_admin_may_choose_their_own_recovery_passphrase` |
| (a)(2)(iii) Automatic logoff (Addressable) | service-enforced idle lock (1–15 min, default 5) on two clocks; sleep and clock changes lock | `test_idle_lock_refuses_work_until_unlocked`, `test_a_sleeping_laptop_wakes_locked`, `test_status_polling_never_keeps_a_session_alive_but_real_actions_do` |
| (a)(2)(iv) Encryption and decryption (Addressable) | app records AES-GCM under a key derived from the practice root key; backups AES-GCM; patient files never leave the computer | `test_store_holds_no_readable_names_or_passwords`, `test_backup_and_restore_drill` |
| (b) Audit controls | hash-chained security log of sign-ins and failures (including password re-entry), locks and unlocks, role denials, account changes, backups; owner-readable only (tested on macOS/Linux; Windows to be verified in the desktop build); no patient data, passwords or keys | `test_security_log_is_private_and_holds_no_secrets`, `test_role_denials_are_logged` |
| (c)(1) Integrity | sealed records; every security event is saved with the records, so the two vouch for each other; a sealed **tamper counter** in the operating system's protected store (outside the app's folder) moves forward with every save, so records and log put back together from an old copy, a deleted or edited counter, or records copied from another computer raise the alert; (only the final "records saved" line of the log is itself unprotected, and it carries no event) | `test_restoring_an_older_copy_of_the_records_raises_an_alert`, `test_a_deleted_cut_or_edited_security_log_raises_an_alert`, `test_cutting_recent_security_events_from_the_log_raises_an_alert`, `test_putting_back_records_and_log_together_is_caught`, `test_a_missing_edited_or_foreign_counter_is_caught` |
| (d) Person or entity authentication | passwords per NIST above, plus an optional phone code (authenticator app); PIN only unlocks a live session | `test_a_pin_unlocks_but_weak_pins_are_refused`, `test_a_linked_phone_is_needed_to_sign_in_and_codes_cannot_be_replayed` |

## HIPAA contingency plan — 45 CFR 164.308(a)(7)

| Specification | How the app supports it | Proven by |
|---|---|---|
| (ii)(A) Data backup plan (Required) | one encrypted backup file of the records, the security log and — when pseudonym mode is in use — the code table, written outside the app's folder; the backup says plainly when no code table is in use. The practice root key is NOT in the backup: keeping it in two safe places is a written procedure, not an app feature | `test_backup_and_restore_drill`, `test_backup_says_plainly_when_no_code_table_is_in_use` |
| (ii)(D) Testing and revision (Addressable) | the restore drill opens a backup in a scratch folder and verifies every file, the key, the log chain, their agreement and the code table — without changing any live records (it only adds a "backup verified" line to the security log) | `test_backup_and_restore_drill`, `test_a_damaged_or_foreign_backup_fails_the_drill` |
| (ii)(B) Disaster recovery plan (Required) | **one official restore door**, open only when the records are lost or damaged, or on a new computer — never over sound records. The person restoring proves they were an admin in the backup (name, password as it was then, phone code if linked); they see a preview and confirm within five minutes, once, with the same unchanged file. Damaged records are set aside, never deleted; the replaced security log is kept beside the restored one as evidence; account bans and password and phone changes recorded after the backup are carried forward. A lost code table alone is restored by an admin only while it is actually missing or damaged | `test_lost_records_come_back_through_the_official_door`, `test_a_new_computer_is_set_up_from_the_backup`, `test_a_damaged_records_file_is_set_aside_and_restored`, `test_the_restore_door_is_shut_while_the_records_are_sound`, `test_a_restore_must_be_confirmed_in_time_once_with_the_same_file`, `test_a_restore_never_undoes_security_changes_made_after_the_backup`, `test_without_a_backup_starting_over_is_allowed_and_flagged`, `test_a_backup_made_under_another_root_key_is_refused`, `test_a_lost_code_table_is_restored_only_when_it_is_actually_lost` |

## Retiring a computer — NIST SP 800-88 (media sanitization)

| Control | How the app meets it | Proven by |
|---|---|---|
| Cryptographic erase | work in progress is encrypted under a key held only in the OS protected store (never in records, logs or backups); the records' key is split, with this computer's half also only there. Retiring destroys both halves first, then deletes the work files, the records and a code table kept in the app's folder; the security log stays (no patient data or secrets) and records the retirement, honestly reporting any part that failed | `test_work_is_encrypted_under_a_key_kept_outside_the_app_folder`, `test_retire_this_computer_destroys_the_key_then_the_files`, `test_records_copied_back_after_a_retire_can_never_be_opened` |
| Who can retire | on the computer: an admin's password, phone code (if linked) and the word RETIRE; at the sign-in screen: the admin's recovery passphrase and phone code (the passphrase alone if no phone is linked) | `test_only_an_admin_with_the_phone_code_can_retire_it`, `test_a_computer_nobody_can_sign_into_is_retired_with_recovery_and_phone_codes` |
| Cloned or copied-back records | records copied to another computer, or back after a retire, cannot be opened (their key half is not there); they are set aside and only a restore from a backup brings the practice back | `test_a_missing_edited_or_foreign_counter_is_caught`, `test_records_copied_back_after_a_retire_can_never_be_opened` |

## Releasing a batch — the order of operations

| Control | How the app meets it | Proven by |
|---|---|---|
| Nothing is destroyed before the output is verified | the order is write, read back, verify, erase, report, zip. Every shape is read off the disk and checked (the right number of records, the agreed words present, none of the removed words showing) and the review report is proved to render, all before the pool's key is destroyed. A failure at any of those steps erases nothing and hands the batch back to the clinician | `test_nothing_is_destroyed_until_every_shape_has_been_read_back`, `test_a_shape_short_of_a_record_is_caught_before_the_erase`, `test_an_export_that_cannot_be_written_destroys_nothing`, `test_nothing_is_destroyed_when_a_shape_cannot_be_written` |
| After the erase nothing can lose the files | each remaining step is attempted independently; a zip that fails leaves that shape's folder in place with its report inside rather than undoing anything | `test_export_writes_every_chosen_shape_as_a_verified_zip` |
| Crypto-erase, key first | the pool's key is destroyed before the files it protected are deleted (NIST SP 800-88), and the closing count ("still inside: 0") is printed on the report and recorded in the security log | `test_a_finished_export_leaves_zips_and_empties_the_pool` |
| An interrupted release cannot strand a batch | a power cut mid-release leaves a stage with no thread behind it; the next start finds it, hands the batch back to the clinician and clears the signature, because the release it signed did not happen | `test_an_export_the_app_did_not_live_through_goes_back_to_the_clinician` |
| A release in flight cannot be erased under itself | cancelling a batch being released is refused, and the time limit does not expire it mid-release | `test_a_batch_being_exported_cannot_be_cancelled_under_the_export`, `test_a_batch_being_exported_is_not_erased_when_its_time_runs_out` |
| What leaves holds only the agreed words | one merge decides the text and every shape carries it; the Office copy re-cleans the practice's own file (so it can only remove more) and is then read back and checked for the very words the review removed — one showing and that file is held, not released | `test_a_file_that_still_shows_a_removed_word_is_held_not_released`, `test_every_shape_holds_the_same_words` |
| A word returns to a page only by full agreement | the engine must have raised it as uncertain and both reviewers must have said to leave it in and the clinician must agree; an "I'm not sure" is not agreement; kept words are that record's own, never the batch's | `test_an_unsure_reviewer_is_not_an_agreement_to_leave_a_word_in`, `test_each_file_is_cleaned_with_only_its_own_records_kept_words`, `test_a_certain_removal_cannot_be_kept_by_anyone` |
| File and folder names are treated as data | every path component of an Office copy goes through the engine; the shape of the filing is kept and any name holding an identifier becomes that record's code | `test_every_shape_holds_the_same_words` (asserts no patient name in any path) |
| The tidy-up tool cannot imply de-identification | it refuses a folder holding anything identifying, and equally any file it cannot read; what it writes states that nothing was removed and that it is not evidence of de-identification | `test_one_identified_file_stops_the_whole_folder`, `test_the_note_says_nothing_was_removed`, `test_a_folder_that_changed_after_the_check_is_caught` |

## Who may open patient work

| Control | How the app meets it | Proven by |
|---|---|---|
| A second factor where it matters most | admins must have an authenticator linked before they can start a batch (they can change everything in the app); for everyone else it is optional, and anyone who has linked one is asked for its code when coming back to open patient work | `test_phones_are_required_of_admins_and_optional_for_everyone_else`, `test_a_locked_pool_reopens_with_the_password_for_someone_with_no_phone` |
| Competence before real notes | a reviewer must hold a current practice-batch badge (12 months); an expired badge is not a badge | `tests/test_practice.py`, `test_a_reviewer_must_pass_the_practice_batch_before_a_real_one` |
| A supported platform | Windows 10 22H2 and macOS 13 are the floor, stated in one place in the code; an older system is named on the first screen with its support code instead of failing later; an unfamiliar system is allowed through and named | `test_an_operating_system_older_than_the_app_supports_is_said_plainly`, `test_the_first_screen_stops_on_an_unsupported_system` |
| A reviewer may correct their own work, on the record | a confirmed note is sealed against accident but can be taken back deliberately before sign-off; every reopening is counted on the decision, on the batch and in the security log; after sign-off nothing can be changed | `test_a_reviewer_can_go_back_to_an_earlier_note_before_signing_off`, `test_once_they_have_signed_off_nothing_can_be_changed` |
| The tidy-up tool claims nothing and needs nothing | before setup it runs with no account — and refuses a folder holding anything identifying or any file it cannot read | `test_the_free_tool_works_before_anything_is_set_up`, `test_before_setup_it_still_refuses_identified_files`, `test_once_there_are_accounts_it_needs_a_sign_in`, `test_the_free_tool_refuses_files_that_still_look_identified` |

## What leaves the computer

| Control | How the app meets it | Proven by |
|---|---|---|
| The app has no way to send anything | no network code anywhere in what ships: no HTTP client, no socket, no mail — checked by reading every module's imports; the finished files are written to a folder the user chooses and nowhere else; feedback is saved as a file, and if the user chooses to send it, the desktop wrapper opens THEIR email program or browser with the checked text filled in, to one of two addresses fixed when the app is built; the user sends it there, or not | `test_nothing_that_ships_imports_a_network_module`, `test_the_check_itself_would_catch_one`, `test_the_doctor_is_asked_for_feedback_and_the_report_carries_no_patient_text`, `only_the_two_fixed_destinations_exist` (Rust) |
| Patient words are hard to take off the screen | inside the reading room copy, cut, print, save, right-click and drag are refused and the note is not selectable; losing the window's focus veils the notes; every refusal and every loss of focus is counted on the batch's record. It cannot stop a camera and does not claim to | `test_copying_the_note_is_refused_and_recorded`, `test_a_blocked_copy_or_a_lost_window_is_counted_never_scolded` |
| Stepping away is a break, not a hole | a break veils the notes and keeps their place; their own password brings it back; past the grace the ordinary idle lock applies (pool closed, phone code needed) | `test_a_break_covers_the_screen_and_their_password_brings_it_back`, `test_a_long_break_becomes_the_real_lock` |
| Leaving part way is recorded, and loses nothing | the emergency exit closes the pool, ends the session in the same call and records how far they had got; their answers are already saved | `test_leaving_part_way_closes_the_pool_signs_them_out_and_says_so`, `test_the_emergency_exit_closes_the_notes_and_signs_them_out` |

## Robustness

| Control | Proven by |
|---|---|
| Hostile input on the screen-to-service pipe (garbage, deep nesting, NaN, oversized lines) answered with a support code, never a crash or traceback | `test_the_pipe_protocol_survives_hostile_input` |
| One running copy at a time | `test_only_one_copy_can_run_at_a_time` |
| A crash mid-write to the security log (a torn last line) is recognised, not mistaken for tampering; the records file is replaced atomically | `test_a_crash_mid_write_is_not_mistaken_for_tampering` |

## Known limits (stated, not hidden)

- Someone with **admin rights on the computer and the practice root key** can
  wipe the records, the security log and the tamper counter together and set
  the app up again. They cannot read what was there: the old records' key
  half is gone with the counter. The wiped history survives in the
  practice's off-computer backups.
- The app works offline, so it cannot switch off an old computer remotely;
  retire it before it leaves the practice, and use full-disk encryption.
- If the computer's protected store (keychain) cannot be reached, the app
  does not open its records at all (APP-STORE) and changes nothing —
  deliberately failing closed.
- Restoring an older code table means people coded after that backup get new
  codes from then on (the practice accepts this knowingly at restore).
- The pipe protocol trusts the local screens to send real user activity; the
  screens' integrity comes from code signing of the desktop build.
- A restart combined with changing the computer's clock can shorten a
  sign-in pause (both need admin rights on the computer).
- Phone codes are required of admins; for everyone else they are optional.
  An account without one is protected by its password and the idle lock only.
