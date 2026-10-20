// The reading room: what the screen does while someone is working on patient
// notes.
//
// A review is one sitting of careful reading that the reviewer signs for, so the
// window is closed to everything else while it runs. What it offers instead
// is the two things a person actually needs:
//
//   • a BREAK — the notes are veiled, their place is kept, and their own
//     password brings it back;
//   • a WAY OUT for an emergency — the pool closes and they are signed out,
//     with everything they decided already saved.
//
// It also does what it honestly can to keep the notes on this screen and
// nowhere else: copying, printing and saving are refused, and the notes are
// veiled the moment the window loses focus. None of that can stop a camera,
// and the app never pretends otherwise — every one of these moments is
// simply counted on the record, because the attestation says this was one
// person reading in one sitting, and that should be true.

import { api } from "./state.js";
import { h } from "./ui.js";

let live = null;          // { batch, onResume } while a room is open
let veiled = false;
let lastBlocked = 0;

const veil = h("div", { class: "veil", hidden: "hidden", "aria-hidden": "true" });

function main() {
  return document.querySelector(".review-main");
}

function setVeil(on) {
  veiled = on;
  const m = main();
  if (m) m.classList.toggle("veiled", on);
  veil.hidden = !on;
}

async function report(what) {
  if (!live) return;
  if (what === "blocked") {
    const now = Date.now();
    if (now - lastBlocked < 2000) return;             // one record per attempt, not per keypress
    lastBlocked = now;
  }
  try { await api("review_away", { batch: live.batch, what }); } catch (e) { /* never in the way */ }
}

function block(e) {
  if (!live) return;
  e.preventDefault();
  report("blocked");
}

// Only a window that has really gone away veils the notes. A native
// dropdown, a file picker or a dialog takes focus without the person leaving
// the app, and veiling the screen for those would make the room unusable.
let awayTimer = null;

function onBlur() {
  if (!live || live.paused) return;
  clearTimeout(awayTimer);
  awayTimer = setTimeout(() => {
    if (!live || live.paused) return;
    if (document.hasFocus()) return;                  // focus is still inside the app
    setVeil(true);
    report("away");
  }, 250);
}

function onFocus() {
  clearTimeout(awayTimer);
  if (!live || live.paused) return;
  setVeil(false);
}

function onHidden() {
  if (!live || live.paused) return;
  if (document.visibilityState === "hidden") {
    setVeil(true);
    report("away");
  } else {
    setVeil(false);
  }
}

function onKey(e) {
  if (!live) return;
  const k = (e.key || "").toLowerCase();
  const mod = e.metaKey || e.ctrlKey;
  if (mod && ["c", "x", "p", "s", "a", "u"].includes(k)) block(e);
  if (k === "printscreen") report("blocked");         // cannot be stopped; it is recorded
}

/** Open the room for a batch. Returns a teardown function.
 *
 * Every listener this room adds to the window is registered against a
 * single AbortController, so leaving removes all of them in one call, with
 * no exit path having to remember each one. Entering twice closes the first
 * room first, so a second copy cannot exist.
 */
export function enter(batch, { onPaused } = {}) {
  leave();                                            // never two rooms at once
  const scope = new AbortController();
  const on = (target, ev, fn) => target.addEventListener(ev, fn, { signal: scope.signal });
  live = { batch, paused: false, onPaused, scope };
  lastBlocked = 0;
  document.body.append(veil);
  document.body.dataset.room = "open";
  setVeil(false);
  for (const ev of ["copy", "cut", "contextmenu", "dragstart", "beforeprint"]) {
    on(document, ev, block);
  }
  on(document, "keydown", onKey);
  on(document, "visibilitychange", onHidden);
  on(window, "blur", onBlur);
  on(window, "focus", onFocus);
  return leave;
}

export function leave() {
  if (live && live.scope) live.scope.abort();          // every listener, removed at once
  clearTimeout(awayTimer);
  awayTimer = null;
  setVeil(false);
  veil.remove();
  delete document.body.dataset.room;
  live = null;
}

export function paused() {
  return !!(live && live.paused);
}

/** The bar's two controls: a break, and the way out. */
export function controls(batch, { onBack, onGone }) {
  const pause = h("button", { class: "btn btn-quiet", type: "button", onclick: async () => {
    live.paused = true;
    setVeil(true);
    const r = await api("review_pause", { batch });
    showPause(batch, r, { onBack });
  } }, "Take a break");
  const stop = h("button", { class: "btn btn-quiet", type: "button",
                             onclick: () => confirmStop(batch, { onGone }) }, "Save and sign out");
  return [pause, stop];
}

// ---- the break ---------------------------------------------------------------------------
//
// Calm, not an alarm: the notes go soft behind a wash of the same blue the
// room is built from, and the only thing on it is the way back.
function showPause(batch, info, { onBack }) {
  const pw = h("input", { class: "input", type: "password", autocomplete: "current-password",
                          placeholder: "Your password" });
  const problem = h("div", { class: "row", role: "alert" });
  const back = h("button", { class: "btn btn-primary", type: "button", onclick: async () => {
    try {
      await api("review_resume", { batch, password: pw.value });
    } catch (e) {
      problem.replaceChildren(h("div", { class: "error" },
        e.code === "SESSION-LOCKED"
          ? "That was a long break, so the screen locked itself. Sign in again and you will "
            + "start where you stopped."
          : "That password did not match. Try again."));
      return;
    }
    live.paused = false;
    setVeil(false);
    screen.remove();
    if (onBack) onBack();
  } }, "Back to the notes");
  const screen = h("div", { class: "pause-screen" },
    h("div", { class: "pause-card" },
      h("p", { class: "pause-why" }, "The notes are hidden while you are away."),
      h("h1", {}, "Taking a break"),
      h("p", {}, "Your place is kept. Nothing you have decided is lost, and the clock on your "
        + "reading stops here."),
      h("label", { class: "label", for: "pause-pw" }, "Your password brings it back"),
      pw, problem,
      h("div", { class: "row row-end" }, back),
      h("p", { class: "small muted" }, `After about ${info.grace_minutes || 5} minutes the `
        + "screen locks itself, and coming back needs the code from your phone as well.")));
  pw.id = "pause-pw";
  document.body.append(screen);
  pw.focus();
}

// ---- the way out -------------------------------------------------------------------------
function confirmStop(batch, { onGone }) {
  const problem = h("div", {}, "");
  const go = h("button", { class: "btn btn-danger", type: "button", onclick: async () => {
    let r;
    try {
      r = await api("review_leave", { batch });
    } catch (e) {
      problem.replaceChildren(h("div", { class: "error" }, "That did not go through. Try again."));
      return;
    }
    leave();
    screen.remove();
    onGone(r);                                        // the shell takes it from here
  } }, "Save and sign me out");
  const screen = h("div", { class: "pause-screen" },
    h("div", { class: "pause-card" },
      h("p", { class: "pause-why" }, "For an emergency, not for a break.")  ,
      h("h1", {}, "Save and sign out"),
      h("p", {}, "The notes close and you are signed out. Everything you have decided is saved, "
        + "and you start where you stopped when you sign in again."),
      h("p", {}, "The batch's record will say you stopped part way, with how far you had got. "
        + "That is not a mark against you — it is what keeps the report true."),
      problem,
      h("div", { class: "row row-end" },
        h("button", { class: "btn", type: "button",
                      onclick: () => { screen.remove(); } }, "Keep going"),
        go)));
  document.body.append(screen);
  go.focus();
}
