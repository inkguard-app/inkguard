// Screens before anyone is signed in: first-run setup, lost records,
// restore from backup, sign-in (with phone code), the recovery passphrase,
// and retiring a computer nobody can sign into.

import { call, ServiceError } from "./bridge.js";
import { tidyOnly } from "./tidy.js";
import {
  action, brand, errorBox, field, h, input, mount, password, passwordMeter, pathField,
  slot, stepBar, when,
} from "./ui.js";

const screen = (...kids) => h("div", { class: "center-screen" }, h("div", { class: "panel card" }, brand(), ...kids));
const buttons = (...kids) => h("div", { class: "row row-end mt" }, ...kids);
const back = (label, fn) => h("button", { class: "btn btn-quiet", type: "button", onclick: fn }, label);

// ---- the recovery passphrase, shown back so it can be written down -----------
// Shown large, once, with a plain instruction and a tick box: the person in
// charge chose it, so the app's only job here is to make sure it is kept.
function keepItScreen(root, { steps, index, phrase, title, intro, next, nextLabel, onBack }) {
  const kept = h("input", { type: "checkbox", id: "kept" });
  const go = h("button", { class: "btn btn-primary", type: "button", disabled: true, onclick: next },
    nextLabel || "Continue");
  kept.addEventListener("change", () => { go.disabled = !kept.checked; });
  mount(root, screen(
    steps ? stepBar(steps, index) : null,
    h("h1", {}, title || "Write it down"),
    h("p", { class: "muted" }, intro || "This is your recovery passphrase. It gets you back in if you "
      + "forget your password or lose your phone. Write it down now and keep it somewhere "
      + "private, like the practice's safe."),
    h("div", { class: "kit" },
      h("div", { class: "small muted" }, "Recovery passphrase"),
      h("div", { class: "bigcode long", "aria-label": "Recovery passphrase" }, phrase),
      h("p", { class: "small muted mt" }, "Anyone who has it can get into the admin account, so keep "
        + "it as carefully as a key. Using it means choosing a new one, and every use is recorded "
        + "under your name.")),
    h("label", { class: "check", for: "kept" }, kept,
      h("span", {}, "I have written it down and put it somewhere safe.")),
    buttons(onBack ? back("Back", onBack) : null, go)));
}

// ---- first-run setup -----------------------------------------------------------------
// An operating system older than the app supports. Said on the first screen,
// in plain words, with the one thing that would fix it — and nothing else,
// because nothing else here will work.
export function unsupportedOS(root, status) {
  mount(root, screen(
    h("h1", {}, "This computer's system is too old for the app"),
    h("p", {}, `This computer is running ${status.os || "an older system"}. The app needs `
      + `${(status.needs || {}).windows || "Windows 10 22H2 or newer"} or `
      + `${(status.needs || {}).macos || "macOS 13 Ventura or newer"}.`),
    h("p", { class: "muted" }, "On an older system the parts this app relies on \u2014 the "
      + "keychain it keeps its keys in, and the window it draws these screens in \u2014 are "
      + "either missing or no longer getting security fixes. It will not run here rather than "
      + "half-run."),
    h("p", { class: "small muted" }, "Support code: " + (status.code || "APP-OS-UNSUPPORTED"))));
}


export function setupWizard(root, { status, done, onRestoreInstead }) {
  const steps = ["Your practice", "Your account", "Recovery passphrase", "Write it down"];
  // What has been typed so far, kept when going Back and forward again.
  const v = { practice: "", name: "", pw: "", pw2: "", rec: "", rec2: "", attest: false };

  function practice() {
    const s = slot();
    const org = input({ autocomplete: "organization", value: v.practice });
    mount(root, screen(
      stepBar(steps, 0),
      h("h1", {}, "Set up this computer"),
      h("p", { class: "muted" }, "This takes about three minutes. Everything stays on this computer: "
        + "there is no account to create anywhere else, and the app works with the Wi-Fi off."),
      field("Your practice, lab or team", org, "Printed on every review report this app makes."),
      st0.tidy_available
        ? h("p", { class: "small muted" },
            "Only want to reorder files you already treat as cleaned? The tidy-up tool "
            + "needs no setup.")
        : null,
      s,
      buttons(
        onRestoreInstead ? back("Restore from a backup instead", onRestoreInstead) : null,
        st0.tidy_available
          ? h("button", { class: "btn", type: "button",
                          onclick: () => tidyOnly(root, { back: practice }) }, "Tidy up a folder")
          : null,
        action("Next", async () => {
          v.practice = org.value.trim();
          if (!v.practice) throw new ServiceError("APP-BAD-REQUEST", "Please enter a name for your practice, lab or team.");
          account();
        }, { primary: true, slot: s }))));
  }
  const st0 = status;

  function account() {
    const s = slot();
    const name = input({ autocomplete: "name", value: v.name });
    const pw = password(); pw.value = v.pw;
    const pw2 = password(); pw2.value = v.pw2;
    const attest = h("input", { type: "checkbox", id: "attest" });
    attest.checked = v.attest;
    const keep = () => { v.name = name.value; v.pw = pw.value; v.pw2 = pw2.value; v.attest = attest.checked; };
    mount(root, screen(
      stepBar(steps, 1),
      h("h1", {}, "Your account"),
      h("p", { class: "muted" }, "The first account is the admin. You'll add your reviewers and colleagues after signing in."),
      field("Your full name", name),
      field("Password", pw), passwordMeter(pw),
      field("Type the password again", pw2),
      h("label", { class: "check", for: "attest" }, attest,
        h("span", {}, "I am responsible for how this app is used here.")),
      s,
      buttons(back("Back", () => { keep(); practice(); }),
        action("Next", async () => {
          keep();
          if (pw.value !== pw2.value) throw new ServiceError("PASSWORD-WEAK", "The two passwords are not the same.");
          if (!attest.checked) throw new ServiceError("ROLE-DENIED", "Please tick the box to say you are responsible for how the app is used here.");
          recovery();
        }, { primary: true, slot: s }))));
  }

  function recovery() {
    const s = slot();
    const rec = password(); rec.value = v.rec;
    const rec2 = password(); rec2.value = v.rec2;
    const keep = () => { v.rec = rec.value; v.rec2 = rec2.value; };
    mount(root, screen(
      stepBar(steps, 2),
      h("h1", {}, "Choose a recovery passphrase"),
      h("p", { class: "muted" }, "If you ever forget your password or lose your phone, this gets you back in. "
        + "Make it different from your password: a short sentence of your own works well."),
      field("Recovery passphrase", rec), passwordMeter(rec),
      field("Type it again", rec2),
      s,
      buttons(back("Back", () => { keep(); account(); }),
        action("Create my account", async () => {
          keep();
          if (rec.value !== rec2.value) throw new ServiceError("PASSWORD-WEAK", "The two passphrases are not the same.");
          await call("setup_first_admin", { name: v.name, password: v.pw, practice: v.practice,
                                            recovery: v.rec, attest_responsible: v.attest });
          const phrase = v.rec;
          v.pw = v.pw2 = v.rec = v.rec2 = "";             // nothing secret kept longer than needed
          keepItScreen(root, { steps, index: 3, phrase, next: done, nextLabel: "Continue to sign in" });
        }, { primary: true, slot: s }))));
  }

  practice();
}

// ---- records lost or damaged ---------------------------------------------------------
export function lostRecords(root, { status, restore, startOver }) {
  const reasons = status.records_lost_reasons || [];
  mount(root, screen(
    h("h1", {}, "This computer's app records need restoring"),
    h("div", { class: "callout callout-warn" },
      h("p", {}, "The app's records on this computer are missing or could not be opened. Your own patient files are not affected — the app never moves or changes them."),
      reasons.length ? h("p", { class: "small" }, reasons.join("; ")) : null),
    h("p", { class: "mt" }, "The usual way back is the practice's latest backup. Only if there is none, start over as a new setup; the old records stay set aside and cannot be opened."),
    buttons(
      h("button", { class: "btn", type: "button", onclick: startOver }, "Start over"),
      h("button", { class: "btn btn-primary", type: "button", onclick: restore }, "Restore from a backup"))));
}

// ---- restore from backup ---------------------------------------------------------------
export function restoreFlow(root, { done, cancel }) {
  const steps = ["Backup", "Check", "Done"];

  function backup() {
    const s = slot();
    const file = pathField("Backup file", "file", "A file ending in .phibak, usually on the practice's backup drive.");
    const name = input({ autocomplete: "username" });
    const pw = password();
    const code = input({ inputmode: "numeric", placeholder: "6-digit code" });
    const codeField = field("Code from your phone", code);
    codeField.hidden = true;
    mount(root, screen(
      stepBar(steps, 0),
      h("h1", {}, "Choose the backup"),
      h("p", { class: "muted" }, "Sign in as an admin as you were when the backup was made: your name and the password you had then."),
      file.el, field("Admin name", name), field("Password (as it was then)", pw), codeField, s,
      buttons(cancel ? back("Back", cancel) : null, action("Check this backup", async () => {
        try {
          const pre = await call("restore_preview", { file: file.value, name: name.value, password: pw.value, code: code.value });
          check(pre);
        } catch (e) {
          if (e.code === "PHONE-CODE-NEEDED") { codeField.hidden = false; code.focus(); }
          throw e;
        }
      }, { primary: true, slot: s }))));
  }

  function check(pre) {
    const s = slot();
    const until = Date.now() + pre.confirm_within_s * 1000;
    const clock = h("span", {});
    const tick = () => {
      const left = Math.max(0, Math.round((until - Date.now()) / 1000));
      clock.textContent = `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
      if (left > 0 && document.body.contains(clock)) setTimeout(tick, 1000);
    };
    tick();
    mount(root, screen(
      stepBar(steps, 1),
      h("h1", {}, "Check before restoring"),
      h("dl", { class: "kv-list" },
        h("dt", {}, "Backup made"), h("dd", {}, when(pre.backup_created)),
        h("dt", {}, "Practice"), h("dd", {}, pre.practice || "—"),
        h("dt", {}, "People"), h("dd", {}, pre.accounts.map((a) => `${a.name} (${a.roles.join(", ")})`).join("; ")),
        h("dt", {}, "Security history"), h("dd", {}, `${pre.log_entries} entries`),
        h("dt", {}, "Code table"), h("dd", {}, pre.code_table)),
      h("ul", { class: "small muted mt" }, pre.notes.map((n) => h("li", {}, n))),
      h("p", { class: "small" }, "Confirm within ", clock, " minutes."),
      s,
      buttons(back("Choose a different backup", backup),
        action("Restore now", async () => {
          const r = await call("restore_confirm", { confirm: pre.confirm });
          finished(r);
        }, { primary: true, slot: s }))));
  }

  function finished(r) {
    mount(root, screen(
      stepBar(steps, 2),
      h("h1", {}, "Restored"),
      h("div", { class: "notice" }, `The app's records are back as of ${when(r.backup_created)}.`),
      h("ul", { class: "small muted" },
        h("li", {}, `Code table: ${r.code_table}.`),
        r.replaced_log ? h("li", {}, "The security history from before the restore is kept beside it as evidence.") : null,
        r.notes.map((n) => h("li", {}, n))),
      h("p", {}, "An admin will be asked to look over the restore after signing in."),
      buttons(h("button", { class: "btn btn-primary", type: "button", onclick: done }, "Continue to sign in"))));
  }

  backup();
}

// ---- sign-in -------------------------------------------------------------------------------
export function signIn(root, { status, notice, signedIn, recover, retire }) {
  const s = slot();
  const name = input({ autocomplete: "username" });
  const pw = password();
  const code = input({ inputmode: "numeric", placeholder: "6-digit code", autocomplete: "one-time-code" });
  const codeField = field("Code from your phone", code, "Open your authenticator app and type the code shown for this app.");
  codeField.hidden = true;
  const go = action("Sign in", async () => {
    try {
      const r = await call("sign_in", { name: name.value, password: pw.value, code: code.value });
      pw.value = ""; code.value = "";
      signedIn(r);
    } catch (e) {
      if (e.code === "PHONE-CODE-NEEDED") { codeField.hidden = false; code.focus(); }
      throw e;
    }
  }, { primary: true, slot: s });
  const form = h("form", { onsubmit: (e) => { e.preventDefault(); go.click(); } },
    field("Name", name), field("Password", pw), codeField, s,
    h("button", { type: "submit", hidden: true }),
    buttons(go));
  mount(root, screen(
    h("h1", {}, "Sign in"),
    status.org ? h("p", { class: "muted" }, status.org) : null,
    notice ? h("div", { class: "notice" }, notice) : null,
    status.integrity_alert ? h("div", { class: "callout callout-warn" },
      "The app noticed a change to its records. Only a practice admin can sign in until they have looked at it.") : null,
    form,
    h("div", { class: "spread mt small" },
      h("button", { class: "btn btn-quiet", type: "button", onclick: recover }, "Forgot password? Use the recovery passphrase"),
      h("button", { class: "btn btn-quiet", type: "button", onclick: retire }, "Retire this computer"))));
}

// ---- the recovery passphrase -----------------------------------------------------------
export function recoverAdmin(root, { done }) {
  const s = slot();
  const name = input({ autocomplete: "username" });
  const rc = password();
  const pw = password();
  const pw2 = password();
  const rec = password();
  mount(root, screen(
    h("h1", {}, "Use the recovery passphrase"),
    h("p", { class: "muted" }, "For an admin who forgot their password or lost their phone. Using the "
      + "passphrase means choosing a new one, and removes the old phone link."),
    field("Admin name", name), field("Recovery passphrase", rc),
    field("New password", pw), passwordMeter(pw), field("Type the new password again", pw2),
    field("New recovery passphrase", rec, "Different from the new password."), s,
    buttons(back("Back to sign in", () => done()),
      action("Set new password", async () => {
        if (pw.value !== pw2.value) throw new ServiceError("PASSWORD-WEAK", "The two passwords are not the same.");
        const r = await call("recover_admin", { name: name.value, recovery_code: rc.value,
                                                new_password: pw.value, new_recovery: rec.value });
        const phrase = r.recovery_code || rec.value;
        keepItScreen(root, { phrase, title: "Your new recovery passphrase",
          intro: "Your old passphrase no longer works. Write the new one down now and keep it somewhere private.",
          next: () => done("Signed out. Sign in with your new password."), nextLabel: "Continue to sign in" });
      }, { primary: true, slot: s }))));
}

// ---- retire a computer nobody can sign into -------------------------------------------
export function retireAtSignIn(root, { done, retired }) {
  const s = slot();
  const name = input({ autocomplete: "username" });
  const rc = password();
  const code = input({ inputmode: "numeric", placeholder: "6-digit code (if a phone is linked)" });
  const confirm = input({ placeholder: "Type RETIRE" });
  mount(root, screen(
    h("h1", {}, "Retire this computer"),
    h("div", { class: "callout callout-danger" },
      "Retiring erases all work in progress on this computer and the app's records here. It cannot be undone. Patient files in your own folders are not touched."),
    field("Admin name", name), field("Recovery passphrase", rc), field("Code from the admin's phone", code),
    field("To confirm, type RETIRE", confirm), s,
    buttons(back("Back to sign in", () => done()),
      action("Retire this computer", async () => {
        await call("retire_at_sign_in", { name: name.value, recovery_code: rc.value, code: code.value, confirm: confirm.value });
        retired();
      }, { danger: true, slot: s }))));
}

export function retiredScreen(root) {
  mount(root, screen(
    h("h1", {}, "This computer has been retired"),
    h("p", {}, "Its work in progress was erased and the app's records here were removed. Please use the practice's current computer."),
    h("p", { class: "small muted" }, "To use the app here again, restore from the practice's latest backup.")));
}

export { errorBox };
