// Batches: choosing a folder, watching the work, and the list of what is on
// the go. The practice's own files are read where they are and never moved,
// changed or deleted — the screens say so plainly, because it is the thing
// a doctor most needs to trust.

import { hasPicker, pick } from "./bridge.js";
import { S, api, isAdmin, isDoctor } from "./state.js";
import { action, dialog, errorBox, field, h, inkMark, input, put, slot, toast, when } from "./ui.js";

const STAGE_WORDS = {
  preparing: "Preparing", processing: "Preparing",
  ready_for_review: "Ready for review", in_review: "In review",
  ready_for_doctor: "Waiting for the doctor", settling: "With the doctor",
  exporting: "Releasing", signed_off: "Signed off", failed: "Stopped",
};

export function stageWord(stage) {
  return STAGE_WORDS[stage] || stage || "—";
}

// "about 42 pages · about 3 minutes left"
export function sizeWords(b) {
  const bits = [];
  if (b.pages) bits.push(`about ${b.pages} page${b.pages === 1 ? "" : "s"}`);
  const left = b.progress && b.progress.seconds_left;
  if (b.stage === "processing" && typeof left === "number") {
    bits.push(left < 60 ? "less than a minute left"
      : `about ${Math.round(left / 60)} minute${Math.round(left / 60) === 1 ? "" : "s"} left`);
  }
  return bits.join(" · ");
}

function daysWords(b) {
  if (b.days_left === null || b.days_left === undefined) return null;
  if (b.days_left < 0) return "out of time";
  if (b.days_left === 0) return "last day";
  return `${b.days_left} day${b.days_left === 1 ? "" : "s"} left`;
}

function progressBar(b) {
  // While it works, the app shows that it is working and nothing else: no
  // counts, no stage names, no estimate ticking down. It speaks when the
  // work is ready.
  const p = b.progress || {};
  const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
  return h("div", { class: "working" },
    inkMark(),
    h("div", { class: "progress", role: "progressbar", "aria-valuenow": String(pct),
               "aria-valuemin": "0", "aria-valuemax": "100",
               "aria-label": "Preparing this batch" },
      h("span", { class: "w" + (Math.round(pct / 10) * 10) })));
}

// ---- the list on Home -------------------------------------------------------------------
export function batchCard(batches, { onOpen, onNew, onReview, onSettle, reload }) {
  const rows = batches.map((b) => h("tr", {},
    h("td", {}, h("button", { class: "linkbtn", type: "button", onclick: () => onOpen(b.id) },
      stageWord(b.stage)), b.running ? h("span", { class: "small muted" }, " · working") : null),
    h("td", {}, `${b.counts.patients || 0} patient${b.counts.patients === 1 ? "" : "s"}`,
      h("div", { class: "small muted" }, `${b.counts.files || 0} files`
        + (b.pages ? ` · about ${b.pages} pages` : ""))),
    h("td", {}, b.reviewers.join(" and ")),
    h("td", {}, daysWords(b) ? h("span", { class: b.days_left <= 3 ? "badge badge-warn" : "small muted" },
      daysWords(b)) : h("span", { class: "small muted" }, "with the reviewers")),
    h("td", {}, when(b.created)),
    h("td", {}, b.my_review && b.my_review.can_review
      ? h("button", { class: "btn btn-primary", type: "button", onclick: () => onReview(b.id) },
          b.my_review.done ? "Carry on" : "Review")
      : (onSettle && (isDoctor() || isAdmin())
         && ["ready_for_doctor", "settling", "exporting"].includes(b.stage)
        ? h("button", { class: "btn btn-primary", type: "button", onclick: () => onSettle(b.id) },
            b.stage === "ready_for_doctor" ? "Settle" : "Carry on")
        : null))));
  return h("section", { class: "card" },
    h("div", { class: "row row-between" }, h("h2", {}, "Batches"),
      (isDoctor() || isAdmin()) ? h("button", { class: "btn btn-primary", type: "button", onclick: onNew },
        "New batch") : null),
    batches.length
      ? h("table", { class: "list" },
          h("thead", {}, h("tr", {}, ["Stage", "Size", "Reviewers", "Time", "Started", ""].map((t) => h("th", {}, t)))),
          h("tbody", {}, rows))
      : h("p", { class: "muted" }, (isDoctor() || isAdmin())
          ? "No batches yet. Choose New batch and pick the folder your files are in."
          : "Nothing to review yet. A batch appears here when a doctor sends it to you."),
    batches.some((b) => b.running) ? h("p", { class: "small muted mt" },
      "You can close the app while a batch is being prepared; it carries on from where it stopped.") : null);
}

// ---- starting one: ONE screen ------------------------------------------------------------
//
// Starting a batch is one screen: the folder is measured before anyone
// commits to it, and the confirmations are ticked one at a time and recorded
// word for word. The button that starts the work stays off until every one
// is ticked.
export function newBatch(people, points, { done }) {
  const s = slot();
  const folder = input({ placeholder: "Full path of the folder your files are in" });
  const eligible = people.filter((p) => !p.disabled && p.id !== (S.me && S.me.id)
    && (p.roles.includes("reviewer") || p.roles.includes("doctor")));
  const label = (p) => p.name
    + (p.has_phone ? "" : " — needs their phone linked")
    + (p.certified || !p.roles.includes("reviewer") ? "" : " — has not passed the practice batch");
  const pickable = (p) => h("option", {
    value: p.id, disabled: p.has_phone ? null : "disabled" }, label(p));
  const one = h("select", { class: "input" }, h("option", { value: "" }, "Choose…"),
    eligible.map(pickable));
  const two = h("select", { class: "input" }, h("option", { value: "" }, "Choose…"),
    eligible.map(pickable));
  const waiting = eligible.filter((p) => !p.has_phone);
  const lds = h("input", { type: "checkbox", id: "lds" });
  const row = h("div", { class: "row" }, folder);
  if (hasPicker()) {
    row.append(h("button", { class: "btn", type: "button", onclick: async () => {
      const p = await pick("folder");
      if (p) { folder.value = p; look(); }
    } }, "Choose folder…"));
  }

  // What the folder holds, shown in place as soon as there is a folder to
  // look at. Nothing is opened or changed by looking.
  const found = h("div", { class: "found" });
  let sized = null;
  const look = async () => {
    const path = folder.value.trim();
    if (!path) return;
    found.replaceChildren(h("p", { class: "small muted" }, "Looking…"));
    try {
      sized = await api("batch_assess", { folder: path });
    } catch (e) {
      sized = null;
      found.replaceChildren(errorBox(e));
      check();
      return;
    }
    found.replaceChildren(sizeSummary(sized));
    check();
  };
  folder.addEventListener("change", look);

  const ticks = points.map((p, i) => ({
    id: p.id, text: p.text, box: h("input", { type: "checkbox", id: "care" + i }),
  }));
  const go = action("Start preparing", async () => {
    const part = sized && sized.parts && sized.parts.length > 1 ? sized.parts[0] : null;
    const b = await api("batch_create", {
      folder: folder.value.trim(),
      reviewers: [one.value, two.value],
      profile: lds.checked ? "limited_data_set" : "safe_harbor",
      confirmed: ticks.map((t) => t.id),
      part: part ? { from: part.from, to: part.to, fingerprint: part.fingerprint } : null,
      part_label: part ? `Part 1 of ${sized.parts.length}` : null,
    });
    closeMe();
    done(b.id);
  }, { primary: true, slot: s });
  const check = () => {
    go.disabled = !(folder.value.trim() && one.value && two.value && sized
                    && ticks.every((t) => t.box.checked));
  };
  for (const el of [folder, one, two]) el.addEventListener("change", check);
  for (const t of ticks) t.box.addEventListener("change", check);

  const closeMe = dialog("New batch", (close) => h("div", {},
    h("p", {}, "Your files are read where they are. Nothing in your folder is moved, changed or "
      + "deleted — now or ever. The app keeps only its own working copies, encrypted, and erases "
      + "them when the batch is signed off."),
    field("Folder", row, "One folder per patient is the usual arrangement; a loose file counts "
      + "as its own patient."),
    found,
    field("First reviewer", one),
    field("Second reviewer", two, "Two different people, neither of them you: they review "
      + "independently."),
    waiting.length
      ? h("div", { class: "callout callout-warn" },
          (waiting.length === 1 ? waiting[0].name + " has" : waiting.length + " people have")
          + " no phone linked yet, so they cannot go on a batch. Each of them signs in and links "
          + "a phone under My account — it takes a minute, and any authenticator app will do.")
      : null,
    h("fieldset", { class: "field" }, h("legend", {}, "What to remove"),
      h("label", { class: "check", for: "lds" }, lds,
        h("span", {}, "Keep dates and town (a limited data set, for research under an "
          + "agreement)"))),
    // The barrier, on the button that starts the work.
    h("fieldset", { class: "field care" },
      h("legend", {}, "Please read each line and tick it"),
      ticks.map((t) => h("label", { class: "check care-point", for: t.box.id }, t.box,
        h("span", {}, t.text)))),
    h("p", { class: "small muted" }, "These confirmations are recorded with the batch and printed "
      + "on its review report. The app works fully offline; Wi-Fi can be off throughout."),
    s,
    h("div", { class: "row row-end" },
      h("button", { class: "btn", type: "button", onclick: close }, "Cancel"), go)));
  check();
  return closeMe;
}

// How long the reading will take, said as a floor — "at least about two
// hours" — never a ceiling, so nobody is told a job is shorter than it is.
function hours(mins) {
  if (!mins || mins < 10) return "a few minutes";
  if (mins < 90) return `about ${Math.max(10, Math.round(mins / 5) * 5)} minutes`;
  const h2 = mins / 60;
  return `about ${h2 < 3 ? h2.toFixed(1).replace(/\.0$/, "") : Math.round(h2)} hours`;
}

function sizeSummary(got) {
  const parts = got.parts || [];
  const acc = (got.accuracy && got.accuracy.lines) || [];
  return h("div", { class: "callout" },
    h("p", {}, `${got.patients} patient${got.patients === 1 ? "" : "s"}, `
      + `${got.files} file${got.files === 1 ? "" : "s"}, about ${got.pages} pages. `
      + `Each reviewer will be reading for at least ${hours(got.at_least_minutes)}.`),
    got.unreadable
      ? h("p", {}, `${got.unreadable} file${got.unreadable === 1 ? "" : "s"} cannot be read `
          + "(an image or scan, an old format, or a system file). They are listed on the batch "
          + "and in its review report, and never exported.")
      : null,
    parts.length > 1
      ? h("p", {}, `That is more than one sitting, so it is split into ${parts.length} parts on `
          + `patient boundaries — never through a patient. This starts Part 1 of ${parts.length} `
          + `(${parts[0].patients} patients, at least ${hours(parts[0].at_least_minutes)} each); `
          + "the rest wait their turn and appear on Home.")
      : null,
    acc.length
      ? h("details", {}, h("summary", {}, "What the tool is measured at"),
          h("ul", {}, acc.map((line) => h("li", { class: "small" },
            typeof line === "string" ? line : `${line.test}: ${line.result}`))),
          got.accuracy.plain ? h("p", { class: "small muted" }, got.accuracy.plain) : null)
      : null);
}

// ---- one batch --------------------------------------------------------------------------
export async function batchView(page, bid, { back, reload, onSettle }) {
  const b = await api("batch_status", { batch: bid });   // returned, so the caller knows to watch
  const s = slot();
  put(page,
    h("div", { class: "row row-between" },
      h("h1", {}, "Batch"),
      h("button", { class: "btn btn-quiet", type: "button", onclick: back }, "Back to Home")),
    h("div", { class: "context-banner" },
      h("span", { class: "title" }, stageWord(b.stage)),
      h("span", { class: "kv" }, "Patients ", h("b", {}, String(b.counts.patients || 0))),
      h("span", { class: "kv" }, "Files ", h("b", {}, String(b.counts.files || 0))),
      h("span", { class: "kv" }, "Pages ", h("b", {}, String(b.pages || 0))),
      h("span", { class: "kv" }, "Reviewers ", h("b", {}, b.reviewers.join(" and "))),
      b.days_left !== null && b.days_left !== undefined
        ? h("span", { class: "kv" }, "Time ", h("b", {}, daysWords(b))) : null));

  if (b.stage === "preparing") {
    // Waiting its turn behind another part: the swirl says it is in hand.
    put(page, h("section", { class: "card mt" }, h("h2", {}, "Preparing"), progressBar(b)));
  }
  if (b.stage === "processing") {
    put(page, h("section", { class: "card mt" }, h("h2", {}, "Preparing"),
      // Quiet while it works: the bar moves, and only a stopped or waiting
      // batch needs words, because then someone has to do something.
      b.paused ? h("p", { class: "small muted" }, "Waiting — the screen was locked.")
        : b.running ? null
          : h("p", { class: "small muted" }, "Stopped part way."),
      progressBar(b),
      b.running ? null : h("div", { class: "row row-end mt" },
        action("Carry on", async () => { await api("batch_resume", { batch: bid }); reload(); },
          { primary: true, slot: s }))));
  }
  if (b.stage === "failed") {
    put(page, h("section", { class: "card mt" }, h("h2", {}, "This batch stopped"),
      h("p", {}, "Nothing in your own files was changed."),
      b.error ? h("p", { class: "small muted" }, "Support code: " + b.error) : null,
      h("div", { class: "row row-end" },
        action("Try again", async () => { await api("batch_resume", { batch: bid }); reload(); },
          { slot: s }))));
  }
  if (b.stage === "ready_for_review") {
    put(page, h("section", { class: "card mt" }, h("h2", {}, "Ready for review"),
      h("p", {}, `${b.counts.prepared} note${b.counts.prepared === 1 ? "" : "s"} are ready for `
        + b.reviewers.join(" and ") + " to review, one at a time."),
      h("p", { class: "small muted" }, "Each reviewer opens it from Home with Review.")));
  }
  if (["ready_for_doctor", "settling"].includes(b.stage) && (isDoctor() || isAdmin())) {
    put(page, h("section", { class: "card mt" }, h("h2", {}, "Ready for you"),
      h("p", {}, "Both reviewers have signed off. What is left is yours: the items they did not "
        + "agree on, the small-numbers check, and choosing what you take away."),
      h("div", { class: "row row-end" },
        h("button", { class: "btn btn-primary", type: "button",
                      onclick: () => onSettle && onSettle(bid) },
          b.stage === "settling" ? "Carry on" : "Settle and release"))));
  }
  if (b.stage === "exporting") {
    put(page, h("section", { class: "card mt" }, h("h2", {}, "Releasing"), progressBar(b)));
  }
  if (b.remind && b.days_left !== null) {
    put(page, h("div", { class: "callout callout-warn mt" }, h("div", { class: "row" },
      h("span", {}, `This batch has ${daysWords(b)}. When the time runs out the app erases its `
        + "working copies; your own files are untouched."),
      (isDoctor() || isAdmin()) && !b.extended
        ? action("Add 7 days", async () => { await api("batch_extend", { batch: bid }); reload(); },
            { slot: s }) : null)));
  }

  const pool = b.pool || {};
  put(page, h("section", { class: "card mt" }, h("h2", {}, "Inside this batch"),
    h("p", { class: "small muted" }, "Everything the app makes while working lives in this batch's "
      + "own area, encrypted under a key of its own. Your files are read where they are and "
      + "never put inside it. When the batch is signed off, the clean copies come out and the key "
      + "is destroyed — nothing readable is left."),
    h("dl", { class: "kv-list pool" },
      h("dt", {}, "Files taken in"), h("dd", {}, String(pool.files_in || 0)),
      h("dt", {}, "Notes being worked on"), h("dd", {}, String(pool.notes_in || 0)),
      h("dt", {}, "Clean copies taken out"), h("dd", {}, String(pool.notes_out || 0)),
      h("dt", {}, "Still inside"), h("dd", {}, h("b", {}, String(pool.in_pool || 0)))),
    b.paused ? h("p", { class: "small" }, h("span", { class: "badge badge-warn" }, "Paused"),
      " Work waits while the screen is locked. Sign back in to carry on.") : null));

  const helpers = b.helpers || [];
  const hName = input({ placeholder: "Their name" });
  const hWhat = input({ placeholder: "What they helped with" });
  const hs = slot();
  put(page, h("section", { class: "card mt" }, h("h2", {}, "Who did the work"),
    h("p", { class: "small muted" }, "The two reviewers and the signing doctor are named on every "
      + "report. If anyone else helped — a student, an assistant, a colleague you asked — name them "
      + "here so the record shows who actually did the work. Names are added, never removed."),
    h("dl", { class: "kv-list" },
      h("dt", {}, "Reviewers"), h("dd", {}, b.reviewers.join(" and ")),
      h("dt", {}, "Started by"), h("dd", {}, b.created_by)),
    helpers.length
      ? h("ul", {}, helpers.map((x) => h("li", {}, x.name, x.what ? " — " + x.what : "",
          h("span", { class: "small muted" }, " · " + when(x.at)))))
      : h("p", { class: "small muted" }, "No helpers named."),
    b.stage !== "signed_off" ? h("div", {},
      h("div", { class: "row" }, field("Their name", hName),
        field("What they helped with", hWhat)), hs,
      h("div", { class: "row row-end" }, action("Name a helper", async () => {
        await api("batch_helper", { batch: bid, name: hName.value, what: hWhat.value });
        hName.value = hWhat.value = ""; reload();
      }, { slot: hs }))) : null));

  const aside = b.set_aside || [];
  put(page, h("section", { class: "card mt" },
    h("h2", {}, `Files not read (${aside.length})`),
    h("p", { class: "small muted" }, "Every file you dropped in is counted. These were not read, "
      + "so they are never part of what is exported."),
    aside.length
      ? h("table", { class: "list" },
          h("thead", {}, h("tr", {}, ["File", "Kind", "Why", "Code"].map((t) => h("th", {}, t)))),
          h("tbody", {}, aside.map((r) => h("tr", {},
            h("td", {}, r.id), h("td", {}, r.kind), h("td", {}, r.reason),
            h("td", {}, r.code ? h("code", {}, r.code) : "—")))))
      : h("p", {}, "Every file was read.")));

  if (isDoctor() || isAdmin()) {
    // RESTART: wipe everything and start again from your own files. A
    // restart report records who, when, why and how far the work had got.
    const why = h("textarea", { class: "input", rows: "2", maxlength: "500",
      placeholder: "Why are you starting again? (Recorded in the restart report. No patient details.)" });
    put(page, h("section", { class: "card mt danger-zone" }, h("h2", {}, "Wipe this batch and start again"),
      h("p", {}, "The app destroys this batch's key, so its working copies become unreadable at "
        + "once, and then deletes them. Your own files are not touched. A restart report is kept: "
        + "who wiped it, when, your reason, and how far the reviews had got. This cannot be undone."),
      h("div", { class: "field" }, why),
      s,
      h("div", { class: "row row-end" },
        action("Wipe and start again", async () => {
          const out = await api("batch_cancel", { batch: bid, reason: why.value });
          toast("Batch wiped. The restart report is shown here and kept in the app's records.");
          if (out && out.report) {
            page.replaceChildren(h("section", { class: "card" }, h("h2", {}, "Restart report"),
              h("pre", { class: "report" }, out.report),
              h("div", { class: "row row-end" }, h("button", { class: "btn", type: "button",
                onclick: () => back() }, "Done"))));
            return;
          }
          back();
        }, { danger: true, slot: s }))));
  }
  return b;
}
