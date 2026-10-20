// The only way the screens reach the secure core. Every request is one JSON
// line handed to the desktop wrapper, which passes it to the core over the
// process's own pipes (this code opens no network port). The screens hold no keys and
// no patient data; the session token lives in memory only, never in storage.

export class ServiceError extends Error {
  constructor(code, message) {
    super(message || code);
    this.code = code;
  }
}

let seq = 0;

function transport() {
  const tauri = window.__TAURI__ && window.__TAURI__.core;
  if (tauri && typeof tauri.invoke === "function") {
    return (line) => tauri.invoke("opal", { line });
  }
  if (typeof window.__opal_test === "function") {
    return (line) => window.__opal_test(line);       // the automated test harness
  }
  return null;
}

export async function call(op, args = {}, session = null) {
  const send = transport();
  if (!send) throw new ServiceError("APP-SERVICE-STOPPED", "The app's secure core is not running. Please close the app and open it again.");
  const req = { id: ++seq, op, args, session };
  let line;
  try {
    line = await send(JSON.stringify(req));
  } catch (e) {
    throw new ServiceError("APP-SERVICE-STOPPED", "The app's secure core stopped answering. Please close the app and open it again; your saved work is still here.");
  }
  let resp;
  try {
    resp = JSON.parse(line);
  } catch (e) {
    throw new ServiceError("APP-BAD-REQUEST", "The app received an answer it could not read.");
  }
  // Every answer must belong to this request; anything else means the line
  // to the core is out of step, and nothing it says can be trusted.
  if (!resp || resp.id !== req.id) {
    throw new ServiceError("APP-SERVICE-STOPPED", "The app's secure core answered out of order. Please close the app and open it again.");
  }
  if (!resp.ok) {
    const err = (resp && resp.error) || {};
    throw new ServiceError(err.code || "APP-BAD-REQUEST", err.message);
  }
  return resp.result;
}

// Native file and folder pickers (desktop wrapper only). Returns a full path
// or null; screens fall back to a typed path when no picker is available.
export async function pick(kind) {
  const dialog = window.__TAURI__ && window.__TAURI__.dialog;
  if (!dialog || typeof dialog.open !== "function") return null;
  const opts = kind === "folder" ? { directory: true, multiple: false }
    : { directory: false, multiple: false, filters: [{ name: "Backup", extensions: ["phibak"] }] };
  const chosen = await dialog.open(opts);
  return typeof chosen === "string" ? chosen : null;
}

export function hasPicker() {
  return !!(window.__TAURI__ && window.__TAURI__.dialog);
}

// Hand a checked feedback report to the person's own email program (desktop
// wrapper only). The app sends nothing itself: the address is fixed inside
// the wrapper, and the person reads the text there and decides whether to
// send it.
function outside() {
  const tauri = window.__TAURI__ && window.__TAURI__.core;
  if (tauri && typeof tauri.invoke === "function") {
    return (how, text) => tauri.invoke("send_feedback", { how, text });
  }
  if (typeof window.__open_test === "function") {
    return (how, text) => window.__open_test(how, text);   // the automated test harness
  }
  return null;
}

export function canSendOutside() {
  return outside() !== null;
}

export async function sendOutside(how, text) {
  const send = outside();
  if (!send) throw new ServiceError("APP-BAD-REQUEST", "Sending is only available in the desktop app.");
  try {
    await send(how, text);
  } catch (e) {
    throw new ServiceError("FEEDBACK-OPEN", typeof e === "string" ? e : "Your email program could not be opened.");
  }
}
