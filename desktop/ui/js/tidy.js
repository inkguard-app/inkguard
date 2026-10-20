// The tidy-up tool: reorder files the practice already treats as cleaned,
// so the order no longer matches an appointment book.
//
// Works before anyone has set the app up. It removes nothing, and the
// screen says so in those words, twice, because a folder that merely looks
// tidy must never be mistaken for a redacted one.

import { call, hasPicker, pick } from "./bridge.js";
import { api } from "./state.js";
import { action, brand, field, h, input, mount, slot, toast } from "./ui.js";

// The tool works signed in (through the session) and before any of this is
// set up (straight to the service, which allows it only while there are no
// accounts at all). `send` is the only difference between the two.
export function tidyCard({ reload, send = api, heading = true } = {}) {
  const s = slot();
  const from = input({ placeholder: "Folder the files are in now" });
  const to = input({ placeholder: "Folder for the tidied copies" });
  const found = h("div", {});
  const fromRow = h("div", { class: "row" }, from);
  const toRow = h("div", { class: "row" }, to);
  if (hasPicker()) {
    fromRow.append(h("button", { class: "btn", type: "button", onclick: async () => {
      const p = await pick("folder");
      if (p) from.value = p;
    } }, "Choose…"));
    toRow.append(h("button", { class: "btn", type: "button", onclick: async () => {
      const p = await pick("folder");
      if (p) to.value = p;
    } }, "Choose…"));
  }
  let how = "shuffled";
  const orders = [["shuffled", "A random order"], ["by_name", "In order of file name"]]
    .map(([id, label]) => {
      const r = h("input", { type: "radio", name: "tidy-how", id: "tidy-" + id,
                             checked: id === how ? "checked" : null,
                             onchange: () => { how = id; } });
      return h("label", { class: "check", for: "tidy-" + id }, r, h("span", {}, label));
    });

  const run = action("Make tidied copies", async () => {
    const r = await send("tidy_run", { folder: from.value.trim(), out: to.value.trim(), order: how });
    toast(`${r.files} file${r.files === 1 ? "" : "s"} copied in a new order.`);
    found.replaceChildren(h("p", { class: "small muted" },
      "The copies and a note saying what was done are in the folder you chose. "
      + "Nothing was removed from them, and your own folder is unchanged."));
  }, { slot: s });
  run.disabled = true;

  const check = action("Check these files first", async () => {
    const r = await send("tidy_look", { folder: from.value.trim() });
    run.disabled = !r.safe_to_tidy;
    if (r.safe_to_tidy) {
      found.replaceChildren(h("div", { class: "callout" },
        `All ${r.files} file${r.files === 1 ? "" : "s"} were read and nothing identifying was `
        + "found by this tool. They can be reordered; a person should still check them."));
      return;
    }
    const kinds = Object.entries(r.kinds || {})
      .map(([what, n]) => h("li", {}, `${what} — in ${n} file${n === 1 ? "" : "s"}`));
    const stuck = (r.unchecked || 0) + (r.unreadable || 0);
    found.replaceChildren(h("div", { class: "callout callout-warn" },
      r.with_identifiers
        ? h("p", {}, r.with_identifiers === 1
            ? `1 of ${r.files} file${r.files === 1 ? "" : "s"} still holds something identifying, `
              + "so the folder will not be reordered."
            : `${r.with_identifiers} of ${r.files} files still hold something identifying, so the `
              + "folder will not be reordered.")
        : null,
      kinds.length ? h("ul", {}, kinds) : null,
      stuck
        ? h("p", {}, `${stuck} file${stuck === 1 ? "" : "s"} could not be read by this tool \u2014 `
            + "a picture, a scan with no text in it, or a file that would not open. A file nobody "
            + "has checked must never be filed as if it had been, so the folder is left alone.")
        : null,
      h("p", { class: "small" }, "A tidy folder must never be mistaken for a redacted one. "
        + "Run these through a batch first, or take them out of the folder and tidy the rest.")));
  }, { slot: s });

  return h("section", { class: "card" }, heading ? h("h2", {}, "Tidy up a folder") : null,
    h("p", { class: "small muted" },
      "For files you already treat as cleaned — here or anywhere else. The copies "
      + "come out in a new order under new names, so the order no longer lines up with an "
      + "appointment book."),
    h("p", { class: "small muted" },
      h("b", {}, "Nothing is removed from the files."), " This is not de-identification. Every "
      + "file is read and checked first, and files that still look identified are refused."),
    field("From", fromRow),
    field("To", toRow, "A separate folder, not inside the one above. Your own files are copied, "
      + "never moved or changed."),
    h("fieldset", { class: "field" }, h("legend", {}, "New order"), orders),
    found, s,
    h("div", { class: "row row-end" }, check, run));
}


// ---- before anything is set up ------------------------------------------------------------
//
// Usable before the app is set up. The way back to setup is one click.
export function tidyOnly(root, { back }) {
  mount(root, h("div", { class: "center-screen wide" },
    h("div", { class: "panel card" }, brand(),
      h("h1", {}, "Tidy up a folder"),
      h("p", { class: "muted" }, "This part needs no setup. Nothing is sent anywhere, and your "
        + "own files are copied, never changed."),
      tidyCard({ send: call, reload: () => {}, heading: false }),
      h("div", { class: "row row-end mt" },
        h("button", { class: "btn btn-quiet", type: "button", onclick: back },
          "Back to setting up")))));
}
