// What the screens remember while the app is open — in memory only. Closing
// the app forgets the session; nothing here is ever written to disk.

import { call } from "./bridge.js";

export const S = {
  session: null,     // the session token from sign-in
  me: null,          // the signed-in person (name, roles, PIN/phone status)
  status: null,      // the last status answer
  batches: [],       // the batches this person may see
  oversight: null,   // "since your last sign-in" counts
  locked: false,
  hooks: { ended: () => {}, locked: () => {} },
};

export const isAdmin = () => !!(S.me && S.me.roles.includes("admin"));
export const isDoctor = () => !!(S.me && S.me.roles.includes("doctor"));

// A request on behalf of the signed-in person. A session the core has ended
// returns everyone to sign-in; a locked one brings up the lock screen.
export async function api(op, args = {}) {
  try {
    return await call(op, args, S.session);
  } catch (e) {
    if (e.code === "SESSION-ENDED" && S.session) S.hooks.ended(e);
    else if (e.code === "SESSION-LOCKED") S.hooks.locked();
    throw e;
  }
}

export async function refreshStatus() {
  S.status = await call("status", {}, S.session);
  return S.status;
}
