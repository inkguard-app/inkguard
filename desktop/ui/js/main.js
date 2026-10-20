// Start-up and the rhythm of a session: which screen to show first, keeping
// a session alive while someone is really working, and noticing the moment
// the core locks or ends it. The core enforces all of this; the screens only
// follow what it says.

import { call } from "./bridge.js";
import { S } from "./state.js";
import { hideLock, render, showLock, startShell } from "./shell.js";
import {
  lostRecords, recoverAdmin, restoreFlow, retireAtSignIn, retiredScreen, setupWizard, signIn,
  unsupportedOS,
} from "./start.js";
import { closeAllDialogs, errorBox, h, mount } from "./ui.js";

const root = document.getElementById("app");
const ACTIVITY_EVERY_MS = 20000;   // tell the core about real activity at most this often
const POLL_MS = 10000;             // ask whether the session is still unlocked
let lastTouch = 0;
let poller = null;

async function boot(notice) {
  stopSession();
  let st;
  try {
    st = await call("status");
  } catch (e) {
    mount(root, h("div", { class: "center-screen" }, h("div", { class: "panel card" },
      h("h1", {}, "The app could not start"), errorBox(e))));
    return;
  }
  S.status = st;
  const again = (msg) => boot(msg);
  if (st.state === "retired") return retiredScreen(root);
  if (st.state === "unsupported_os") return unsupportedOS(root, st);
  if (st.state === "needs_restore") {
    return lostRecords(root, {
      status: st,
      restore: () => restoreFlow(root, { done: () => again(), cancel: () => again() }),
      startOver: () => setupWizard(root, { status: st, done: () => again("Set up. Sign in with your new account.") }),
    });
  }
  if (st.state === "needs_setup") {
    return setupWizard(root, {
      status: st,
      done: () => again("Set up. Sign in with your new account."),
      onRestoreInstead: () => restoreFlow(root, { done: () => again(), cancel: () => again() }),
    });
  }
  signIn(root, {
    status: st, notice,
    signedIn,
    recover: () => recoverAdmin(root, { done: (msg) => again(msg) }),
    retire: () => retireAtSignIn(root, { done: () => again(), retired: () => retiredScreen(root) }),
  });
}

function signedIn(r) {
  S.session = r.session;
  S.me = r.user;
  S.oversight = r.since_last_sign_in || null;
  S.locked = false;
  lastTouch = Date.now();
  poller = setInterval(poll, POLL_MS);
  startShell(root, {
    signOut: async () => {
      try { await call("sign_out", {}, S.session); } catch (e) { /* signing out always succeeds locally */ }
      boot("Signed out.");
    },
    retired: () => { stopSession(); retiredScreen(root); },
  });
}

function stopSession() {
  closeAllDialogs();
  if (poller) clearInterval(poller);
  poller = null;
  S.session = null;
  S.me = null;
  S.oversight = null;
  hideLock();
}

async function poll() {
  if (!S.session) return;
  try {
    const st = await call("status", {}, S.session);
    S.status = st;
    if (st.session_locked === null) return boot("Your session ended. Please sign in again.");
    if (st.session_locked && !S.locked) showLock();
  } catch (e) { /* the next poll tries again */ }
}

// Real activity — a key, a click, a scroll — keeps the session alive. Moving
// the mouse alone does not, and the lock screen never counts.
async function activity() {
  if (!S.session || S.locked) return;
  const now = Date.now();
  if (now - lastTouch < ACTIVITY_EVERY_MS) return;
  lastTouch = now;
  try { await call("touch", {}, S.session); } catch (e) {
    if (e.code === "SESSION-LOCKED") showLock();
    else if (e.code === "SESSION-ENDED") boot("Your session ended. Please sign in again.");
  }
}
["keydown", "mousedown", "wheel", "touchstart"].forEach((ev) => document.addEventListener(ev, activity, { passive: true }));

S.hooks.ended = () => boot("Your session ended. Please sign in again.");
S.hooks.locked = () => showLock();

window.addEventListener("unhandledrejection", (e) => e.preventDefault());

// Enter submits: in any single-line field, Enter presses the primary button
// of the card, dialog or panel the field sits in.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || e.shiftKey || e.isComposing) return;
  const t = e.target;
  if (!(t instanceof HTMLInputElement) || ["checkbox", "radio", "button", "submit"].includes(t.type)) return;
  if (t.closest("form")) return;                     // forms handle their own submit
  const box = t.closest(".dialog, .card, .panel, section");
  const btn = box && box.querySelector("button.btn-primary:not([disabled])");
  if (btn) { e.preventDefault(); btn.click(); }
});

// The browser's own menus and shortcuts are not part of the app: no reload
// (it would drop the session), and no "save page" or print of what is on
// screen.
document.addEventListener("contextmenu", (e) => e.preventDefault());
document.addEventListener("keydown", (e) => {
  const k = e.key.toLowerCase();
  const mod = e.ctrlKey || e.metaKey;
  if (k === "f5" || (mod && ["r", "p", "s", "o", "u", "f", "g"].includes(k)) || (mod && e.shiftKey && k === "i")) {
    e.preventDefault();
  }
}, true);
boot();
export { boot, render };
