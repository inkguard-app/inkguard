// The review screen: one note at a time, nothing else on the screen.
//
// It shows patient words (as the doctor's settle screen does), so it is
// stripped to the work: the note, what the tool marked, and the words it
// could not name. No app bar, no tabs, no dashboard — a reviewer should be
// unable to tell this screen has anything else in it. The only ways out are
// Take a break, Save and sign out, and the lock.

import { controls, enter, leave as leaveRoom } from "./room.js";
import { S, api } from "./state.js";
import { action, errorBox, h, input, slot, toast } from "./ui.js";

let pack = null;          // the note on screen
let dirty = false;        // answers not yet saved
let saveTimer = null;
let notes = [];           // every note of theirs, in order, so they can go back

const ANSWER_WORDS = {
  remove: "Take it out",
  fine: "Leave it in",
  unsure: "I'm not sure",
};

// ---- what the reviewer has decided, held while the note is open --------------------------
const D = { keep: new Set(), unsure: new Set(), answers: {}, kinds: {}, words: {}, fb: {} };

// Leaving the review screen lets go of the note: no patient words stay in
// the screen behind the lock or on the way back to Home.
function forget() {
  leaveRoom();
  clearTimeout(saveTimer);
  saveTimer = null;
  pack = null;
  dirty = false;
  notes = [];
  loadDecision({});
}

function loadDecision(d) {
  D.keep = new Set(d.keep || []);
  D.unsure = new Set(d.unsure || []);
  D.answers = { ...(d.answers || {}) };
  D.kinds = { ...(d.kinds || {}) };
  D.words = { ...(d.words || {}) };
  D.fb = { ...(d.fb || {}) };
}

function asDecision() {
  return {
    keep: [...D.keep], unsure: [...D.unsure], answers: D.answers,
    kinds: D.kinds, words: D.words, fb: D.fb, add: [],
  };
}

function touched() {
  dirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(keep, 1500);                 // kept as they work
}

async function save(confirm) {
  clearTimeout(saveTimer);
  const r = await api("review_save", {
    batch: pack.batch, note: pack.note, decision: asDecision(), confirm,
  });
  dirty = false;
  return r;
}

// The screen may lock between keeping answers. Nothing is lost: they stay
// here until the person is back, and the next save sends them.
async function keep() {
  try {
    await save(false);
  } catch (e) {
    dirty = true;
    if (e.code !== "SESSION-LOCKED" && e.code !== "SESSION-ENDED") throw e;
  }
}

// ---- the note, with the tool's marks shown in place --------------------------------------
function marked(text, removals, generalized, questions) {
  const spans = [];
  for (const r of removals) spans.push({ ...r, what: "removal" });
  for (const g of generalized) spans.push({ ...g, what: "general" });
  for (const q of questions) spans.push({ ...q, what: "question" });
  spans.sort((a, b) => a.start - b.start || a.end - b.end);
  const out = [];
  let at = 0;
  for (const sp of spans) {
    if (sp.start < at) continue;                       // overlapping: the first one wins
    if (sp.start > at) out.push(text.slice(at, sp.start));
    const word = text.slice(sp.start, sp.end);
    if (sp.what === "removal") {
      const kept = D.keep.has(sp.id);
      out.push(h("mark", {
        class: "mark mark-tool" + (kept ? " mark-kept" : ""),
        id: "m-" + sp.id, title: kept ? "Left in" : "Taken out",
      }, word));
    } else if (sp.what === "general") {
      out.push(h("mark", { class: "mark mark-added", title: "Made less exact" }, word));
    } else {
      out.push(h("mark", { class: "mark mark-unsure", id: "m-" + sp.id, title: "The tool was unsure" }, word));
    }
    at = sp.end;
  }
  if (at < text.length) out.push(text.slice(at));
  return h("div", { class: "note-text" }, out);
}

// ---- one word the tool could not name ----------------------------------------------------
function questionCard(q, onChange) {
  const sealed = pack && pack.confirmed;
  const chosen = () => D.answers[q.id];
  const buttons = Object.entries(ANSWER_WORDS).map(([key, label]) =>
    h("button", {
      type: "button", class: "btn pick" + (chosen() === key ? " on" : ""),
      "aria-pressed": chosen() === key ? "true" : "false",
      disabled: sealed ? "disabled" : null,
      onclick: (e) => {
        D.answers[q.id] = key;
        touched();
        onChange();
      },
    }, label));
  const kinds = h("select", { class: "input", onchange: (e) => {
    if (e.target.value) D.kinds[q.id] = e.target.value; else delete D.kinds[q.id];
    touched();
  } }, h("option", { value: "" }, "What is it?"),
    (pack.kinds || []).map((k) => h("option", {
      value: k.id, selected: D.kinds[q.id] === k.id ? "selected" : null }, k.label)));
  const own = input({
    placeholder: "Or say it in your own words", value: D.words[q.id] || "",
    oninput: (e) => { D.words[q.id] = e.target.value; touched(); },
  });
  return h("li", { class: "question" + (chosen() ? " answered" : ""), id: "q-" + q.id },
    h("div", { class: "qword" },
      h("button", { class: "linkbtn", type: "button", onclick: () => {
        const el = document.getElementById("m-" + q.id);
        if (el) { el.scrollIntoView({ block: "center" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1200); }
      } }, "“" + q.word + "”"),
      h("span", { class: "small muted" }, " the tool thinks: " + (q.guess || "not sure"))),
    h("div", { class: "row" }, buttons),
    h("div", { class: "row" }, kinds, own));
}

// ---- the whole screen --------------------------------------------------------------------
export async function reviewScreen(root, bid, { done }) {
  const opened = await api("review_open", { batch: bid });
  notes = opened.notes || [];
  enter(bid);
  if (!opened.next && !opened.signed_off) return signOff(root, bid, opened, { done });
  if (opened.signed_off) return signedAlready(root, opened, { done });
  await showNote(root, bid, opened.next, { done });
}

async function showNote(root, bid, nid, { done }) {
  const got = await api("review_note", { batch: bid, note: nid });
  pack = { ...got, batch: bid };
  loadDecision(got.decision || {});
  dirty = false;
  const s = slot();
  const list = h("ul", { class: "questions" });
  const redraw = () => {
    list.replaceChildren(...(pack.questions || []).map((q) => questionCard(q, redraw)));
    body.replaceChildren(marked(pack.text, pack.removals, pack.generalized, pack.questions));
    left.textContent = waiting();
  };
  const waiting = () => {
    const n = (pack.questions || []).filter((q) => !D.answers[q.id]).length;
    return n ? `${n} still to answer` : "All answered";
  };
  const body = h("div", { class: "note-body" });
  const left = h("span", { class: "pill" }, "");

  // A note they already confirmed is read-only until they say they want it
  // back. Deliberate, one note at a time, and counted.
  const reopen = action("Change my answer", async () => {
    await api("review_reopen", { batch: bid, note: nid });
    return showNote(root, bid, nid, { done });
  }, { slot: s });

  const next = action("Save and next", async () => {
    const r = await save(true);
    // The note after this one if there is one — so going back and coming
    // forward again walks the batch in order — then whatever is still
    // unanswered, and only then the sign-off.
    const after = notes[got.place];
    if (after) return showNote(root, bid, after.id, { done });
    const opened = await api("review_open", { batch: bid });
    notes = opened.notes || notes;
    if (opened.next) return showNote(root, bid, opened.next, { done });
    return signOff(root, bid, opened, { done });
  }, { primary: true, slot: s });

  root.replaceChildren(h("div", { class: "review room" },
    h("header", { class: "review-bar" },
      // Going back is deliberately one step at a time and unlabelled with
      // counts: the screen holds the task, not a map of the batch.
      got.place > 1
        ? h("button", { class: "btn btn-quiet", type: "button", title: "The note before this one",
                        onclick: async () => {
                          if (dirty) await keep();
                          const prev = notes[got.place - 2];
                          if (prev) return showNote(root, bid, prev.id, { done });
                          return null;
                        } }, "Back")
        : null,
      h("span", { class: "where" }, `Note ${got.place} of ${got.of}`),
      // How far through, without a number in the way: the rail fills.
      h("span", { class: "rail", role: "progressbar", "aria-label": "How far through this batch",
                  "aria-valuemin": "0", "aria-valuemax": String(got.of),
                  "aria-valuenow": String(got.place - 1) },
        h("span", { class: "rail-fill w"
                    + Math.round(((got.place - 1) / got.of) * 10) * 10 })),
      h("span", { class: "spacer" }),
      left,
      ...controls(bid, {
        onBack: () => showNote(root, bid, nid, { done }),
        onGone: () => {
          forget();
          S.session = null;                           // the core ended it; forget it here too
          S.hooks.ended({ code: "SESSION-ENDED" });
        },
      })),
    h("main", { class: "review-main" },
      h("section", { class: "note-pane" },
        got.status === "blocked"
          ? h("div", { class: "callout callout-warn" }, got.block_reason
              || "The tool stopped on this file as a precaution.")
          : null,
        body),
      h("aside", { class: "ask-pane" },
        h("h2", {}, "Words the tool was unsure about"),
        list,
        (pack.questions || []).length === 0
          ? h("p", { class: "muted" }, "None in this note — read it through and move on.") : null,
        h("label", { class: "check" },
          h("input", { type: "checkbox", checked: !!D.fb.over,
                       onchange: (e) => { D.fb.over = e.target.checked; touched(); } }),
          h("span", {}, "This note had too much taken out")),
        h("label", { class: "check" },
          h("input", { type: "checkbox", checked: !!D.fb.under,
                       onchange: (e) => { D.fb.under = e.target.checked; touched(); } }),
          h("span", {}, "Something identifying was missed here")),
        s,
        h("div", { class: "row row-end" },
          got.confirmed ? reopen : null,
          got.confirmed
            ? h("button", { class: "btn btn-primary", type: "button", onclick: async () => {
                const after = notes[got.place];
                if (after) return showNote(root, bid, after.id, { done });
                const opened = await api("review_open", { batch: bid });
                notes = opened.notes || notes;
                if (opened.next) return showNote(root, bid, opened.next, { done });
                return signOff(root, bid, opened, { done });
              } }, "Next")
            : next)))));
  redraw();
}

function signedAlready(root, opened, { done }) {
  root.replaceChildren(h("div", { class: "review" },
    h("main", { class: "review-main one" }, h("section", { class: "panel card" },
      h("h1", {}, "You have signed this batch off"),
      h("p", {}, "Your part is finished and locked. If something needs to change, tell the doctor "
        + "settling the batch."),
      h("div", { class: "row row-end" },
        h("button", { class: "btn btn-primary", type: "button", onclick: done }, "Back"))))));
}

function signOff(root, bid, opened, { done }) {
  const s = slot();
  const tick = h("input", { type: "checkbox", id: "attest" });
  const go = action("Sign off my review", async () => {
    const r = await api("review_sign_off", { batch: bid, attest: tick.checked });
    toast("Signed off. Thank you.");                 // their part only; the other's is not theirs to see
    forget();
    done();
  }, { primary: true, slot: s });
  go.disabled = true;
  tick.addEventListener("change", () => { go.disabled = !tick.checked; });
  root.replaceChildren(h("div", { class: "review" },
    h("main", { class: "review-main one" }, h("section", { class: "panel card" },
      h("h1", {}, "That is every note"),
      h("p", {}, `You worked through all ${opened.of} notes in this batch.`),
      h("label", { class: "check attest", for: "attest" }, tick,
        h("span", {}, "I read every note in this batch myself and answered every word the tool "
          + "was unsure about. To the best of my knowledge what is blacked out leaves nothing "
          + "that identifies a patient.")),
      h("p", { class: "small muted" }, "Your name and this statement are printed in the batch's "
        + "review report."),
      s,
      h("div", { class: "row row-end" },
        h("button", { class: "btn", type: "button", onclick: done }, "Not yet"), go)))));
}
