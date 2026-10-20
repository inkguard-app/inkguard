// Small DOM helpers. Text always goes in as text (never as HTML), so nothing
// a person types, and nothing the core returns, can turn into markup.

import { ServiceError, hasPicker, pick } from "./bridge.js";

export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}

// Append children the way h() does: null, undefined and false are skipped.
// Native append() would print them as the words "null" and "undefined".
export function put(parent, ...kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    parent.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return parent;
}

export function mount(root, ...nodes) {
  root.replaceChildren(...nodes.flat().filter(Boolean));
  const first = root.querySelector("[autofocus], input:not([type=checkbox]), textarea, button.btn-primary");
  if (first) first.focus();
  // Screen readers hear the new screen's title once, not the whole screen.
  const title = root.querySelector("h1");
  const say = document.getElementById("announce");
  if (say) say.textContent = title ? title.textContent : "";
}

// While a dialog or the lock screen is up, everything behind it is inert:
// it cannot be clicked, tabbed to or read out.
let overlays = 0;
export function coverApp() {
  overlays += 1;
  const app = document.getElementById("app");
  if (app) app.inert = true;
}
export function uncoverApp() {
  overlays = Math.max(0, overlays - 1);
  const app = document.getElementById("app");
  if (app && overlays === 0) app.inert = false;
}

// Closes every dialog: used when a session ends or locks, so nothing is
// left on top of the next screen.
export function closeAllDialogs() {
  document.querySelectorAll(".scrim").forEach((s) => s._close ? s._close() : s.remove());
}

// An error from the core, in its own plain words, with the support code
// underneath for the IT person.
export function errorBox(err) {
  if (!err) return null;
  const code = err instanceof ServiceError ? err.code : "APP-BAD-REQUEST";
  const message = err.message || "Something went wrong.";
  return h("div", { class: "error", role: "alert" }, message,
    h("span", { class: "code" }, "Support code: " + code));
}

let fieldSeq = 0;
export function field(label, input, hint) {
  const id = "f" + (++fieldSeq);
  const target = input.matches("input, textarea, select") ? input : input.querySelector("input, textarea, select");
  if (target) target.id = id;
  return h("div", { class: "field" },
    h("label", { for: id }, label), input, hint ? h("div", { class: "hint" }, hint) : null);
}

export const input = (props) => h("input", { class: "input", autocomplete: "off", spellcheck: "false", ...props });
export const password = (props) => input({ type: "password", autocomplete: "off", ...props });
export const textarea = (props) => h("textarea", { class: "input", spellcheck: "false", ...props });

// A button that runs an async action once at a time, showing it is busy and
// showing any error in the given slot.
export function action(label, fn, { primary = false, danger = false, quiet = false, slot = null } = {}) {
  const cls = "btn" + (primary ? " btn-primary" : "") + (danger ? " btn-danger" : "") + (quiet ? " btn-quiet" : "");
  const btn = h("button", { class: cls, type: "button" }, label);
  btn.addEventListener("click", async () => {
    if (btn.disabled) return;
    btn.disabled = true;
    const was = btn.textContent;
    btn.textContent = "Working…";
    if (slot) slot.replaceChildren();
    try {
      await fn();
    } catch (e) {
      if (slot) slot.replaceChildren(errorBox(e));
      else toast(e.message || "Something went wrong.");
    } finally {
      btn.disabled = false;
      btn.textContent = was;
    }
  });
  return btn;
}

export function slot() {
  return h("div", { "aria-live": "polite" });
}

let toastTimer = null;
export function toast(message) {
  document.querySelectorAll(".toast").forEach((t) => t.remove());
  const t = h("div", { class: "toast", role: "status" }, message);
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 5000);
}

// A modal dialog. `build(close)` returns its content.
export function dialog(title, build, { kind = "" } = {}) {
  const scrim = h("div", { class: "scrim" + (kind ? " scrim-" + kind : "") });
  const before = document.activeElement;
  let open = true;
  const close = () => {
    if (!open) return;
    open = false;
    scrim.remove();
    uncoverApp();
    if (before && before.isConnected && typeof before.focus === "function") before.focus();
  };
  scrim._close = close;
  const box = h("div", { class: "dialog" + (kind ? " dialog-" + kind : ""), role: "dialog",
                         "aria-modal": "true", "aria-label": title },
    h("h2", {}, title), build(close));
  scrim.append(box);
  scrim.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  coverApp();
  document.body.append(scrim);
  const first = box.querySelector("input, textarea, button");
  if (first) first.focus();
  return close;
}

// A path chooser: a native picker in the desktop app, a typed full path otherwise.
export function pathField(label, kind, hint) {
  const inp = input({ placeholder: kind === "folder" ? "Full path of a folder" : "Full path of the backup file" });
  const wrap = h("div", { class: "row" }, inp);
  if (hasPicker()) {
    wrap.append(h("button", { class: "btn", type: "button", onclick: async () => {
      const p = await pick(kind);
      if (p) inp.value = p;
    } }, "Choose…"));
  }
  return { el: field(label, wrap, hint), get value() { return inp.value.trim(); } };
}

export function stepBar(names, now) {
  return h("ol", { class: "steps", "aria-label": "Steps" },
    names.map((n, i) => h("li", { class: i < now ? "done" : i === now ? "now" : "", "aria-current": i === now ? "step" : null },
      `${i + 1}. ${n}`)));
}

// The Inkguard mark drawn in CSS, so its bar can breathe while the app works.
// Used only as the "working" sign; everywhere else the mark is still.
export function inkMark(size = "") {
  return h("span", { class: "inkmark" + (size ? " " + size : ""), "aria-hidden": "true" },
    h("span", { class: "inkbar" }));
}

export function brand() {
  return h("div", { class: "brand" },
    h("img", { class: "mark-logo", src: "img/inkguard-mark.svg", alt: "", width: "44", height: "44" }),
    h("div", {}, h("b", {}, "Inkguard"), h("div", { class: "small muted" }, "Removes patient identifiers, and shows its work"),
      h("div", { class: "small muted verify-notice", role: "note" }, VERIFY_NOTICE)));
}

// The standing notice, word for word as inkguard/fluorite.py VERIFY_NOTICE
// (tests/test_fluorite.py compares them). It is on every screen.
export const VERIFY_NOTICE =
  "Inkguard is an automated tool. It can make mistakes, including misreading context. A person must check every output before it is used.";

export function when(iso) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  if (isNaN(d)) return iso;
  return iso.length === 10 ? d.toLocaleDateString() : d.toLocaleString();
}

// A QR code drawn as SVG shapes (no images, no network).
export function qrSvg(text) {
  const qr = window.qrcode(0, "M");
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount();
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `-2 -2 ${n + 4} ${n + 4}`);
  svg.setAttribute("class", "qr");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "QR code to scan with an authenticator app");
  const bg = document.createElementNS(ns, "rect");
  bg.setAttribute("x", "-2"); bg.setAttribute("y", "-2");
  bg.setAttribute("width", String(n + 4)); bg.setAttribute("height", String(n + 4));
  bg.setAttribute("fill", "#fff");
  svg.append(bg);
  let d = "";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c} ${r}h1v1h-1z`;
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "#000");
  svg.append(path);
  return svg;
}

// Password strength as the core judges it is decided by the core; this only
// shows progress toward the 15-character minimum while typing.
export function passwordMeter(inp) {
  const bar = h("span", { class: "w0" });
  const meter = h("div", { class: "meter", "aria-hidden": "true" }, bar);
  const note = h("div", { class: "hint" }, "At least 15 characters. A few unrelated words make a strong, easy-to-type passphrase.");
  inp.addEventListener("input", () => {
    const n = Math.min(15, inp.value.length);
    bar.className = "w" + Math.min(100, Math.round((n / 15) * 10) * 10);
    meter.classList.toggle("ok", inp.value.length >= 15);
  });
  return h("div", {}, meter, note);
}
