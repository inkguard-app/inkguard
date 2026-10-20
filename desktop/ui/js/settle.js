// The clinician's screen: decide what the two reviewers did not agree on,
// answer the small-numbers warnings, choose the shapes, and release.
//
// Like the review screen this one is bare, because it shows patient words.
// Four steps, one at a time, and nothing else on the screen:
//
//   1. decide   — each item the reviewers split on, with both their answers
//   2. check    — the small-numbers warnings
//   3. release  — shapes, arrangement, folder, the statement
//   4. working  — a swirl, then where the files are
//
// Nothing is destroyed until the files are written and verified, and the
// screen says so in those words.

import { canSendOutside, hasPicker, pick, sendOutside } from "./bridge.js";
import { controls, enter, leave as leaveRoom } from "./room.js";
import { S, api } from "./state.js";
import { action, h, inkMark, input, slot, toast } from "./ui.js";

let open = null;                // the batch being settled
let note = null;                // the note on screen
const R = {};                   // rulings held while a note is open
let poll = null;

const FATE = { remove: "Take it out", keep: "Leave it in" };
const SAID = { remove: "take it out", keep: "leave it in", unsure: "was not sure" };
const RULING_WORDS = {
  generalize: "I will make the wording less exact myself",
  withhold: "Leave this record out",
  accept: "Accept it on the record",
};

export function forget() {
  leaveRoom();
  if (poll) clearTimeout(poll);
  poll = null;
  open = null;
  note = null;
  for (const k of Object.keys(R)) delete R[k];
}

// ---- step 1: decide ----------------------------------------------------------------------

export async function settleScreen(root, bid, { done }) {
  open = await api("settle_open", { batch: bid });
  open.batch = bid;
  // The clinician reads the same patient words the reviewers did, so the
  // same room closes around them: copying and printing refused, the notes
  // veiled when the window loses focus, a break, and a way out.
  enter(bid);
  const ex = (open.export || {});
  if (ex.stage === "working") return working(root, bid, { done });
  if (open.left && open.left.length) return showNote(root, bid, open.left[0], { done });
  return checkScreen(root, bid, { done });
}

function marked(text, removals, generalized, bucket) {
  const spans = [...removals.map((r) => ({ ...r, what: "removal" })),
                 ...generalized.map((g) => ({ ...g, what: "general" })),
                 ...bucket.map((b) => ({ ...b, what: "split" }))];
  spans.sort((a, b) => a.start - b.start || a.end - b.end);
  const out = [];
  let at = 0;
  for (const sp of spans) {
    if (sp.start < at) continue;
    if (sp.start > at) out.push(text.slice(at, sp.start));
    const word = text.slice(sp.start, sp.end);
    if (sp.what === "split") {
      out.push(h("mark", { class: "mark mark-unsure", id: "s-" + sp.key,
                           title: "The two reviewers did not agree" }, word));
    } else if (sp.what === "general") {
      out.push(h("mark", { class: "mark mark-added", title: "Made less exact" }, word));
    } else {
      out.push(h("mark", { class: "mark mark-tool", title: "Taken out" }, word));
    }
    at = sp.end;
  }
  if (at < text.length) out.push(text.slice(at));
  return h("div", { class: "note-text" }, out);
}

function itemCard(b, onChange) {
  const chosen = () => R[b.key];
  const buttons = Object.entries(FATE).map(([key, label]) => h("button", {
    type: "button", class: "btn pick" + (chosen() === key ? " on" : ""),
    disabled: key === "keep" && b.locked ? "disabled" : null,
    "aria-pressed": chosen() === key ? "true" : "false",
    onclick: () => { R[b.key] = key; onChange(); },
  }, label));
  return h("li", { class: "question" + (chosen() ? " answered" : ""), id: "b-" + b.key },
    h("div", { class: "qword" },
      h("button", { class: "linkbtn", type: "button", onclick: () => {
        const el = document.getElementById("s-" + b.key);
        if (el) {
          el.scrollIntoView({ block: "center" });
          el.classList.add("flash");
          setTimeout(() => el.classList.remove("flash"), 1200);
        }
      } }, "“" + b.word + "”"),
      h("span", { class: "small muted" }, " the tool thinks: " + (b.guess || "not sure"))),
    h("div", { class: "small muted" },
      "One reviewer said " + (SAID[b.votes.R1] || b.votes.R1)
      + "; the other said " + (SAID[b.votes.R2] || b.votes.R2) + "."),
    h("div", { class: "row" }, buttons));
}

async function showNote(root, bid, nid, { done }) {
  note = await api("settle_note", { batch: bid, note: nid });
  for (const k of Object.keys(R)) delete R[k];
  Object.assign(R, note.ruled || {});
  const s = slot();
  const list = h("ul", { class: "questions" });
  const body = h("div", { class: "note-body" });
  const left = h("span", { class: "pill" }, "");
  const redraw = () => {
    list.replaceChildren(...(note.bucket || []).map((b) => itemCard(b, redraw)));
    body.replaceChildren(marked(note.text, note.removals, note.generalized, note.bucket || []));
    const n = (note.bucket || []).filter((b) => !R[b.key]).length;
    left.textContent = n ? `${n} still to decide` : "All decided";
    go.disabled = n > 0;
  };
  const go = action("Save and next", async () => {
    await api("settle_rule", { batch: bid, note: nid, rulings: R, seconds: 0 });
    const again = await api("settle_open", { batch: bid });
    const next = (again.left || []).filter((x) => x !== nid)[0];
    if (next) return showNote(root, bid, next, { done });
    return checkScreen(root, bid, { done });
  }, { primary: true, slot: s });

  root.replaceChildren(h("div", { class: "review room" },
    h("header", { class: "review-bar" },
      h("span", { class: "where" }, `Note ${note.place} of ${note.of}`),
      h("span", { class: "rail", role: "progressbar", "aria-label": "How far through this batch",
                  "aria-valuemin": "0", "aria-valuemax": String(note.of),
                  "aria-valuenow": String(note.place - 1) },
        h("span", { class: "rail-fill w"
                    + Math.round(((note.place - 1) / note.of) * 10) * 10 })),
      h("span", { class: "spacer" }), left,
      ...controls(bid, {
        onBack: () => showNote(root, bid, nid, { done }),
        onGone: () => {
          forget();
          S.session = null;
          S.hooks.ended({ code: "SESSION-ENDED" });
        },
      })),
    h("main", { class: "review-main" },
      h("section", { class: "note-pane" }, body),
      h("aside", { class: "ask-pane" },
        h("h2", {}, "Where the two reviewers differed"),
        list,
        (note.bucket || []).length === 0
          ? h("p", { class: "muted" }, "Nothing in this note — they agreed throughout.") : null,
        s,
        h("div", { class: "row row-end" }, go)))));
  redraw();
}

// ---- step 2: the small-numbers check -----------------------------------------------------

async function checkScreen(root, bid, { done }) {
  const check = await api("settle_check", { batch: bid });
  const chosen = { ...(check.ruled || {}) };
  const s = slot();
  const rows = (check.odd_one_out || []).map((r) => {
    // The ruling is on the CONDITION, so it applies to every note it was
    // found in — leaving out only the first of a patient's notes would
    // leave the patient just as singled out.
    const nid = r.notes[0];
    const buttons = Object.entries(RULING_WORDS).map(([key, label]) => h("button", {
      type: "button", class: "btn pick" + (chosen[nid] === key ? " on" : ""),
      "aria-pressed": chosen[nid] === key ? "true" : "false",
      onclick: () => { for (const n of r.notes) chosen[n] = key; redraw(); },
    }, label));
    return h("li", { class: "question" + (chosen[nid] ? " answered" : "") },
      h("div", { class: "qword" }, h("strong", {}, r.condition),
        h("span", { class: "small muted" },
          ` — ${r.patients} patient${r.patients === 1 ? "" : "s"} in this batch`)),
      h("div", { class: "row" }, buttons));
  });
  // A rare condition is about the PATIENT, so the ruling covers every one of
  // their notes in this batch.
  const rareRows = (check.rare_in_world || []).map((r) => {
    const buttons = Object.entries(RULING_WORDS).map(([key, label]) => h("button", {
      type: "button", class: "btn pick" + (chosen[r.notes[0]] === key ? " on" : ""),
      "aria-pressed": chosen[r.notes[0]] === key ? "true" : "false",
      onclick: () => { for (const n of r.notes) chosen[n] = key; redraw(); },
    }, label));
    const also = (r.also || []).length ? r.also.join(", ") : "nothing else in the note";
    return h("li", { class: "question" + (chosen[r.notes[0]] ? " answered" : "") },
      h("div", { class: "qword" }, h("strong", {}, (r.kinds || []).includes("event")
          ? "A newsworthy event that can identify everyone in it" : "A condition that is rare anywhere"),
        h("span", { class: "small muted" },
          ` \u2014 in ${r.flags} place${r.flags === 1 ? "" : "s"}, alongside ${also}`)),
      h("div", { class: "row" }, buttons));
  });
  const list = h("ul", { class: "questions" }, rows);
  const rareList = h("ul", { class: "questions" }, rareRows);
  const go = action("Choose how the files come back", async () => {
    if (Object.keys(chosen).length) {
      await api("settle_small", { batch: bid, rulings: chosen });
    }
    return releaseScreen(root, bid, check, { done });
  }, { primary: true, slot: s });
  const redraw = () => {
    const undone = [...(check.odd_one_out || []), ...(check.rare_in_world || [])]
      .some((r) => r.notes.some((n) => !chosen[n]));
    go.disabled = undone;
    root.replaceChildren(screen());
  };
  const screen = () => h("div", { class: "review room" },
    h("header", { class: "review-bar" },
      h("span", { class: "where" }, "Small numbers"),
      h("span", { class: "spacer" }),
      h("button", { class: "btn btn-quiet", type: "button",
                    onclick: () => { forget(); toast("Saved. You will pick up here next time."); done(); } },
        "Save and continue later")),
    h("main", { class: "review-main one" }, h("section", { class: "panel card" },
      h("h1", {}, "What could still single someone out"),
      check.small_set_note ? h("div", { class: "callout" }, check.small_set_note) : null,
      (check.odd_one_out || []).length
        ? h("div", {},
            h("p", {}, "A condition only one patient in this batch carries makes that patient the "
              + "only one of their kind in the set, whatever else was taken out."),
            list)
        : h("p", {}, "Nothing in this batch falls at or below the warning line."),
      (check.rare_in_world || []).length
        ? h("div", {},
            h("h2", {}, "Conditions that are rare anywhere"),
            h("p", {}, "A condition few people in the world have can identify someone on its own, "
              + "especially next to an age, a job or a word like \u201conly\u201d. This is about "
              + "the patient, so your answer covers every note of theirs in this batch."),
            rareList)
        : null,
      (check.held || []).length
        ? h("div", { class: "callout callout-warn" },
            `${check.held.length} record${check.held.length === 1 ? " was" : "s were"} not processed `
            + "by the tool and will not be released. The reason for each is in the review report.")
        : null,
      s,
      h("div", { class: "row row-end" }, go))));
  redraw();
}

// ---- step 3: release ---------------------------------------------------------------------

function releaseScreen(root, bid, check, { done }) {
  const s = slot();
  const ticks = {};
  const shapes = (open.shapes || []).map((sh) => {
    ticks[sh.id] = h("input", { type: "checkbox", id: "sh-" + sh.id,
                                checked: sh.id === "research" ? "checked" : null,
                                onchange: () => redraw() });
    return h("label", { class: "check", for: "sh-" + sh.id }, ticks[sh.id],
      h("span", {}, sh.label));
  });
  let how = "shuffled";
  const arrangements = (open.arrangements || []).map((a) => {
    const r = h("input", { type: "radio", name: "how", id: "how-" + a.id,
                           checked: a.id === how ? "checked" : null,
                           onchange: () => { how = a.id; } });
    return h("label", { class: "check", for: "how-" + a.id }, r, h("span", {}, a.label));
  });
  // How each removal looks in the finished files: typed or opaque markers.
  let markers = "typed";
  const markerChoices = [
    ["typed", "Typed markers: [NAME], [DATE], [MRN]. Readers can tell what kind of thing was removed."],
    ["opaque", "Opaque markers: every removal reads [REMOVED]. Nothing on the page says what was there."],
  ].map(([id, label]) => h("label", { class: "check", for: "markers-" + id },
    h("input", { type: "radio", name: "markers", id: "markers-" + id,
                 checked: id === markers ? "checked" : null, onchange: () => { markers = id; } }),
    h("span", {}, label)));
  const folder = input({ placeholder: "Full path of the folder for the finished files" });
  const row = h("div", { class: "row" }, folder);
  if (hasPicker()) {
    row.append(h("button", { class: "btn", type: "button", onclick: async () => {
      const p = await pick("folder");
      if (p) { folder.value = p; redraw(); }
    } }, "Choose folder…"));
  }
  const tick = h("input", { type: "checkbox", id: "attest-doc", onchange: () => redraw() });
  const go = action("Release the files", async () => {
    const chosen = Object.keys(ticks).filter((k) => ticks[k].checked);
    await api("export", { batch: bid, shapes: chosen, arrangement: how, markers,
                          out: folder.value.trim(), attest: true });
    return working(root, bid, { done });
  }, { primary: true, slot: s });
  const redraw = () => {
    go.disabled = !tick.checked || !folder.value.trim()
      || !Object.keys(ticks).some((k) => ticks[k].checked);
  };
  root.replaceChildren(h("div", { class: "review room" },
    h("header", { class: "review-bar" },
      h("span", { class: "where" }, "How the files come back"),
      h("span", { class: "spacer" }),
      h("button", { class: "btn btn-quiet", type: "button",
                    onclick: () => { forget(); toast("Saved. You will pick up here next time."); done(); } },
        "Save and continue later")),
    h("main", { class: "review-main one" }, h("section", { class: "panel card" },
      h("h1", {}, "Choose what you take away"),
      h("fieldset", { class: "field" }, h("legend", {}, "Shapes — take any or all"), shapes),
      h("fieldset", { class: "field" }, h("legend", {}, "Arrangement"), arrangements),
      h("fieldset", { class: "field" }, h("legend", {}, "Markers"), markerChoices),
      h("div", { class: "field" }, h("label", { class: "label" }, "Where to put them"), row),
      h("label", { class: "check attest", for: "attest-doc" }, tick,
        h("span", {}, open.attestation || "")),
      h("p", { class: "small muted" },
        "The files are written and checked first. Only then is this batch's working copy "
        + "destroyed — key first, then the files. Your own folder is never touched."),
      s,
      h("div", { class: "row row-end" }, go)))));
  redraw();
}

// ---- step 4: working, then done ----------------------------------------------------------

function working(root, bid, { done }) {
  root.replaceChildren(h("div", { class: "review room" },
    h("main", { class: "review-main one" }, h("section", { class: "panel card center" },
      inkMark("big"),
      h("div", { class: "progress", role: "progressbar", "aria-label": "Releasing the files" },
        h("span", { class: "w50" }))))));
  const tick = async () => {
    let st;
    try { st = await api("export_status", { batch: bid }); } catch (e) { st = null; }
    const ex = (st && st.export) || {};
    if (ex.stage === "done") return finished(root, bid, ex, { done });
    if (ex.stage === "stopped") return stopped(root, bid, ex, { done });
    poll = setTimeout(tick, 2000);
    return null;
  };
  poll = setTimeout(tick, 1500);
}

async function finished(root, bid, ex, { done }) {
  const s = slot();
  // The tick-list comes from the core, so one list serves the screens, the
  // report and the counts we keep.
  let asked = { issues: [] };
  try { asked = await api("feedback_open", { batch: bid }); } catch (e) { /* prose still works */ }
  const ticks = {};
  const boxes = (asked.issues || []).map((it) => {
    ticks[it.id] = h("input", { type: "checkbox", id: "fb-" + it.id });
    return h("label", { class: "check", for: "fb-" + it.id }, ticks[it.id],
      h("span", {}, it.label));
  });
  const nothing = h("input", { type: "checkbox", id: "fb-none",
    onchange: () => {
      for (const k of Object.keys(ticks)) {
        ticks[k].checked = nothing.checked ? false : ticks[k].checked;
        ticks[k].disabled = nothing.checked ? "disabled" : null;
      }
    } });
  const note = h("textarea", { class: "input", rows: "3",
    placeholder: "Anything the tool got wrong, in general terms" });
  const sugg = h("textarea", { class: "input", rows: "2",
    placeholder: "Anything that would have made this easier" });
  const result = h("div", {});
  const send = action("Send this back", async () => {
    const r = await api("feedback_save", {
      batch: bid, note: note.value, suggestion: sugg.value,
      issues: nothing.checked ? [] : Object.keys(ticks).filter((k) => ticks[k].checked),
    });
    if (!r.saved) {
      result.replaceChildren(h("div", { class: "callout callout-warn" }, r.why));
      return;
    }
    forget();
    const thanks = h("div", { class: "callout" },
      "Thank you. The report is saved beside your files. It carries codes, counts and your own "
      + "words, checked for patient details.");
    const parts = [thanks];
    if (canSendOutside() && r.text) {
      // Nothing leaves this computer from here: this opens the person's own
      // email program with the report filled in, and they send it.
      const said = h("p", { class: "small muted" },
        "To send it to the project, open it in your own email program. You will see exactly "
        + "what is sent, and nothing goes until you press Send.");
      const opened = h("div", {});
      const via = (how) => async () => {
        try {
          await sendOutside(how, r.text);
          opened.replaceChildren(h("p", { class: "small muted" },
            how === "email" ? "Your email program should now show the report, ready to send."
              : "Your browser should now show the issue, ready to submit."));
        } catch (e) {
          opened.replaceChildren(h("div", { class: "callout callout-warn" }, e.message));
        }
      };
      // Email only: a GitHub issue is public, so a report is never opened
      // there from the app.
      parts.push(said, h("div", { class: "row" },
        action("Email it to the project", via("email"), { quiet: true })), opened);
    }
    // The person leaves when they are ready, so the send buttons stay put.
    parts.push(h("div", { class: "row row-end" }, action("Done", async () => done(), { primary: true })));
    result.replaceChildren(...parts);
    send.remove();
    skip.remove();
  }, { primary: true, slot: s });
  const skip = action("Not this time", async () => {
    await api("feedback_skip", { batch: bid });
    forget();
    done();
  }, { quiet: true });
  root.replaceChildren(h("div", { class: "review" },
    h("main", { class: "review-main one" }, h("section", { class: "panel card" },
      h("h1", {}, "Finished"),
      h("p", {}, `${ex.records} record${ex.records === 1 ? "" : "s"} released. `
        + "The files and their review report are in the folder you chose."),
      h("p", { class: "small muted" }, ex.out || ""),
      h("p", {}, "This batch's working copy has been destroyed. Your own files are where they "
        + "always were."),
      h("h2", {}, "Before you go"),
      h("p", { class: "small muted" },
        "This report carries support codes, counts and anything you type below \u2014 no text "
        + "from the notes and no file. What you type is checked before anything is written, and "
        + "a sentence that still looks like it holds patient details is refused rather than sent."),
      h("fieldset", { class: "field" },
        h("legend", {}, "Anything wrong this time?"),
        h("label", { class: "check", for: "fb-none" }, nothing,
          h("span", {}, "No problems")),
        ...boxes),
      h("div", { class: "field" },
        h("label", { class: "label" }, "Anything else, in your own words?"), note),
      h("div", { class: "field" },
        h("label", { class: "label" }, "What would have made this easier?"), sugg),
      result, s,
      h("div", { class: "row row-end" },
        skip,
        send)))));
}

function stopped(root, bid, ex, { done }) {
  root.replaceChildren(h("div", { class: "review" },
    h("main", { class: "review-main one" }, h("section", { class: "panel card" },
      h("h1", {}, "The files were not released"),
      h("div", { class: "error", role: "alert" },
        "The files were not written, so nothing was destroyed.",
        h("span", { class: "code" }, "Support code: " + (ex.error || "EXPORT-WRITE"))),
      h("p", {}, "Nothing was destroyed. This batch is still here and can be released again."),
      h("div", { class: "row row-end" },
        h("button", { class: "btn", type: "button", onclick: () => { forget(); toast("Saved. You will pick up here next time."); done(); } },
        "Save and continue later"),
        h("button", { class: "btn btn-primary", type: "button",
                      onclick: () => settleScreen(root, bid, { done }) }, "Try again"))))));
}
