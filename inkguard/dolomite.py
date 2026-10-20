"""
dolomite — the tidy-up tool: reorder files a practice already holds as
de-identified, so the order no longer lines up with an appointment book.

A practice often has a folder of files it
already considers de-identified — cleaned here months ago, or somewhere else
entirely — sitting in the order they were made. That order is itself a clue:
the third file in the folder is the third patient of the morning. Putting
them in a new order costs nothing and removes that clue, and it needs no
setup.

Three rules, and the first one is the whole point.

  1. IT CHECKS BEFORE IT REORDERS. Every file is read and put through the
     engine first (the first 60,000 characters of each; see CHECK_CHARS). If anything that looks like an identifier is found, the
     tool STOPS and says how many files it saw it in — because a tidy folder
     must never be mistaken for a de-identified one. It will not reorganize
     files that still look identified, however the practice describes them.

  2. IT NEVER CLAIMS THE FILES ARE CLEAN. The note it writes says what it
     did — reordered and renamed — and says plainly that nothing was removed
     and that this is not de-identification. It states facts about the
     practice's own files only, and never anything about whatever tool
     produced them.

  3. IT NEVER TOUCHES THE ORIGINALS. The files are copied into a new folder
     in a new order under new names. The practice's own folder is left
     exactly as it was, so a mistake costs nothing.

Everything runs on the practice's computer. Nothing is sent anywhere, and no
copy is kept by the app.
"""

from __future__ import annotations

import random
import re
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

#: How the files come back out.
ORDERS = ("shuffled", "by_name")

#: Read at most this much of a file when checking it, so a big folder does
#: not take all afternoon. Anything after the first 60,000 characters of a
#: file is NOT checked, and identifiers can sit at the end of a note (an
#: addendum, a sign-off).
CHECK_CHARS = 60_000

#: A folder bigger than this is refused rather than half-done.
MAX_FILES = 5_000

_CODE = "ACDEFGHJKLMNPQRTUVWXY349"


class TidyError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code, self.detail = code, detail


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Look:
    """What the check found. Counts only: no text, no names, no paths."""
    files: int = 0
    unreadable: int = 0                               # a file that would not open
    unchecked: int = 0                                # a file with no text to check
    with_identifiers: int = 0
    kinds: dict = field(default_factory=dict)         # what kind, how many files
    clean: int = 0
    paths: list = field(default_factory=list)         # the files this look covered

    @property
    def safe_to_tidy(self) -> bool:
        """A folder is safe to reorder only when EVERY file in it was read
        and came back clean.

        A file nobody could check is not a clean file. A scan with no text
        layer, a picture, an archive, a file that would not open — the
        engine finds nothing in any of them because there is nothing to
        find, and counting that as clean would let the tool say a folder of
        scanned notes holds no identifiers."""
        return (self.files > 0 and self.with_identifiers == 0
                and self.unreadable == 0 and self.unchecked == 0)

    def public(self) -> dict:
        return {"files": self.files, "unreadable": self.unreadable,
                "unchecked": self.unchecked,
                "with_identifiers": self.with_identifiers, "clean": self.clean,
                "kinds": dict(sorted(self.kinds.items())),
                "safe_to_tidy": self.safe_to_tidy}


def _files_of(folder: Path) -> list[Path]:
    from inkguard import granite
    out = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        if p.name.startswith(".") or p.name in ("Thumbs.db",):
            continue
        out.append(p)
    if len(out) > MAX_FILES:
        raise TidyError("TIDY-TOO-BIG")
    return out


#: The only kinds of file whose words this tool can read. Anything else —
#: a picture, a scan with no text layer, an archive, a program — cannot be
#: checked, and an unchecked file stops the folder.
TEXT_SUFFIXES = {".txt", ".text", ".md", ".note", ".rtf", ".log"}


def _text_of(path: Path) -> str:
    """The words in one file, or "" when there are none to read.

    Raises TidyError("TIDY-UNCHECKABLE") for a kind of file this tool
    cannot read at all, so it is never mistaken for an empty one."""
    from inkguard import calcite
    suffix = path.suffix.lower()
    from inkguard import lapis
    if suffix in calcite.DOC_SUFFIXES:
        return lapis.strip(calcite.read_text(path))[:CHECK_CHARS]
    if suffix not in TEXT_SUFFIXES:
        raise TidyError("TIDY-UNCHECKABLE", suffix or "no extension")
    raw = path.read_bytes()[:CHECK_CHARS * 4]
    if b"\x00" in raw and raw[:2] not in (b"\xff\xfe", b"\xfe\xff"):   # not text
        raise TidyError("TIDY-UNCHECKABLE", suffix)
    return lapis.strip(calcite.decode_text(raw, cut=True))[:CHECK_CHARS]


def look(folder: Path) -> Look:
    """Read every file and check whether it still holds identifiers.

    This is the gate. A folder with even one identified file is refused:
    reordering it would leave the practice with a folder that looks tidied
    and is not de-identified."""
    from inkguard import contract
    folder = Path(folder)
    if not folder.is_dir():
        raise TidyError("TIDY-FOLDER")
    out = Look()
    found = _files_of(folder)
    out.paths = list(found)
    for p in found:
        out.files += 1
        try:
            text = _text_of(p)
        except TidyError:
            out.unchecked += 1
            continue
        except Exception:                              # noqa: BLE001
            out.unreadable += 1
            continue
        if not text.strip():
            # A readable file with no words in it: nothing to find, and
            # nothing that can be claimed about it either.
            out.unchecked += 1
            continue
        try:
            r = contract.deidentify(text, profile="safe_harbor", mode="mask")
        except Exception:                              # noqa: BLE001
            out.unreadable += 1
            continue
        if r.status != "clear":
            out.with_identifiers += 1
            out.kinds["could not be checked"] = out.kinds.get("could not be checked", 0) + 1
            continue
        # The engine reports where it changed the text; a file it changed at
        # all is a file that still held something identifying.
        kinds = {_plain_from(rep) for _s, _e, rep in (r.edits or [])}
        if kinds:
            out.with_identifiers += 1
            for k in kinds:
                out.kinds[k] = out.kinds.get(k, 0) + 1
        else:
            out.clean += 1
    if out.files == 0:
        raise TidyError("TIDY-EMPTY")
    return out


#: The engine's own placeholder, said in words a practice would use. Kept by
#: hand: it must match the engine's placeholder list (talc.PLACEHOLDER). A
#: placeholder missing here is shown as "something that could identify
#: someone".
_PLAIN_BY_TAG = {
    "NAME": "a person's name", "LOCATION": "a place or address", "DATE": "a date",
    "AGE": "an age over 89", "PHONE": "a telephone number", "FAX": "a fax number",
    "EMAIL": "an email address", "SSN": "a social security number",
    "MRN": "a record number", "PLAN ID": "a health plan number",
    "ACCOUNT": "an account number", "LICENSE": "a licence number",
    "VEHICLE ID": "a vehicle number", "DEVICE ID": "a device number",
    "URL": "a web address", "IP": "a computer address",
    "BIOMETRIC": "a biometric identifier", "PHOTO": "a photograph of a face",
    "ID": "a number that belongs to one person", "EMPLOYER": "an employer",
    "FACILITY": "a clinic or hospital", "REDACTED": "something identifying",
}

_TAG = re.compile(r"\[([A-Z_0-9 ]+)\]")


def _plain_from(replacement: str) -> str:
    """What the engine put in place of a word, said in plain words. The
    placeholder is all this tool needs: it never keeps the word itself."""
    rep = (replacement or "").strip()
    if re.fullmatch(r"\d{4}", rep):
        return "a date"                   # Safe Harbor keeps the year and drops the rest
    if re.fullmatch(r"\d{3}(?:XX|\*\*)", rep) or rep in ("90 or older", "90+"):
        return "a ZIP code or an age over 89"
    m = _TAG.match(rep)
    tag = m.group(1).strip() if m else ""
    return _PLAIN_BY_TAG.get(tag, "something that could identify someone")





def _new_name(n: int, suffix: str, rng: random.Random) -> str:
    code = "".join(rng.choice(_CODE) for _ in range(5))
    return f"{n:04d}-{code}{suffix}"


def tidy(folder: Path, out_dir: Path, *, order: str = "shuffled",
         checked: Look | None = None, seed: str | None = None) -> dict:
    """Copy the files into `out_dir` in a new order under new names.

    The check runs again here even if it was run a moment ago: a folder can
    change between looking and tidying, and the gate must hold at the moment
    the copies are made, not a minute earlier."""
    folder, out_dir = Path(folder), Path(out_dir)
    if order not in ORDERS:
        raise TidyError("TIDY-ORDER")
    try:
        out_dir.resolve().relative_to(folder.resolve())
        raise TidyError("TIDY-OVERLAP")
    except ValueError:
        pass
    try:
        folder.resolve().relative_to(out_dir.resolve())
        raise TidyError("TIDY-OVERLAP")
    except ValueError:
        pass
    found = look(folder)
    if found.with_identifiers:
        raise TidyError("TIDY-STILL-IDENTIFIED")
    if found.unchecked or found.unreadable:
        raise TidyError("TIDY-CANNOT-CHECK")
    if not found.safe_to_tidy:
        raise TidyError("TIDY-EMPTY")
    # EXACTLY the files the check covered, not a fresh listing: a file put
    # into the folder between the two would be copied unchecked.
    files = list(found.paths)
    rng = random.Random(seed or secrets.token_hex(16))
    if order == "shuffled":
        rng.shuffle(files)
    else:
        files.sort(key=lambda p: p.name.lower())
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for n, p in enumerate(files, start=1):
        dst = out_dir / _new_name(n, p.suffix.lower(), rng)
        while dst.exists():
            dst = out_dir / _new_name(n, p.suffix.lower(), rng)
        shutil.copyfile(p, dst)                        # the original is not touched
        made.append(dst)
    (out_dir / "What this folder is.md").write_text(note(found, order, len(made)),
                                                    encoding="utf-8")
    return {"files": len(made), "order": order, "out": str(out_dir), "checked": found.public()}


def note(found: Look, order: str, made: int) -> str:
    """The note that goes in the new folder. It says what was done and, just
    as plainly, what was NOT done."""
    how = ("put in a random order" if order == "shuffled" else "put in order of file name")
    return f"""# What this folder is

{made} file(s) were copied here from a folder of your own, {how}, and given
new names in that order.

## What was done

- The files were **copied**. Your own folder was not changed, renamed or
  deleted, and nothing was moved out of it.
- The new names carry no information about who is in which file. If the
  files were put in order of file name, the new order follows the old names.
- Every one of the {found.files} file(s) was read first, and the first 60,000
  characters of each were checked for identifiers; none were found. Text
  after that point was not checked. A file this tool cannot
  read — a picture, a scan with no text in it, an archive — stops the whole
  folder rather than being copied unchecked, so there are none of those here.

## What was NOT done

**Nothing was removed from these files.** This tool does not de-identify
anything — it only changes the order and the names. If a file held something
identifying, this folder holds it too.

This is not a de-identification record, and it is not evidence that these
files are de-identified. It says only what this tool did to them.

Written on this computer, {_now()}. Nothing was sent anywhere.
"""
