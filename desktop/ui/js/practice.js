// The practice batch: invented notes, the real review screen, a known
// answer key.
//
// Nobody's first look at a real patient's notes should also be their first
// look at this tool. These people do not exist, so the screen can say so
// plainly and the reviewer can take it apart without fear — and the marking
// is objective, because the tool already knows every answer.

import { S, api } from "./state.js";
import { action, h, input, slot } from "./ui.js";

let set = null;                 // the practice notes, once opened
let at = 0;                     // which one is on screen
const D = {};                   // decisions per note

const ANSWER_WORDS = {
  remove: "Take it out",
  fine: "Leave it in",
  unsure: "I'm not sure",
};

export function forget() {
  set = null;
  at = 0;
  for (const k of Object.keys(D)) delete D[k];
}

function here() {
  const note = set.notes[at];
  if (!D[note.note]) D[note.note] = { answers: {}, kinds: {}, words: {}, add: [] };
  return { note, d: D[note.note] };
}

// ---- the note, with the tool's marks and the reviewer's own ------------------------------
function marked(note, d, onChange) {
  const spans = [
    ...note.removals.map((r) => ({ ...r, what: "removal" })),
    ...note.generalized.map((g) => ({ ...g, what: "general" })),
    ...note.questions.map((q) => ({ ...q, what: "question" })),
    ...d.add.map((a, i) => ({ ...a, what: "mine", i })),
  ].sort((a, b) => a.start - b.start || a.end - b.end);
  const out = [];
  let cut = 0;
  for (const sp of spans) {
    if (sp.start < cut) continue;
    if (sp.start > cut) out.push(part(note.text.slice(cut, sp.start), cut, d, onChange));
    const word = note.text.slice(sp.start, sp.end);
    if (sp.what === "mine") {
      out.push(h("mark", { class: "mark mark-mine", title: "You marked this",
        onclick: () => { d.add.splice(sp.i, 1); onChange(); } }, word));
    } else if (sp.what === "removal") {
      out.push(h("mark", { class: "mark mark-tool", title: "Taken out by the tool" }, word));
    } else if (sp.what === "general") {
      out.push(h("mark", { class: "mark mark-added", title: "Made less exact" }, word));
    } else {
      out.push(h("mark", { class: "mark mark-unsure", id: "pq-" + sp.id }, word));
    }
    cut = sp.end;
  }
  if (cut < note.text.length) out.push(part(note.text.slice(cut), cut, d, onChange));
  return h("div", { class: "note-text selectable" }, out);
}

// Plain text the reviewer can select: selecting it marks it, which is the
// whole exercise — the tool has not marked these.
function part(text, offset, d, onChange) {
  return h("span", { class: "plain", onmouseup: () => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed) return;
    const s = sel.toString();
    const within = text.indexOf(s);
    if (within < 0 || !s.trim()) return;
    d.add.push({ start: offset + within, end: offset + within + s.length, type: "other" });
    sel.removeAllRanges();
    onChange();
  } }, text);
}

function questionCard(q, d, onChange) {
  const buttons = Object.entries(ANSWER_WORDS).map(([key, label]) => h("button", {
    type: "button", class: "btn pick" + (d.answers[q.id] === key ? " on" : ""),
    "aria-pressed": d.answers[q.id] === key ? "true" : "false",
    onclick: () => { d.answers[q.id] = key; onChange(); },
  }, label));
  return h("li", { class: "question" + (d.answers[q.id] ? " answered" : "") },
    h("div", { class: "qword" }, h("b", {}, "“" + q.word + "”"),
      h("span", { class: "small muted" }, " the tool thinks: " + (q.guess || "not sure"))),
    h("div", { class: "row" }, buttons));
}

// ---- the screen -------------------------------------------------------------------------
export async function practiceScreen(root, { done }) {
  set = await api("practice_open", {});
  at = 0;
  if (set.badge && set.badge.certified) return standing(root, { done });
  return intro(root, { done });
}

function intro(root, { done }) {
  const s = slot();
  root.replaceChildren(h("div", { class: "review" },
    h("main", { class: "review-main one" }, h("section", { class: "panel card" },
      h("h1", {}, "Practice batch"),
      h("p", {}, set.about),
      h("p", {}, `There are ${set.of} notes. Read each one as you would a real note: answer `
        + "every word the tool was unsure about, and mark anything identifying that the tool "
        + "has not marked — select it with the mouse to mark it, click your mark to undo it."),
      h("p", {}, h("b", {}, "Some of the notes hold something identifying that the tool does not "
        + "mark."), " Finding those is the point: the tool misses things, and a person reading "
        + "is how they are caught."),
      h("p", { class: "small muted" }, `Passing records a practice badge against your `
        + `account for ${set.needs.months} months. The date is printed on every review report `
        + "you sign. Nothing is held against anyone who does not pass — it can be done again "
        + "straight away."),
      s,
      h("div", { class: "row row-end" },
        h("button", { class: "btn", type: "button", onclick: () => { forget(); done(); } },
          "Not now"),
        h("button", { class: "btn btn-primary", type: "button",
                      onclick: () => showNote(root, { done }) }, "Begin"))))));
}

function showNote(root, { done }) {
  const { note, d } = here();
  const list = h("ul", { class: "questions" });
  const body = h("div", { class: "note-body" });
  const left = h("span", { class: "pill" }, "");
  const s = slot();
  const redraw = () => {
    list.replaceChildren(...note.questions.map((q) => questionCard(q, d, redraw)));
    body.replaceChildren(marked(note, d, redraw));
    const n = note.questions.filter((q) => !d.answers[q.id]).length;
    left.textContent = n ? `${n} still to answer` : "All answered";
    next.disabled = n > 0;
  };
  const next = action(at + 1 < set.of ? "Next note" : "Finish and mark", async () => {
    if (at + 1 < set.of) {
      at += 1;
      return showNote(root, { done });
    }
    const out = await api("practice_mark", { decisions: D });
    return result(root, out, { done });
  }, { primary: true, slot: s });

  root.replaceChildren(h("div", { class: "review" },
    h("header", { class: "review-bar" },
      h("span", { class: "where" }, `Practice note ${at + 1} of ${set.of}`),
      h("span", { class: "spacer" }), left,
      h("button", { class: "btn btn-quiet", type: "button",
                    onclick: () => { forget(); done(); } }, "Stop")),
    h("main", { class: "review-main" },
      h("section", { class: "note-pane" }, body),
      h("aside", { class: "ask-pane" },
        h("h2", {}, "Words the tool was unsure about"),
        list,
        note.questions.length === 0
          ? h("p", { class: "muted" }, "None in this note — read it through and look for "
              + "yourself.") : null,
        h("p", { class: "small muted" }, "Select any words in the note to mark them yourself."),
        s,
        h("div", { class: "row row-end" }, next)))));
  redraw();
}

function result(root, out, { done }) {
  const good = out.passed;
  root.replaceChildren(h("div", { class: "review" },
    h("main", { class: "review-main one" }, h("section", { class: "panel card" },
      h("h1", {}, good ? "Passed" : "Not this time"),
      h("div", { class: good ? "callout" : "callout-warn callout" },
        h("ul", {}, (out.why || []).map((w) => h("li", {}, w)))),
      h("dl", { class: "kv-list" },
        h("dt", {}, "Identifiers that would have been released"),
        h("dd", {}, String(out.wrongly_kept)),
        h("dt", {}, "Found by reading, not by the tool"),
        h("dd", {}, `${out.planted_found} of ${out.planted_of}`),
        h("dt", {}, "Clinical details removed that should have stayed"),
        h("dd", {}, String(out.over_removed))),
      good && out.badge && out.badge.until
        ? h("p", {}, `Your practice badge runs until ${out.badge.until}. The date is `
            + "printed on every review report you sign.")
        : null,
      h("div", { class: "row row-end" },
        good
          ? h("button", { class: "btn btn-primary", type: "button",
                          onclick: () => { forget(); done(); } }, "Done")
          : h("div", { class: "row" },
              h("button", { class: "btn", type: "button",
                            onclick: () => { forget(); done(); } }, "Later"),
              h("button", { class: "btn btn-primary", type: "button", onclick: async () => {
                forget();
                return practiceScreen(root, { done });
              } }, "Try again")))))));
}

function standing(root, { done }) {
  const b = set.badge;
  root.replaceChildren(h("div", { class: "review" },
    h("main", { class: "review-main one" }, h("section", { class: "panel card" },
      h("h1", {}, "You passed the practice batch"),
      h("p", {}, `Earned ${b.earned}, good until ${b.until}`
        + (b.due_soon ? " — which is soon." : ".")),
      h("p", { class: "small muted" }, "You can work through the practice notes again whenever "
        + "you like; it does not affect your badge unless you pass, which renews it."),
      h("div", { class: "row row-end" },
        h("button", { class: "btn", type: "button", onclick: () => { forget(); done(); } },
          "Back"),
        h("button", { class: "btn btn-primary", type: "button", onclick: () => {
          set.badge = { certified: false };
          return intro(root, { done });
        } }, "Practise again"))))));
}

// ---- the card on Home -------------------------------------------------------------------
export function practiceCard({ onOpen }) {
  const me = S.me || {};
  const b = me.badge || {};
  if (b.certified && !b.due_soon) {
    return h("section", { class: "card" }, h("h2", {}, "Practice badge"),
      h("p", { class: "small muted" }, `Your badge runs until ${b.until}. It is printed on `
        + "every review report you sign."),
      h("div", { class: "row row-end" },
        h("button", { class: "btn btn-quiet", type: "button", onclick: onOpen },
          "Practise again")));
  }
  return h("section", { class: "card needs" },
    h("h2", {}, b.state === "run out" ? "Your badge has run out" : "Practice batch"),
    h("p", {}, b.state === "run out"
      ? "Work through the practice notes again to renew it. Until then you cannot be put on a "
        + "new batch."
      : "Before you can be put on a batch, work through a set of invented notes once. The "
        + "people in them do not exist."),
    h("div", { class: "row row-end" },
      h("button", { class: "btn btn-primary", type: "button", onclick: onOpen },
        b.state === "run out" ? "Renew my badge" : "Start the practice batch")));
}
