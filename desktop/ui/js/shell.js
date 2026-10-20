// The signed-in app: the frame (app bar, banners), Home, My account,
// Settings (admins), and the lock screen. Everything shown comes from the
// secure core; nothing here decides who may do what — the core does.

import { batchCard, batchView, newBatch } from "./batches.js";
import { reviewScreen } from "./review.js";
import { forget as forgetSettle, settleScreen } from "./settle.js";
import { tidyCard } from "./tidy.js";
import { forget as forgetPractice, practiceCard, practiceScreen } from "./practice.js";
import { ServiceError } from "./bridge.js";
import { S, api, isAdmin, isDoctor, refreshStatus } from "./state.js";
import {
  action, closeAllDialogs, coverApp, dialog, errorBox, field, h, input, mount, password, passwordMeter,
  pathField, put, qrSvg, slot, toast, uncoverApp, when,
} from "./ui.js";

let root = null;
let current = "home";
let openBatch = null;         // the batch being looked at, if any
let watcher = null;           // re-draws while a batch is being prepared
let out = { signOut: () => {}, retired: () => {} };

export function startShell(el, callbacks) {
  root = el;
  out = callbacks;
  current = "home";
  render();
}

export async function render() {
  if (!root) return;
  stopWatching();
  // The bare screens: the whole window, nothing but the work. They are the
  // only places patient words (or practice words) appear.
  if (current === "review" || current === "settle" || current === "practice") {
    const page = h("div", { class: "page-bare" });
    mount(root, page);
    const back = { done: async () => {
      const was = current;
      current = was === "practice" ? "home" : "batch";
      // A badge earned a moment ago must be the badge the screens see.
      if (was === "practice") {
        try { S.me = await api("me"); } catch (e) { /* the home screen still shows */ }
      }
      render();
    } };
    try {
      if (current === "practice") {
        await practiceScreen(page, back);
      } else {
        await (current === "settle" ? settleScreen : reviewScreen)(page, openBatch, back);
      }
    } catch (e) {
      forgetSettle();
      forgetPractice();
      page.replaceChildren(h("div", { class: "center-screen" }, h("div", { class: "panel card" },
        h("h1", {}, "This review could not be opened"), errorBox(e),
        h("div", { class: "row row-end" },
          h("button", { class: "btn btn-primary", type: "button",
                        onclick: () => { current = "home"; render(); } }, "Back to Home")))));
    }
    return;
  }
  try { await refreshStatus(); } catch (e) { /* the frame still shows */ }
  const page = h("div", { class: "page" });
  mount(root, frame(), banners(), h("main", { id: "main" }, page));
  const views = { home, account, settings, batch };
  try {
    await (views[current] || home)(page);
  } catch (e) {
    page.replaceChildren(errorBox(e));
  }
}

function go(name) { current = name; render(); }

// While a batch is being prepared the screen refreshes itself, so progress
// moves without anyone clicking. It stops the moment the screen changes.
function watch() {
  stopWatching();
  watcher = setTimeout(() => { if (S.session && !S.locked) render(); }, 2000);
}
function stopWatching() {
  if (watcher) clearTimeout(watcher);
  watcher = null;
}

async function batch(page) {
  const b = await batchView(page, openBatch, {
    back: () => { openBatch = null; go("home"); },
    reload: () => render(),
    onSettle: openSettle,
  });
  if (b && (b.stage === "processing" || b.stage === "exporting")) watch();          // keep the progress moving
}

function openBatchView(id) { openBatch = id; go("batch"); }
function openReview(id) { openBatch = id; go("review"); }
function openSettle(id) { openBatch = id; go("settle"); }
function openPractice() { go("practice"); }

// ---- frame ----------------------------------------------------------------------------
function frame() {
  const st = S.status || {};
  const tab = (name, label) => h("button", { type: "button", "aria-current": current === name ? "page" : null, onclick: () => go(name) }, label);
  return h("header", { class: "appbar" },
    h("span", { class: "appname" },
      h("img", { class: "appmark", src: "img/inkguard-mark.svg", alt: "", width: "22", height: "22" }),
      "Inkguard", st.org ? h("small", { class: "org" }, st.org) : null),
    h("nav", { "aria-label": "Main" }, tab("home", "Home"), tab("account", "My account"), isAdmin() ? tab("settings", "Settings") : null),
    h("span", { class: "spacer" }),
    h("span", { class: "pill", title: "The app's own code makes no network connections. You can turn off Wi-Fi while you work." }, "Works offline"),
    h("span", { class: "who" }, S.me ? S.me.name : ""),
    h("button", { class: "btn btn-quiet", type: "button", onclick: lockNow }, "Lock"),
    h("button", { class: "btn btn-quiet", type: "button", onclick: confirmSignOut }, "Sign out"));
}

// Signing out only ever makes the computer safer, so it asks for a
// confirmation, never a password: someone in a hurry must always be able to
// leave. Everything is already saved; the dialog says so.
function confirmSignOut() {
  dialog("Sign out?", (close) => h("div", {},
    h("p", {}, "Your work is saved. You can pick up exactly where you left off when you sign in again."),
    h("div", { class: "row row-end" },
      h("button", { class: "btn", type: "button", onclick: close }, "Stay signed in"),
      h("button", { class: "btn btn-primary", type: "button",
                    onclick: () => { close(); out.signOut(); } }, "Sign out"))));
}

function banners() {
  const st = S.status || {};
  const box = h("div", { class: "banners" });
  if (st.integrity_alert) {
    box.append(h("div", { class: "callout callout-warn" }, h("div", { class: "row" },
      h("span", {}, "The app noticed a change to its records outside the app. Only admins can sign in until an admin has looked at it."),
      isAdmin() ? h("button", { class: "btn", type: "button", onclick: reviewAlert }, "Review the alert") : null)));
  }
  return box.childElementCount ? box : null;
}

function reviewAlert() {
  const detail = (S.status && S.status.integrity_detail) || {};
  dialog("Integrity alert", (close) => {
    const s = slot();
    return h("div", {},
      h("p", {}, "What the app noticed:"),
      h("ul", {}, (detail.reasons || []).map((r) => h("li", {}, r))),
      h("p", { class: "small muted" }, "Check the security log and the people list. If nobody at the practice restored or moved anything, treat it as a possible break-in: keep a copy of the app's data folder and ask your practice's IT staff to look at it before acknowledging."),
      s,
      h("div", { class: "row row-end" },
        h("button", { class: "btn", type: "button", onclick: close }, "Not yet"),
        action("I have looked into it", async () => {
          await api("integrity_acknowledge");
          close();
          toast("Alert acknowledged. It is recorded in the security log.");
          render();
        }, { primary: true, slot: s })));
  });
}

// ---- Home -------------------------------------------------------------------------------
function greeting() {
  const hr = new Date().getHours();
  const first = (S.me && S.me.name || "").split(/\s+/).filter((w) => !/^(dr\.?|mr\.?|ms\.?|mrs\.?)$/i.test(w))[0] || "";
  return (hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening") + (first ? `, ${first}` : "");
}

async function home(page) {
  const st = S.status || {};
  const needs = [];
  if (S.me && !S.me.has_phone) needs.push(h("li", {}, isAdmin()
      ? "Link your phone for sign-in codes — admins need it before starting a batch. "
      : "Optional: link your phone for sign-in codes, for extra protection. ",
    h("button", { class: "linkbtn", type: "button", onclick: () => go("account") }, "Link phone")));
  const others = (st.admins_without_phone || []).filter((n) => !S.me || n !== S.me.name);
  if (isAdmin() && others.length) needs.push(h("li", {}, "Other admins without a phone code: " + others.join(", ") + "."));
  if (st.integrity_alert && isAdmin()) needs.push(h("li", {}, "An integrity alert is waiting for an admin to review."));

  const cards = [];
  if (S.oversight && (isAdmin() || isDoctor())) {
    const counts = S.oversight.counts || {};
    const rows = Object.entries(counts);
    cards.push(h("section", { class: "card" },
      h("h2", {}, "Since your last sign-in"),
      S.oversight.since ? h("p", { class: "small muted" }, "Since " + when(S.oversight.since)) : null,
      rows.length ? h("table", { class: "list" }, h("tbody", {}, rows.map(([k, v]) => h("tr", {}, h("td", {}, k.charAt(0).toUpperCase() + k.slice(1)), h("td", {}, h("b", {}, String(v)))))))
        : h("p", {}, "Nothing notable happened.")));
  }
  cards.push(h("section", { class: "card" },
    h("h2", {}, "What needs you"),
    needs.length ? h("ul", {}, needs) : h("p", {}, "Nothing needs you right now.")));
  let batches = [];
  try {
    const r = await api("batch_list");
    batches = r.batches || [];
    S.batches = batches;
  } catch (e) { /* the rest of Home still shows */ }
  put(page, h("h1", {}, greeting()),
    // Only in the way of someone it is actually about: a reviewer with no
    // current badge cannot be put on a batch. Everyone else finds it under
    // My account, where nothing is being decided.
    (S.me && S.me.roles.includes("reviewer")) ? practiceCard({ onOpen: openPractice }) : null,
    batchCard(batches, {
      onOpen: openBatchView,
      onReview: openReview,
      onSettle: openSettle,
      onNew: async () => {
        const [{ accounts }, { points }] = await Promise.all([api("account_list"), api("care_points")]);
        newBatch(accounts, points, { done: openBatchView });
      },
      reload: () => render(),
    }),
    h("div", { class: "grid-2 mt" }, cards),
    h("div", { class: "mt" }, tidyCard({ reload: () => render() })));
  if (batches.some((b) => b.stage === "processing")) watch();
}

// ---- My account ----------------------------------------------------------------------------
async function account(page) {
  S.me = await api("me");
  const me = S.me;
  put(page, h("h1", {}, "My account"),
    h("p", { class: "muted" }, `${me.name} · ${me.roles.join(", ")}`),
    h("div", { class: "grid-2 mt" }, changePasswordCard(), practiceCard({ onOpen: openPractice }),
      pinCard(me), phoneCard(me), isAdmin() ? recoveryCard() : null));
}

function changePasswordCard() {
  const s = slot();
  const old = password(); const pw = password(); const pw2 = password();
  return h("section", { class: "card" }, h("h2", {}, "Password"),
    field("Current password", old), field("New password", pw), passwordMeter(pw), field("Type the new password again", pw2), s,
    h("div", { class: "row row-end" }, action("Change password", async () => {
      if (pw.value !== pw2.value) throw new ServiceError("PASSWORD-WEAK", "The two passwords are not the same.");
      await api("change_password", { old: old.value, new: pw.value });
      old.value = pw.value = pw2.value = "";
      toast("Password changed.");
    }, { slot: s })));
}

function pinCard(me) {
  const s = slot();
  const pw = password(); const pin = password({ inputmode: "numeric" });
  return h("section", { class: "card" }, h("h2", {}, "Quick unlock PIN"),
    h("p", { class: "small muted" }, me.has_pin ? "A PIN is set. It only unlocks the lock screen; signing in always needs the password."
      : "Optional: a PIN of 4–8 digits to unlock the lock screen faster."),
    field("Password", pw), field("PIN", pin), s,
    h("div", { class: "row row-end" }, action(me.has_pin ? "Change PIN" : "Set PIN", async () => {
      await api("set_pin", { password: pw.value, pin: pin.value });
      toast("PIN set."); render();
    }, { slot: s })));
}

function phoneCard(me) {
  const s = slot();
  const pw = password();
  const code = input({ inputmode: "numeric", placeholder: "6-digit code" });
  const area = h("div", {});
  const card = h("section", { class: "card" }, h("h2", {}, "Phone codes"), area);
  if (me.has_phone) {
    area.append(h("p", {}, h("span", { class: "badge badge-ok" }, "Linked"), " Sign-in and restore ask for a code from your phone."),
      field("Password", pw), field("Current code from your phone", code), s,
      h("div", { class: "row row-end" },
        action("Move to a new phone", async () => linkPhone(area, pw.value, code.value), { slot: s }),
        action("Remove phone", async () => {
          await api("phone_unlink", { password: pw.value, code: code.value });
          toast("Phone link removed."); render();
        }, { danger: true, slot: s })));
  } else {
    area.append(h("p", { class: "small muted" }, "Use any authenticator app (Google Authenticator, Microsoft Authenticator, and others). It works without the internet."),
      field("Password", pw), s,
      h("div", { class: "row row-end" }, action("Link my phone", async () => linkPhone(area, pw.value, ""), { primary: true, slot: s })));
  }
  return card;
}

async function linkPhone(area, pw, current) {
  const r = await api("phone_link_begin", { password: pw, code: current });
  const s = slot();
  const code = input({ inputmode: "numeric", placeholder: "6-digit code", autocomplete: "one-time-code" });
  area.replaceChildren(
    h("p", {}, "Scan this with your authenticator app, then type the code it shows."),
    qrSvg(r.uri),
    h("p", { class: "small muted" }, "Can't scan? Enter this key by hand: ", h("code", {}, r.secret)),
    field("Code from your phone", code), s,
    h("div", { class: "row row-end" }, action("Finish linking", async () => {
      await api("phone_link_confirm", { code: code.value });
      toast("Phone linked."); render();
    }, { primary: true, slot: s })));
}

function recoveryCard() {
  const s = slot();
  const pw = password();
  const rec = password();
  const rec2 = password();
  const shown = h("div", {});
  return h("section", { class: "card" }, h("h2", {}, "Recovery passphrase"),
    h("p", { class: "small muted" }, "Lost the written copy, or worried someone saw it? Choose a new one; the old one stops working at once."),
    field("Your password", pw), field("New recovery passphrase", rec, "Different from your password."),
    passwordMeter(rec), field("Type it again", rec2), s, shown,
    h("div", { class: "row row-end" }, action("Change recovery passphrase", async () => {
      if (rec.value !== rec2.value) throw new ServiceError("PASSWORD-WEAK", "The two passphrases are not the same.");
      await api("recovery_code_new", { password: pw.value, recovery: rec.value });
      const phrase = rec.value;
      pw.value = rec.value = rec2.value = "";
      shown.replaceChildren(h("div", { class: "kit" },
        h("p", {}, "Write it down now and keep it somewhere private, like the practice's safe:"),
        h("div", { class: "bigcode long" }, phrase)));
    }, { slot: s })));
}

// ---- Settings (admins) -----------------------------------------------------------------------
let settingsTab = "people";
async function settings(page) {
  const tabs = [["people", "People"], ["security", "Security"], ["backups", "Backups"], ["log", "Security log"], ["computer", "This computer"]];
  const body = h("div", {});
  put(page, h("h1", {}, "Settings"),
    h("div", { class: "tabs", role: "tablist" }, tabs.map(([k, label]) => h("button", {
      type: "button", role: "tab", "aria-selected": settingsTab === k ? "true" : "false",
      onclick: () => { settingsTab = k; render(); } }, label))),
    body);
  await ({ people, security, backups, securityLog, computer }[{ log: "securityLog" }[settingsTab] || settingsTab])(body);
}

async function people(body) {
  const { accounts } = await api("account_list");
  const s = slot();
  const rows = accounts.map((a) => h("tr", { class: a.disabled ? "dim" : "" },
    h("td", {}, a.name, a.id === S.me.id ? h("span", { class: "small muted" }, " (you)") : null),
    h("td", {}, a.roles.join(", ")),
    h("td", {}, a.has_phone ? h("span", { class: "badge badge-ok" }, "Phone") : h("span", { class: "badge" }, "No phone")),
    h("td", {}, a.disabled ? "Disabled" : a.password_disabled ? "Password needs reset" : "Active"),
    h("td", {}, a.disabled || a.id === S.me.id ? null : h("div", { class: "row" },
      h("button", { class: "btn btn-quiet", type: "button", onclick: () => resetPassword(a) }, "Reset password"),
      a.has_phone ? h("button", { class: "btn btn-quiet", type: "button", onclick: () => removePhone(a) }, "Remove phone") : null,
      h("button", { class: "btn btn-quiet btn-danger", type: "button", onclick: () => disable(a) }, "Disable")))));
  put(body,
    h("section", { class: "card" }, h("h2", {}, "People"),
      h("table", { class: "list" },
        h("thead", {}, h("tr", {}, ["Name", "Roles", "Phone", "Status", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, rows)), s),
    addPersonCard());
}

function addPersonCard() {
  const s = slot();
  const name = input({});
  const pw = password();
  const roles = ["reviewer", "doctor", "admin"].map((r) => ({ r, box: h("input", { type: "checkbox", id: "role-" + r, checked: r === "reviewer" }) }));
  return h("section", { class: "card mt" }, h("h2", {}, "Add a person"),
    field("Full name", name),
    h("fieldset", { class: "field" }, h("legend", {}, "Roles"),
      h("div", { class: "row" }, roles.map(({ r, box }) => h("label", { class: "check", for: box.id }, box, h("span", {}, r))))),
    field("Temporary password", pw, "Give it to them in person; they change it after signing in."), passwordMeter(pw), s,
    h("div", { class: "row row-end" }, action("Add person", async () => {
      await api("account_create", { name: name.value, password: pw.value, roles: roles.filter((x) => x.box.checked).map((x) => x.r) });
      toast(`${name.value} added.`); render();
    }, { primary: true, slot: s })));
}

function resetPassword(a) {
  dialog(`Reset ${a.name}'s password`, (close) => {
    const s = slot(); const pw = password();
    return h("div", {}, h("p", { class: "small muted" }, "This also clears their PIN and any pause from wrong guesses."),
      field("New temporary password", pw), passwordMeter(pw), s,
      h("div", { class: "row row-end" }, h("button", { class: "btn", type: "button", onclick: close }, "Cancel"),
        action("Reset password", async () => {
          await api("reset_password", { id: a.id, password: pw.value });
          close(); toast("Password reset."); render();
        }, { primary: true, slot: s })));
  });
}

function removePhone(a) {
  dialog(`Remove ${a.name}'s phone link`, (close) => {
    const s = slot(); const pw = password();
    return h("div", {}, h("p", { class: "small muted" }, "For someone who lost or replaced their phone. They link the new one after signing in."),
      field("Your password", pw), s,
      h("div", { class: "row row-end" }, h("button", { class: "btn", type: "button", onclick: close }, "Cancel"),
        action("Remove phone link", async () => {
          await api("phone_unlink", { id: a.id, password: pw.value });
          close(); toast("Phone link removed."); render();
        }, { danger: true, slot: s })));
  });
}

function disable(a) {
  dialog(`Disable ${a.name}?`, (close) => {
    const s = slot();
    return h("div", {}, h("p", {}, "They are signed out at once and cannot sign in again."), s,
      h("div", { class: "row row-end" }, h("button", { class: "btn", type: "button", onclick: close }, "Cancel"),
        action("Disable", async () => {
          await api("account_disable", { id: a.id });
          close(); toast(`${a.name} disabled.`); render();
        }, { danger: true, slot: s })));
  });
}

function reviewPeopleCard(st) {
  const s = slot();
  const choice = (n, label, hint) => {
    const box = h("input", { type: "radio", name: "people", id: "people" + n, value: String(n),
                             checked: (st.review_people || 2) === n });
    return h("label", { class: "check", for: box.id }, box, h("span", {}, h("b", {}, label), h("br", {}),
      h("span", { class: "small muted" }, hint)));
  };
  const two = choice(2, "Two people", "Two reviewers; the doctor who signs off may be one of them. Easiest for small teams.");
  const three = choice(3, "Three people", "Two reviewers and a separate doctor who signs off.");
  return h("section", { class: "card mt" }, h("h2", {}, "Who reviews a batch"),
    h("p", { class: "small muted" }, "Either way, the two reviewers work independently and never see "
      + "each other's answers. The review report says which arrangement was used."),
    h("fieldset", { class: "field" }, h("legend", {}, "This practice works with"), two, three), s,
    h("div", { class: "row row-end" }, action("Save", async () => {
      const n = three.querySelector("input").checked ? 3 : 2;
      await api("settings_set", { review_people: n });
      toast("Saved."); render();
    }, { slot: s })));
}

async function security(body) {
  const st = S.status || {};
  const s = slot();
  const sel = h("select", { class: "input" }, Array.from({ length: 15 }, (_, i) => i + 1).map((m) =>
    h("option", { value: String(m), selected: m === st.idle_minutes ? "selected" : null }, `${m} minute${m > 1 ? "s" : ""}`)));
  const carryOn = h("input", { type: "checkbox", id: "pwl", checked: !!st.prepare_while_locked });
  const s2 = slot();
  const days = h("select", { class: "input" }, Array.from({ length: 24 }, (_, i) => i + 7).map((d) =>
    h("option", { value: String(d), selected: d === (st.review_days || 14) ? "selected" : null }, `${d} days`)));
  put(body,
    h("section", { class: "card" }, h("h2", {}, "Automatic lock"),
      h("p", { class: "small muted" }, "After this long without use, the screen locks so patient information is never left showing."),
      field("Lock after", sel),
      h("fieldset", { class: "field" }, h("legend", {}, "While the screen is locked"),
        h("label", { class: "check", for: "pwl" }, carryOn,
          h("span", {}, "Keep preparing batches (the screens still show nothing)"))),
      h("p", { class: "small muted" }, "Off by default: when the screen locks, the app lets go of "
        + "the batch's key and waits. Turn this on for large overnight batches."),
      s,
      h("div", { class: "row row-end" }, action("Save", async () => {
        await api("settings_set", { idle_minutes: Number(sel.value),
                                    prepare_while_locked: carryOn.checked });
        toast("Saved."); render();
      }, { slot: s }))),
    reviewPeopleCard(st),
    h("section", { class: "card mt" }, h("h2", {}, "Time to sign off a batch"),
      h("p", { class: "small muted" }, "How long the doctor has to sign off after the reviewers finish. "
        + "When the time runs out the app erases that batch's working copies; your own files are untouched. "
        + "Each batch can be given one extra week."),
      field("Sign off within", days), s2,
      h("div", { class: "row row-end" }, action("Save", async () => {
        await api("settings_set", { review_days: Number(days.value) });
        toast("Saved."); render();
      }, { slot: s2 }))),
    h("section", { class: "card mt" }, h("h2", {}, "Record protection"),
      h("dl", { class: "kv-list" },
        h("dt", {}, "Security log"), h("dd", {}, st.security_log_ok ? "Intact" : "Damaged — see the integrity alert"),
        h("dt", {}, "Tamper counter"), h("dd", {}, { ok: "Working", missing: "Not yet set", unavailable: "Unreachable", edited: "Changed outside the app" }[st.tamper_counter] || "—"),
        h("dt", {}, "Integrity alert"), h("dd", {}, st.integrity_alert ? "Waiting for review" : "None"))));
}

async function backups(body) {
  const s1 = slot(), s2 = slot(), s3 = slot();
  const folder = pathField("Save the backup to", "folder", "An external drive or a network folder — not this computer's own disk.");
  const file = pathField("Backup file", "file");
  const tfile = pathField("Backup file", "file");
  const tpw = password(); const tcode = input({ inputmode: "numeric", placeholder: "6-digit code (if linked)" });
  const result = h("div", {}), drill = h("div", {});
  put(body,
    h("section", { class: "card" }, h("h2", {}, "Make a backup"),
      h("p", { class: "small muted" }, "One encrypted file with the app's records, security log and code table. Patient files are never in it. Keep backups on two separate drives."),
      folder.el, s1, result,
      h("div", { class: "row row-end" }, action("Make backup", async () => {
        const r = await api("backup_create", { folder: folder.value });
        result.replaceChildren(h("div", { class: "notice" }, "Backup saved: ", h("code", {}, r.file), h("div", { class: "small" }, `Fingerprint ${r.sha256.slice(0, 16)}… · code table: ${r.code_table}`)));
      }, { primary: true, slot: s1 }))),
    h("section", { class: "card mt" }, h("h2", {}, "Check a backup (restore drill)"),
      h("p", { class: "small muted" }, "Opens the backup safely, without touching this computer's records, and checks that it is complete."),
      file.el, s2, drill,
      h("div", { class: "row row-end" }, action("Check backup", async () => {
        const r = await api("backup_verify", { file: file.value });
        drill.replaceChildren(h("div", { class: "notice" }, `Backup from ${when(r.created)} is complete: ${r.accounts} people, ${r.log_entries} security entries, code table ${r.code_table}.`));
      }, { slot: s2 }))),
    h("section", { class: "card mt" }, h("h2", {}, "Restore a lost code table"),
      h("p", { class: "small muted" }, "Only when the coded-names table on this computer is missing or damaged; a sound table is never replaced."),
      tfile.el, field("Your password", tpw), field("Code from your phone", tcode), s3,
      h("div", { class: "row row-end" }, action("Restore code table", async () => {
        await api("code_table_restore", { file: tfile.value, password: tpw.value, code: tcode.value });
        toast("Code table restored.");
      }, { slot: s3 }))));
}

const EVENT_WORDS = {
  signed_in: "Signed in", signed_out: "Signed out", sign_in_failed: "Failed sign-in", auto_lock: "Locked automatically",
  locked: "Locked", unlocked: "Unlocked", unlock_failed: "Failed unlock", reauth_failed: "Wrong password re-entered",
  phone_code_failed: "Wrong phone code", phone_linked: "Phone linked", phone_link_removed: "Phone link removed",
  account_created: "Person added", account_disabled: "Person disabled", password_reset: "Password reset by admin",
  password_changed: "Password changed", pin_set: "PIN set", first_admin_created: "First admin set up",
  admin_recovered_with_code: "Recovery passphrase used", recovery_failed: "Failed recovery-passphrase use",
  recovery_code_replaced: "Recovery passphrase changed",
  settings_changed: "Settings changed", role_denied: "Action refused (role)", integrity_alert: "Integrity alert",
  integrity_acknowledged: "Integrity alert reviewed", backup_created: "Backup made", backup_verified: "Backup checked",
  backup_restored: "Restored from backup", restore_previewed: "Restore checked", restore_sign_in_failed: "Failed restore sign-in",
  code_table_restored: "Code table restored", device_retired: "Computer retired",
  batch_created: "Batch started", batch_prepared: "Batch ready for review", batch_erased: "Batch erased",
  batch_failed: "Batch stopped", batch_extended: "Batch given more time",
  tamper_counter_unavailable: "Tamper counter unreachable",
  password_disabled_after_failures: "Password disabled after failures", session_ended_after_failed_unlocks: "Session ended after failed unlocks",
  password_hash_upgraded: "Password storage upgraded", log_restarted: "Security log restarted", torn_line_trimmed: "Unfinished log line trimmed",
};

async function securityLog(body) {
  // The log must open even when other settings are refused; names are a
  // convenience.
  const { events, ok } = await api("security_log", { last: 200 });
  let names = {};
  try {
    const { accounts } = await api("account_list");
    names = Object.fromEntries(accounts.map((a) => [a.id, a.name]));
  } catch (e) { /* show ids instead */ }
  const shown = events.filter((e) => e.event !== "store_saved").reverse();
  put(body, h("section", { class: "card" }, h("h2", {}, "Security log"),
    h("p", { class: "small muted" }, (ok ? "Intact: every entry is chained to the one before it. " : "This log failed its check. ") + "Newest first; no patient information is ever recorded here."),
    h("table", { class: "list" },
      h("thead", {}, h("tr", {}, ["When", "What", "Who"].map((t) => h("th", {}, t)))),
      h("tbody", {}, shown.map((e) => h("tr", {}, h("td", {}, when(e.ts)), h("td", {}, EVENT_WORDS[e.event] || e.event), h("td", {}, e.uid ? (names[e.uid] || e.uid) : "—")))))));
}

async function computer(body) {
  const st = S.status || {};
  const s4 = slot();
  const pw = password(); const code = input({ inputmode: "numeric", placeholder: "6-digit code (if linked)" });
  const confirm = input({ placeholder: "Type RETIRE" });
  put(body,
    h("section", { class: "card" }, h("h2", {}, "This computer"),
      h("dl", { class: "kv-list" },
        h("dt", {}, "Practice"), h("dd", {}, st.org || "—"),
        h("dt", {}, "Network"), h("dd", {}, "None. The app's own code makes no network connections; it works with the Wi-Fi off.")),
      h("p", { class: "small muted" }, "This app is free and open source, under the Apache 2.0 "
        + "licence. There is no account with anyone, and nothing to renew.")),
    h("section", { class: "card mt danger-zone" }, h("h2", {}, "Retire this computer"),
      h("p", {}, "When this computer is replaced, sold or lost to the practice. Its encryption keys are destroyed, so all work in progress becomes unreadable at once; then the app's records here are removed. Patient files in your own folders are not touched. This cannot be undone."),
      field("Your password", pw), field("Code from your phone", code), field("To confirm, type RETIRE", confirm), s4,
      h("div", { class: "row row-end" }, action("Retire this computer", async () => {
        await api("retire_here", { password: pw.value, code: code.value, confirm: confirm.value });
        out.retired();
      }, { danger: true, slot: s4 }))));
}

// ---- the lock screen ---------------------------------------------------------------------------
export function showLock() {
  // Only a live session can be locked; an ended one goes back to sign-in.
  if (!S.session || document.querySelector(".lock-overlay")) return;
  closeAllDialogs();
  S.locked = true;
  const s = slot();
  const usePin = !!(S.me && S.me.has_pin);
  const pw = password({ placeholder: usePin ? "PIN or password" : "Password", autocomplete: "off" });
  const phone = input({ inputmode: "numeric", placeholder: "6-digit code from your phone" });
  const phoneField = field("Code from your phone", phone,
    "Patient work was open on this screen, so the code on your own phone is needed to come back.");
  phoneField.hidden = true;
  const unlock = action("Unlock", async () => {
    const v = pw.value;
    const args = usePin && /^\d{4,8}$/.test(v) ? { pin: v } : { password: v };
    if (phone.value.trim()) args.code = phone.value.trim();
    try {
      await api("unlock", args);
    } catch (e) {
      if (e.code === "PHONE-CODE-NEEDED") { phoneField.hidden = false; phone.focus(); }
      throw e;
    }
    pw.value = ""; phone.value = "";
    hideLock();
    render();
  }, { primary: true, slot: s });
  const overlay = h("div", { class: "lock-overlay", role: "dialog", "aria-modal": "true", "aria-label": "Locked" },
    h("div", { class: "panel card" },
      h("div", { class: "lock-title" }, h("h1", {}, "Locked")),
      h("p", { class: "muted" }, "To protect patient information, the screen locked. Your work is saved."),
      h("p", {}, S.me ? S.me.name : ""),
      h("form", { onsubmit: (e) => { e.preventDefault(); unlock.click(); } },
        field(usePin ? "PIN or password" : "Password", pw), phoneField, s,
        h("div", { class: "row row-end" },
          h("button", { class: "btn btn-quiet", type: "button", onclick: () => out.signOut() }, "Sign out"), unlock))));
  coverApp();
  document.body.append(overlay);
  pw.focus();
}

export function hideLock() {
  S.locked = false;
  document.querySelectorAll(".lock-overlay").forEach((o) => { o.remove(); uncoverApp(); });
}

async function lockNow() {
  try { await api("lock_now"); } catch (e) { /* locking is never refused */ }
  showLock();
}
