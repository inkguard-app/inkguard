"""
calcite — document formats: Word (.docx, .docm), Excel (.xlsx, .xlsm), CSV/TSV and text PDF.

A clinical document hides text in more places than its page shows: tracked
changes still hold deleted words, comments name their authors, the file's
properties name whoever wrote and last saved it, headers repeat the patient's
name on every page, hidden sheets and rows keep whole tables, a thumbnail
image shows the first page, and a PDF can carry form values, notes and
attachments. Cleaning only the visible text would leave all of that in the file.

calcite reads the places these formats are known to hold text, runs the
engine over the text a reader would see, and writes a clean copy of the
same kind:

  - Text a reader sees (body, tables, headers and footers, footnotes, text
    boxes, cell values, sheet names) goes through the engine, with the
    file's own author names given to it as known names.
  - What cannot be checked is removed and counted, never carried over:
    tracked changes are accepted (deleted words are gone for good), and
    comments, hidden text, images, charts, embedded files, macros, field
    codes, links, document properties, thumbnails, custom data, pivot
    caches, data validations and defined names are dropped. Hidden sheets,
    rows and columns are cleaned and shown, so a reviewer sees everything.
  - A PDF becomes a flattened copy (each page an image, with removed text
    covered and images blanked), so removed text cannot be recovered from
    underneath, plus a plain-text copy. Each page is read back with OCR
    before it is covered; a page that shows words the tool did not read
    (text drawn as shapes, text in a form picture) is held for a person, and
    so is a PDF when OCR is not installed. Text that runs sideways, rotated
    or cropped pages, and filled form fields are held too.

Then every clean copy is SWEPT: every part of the output file is searched
for every string that was removed and for the names in the original's
hidden details (author, last editor, comment and revision authors). If any
is still there the file is held for a person. The report says how many
parts and strings were searched.

What this does not do. The sweep finds what was removed, reappearing; it
cannot find an identifier the engine never recognised in the first place.
Text inside pictures is not read (pictures are removed, scans are set aside).
Formats and file parts not listed here are dropped or the file is failed,
never passed through. A person's review is still the last check.

Offsets: the engine reads one joined text per document (a spreadsheet in
chunks of rows); calcite remembers, for every character, which text node,
cell or PDF character it came from, so each removal lands in the right
place. The engine's own output is reproduced from the same removals and
checked, as for plain text; a file whose output cannot be reproduced is held.
"""
from __future__ import annotations

import copy
import datetime as _dt
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

DOC_SUFFIXES = {".docx", ".docm", ".xlsx", ".xlsm", ".pdf", ".csv", ".tsv"}
MAX_UNZIPPED_BYTES = 300_000_000        # a zip bomb stops here
MAX_PARTS = 5_000
XLSX_CHUNK_ROWS = 40                     # rows per engine call in a sheet
MAX_CELL_CHARS = 20_000                  # a table cell longer than this is not a cell

# Author names that name nobody.
_GENERIC_AUTHORS = frozenset(s.casefold() for s in (
    "author", "user", "admin", "administrator", "owner", "unknown", "windows user",
    "microsoft office user", "microsoft office", "office user", "apple", "python-docx",
    "openpyxl", "reportlab", "anonymous", "guest", "default", "staff", "doctor", "provider",
    "nurse", "scribe", "system"))


class DocumentError(Exception):
    """A file that cannot be read safely. Nothing is written for it."""


# =====================================================================
# the joined text and where each character came from
# =====================================================================

class Canvas:
    """The text the engine reads. `owner[i]` is (slot, offset) for a
    character that lives in the file, or None for a separator calcite
    added (a newline between paragraphs, a column label in a sheet)."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self.owner: list[tuple[int, int] | None] = []
        self.slots: list[object] = []
        self.slot_text: list[str] = []
        self.dropped: list[object] = []

    def slot(self, ref: object, text: str) -> None:
        i = len(self.slots)
        self.slots.append(ref)
        self.slot_text.append(text)
        self._parts.append(text)
        self.owner.extend((i, k) for k in range(len(text)))

    def sep(self, text: str, *, droppable: bool = False, ref: object = None) -> None:
        """A character calcite added. `droppable`: inside a removal it goes
        too (a space between PDF words, a non-breaking hyphen element in
        Word, which is then removed from the file: `dropped`)."""
        self._parts.append(text)
        self.owner.extend([("drop", ref) if droppable else None] * len(text))

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def apply(self, edits: list[tuple[int, int, str]]) -> tuple[list[str], str]:
        """Apply removals. Returns (new text of every slot, the new joined
        text). A separator inside a removal stays (it is white space or a
        label calcite added); the replacement goes where the first stored
        character of the removal was."""
        deleted: set[tuple[int, int]] = set()
        insert: dict[tuple[int, int], str] = {}
        sep_insert: dict[int, str] = {}
        self.dropped = []

        def stored(o) -> bool:
            return o is not None and o[0] != "drop"
        for s, e, rep in sorted(edits):
            first = next((self.owner[i] for i in range(s, e) if stored(self.owner[i])), None)
            for i in range(s, e):
                o = self.owner[i]
                if stored(o):
                    deleted.add(o)
                elif o is not None and o[1] is not None and o[1] not in self.dropped:
                    self.dropped.append(o[1])
            if first is not None:
                insert[first] = insert.get(first, "") + rep
            else:
                sep_insert[s] = sep_insert.get(s, "") + rep
        new_slots = []
        for i, t in enumerate(self.slot_text):
            out = []
            for k, ch in enumerate(t):
                out.append(insert.get((i, k), ""))
                if (i, k) not in deleted:
                    out.append(ch)
            new_slots.append("".join(out))
        # The joined text as the file will now read, separators kept.
        joined, pos = [], 0
        text = self.text
        for s, e, rep in sorted(edits):
            joined.append(text[pos:s])
            joined.append(rep)
            joined.extend(text[i] for i in range(s, e) if self.owner[i] is None)   # kept separators
            pos = max(pos, e)
        joined.append(text[pos:])
        return new_slots, "".join(joined)


def _sanitize_piece(text: str) -> str:
    """The same normalisation the engine applies, one piece at a time, so the
    joined text is already what the engine will read. Through the contract,
    so the shell does not reach into the engine."""
    from inkguard import contract
    return contract.sanitize(text)


@dataclass
class Engine:
    """How the batch tool wants documents redacted."""
    evaluate: object
    profile_name: str = "safe_harbor"
    patient_key: str | None = None
    memory: set = field(default_factory=set)
    ctx: object = None
    #: Words two reviewers AND the responsible clinician agreed are not
    #: patient information (halite, desktop app, in development). The only
    #: way text a detector marked
    #: can stay on a page, and only for an exact whole-word match.
    keep: frozenset = frozenset()


@dataclass
class Pass:
    """One engine pass over one joined text."""
    result: object
    edits: list[tuple[int, int, str]]
    secrets: set[str]
    words_to_check: list[dict]
    names: set[str] = field(default_factory=set)       # removed person names


def _engine_pass(text: str, eng: Engine, names: set[str]) -> Pass:
    """Ask the core to redact one document's joined text, through the
    one contract (inkguard/contract.py) — never the engine's internals.

    Documents use placeholders: no pseudonym codes, and no date shifting,
    since a shifted date could not be placed back on the page exactly. Dates
    keep their year."""
    from inkguard import contract
    r = contract.deidentify(text, profile=eng.profile_name, mode="mask",
                            patient_key=eng.patient_key, memory=set(eng.memory) | set(names),
                            knowledge=eng.ctx, prevent_date_shift=True)
    if r.status == "stop":
        # r.reason is the contract's generic precaution line; the engine's
        # detailed error (which can quote fragments of the note) stays in the
        # private log and never reaches the shareable report.
        raise _Stop("failed", r.reason or "the tool stopped on this file as a precaution", r.raw)
    # The core sanitizes in place; a document whose text could not be
    # normalised, or whose edits do not rebuild the core's own output
    # exactly, is held rather than written.
    if not r.reproduced:
        raise _Stop("held", "the engine's output could not be placed on the file exactly", r.raw)
    edits, drops = r.edits, set()
    if eng.keep:
        edits, drops = _honour_keep(text, r.edits, eng.keep)
    secrets_left = {x for x in r.secrets if x not in drops}
    names_left = {x for x in r.removed_person_names if x not in drops}
    return Pass(r.raw, edits, secrets_left, r.words_to_check, names_left)


def _honour_keep(text: str, edits: list, keep) -> tuple[list, set]:
    """Drop the edits whose own words are in `keep`.

    A human determination can put text back on a page, so this gate is as
    narrow as it can be: the span's text must match a kept word EXACTLY,
    whole, after stripping surrounding space. Partial and overlapping
    matches are never kept, because half an identifier left behind is worse
    than the whole one."""
    out, dropped = [], set()
    for s, e, rep in edits:
        word = text[s:e].strip()
        if word and word in keep:
            dropped.add(word)
            continue
        out.append((s, e, rep))
    return out, dropped


class _Stop(Exception):
    def __init__(self, status: str, reason: str, result=None):
        super().__init__(reason)
        self.status, self.reason, self.result = status, reason, result


@dataclass
class DocResult:
    """What happened to one file."""
    source: str
    kind: str                                   # docx | xlsx | pdf
    status: str = "clean"                       # clean | held | failed
    reason: str = ""
    outputs: list[Path] = field(default_factory=list)
    hidden_found: dict[str, int] = field(default_factory=dict)
    removed: dict[str, int] = field(default_factory=dict)
    sweep: dict = field(default_factory=dict)
    words_to_check: list[dict] = field(default_factory=list)
    results: list = field(default_factory=list)  # the engine's results, for the run report
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        """For the report: counts only, no text."""
        return {"kind": self.kind, "status": self.status, "reason": self.reason,
                "hidden_content_found": dict(sorted(self.hidden_found.items())),
                "removed_by_type": dict(sorted(self.removed.items())),
                "sweep": self.sweep, "notes": self.notes}


def _count(d: dict, key: str, n: int = 1) -> None:
    if n:
        d[key] = d.get(key, 0) + n


def _author_names(values) -> set[str]:
    """Names from a file's hidden details that name a person."""
    out = set()
    for v in values:
        v = re.sub(r"\s+", " ", str(v or "")).strip()
        if not v or v.casefold() in _GENERIC_AUTHORS or len(v) < 3:
            continue
        if not re.fullmatch(r"[^\W\d_][\w .,'’-]*", v) or not re.search(r"[^\W\d_]{2}", v):
            continue
        out.add(v)
        for part in re.split(r"[\s,]+", v):
            part = part.strip(".,")
            if (len(part) >= 3 and part[:1].isupper() and part.casefold() not in _GENERIC_AUTHORS
                    and part.lower() not in _TITLE_WORDS):
                out.add(part)
    return out


# Word's default template names, which name nobody.
_DEFAULT_TEMPLATES = frozenset({"normal.dotm", "normal.dot", "normal", "normal.dotx"})

# Titles and credentials that sit beside a name but are not one.
_TITLE_WORDS = frozenset("""dr dr. mr mrs ms miss mx prof sir md do rn np pa phd mph msn bsn lpn
    cna crna dnp pharmd dds dmd od facs facp mba jr sr ii iii iv doctor nurse""".split())


# =====================================================================
# the sweep
# =====================================================================

def _secret_list(secrets: set[str], names: set[str] = frozenset()) -> list[str]:
    """What the sweep searches for: every removed string whole, and for
    person names also every single name word of 3+ letters, so a surname
    found alone elsewhere ("Lindqvist" after "Hubert Lindqvist" was
    removed) is caught too."""
    out = set()
    for s in set(secrets) | set(names):
        s = re.sub(r"\s+", " ", s or "").strip()
        if len(s) >= 3 and re.search(r"\w", s):
            out.add(s)
    for s in names:
        for tok in re.findall(r"[^\W\d_][^\W\d_'’-]{2,}", s or ""):
            if tok[:1].isupper() and tok.lower() not in _TITLE_WORDS:
                out.add(tok)
    return sorted(out, key=len, reverse=True)


def _common_word(tok: str) -> bool:
    try:
        from wordfreq import zipf_frequency
        return zipf_frequency(tok.lower(), "en") >= 3.5
    except Exception:  # noqa: BLE001
        return True


def _sweep_patterns(sec: list[str]) -> list[re.Pattern]:
    """Combined patterns. Letters and digits bound a match separately, so
    "_" and digits break words ("Lindqvist_Hubert", "MRN4471822"). A rare
    word matches in any case ("LINDQVIST"); a common word ("Rose") only as
    written, so "BP rose" is not a hit."""
    groups: dict[tuple[bool, bool, bool], list[str]] = {}
    for s in sec:
        body = r"\s+".join(re.escape(w) for w in s.split(" "))
        ignore_case = not (" " not in s and s.isalpha() and _common_word(s))
        groups.setdefault((s[0].isdigit(), s[-1].isdigit(), ignore_case), []).append(body)
    pats = []
    for (d0, d1, ic), bodies in groups.items():
        lead = r"(?<!\d)" if d0 else r"(?<![^\W\d_])"
        tail = r"(?!\d)" if d1 else r"(?![^\W\d_])"
        for i in range(0, len(bodies), 400):
            pats.append(re.compile(lead + "(?:" + "|".join(bodies[i:i + 400]) + ")" + tail,
                                   re.IGNORECASE if ic else 0))
    return pats


def _found(haystack: str, secret: str) -> bool:
    return any(p.search(haystack) for p in _sweep_patterns([secret]))


def sweep_texts(texts: dict[str, str], secrets: set[str], names: set[str] = frozenset()) -> dict:
    """Search every text for every secret. Returns counts, and the parts
    where something was found (part names only, never the strings)."""
    import html
    sec = _secret_list(secrets, names)
    pats = _sweep_patterns(sec) if sec else []
    hits: dict[str, int] = {}
    for name, t in texts.items():
        t2 = html.unescape(t) if "&" in t else t
        found = {m.group(0).lower() for p in pats for m in p.finditer(t2)}
        if found:
            hits[name] = len(found)
    return {"parts_searched": len(texts), "strings_searched": len(sec),
            "found": sum(hits.values()), "found_in": sorted(hits)}


def _decode_part(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="ignore")
    head = data[:100]
    if re.search(rb"encoding=[\"']utf-16", head, re.I) or (len(data) > 4 and data[1:2] == b"\x00"
                                                          and data[3:4] == b"\x00"):
        return data.decode("utf-16-le", errors="ignore")
    return data.decode("utf-8", errors="ignore")


def _xml_views(name: str, data: bytes) -> dict[str, str]:
    """A part as text: raw (attributes too), with the tags taken out, and
    with runs joined (a name split across runs reads whole)."""
    raw = _decode_part(data)
    if name.endswith((".xml", ".rels", ".vml")) or raw.lstrip().startswith("<"):
        stripped = re.sub(r"<[^>]+>", "", raw)
        joined = re.sub(r"</(?:w|a|m):t>\s*(?:<[^>]+>\s*)*?<(?:w|a|m):t(?:\s[^>]*)?>", "", raw)
        joined = re.sub(r"<[^>]+>", " ", joined)
        return {name: raw, name + " (text)": stripped, name + " (joined)": joined}
    return {name: data.decode("latin-1", errors="ignore"), name + " (utf-16)": _decode_part(data)}


def sweep_zip(path: Path, secrets: set[str], names: set[str] = frozenset(),
              extra: dict[str, str] | None = None) -> dict:
    texts: dict[str, str] = dict(extra or {})
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            texts.update(_xml_views(info.filename, z.read(info)))
    out = sweep_texts(texts, secrets, names)
    out["parts_searched"] = len({k.split(" (")[0] for k in texts})
    return out


def _read_zip(path: Path) -> dict[str, bytes]:
    head = path.read_bytes()[:8]
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise DocumentError("this file is password-protected or in the old binary format; "
                            "save it as a regular .docx/.xlsx without a password and try again")
    if not zipfile.is_zipfile(path):
        raise DocumentError("this file is not a valid Office document")
    parts: dict[str, bytes] = {}
    total = 0
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > MAX_PARTS:
            raise DocumentError("this file has too many parts to check safely")
        for info in infos:
            total += info.file_size
            if total > MAX_UNZIPPED_BYTES:
                raise DocumentError("this file unpacks to more than can be checked safely")
            if info.is_dir():
                continue
            parts[info.filename] = z.read(info)
    return parts


def _write_zip(path: Path, parts: dict[str, bytes]) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        order = ["[Content_Types].xml"] + sorted(n for n in parts if n != "[Content_Types].xml")
        for name in order:
            if name in parts:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, parts[name])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())


# =====================================================================
# WORD (.docx)
# =====================================================================

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
V = "urn:schemas-microsoft-com:vml"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
O = "urn:schemas-microsoft-com:office:office"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


_STORY_TYPES = ("/header", "/footer", "/footnotes", "/endnotes")
# Parts in which nothing can be checked, or which only describe people and
# history. Used only by _prune_package, which nothing calls at present:
# the Word writer keeps just the parts on its allow-list (_rebuild_docx).
_DROP_PREFIXES = ("word/media/", "word/embeddings/", "word/charts/", "word/diagrams/",
                  "word/activeX/", "word/glossary/", "customXml/", "word/printerSettings/",
                  "word/webextensions/", "docProps/thumbnail", "word/ink/", "word/drawings/")
_DROP_NAMES = ("word/comments.xml", "word/commentsExtended.xml", "word/commentsIds.xml",
               "word/commentsExtensible.xml", "word/people.xml", "docProps/custom.xml",
               "docProps/app.xml", "word/vbaProject.bin", "word/vbaData.xml")
_REVISION_REMOVE = ("del", "moveFrom", "rPrChange", "pPrChange", "sectPrChange", "tblPrChange",
                    "tcPrChange", "trPrChange", "tblGridChange", "tblPrExChange", "numberingChange",
                    "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
                    "cellIns", "cellDel", "cellMerge", "customXmlInsRangeStart", "customXmlInsRangeEnd",
                    "customXmlDelRangeStart", "customXmlDelRangeEnd",
                    "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd",
                    "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd")


def _unwrap(el) -> None:
    parent = el.getparent()
    if parent is None:
        return
    i = parent.index(el)
    for child in list(el):
        parent.insert(i, child)
        i += 1
    parent.remove(el)


def _rels_path(part: str) -> str:
    p = PurePosixPath(part)
    return str(p.parent / "_rels" / (p.name + ".rels"))


def _resolve(part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base = PurePosixPath(part).parent
    parts = []
    for seg in (base / target).parts:
        if seg == "..":
            if parts:
                parts.pop()
        elif seg != ".":
            parts.append(seg)
    return "/".join(parts)


def _parse(data: bytes):
    from lxml import etree
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False,
                             remove_blank_text=False)
    return etree.fromstring(data, parser)


def _tostring(root) -> bytes:
    from lxml import etree
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _drop_provenance(root) -> None:
    """A file this tool wrote starts with its provenance line. Read back (a
    later run, the tidy-up tool), that line is the tool's own label, not
    words to check: it is removed, and the new run writes its own (before
    this, it was read as a name and every re-run Word file was held)."""
    from inkguard import lapis
    body = root.find(_w("body"))
    if body is None or not len(body) or body[0].tag != _w("p"):
        return
    text = "".join(t.text or "" for t in body[0].iter(_w("t")))
    if lapis.is_line(text):
        body.remove(body[0])


def _docx_inventory(parts: dict[str, bytes], stories: dict, main: str) -> tuple[dict, set[str]]:
    """What the file holds beyond its visible text, and the people it names."""
    found: dict[str, int] = {}
    names: set[str] = set()
    company: set[str] = set()
    for name, root in stories.items():
        for tag in ("ins", "del", "moveFrom", "moveTo"):
            els = root.findall(f".//{_w(tag)}")
            _count(found, "tracked changes", len(els))
            names |= {e.get(_w("author")) for e in els if e.get(_w("author"))}
        _count(found, "tracked formatting changes",
               sum(len(root.findall(f".//{_w(t)}")) for t in ("rPrChange", "pPrChange")))
        _count(found, "hidden text", len([v for v in root.iter(_w("vanish"))
                                          if v.get(_w("val"), "true") not in ("0", "false")]))
        _count(found, "images and drawings", len(root.findall(f".//{_w('drawing')}"))
               + len(root.findall(f".//{_w('pict')}")))
        _count(found, "embedded objects", len(root.findall(f".//{_w('object')}")))
        _count(found, "text boxes", len(root.findall(f".//{_w('txbxContent')}")))
        _count(found, "fields", len([f for f in root.iter(_w("fldChar"))
                                     if f.get(_w("fldCharType")) == "begin"])
               + len(root.findall(f".//{_w('fldSimple')}")))
        _count(found, "links", len(root.findall(f".//{_w('hyperlink')}")))
        _count(found, "content controls", len(root.findall(f".//{_w('sdt')}")))
        _count(found, "alternative text", len([d for d in root.iter(f"{{{WP}}}docPr")
                                               if d.get("descr") or d.get("title")]))
        if name != main:
            kind = next((k for k in ("header", "footer", "footnotes", "endnotes") if k in name), "")
            if kind and "".join(t.text or "" for t in root.iter(_w("t"))).strip():
                _count(found, {"header": "headers", "footer": "footers"}.get(kind, kind))
    if "word/comments.xml" in parts:
        root = _parse(parts["word/comments.xml"])
        cs = root.findall(_w("comment"))
        _count(found, "comments", len(cs))
        names |= {c.get(_w("author")) for c in cs if c.get(_w("author"))}
    if "word/people.xml" in parts:
        root = _parse(parts["word/people.xml"])
        for el in root.iter():
            for k, v in el.attrib.items():
                if k.endswith("author"):
                    names.add(v)
    if "docProps/core.xml" in parts:
        root = _parse(parts["docProps/core.xml"])
        for el in root:
            if (el.text or "").strip():
                tag = el.tag.split("}")[-1]
                if tag not in ("created", "modified"):
                    _count(found, "document properties")
                if tag in ("creator", "lastModifiedBy"):
                    names.add(el.text)
    if "docProps/app.xml" in parts:
        root = _parse(parts["docProps/app.xml"])
        for el in root:
            tag = el.tag.split("}")[-1]
            if tag in ("Company", "Manager", "Template", "HyperlinkBase") and (el.text or "").strip():
                _count(found, "document properties")
                if tag == "Manager":
                    names.add(el.text)
                elif not (tag == "Template" and el.text.strip().lower() in _DEFAULT_TEMPLATES):
                    company.add(el.text)
    if "docProps/custom.xml" in parts:
        _count(found, "custom properties", len(_parse(parts["docProps/custom.xml"])))
    for n in parts:
        if n.startswith("docProps/thumbnail"):
            _count(found, "thumbnail image")
        elif n.startswith("customXml/") and n.endswith(".xml") and "/_rels/" not in n and "itemProps" not in n:
            _count(found, "custom data")
        elif n == "word/vbaProject.bin":
            _count(found, "macros")
        elif n.startswith("word/glossary/document"):
            _count(found, "saved building blocks")
    settings = parts.get("word/settings.xml")
    if settings:
        root = _parse(settings)
        for tag, label in (("attachedTemplate", "template path"), ("docVars", "document variables"),
                           ("mailMerge", "mail-merge source")):
            if root.find(_w(tag)) is not None:
                _count(found, label)
    return found, {n for n in names if n} | {f"company:{c}" for c in company if c}


def _clean_story(root, removed: dict) -> None:
    """Accept tracked changes and remove everything that is not plain text."""
    from lxml import etree
    # Content controls, custom XML and smart tags: keep what they show.
    for tag in ("sdt",):
        for el in list(root.iter(_w(tag))):
            content = el.find(_w("sdtContent"))
            parent = el.getparent()
            if parent is None:
                continue
            if content is not None:
                _unwrap(content)
                for pr in (el.find(_w("sdtPr")), el.find(_w("sdtEndPr"))):
                    if pr is not None:
                        el.remove(pr)
            _unwrap(el)
    for tag in ("customXml", "smartTag"):
        for el in list(root.iter(_w(tag))):
            for pr in el.findall(_w(f"{tag}Pr")):
                el.remove(pr)
            _unwrap(el)
    # Tracked changes: deleted text goes, inserted text stays.
    for tag in _REVISION_REMOVE:
        for el in list(root.iter(_w(tag))):
            if el.getparent() is not None:
                el.getparent().remove(el)
    for tag in ("ins", "moveTo"):
        for el in list(root.iter(_w(tag))):
            _unwrap(el)
    # Alternate content: keep the first choice only.
    for el in list(root.iter(f"{{{MC}}}AlternateContent")):
        choice = el.find(f"{{{MC}}}Choice")
        if choice is not None:
            _unwrap(choice)
        for fb in el.findall(f"{{{MC}}}Fallback"):
            el.remove(fb)
        _unwrap(el)
    # Comment markers.
    for tag in ("commentRangeStart", "commentRangeEnd"):
        for el in list(root.iter(_w(tag))):
            el.getparent().remove(el)
    for ref in list(root.iter(_w("commentReference"))):
        run = ref.getparent()
        if run is not None and run.tag == _w("r") and run.getparent() is not None:
            run.getparent().remove(run)
    # Hidden text.
    for v in list(root.iter(_w("vanish"))):
        if v.get(_w("val"), "true") in ("0", "false"):
            continue
        rpr = v.getparent()
        run = rpr.getparent() if rpr is not None else None
        if run is not None and run.tag == _w("r") and run.getparent() is not None:
            run.getparent().remove(run)
    # Fields: keep the result a reader sees, drop the instruction.
    _flatten_fields(root)
    for el in list(root.iter(_w("fldSimple"))):
        _unwrap(el)
    for el in list(root.iter(_w("instrText"))):
        el.getparent().remove(el)
    # Links: keep the words, drop the target.
    for el in list(root.iter(_w("hyperlink"))):
        _unwrap(el)
    # Drawings: a text box stays (its text is read like any other); a
    # picture, chart, diagram or embedded object is replaced by a marker.
    for el in list(root.iter(_w("drawing"), _w("pict"), _w("object"))):
        if el.getparent() is None:
            continue
        has_text = el.find(f".//{_w('txbxContent')}") is not None
        if el.tag == _w("object") or not has_text:
            label = "[embedded object removed]" if el.tag == _w("object") else "[image removed]"
            t = etree.Element(_w("t"))
            t.text = label
            el.getparent().replace(el, t)
            _count(removed, "images and objects")
        else:
            for x in list(el.iter()):
                if x.tag.split("}")[-1] in ("blipFill",) and x.getparent() is not None:
                    x.getparent().remove(x)
    for d in root.iter(f"{{{WP}}}docPr"):
        for k in ("descr", "title"):
            if k in d.attrib:
                del d.attrib[k]
        d.set("name", f"Shape {d.get('id', '1')}")
    # Revision-session ids: harmless, but they link documents edited together.
    for el in root.iter():
        for k in [k for k in el.attrib if k.startswith(f"{{{W}}}rsid")]:
            del el.attrib[k]


def _flatten_fields(root) -> None:
    """Complex fields (begin … instruction … separate … result … end)
    become their result text. Runs are visited in document order; nested
    fields inside an instruction go with it."""
    stack: list[str] = []            # "instr" or "result" per open field
    drop = []
    for run in root.iter(_w("r")):
        fcs = run.findall(_w("fldChar"))
        in_instr = any(s == "instr" for s in stack)
        if fcs:
            for fc in fcs:
                kind = fc.get(_w("fldCharType"))
                if kind == "begin":
                    stack.append("instr")
                elif kind == "separate" and stack:
                    stack[-1] = "result"
                elif kind == "end" and stack:
                    stack.pop()
            drop.append(run)
        elif in_instr or run.find(_w("instrText")) is not None:
            drop.append(run)
    for run in drop:
        if run.getparent() is not None:
            run.getparent().remove(run)


_TEXT_TAGS = (f"{{{W}}}t", f"{{{M}}}t", f"{{{A}}}t")


def _docx_canvas(stories: dict, order: list[str], numbering=None) -> Canvas:
    """Every text node of every story part (body, headers, footers, notes),
    including equation text and shape text, and the labels of numbered
    lists, in reading order."""
    cv = Canvas()
    for n_part, name in enumerate(order):
        root = stories[name]
        if n_part:
            cv.sep("\n\n")
        first = True
        for p in root.iter(_w("p")):
            if not first:
                cv.sep("\n")
            first = False
            for node in p.iter():
                if node is p:
                    continue
                # Only this paragraph's own text: a text box inside it is
                # its own paragraph, read when the walk reaches it.
                anc = node.getparent()
                while anc is not None and anc.tag != _w("p"):
                    anc = anc.getparent()
                if anc is not p:
                    continue
                tag = node.tag
                if tag in _TEXT_TAGS:
                    cv.slot(node, node.text or "")
                elif tag in (_w("tab"), _w("ptab")):
                    cv.sep("\t")
                elif tag in (_w("br"), _w("cr")):
                    cv.sep("\n")
                elif tag == _w("noBreakHyphen"):
                    cv.sep("-", droppable=True, ref=node)
    if numbering is not None:
        labels = [el for el in numbering.iter(_w("lvlText"))
                  if re.search(r"[^\W\d_]", re.sub(r"%\d", "", el.get(_w("val"), "")))]
        if labels:
            cv.sep("\n\n")
        for el in labels:
            cv.slot(("attr", el, _w("val")), el.get(_w("val"), ""))
            cv.sep("\n")
    return cv


def _set_slot(ref, value: str) -> None:
    if isinstance(ref, tuple) and ref[0] == "attr":
        ref[1].set(ref[2], value)
        return
    ref.text = value
    if value != value.strip() and ref.tag == _w("t"):
        ref.set(f"{{{XML_NS}}}space", "preserve")


# The only parts a clean Word file keeps, by relationship type from the main
# document. Everything else (comments, people, custom XML, glossary, embedded
# HTML or documents, images, charts, fonts, printer settings, properties
# under any name, orphan parts) is not copied at all.
_KEEP_MAIN_RELS = {"styles", "stylesWithEffects", "numbering", "fontTable", "settings",
                   "webSettings", "theme", "header", "footer", "footnotes", "endnotes"}
_CORE_TYPE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
_DOC_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"


def _rebuild_docx(parts: dict[str, bytes], main: str, removed: dict) -> dict[str, bytes]:
    from lxml import etree
    new: dict[str, bytes] = {}
    keep = [main]
    rels = _parse(parts.get(_rels_path(main), f'<Relationships xmlns="{PR}"/>'.encode()))
    for rel in list(rels):
        kind = rel.get("Type", "").rsplit("/", 1)[-1]
        target = _resolve(main, rel.get("Target", "")) if rel.get("TargetMode") != "External" else ""
        if kind in _KEEP_MAIN_RELS and target in parts:
            if target not in keep:
                keep.append(target)
            continue
        rels.remove(rel)
        label = {"hyperlink": "links and external references", "image": "images and objects",
                 "aFChunk": "embedded HTML or documents", "oleObject": "images and objects",
                 "package": "images and objects", "chart": "images and objects",
                 "comments": "comments", "customXml": "custom data",
                 "glossaryDocument": "saved building blocks"}.get(kind)
        if label:
            _count(removed, label)
    new[_rels_path(main)] = _tostring(rels)
    for n in keep:
        new[n] = parts[n]
    # Kept parts keep NO relationships of their own: a header's images, the
    # template a settings part points to, a font table's embedded fonts, a
    # theme's pictures.
    root = etree.Element(f"{{{PR}}}Relationships", nsmap={None: PR})
    for rid, typ, target in (("rId1", _DOC_TYPE, main), ("rId2", _CORE_TYPE, "docProps/core.xml")):
        el = etree.SubElement(root, f"{{{PR}}}Relationship")
        el.set("Id", rid)
        el.set("Type", typ)
        el.set("Target", target)
    new["_rels/.rels"] = _tostring(root)
    new["docProps/core.xml"] = _EMPTY_CORE
    ct = _parse(parts["[Content_Types].xml"])
    for ov in list(ct.findall(f"{{{CT}}}Override")):
        if ov.get("PartName", "").lstrip("/") not in new:
            ct.remove(ov)
    if not any(o.get("PartName") == "/docProps/core.xml" for o in ct.findall(f"{{{CT}}}Override")):
        ov = etree.SubElement(ct, f"{{{CT}}}Override")
        ov.set("PartName", "/docProps/core.xml")
        ov.set("ContentType", "application/vnd.openxmlformats-package.core-properties+xml")
    new["[Content_Types].xml"] = _tostring(ct)
    # Whatever still points at a relationship that is gone goes too.
    ids = {r.get("Id") for r in rels}
    for n in list(new):
        if not n.endswith(".xml") or n in ("[Content_Types].xml", "docProps/core.xml"):
            continue
        root = _parse(new[n])
        live = ids if n == main else set()
        if n.endswith("theme1.xml") or "/theme/" in n:
            if any(k.startswith(f"{{{R}}}") for el in root.iter() for k in el.attrib):
                del new[n]                    # a theme with pictures: dropped whole
                continue
        changed = False
        for el in list(root.iter()):
            if el.getparent() is None:
                continue
            if any(k.startswith(f"{{{R}}}") and v not in live for k, v in el.attrib.items()):
                local = el.tag.split("}")[-1]
                if local == "blip" and el.getparent().tag.endswith("blipFill"):
                    gp = el.getparent().getparent()
                    if gp is not None:
                        gp.remove(el.getparent())
                elif local == "altChunk":
                    _count(removed, "embedded HTML or documents")
                    el.getparent().remove(el)
                else:
                    el.getparent().remove(el)
                changed = True
        if changed:
            new[n] = _tostring(root)
    _scrub_hidden_text(new, removed)
    if not any("/theme/" in n for n in new):
        rels2 = _parse(new[_rels_path(main)])
        for rel in list(rels2):
            if rel.get("Type", "").endswith("/theme"):
                rels2.remove(rel)
        new[_rels_path(main)] = _tostring(rels2)
        ct = _parse(new["[Content_Types].xml"])
        for ov in list(ct.findall(f"{{{CT}}}Override")):
            if ov.get("PartName", "").lstrip("/") not in new:
                ct.remove(ov)
        new["[Content_Types].xml"] = _tostring(ct)
    return new


# Places in a kept Word part that hold free text no reader sees on the page,
# and so the engine never reads. Each is removed whole: in internal testing,
# text placed in these elements was not read by the engine.
_HIDDEN_TEXT_ELEMENTS = (
    "tblCaption", "tblDescription",       # table Alt Text (Table Properties)
    "permStart", "permEnd",               # restricted editing: user logins
    "latentStyles",                       # styles.xml: any name may be written here
    "captions",                           # settings.xml: caption labels
    "docVars",                            # settings.xml: document variables
    "mailMerge",                          # settings.xml: data source and queries
    "documentProtection", "writeProtection", "attachedTemplate",
)


def _scrub_hidden_text(parts: dict[str, bytes], removed: dict) -> None:
    """Every kept XML part loses its comments, processing instructions and
    the free-text elements above. Run on the rebuilt package, so nothing
    added by any earlier step escapes it."""
    from lxml import etree
    for n in list(parts):
        if not n.endswith(".xml") or n == "[Content_Types].xml":
            continue
        root = _parse(parts[n])
        doomed = [el for el in root.iter(etree.Comment, etree.PI)]
        for name in _HIDDEN_TEXT_ELEMENTS:
            doomed += list(root.iter(_w(name)))
        if not doomed:
            continue
        for el in doomed:
            parent = el.getparent()
            if parent is not None:
                if el.tail and isinstance(el.tag, str) is False:
                    prev = el.getprevious()
                    if prev is not None:
                        prev.tail = (prev.tail or "") + el.tail
                    else:
                        parent.text = (parent.text or "") + el.tail
                parent.remove(el)
        _count(removed, "hidden text in document settings")
        parts[n] = _tostring(root)


def _neutral_names(parts: dict[str, bytes], stories: dict) -> None:
    """Names a reader never sees but a file keeps: bookmark names, custom
    style names and ids, the theme's names, shape titles and word-art text.
    Each becomes a neutral one."""
    # Bookmarks.
    k = 0
    for root in stories.values():
        for b in root.iter(_w("bookmarkStart")):
            k += 1
            b.set(_w("name"), f"_bm{k}")
        # VML shape text and titles that no reader sees as text.
        for el in list(root.iter()):
            for a in [a for a in el.attrib if a in ("alt", "title") or a == f"{{{O}}}title"
                      or a.endswith("}alt")]:
                del el.attrib[a]
            if el.tag == f"{{{V}}}textpath" and el.getparent() is not None:
                el.getparent().remove(el)
    # Custom styles: ids and names become neutral, and every reference follows.
    if "word/styles.xml" in parts:
        styles = _parse(parts["word/styles.xml"])
        ren: dict[str, str] = {}
        for i, st in enumerate(styles.iter(_w("style")), 1):
            if st.get(_w("customStyle")) in ("1", "true"):
                old = st.get(_w("styleId"), "")
                ren[old] = f"CustomStyle{i}"
                st.set(_w("styleId"), ren[old])
                nm = st.find(_w("name"))
                if nm is not None:
                    nm.set(_w("val"), f"Custom style {i}")
                for al in st.findall(_w("aliases")):
                    st.remove(al)
            else:
                # A built-in style keeps its id; a display name that is not
                # that id ("Letterhead Delacroix-Ferreira" on "Title") goes.
                nm = st.find(_w("name"))
                sid = st.get(_w("styleId"), "")
                if nm is not None and re.sub(r"[\s_-]", "", nm.get(_w("val"), "")).lower() != \
                        re.sub(r"[\s_-]", "", sid).lower():
                    nm.set(_w("val"), sid or f"Style {i}")
                for al in st.findall(_w("aliases")):
                    st.remove(al)
        refs = ("pStyle", "rStyle", "tblStyle", "basedOn", "next", "link", "numStyleLink", "styleLink")
        targets = [styles] + list(stories.values())
        numbering = _parse(parts["word/numbering.xml"]) if "word/numbering.xml" in parts else None
        if numbering is not None:
            targets.append(numbering)
        if ren:
            for root in targets:
                for tag in refs:
                    for el in root.iter(_w(tag)):
                        if el.get(_w("val")) in ren:
                            el.set(_w("val"), ren[el.get(_w("val"))])
            if numbering is not None:
                parts["word/numbering.xml"] = _tostring(numbering)
        parts["word/styles.xml"] = _tostring(styles)
    for n in [n for n in parts if "/theme/" in n and n.endswith(".xml")]:
        root = _parse(parts[n])
        for el in root.iter():
            if el.tag.split("}")[-1] in ("theme", "clrScheme", "fontScheme", "fmtScheme") and el.get("name"):
                el.set("name", "Theme")
        parts[n] = _tostring(root)
    if "word/fontTable.xml" in parts:
        root = _parse(parts["word/fontTable.xml"])
        for el in list(root.iter()):
            if el.tag.split("}")[-1].startswith("embed") and el.getparent() is not None:
                el.getparent().remove(el)
        parts["word/fontTable.xml"] = _tostring(root)


def _prune_package(parts: dict[str, bytes], removed: dict) -> None:
    """Drop the parts nothing can check, every relationship to them and
    every external relationship, and whatever pointed at those. Not called
    at present: _rebuild_docx's allow-list does this job."""
    dropped = [n for n in parts if n.startswith(_DROP_PREFIXES) or n in _DROP_NAMES]
    for n in dropped:
        del parts[n]
    # Relationships.
    live_ids: dict[str, set[str]] = {}
    for rels_name in [n for n in parts if n.endswith(".rels")]:
        src = rels_name.replace("_rels/", "").removesuffix(".rels")
        if src.endswith("/") or src == "":
            src = ""
        if src and src not in parts:
            del parts[rels_name]            # the relationships of a part that is gone
            continue
        root = _parse(parts[rels_name])
        keep = set()
        for rel in list(root):
            target = rel.get("Target", "")
            external = rel.get("TargetMode") == "External"
            resolved = _resolve(src if src else "x", target) if not external else ""
            if not src:
                resolved = target.lstrip("/")
            if external or (resolved and resolved not in parts):
                root.remove(rel)
                if external:
                    _count(removed, "links and external references")
            else:
                keep.add(rel.get("Id"))
        live_ids[src] = keep
        parts[rels_name] = _tostring(root)
    # Elements that pointed at a removed relationship.
    for name in [n for n in parts if n.endswith(".xml") and n.startswith("word/")]:
        root = _parse(parts[name])
        ids = live_ids.get(name, set())
        changed = False
        for el in list(root.iter()):
            if el.getparent() is None:
                continue
            for k, v in list(el.attrib.items()):
                if k.startswith(f"{{{R}}}") and v not in ids:
                    local = el.tag.split("}")[-1]
                    if local in ("headerReference", "footerReference"):
                        el.getparent().remove(el)
                    elif local == "blip" and el.getparent().tag.endswith("blipFill"):
                        gp = el.getparent().getparent()
                        if gp is not None:
                            gp.remove(el.getparent())
                    else:
                        el.getparent().remove(el)
                    changed = True
                    break
        if changed:
            parts[name] = _tostring(root)
    # Content types: no override for a part that is gone.
    if "[Content_Types].xml" in parts:
        root = _parse(parts["[Content_Types].xml"])
        for ov in list(root.findall(f"{{{CT}}}Override")):
            if ov.get("PartName", "").lstrip("/") not in parts:
                root.remove(ov)
        parts["[Content_Types].xml"] = _tostring(root)


_EMPTY_CORE = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
               b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
               b'metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
               b'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
               b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"/>')


def _clean_settings(data: bytes) -> bytes:
    from lxml import etree
    root = _parse(data)
    for tag in ("attachedTemplate", "rsids", "docVars", "mailMerge", "trackRevisions",
                "saveThroughXslt", "linkStyles"):
        for el in root.findall(_w(tag)):
            root.remove(el)
    if root.find(_w("removePersonalInformation")) is None:
        # Word then keeps author names out of anything saved later. The
        # schema fixes the order: after writeProtection, view and zoom.
        pos = 0
        for i, el in enumerate(root):
            if el.tag in (_w("writeProtection"), _w("view"), _w("zoom")):
                pos = i + 1
        root.insert(pos, etree.Element(_w("removePersonalInformation")))
    return _tostring(root)


def _main_part(parts: dict[str, bytes]) -> str:
    root = _parse(parts.get("_rels/.rels", b"<x/>"))
    for rel in root:
        if rel.get("Type", "").endswith("/officeDocument"):
            return rel.get("Target", "").lstrip("/")
    raise DocumentError("this file has no main document")


def clean_docx(src: Path, dst: Path, eng: Engine) -> DocResult:
    out = DocResult(source=str(src), kind="docx")
    try:
        parts = _read_zip(src)
        main = _main_part(parts)
        if main not in parts:
            raise DocumentError("this file has no main document")
        rels = _parse(parts.get(_rels_path(main), b"<x/>"))
        order = [main]
        for rel in rels:
            if rel.get("Type", "").endswith(_STORY_TYPES) and rel.get("TargetMode") != "External":
                n = _resolve(main, rel.get("Target", ""))
                if n in parts and n not in order:
                    order.append(n)
        stories = {n: _parse(parts[n]) for n in order}
        _drop_provenance(stories[main])
        out.hidden_found, raw_names = _docx_inventory(parts, stories, main)
        names = _author_names(n for n in raw_names if not n.startswith("company:"))
        company = {n.split(":", 1)[1] for n in raw_names if n.startswith("company:")}
        templates: set[str] = set()
        if src.suffix.lower() == ".docm" or "word/vbaProject.bin" in parts:
            out.notes.append("macros removed; the clean copy is a .docx")

        for n in order:
            _clean_story(stories[n], out.removed)
        # Normalise every text node in place, then read.
        for root in stories.values():
            for t in root.iter(_w("t")):
                if t.text:
                    t.text = _sanitize_piece(t.text)
        numbering = _parse(parts["word/numbering.xml"]) if "word/numbering.xml" in parts else None
        if numbering is not None:
            for el in numbering.iter(_w("lvlText")):
                if el.get(_w("val")):
                    el.set(_w("val"), _sanitize_piece(el.get(_w("val"))))
        for root in stories.values():
            for t in root.iter(f"{{{M}}}t", f"{{{A}}}t"):
                if t.text:
                    t.text = _sanitize_piece(t.text)
        cv = _docx_canvas(stories, order, numbering)
        text = cv.text
        if _sanitize_piece(text) != text:
            raise _Stop("held", "the text could not be normalised in place")
        ps = _engine_pass(text, eng, names)
        out.results.append(ps.result)
        out.words_to_check = ps.words_to_check
        new_slots, expected = cv.apply(ps.edits)
        for ref, new in zip(cv.slots, new_slots):
            _set_slot(ref, new)
        for el in cv.dropped:
            if el.getparent() is not None:
                el.getparent().remove(el)
        out.removed.update(_removed_types(ps.result))
        if numbering is not None:
            parts["word/numbering.xml"] = _tostring(numbering)
        _neutral_names(parts, stories)
        for n in order:
            parts[n] = _tostring(stories[n])
        if "word/settings.xml" in parts:
            parts["word/settings.xml"] = _clean_settings(parts["word/settings.xml"])
        parts = _rebuild_docx(parts, main, out.removed)
        dst = dst.with_suffix(".docx")
        _write_zip(dst, parts)

        # Check: the clean copy opens, reads exactly as expected, and holds
        # none of the removed strings or the file's author names anywhere.
        after = _read_zip(dst)
        stories_after = {n: _parse(after[n]) for n in order if n in after}
        num_after = _parse(after["word/numbering.xml"]) if "word/numbering.xml" in after else None
        got = _docx_canvas(stories_after, [n for n in order if n in after], num_after).text
        if got != expected:
            raise _Stop("held", "the clean copy does not read as expected")
        try:
            import docx  # python-docx: the package must open
            docx.Document(str(dst))
        except Exception as exc:  # noqa: BLE001
            raise _Stop("failed", f"the clean copy did not open ({type(exc).__name__})")
        out.sweep = sweep_zip(dst, ps.secrets | company | templates, ps.names | names)
        out.outputs = [dst]
        _settle(out, ps.result)
    except DocumentError as exc:
        out.status, out.reason = "failed", str(exc)
    except _Stop as st:
        out.status, out.reason = st.status, st.reason
        if st.result is not None:
            out.results.append(st.result)
    return out


def _rel_targets(parts: dict, rels_name: str) -> set[str]:
    if rels_name not in parts:
        return set()
    return {r.get("Target", "").lstrip("/") for r in _parse(parts[rels_name])}


def _add_core_rel(parts: dict) -> None:
    from lxml import etree
    root = _parse(parts.get("_rels/.rels", f'<Relationships xmlns="{PR}"/>'.encode()))
    ids = {r.get("Id") for r in root}
    i = 1
    while f"rIdCore{i}" in ids:
        i += 1
    el = etree.SubElement(root, f"{{{PR}}}Relationship")
    el.set("Id", f"rIdCore{i}")
    el.set("Type", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties")
    el.set("Target", "docProps/core.xml")
    parts["_rels/.rels"] = _tostring(root)
    ct = _parse(parts["[Content_Types].xml"])
    if not any(o.get("PartName") == "/docProps/core.xml" for o in ct.findall(f"{{{CT}}}Override")):
        ov = etree.SubElement(ct, f"{{{CT}}}Override")
        ov.set("PartName", "/docProps/core.xml")
        ov.set("ContentType", "application/vnd.openxmlformats-package.core-properties+xml")
        parts["[Content_Types].xml"] = _tostring(ct)


def _removed_types(result) -> dict[str, int]:
    from inkguard import contract
    out: dict[str, int] = {}
    for h in getattr(result, "hits", None) or []:
        _count(out, contract.canonical_type(h.entity_type))
    return out


def _settle(out: DocResult, result) -> None:
    """The file's final state: held when the engine held it, when the
    sweep found anything, or when the engine was unsure."""
    if out.sweep.get("found"):
        out.status = "held"
        out.reason = ("a removed string or a name from the file's hidden details is still in "
                      f"the clean copy ({out.sweep['found']} found)")
    elif result is not None and result.action.value == "review":
        out.status = "held"
        out.reason = "words the tool could not name"


# =====================================================================
# EXCEL (.xlsx, .xlsm)
# =====================================================================

def _xlsx_inventory(parts: dict[str, bytes]) -> tuple[dict, set[str]]:
    found: dict[str, int] = {}
    names: set[str] = set()
    company: set[str] = set()
    for n, data in parts.items():
        if re.match(r"xl/comments[^/]*\.xml$|xl/comments/comment[^/]*\.xml$", n):
            root = _parse(data)
            _count(found, "cell notes", len([e for e in root.iter() if e.tag.endswith("}comment")]))
            names |= {a.text for a in root.iter() if a.tag.endswith("}author") and a.text}
        elif n.startswith("xl/threadedComments/"):
            _count(found, "comments", len([e for e in _parse(data).iter()
                                           if e.tag.endswith("}threadedComment")]))
        elif n.startswith("xl/persons/"):
            names |= {e.get("displayName") for e in _parse(data).iter() if e.get("displayName")}
        elif n.startswith("xl/pivotCache/pivotCacheRecords"):
            _count(found, "pivot caches (a copy of the data)")
        elif n.startswith("xl/externalLinks/externalLink"):
            _count(found, "links to other files")
        elif n.startswith("xl/media/"):
            _count(found, "images")
        elif n.startswith("xl/charts/chart"):
            _count(found, "charts")
        elif n == "xl/vbaProject.bin":
            _count(found, "macros")
        elif n.startswith("customXml/") and "itemProps" not in n and "_rels" not in n:
            _count(found, "custom data")
        elif n.startswith("docProps/thumbnail"):
            _count(found, "thumbnail image")
        elif n == "docProps/custom.xml":
            _count(found, "custom properties", len(_parse(data)))
        elif n == "docProps/core.xml":
            for el in _parse(data):
                tag = el.tag.split("}")[-1]
                if (el.text or "").strip() and tag not in ("created", "modified"):
                    _count(found, "document properties")
                if tag in ("creator", "lastModifiedBy") and el.text:
                    names.add(el.text)
        elif n == "docProps/app.xml":
            for el in _parse(data):
                tag = el.tag.split("}")[-1]
                if tag in ("Company", "Manager") and (el.text or "").strip():
                    _count(found, "document properties")
                    (company if tag == "Company" else names).add(el.text)
    return found, {x for x in names if x} | {f"company:{c}" for c in company if c}


def _cell_text(v) -> tuple[str | None, str]:
    """(text the engine reads, kind) for a cell value; None: nothing to read."""
    if v is None or isinstance(v, bool):
        return None, ""
    if isinstance(v, _dt.datetime):
        if v.hour or v.minute or v.second:
            return f"{v.month:02d}/{v.day:02d}/{v.year} {v.hour:02d}:{v.minute:02d}", "datetime"
        return f"{v.month:02d}/{v.day:02d}/{v.year}", "date"
    if isinstance(v, _dt.date):
        return f"{v.month:02d}/{v.day:02d}/{v.year}", "date"
    if isinstance(v, _dt.time):
        return None, ""
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return str(int(v)), "number"
    if isinstance(v, (int, float)):
        return repr(v), "number"
    return _unescape_ooxml(str(v)), "text"


_OOXML_ESCAPE = re.compile(r"_x([0-9A-Fa-f]{4})_")


def _unescape_ooxml(s: str) -> str:
    """Excel shows "_x004C_indqvist" as "Lindqvist": read it the way Excel shows it."""
    return _OOXML_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), s) if "_x" in s else s


def _header_row(ws) -> int | None:
    """The first of the top five rows whose filled cells are all words."""
    for r in range(1, min(ws.max_row, 5) + 1):
        vals = [c.value for c in ws[r] if c.value not in (None, "")]
        if vals and all(isinstance(v, str) and not re.fullmatch(r"[\d\s./:-]+", v) for v in vals):
            return r
    return None


_TITLE_BAD = re.compile(r"[\[\]:*?/\\]")


def _safe_title(t: str, taken: set[str]) -> str:
    t = t.replace("[", "(").replace("]", ")")
    t = _TITLE_BAD.sub("-", t).strip("'")[:31] or "Sheet"
    base, k = t, 2
    while t.casefold() in taken:
        suffix = f" {k}"
        t = base[:31 - len(suffix)] + suffix
        k += 1
    taken.add(t.casefold())
    return t


def clean_xlsx(src: Path, dst: Path, eng: Engine) -> DocResult:
    out = DocResult(source=str(src), kind="xlsx")
    try:
        import openpyxl
        from openpyxl.packaging.core import DocumentProperties
        from openpyxl.worksheet.header_footer import HeaderFooter
        from openpyxl.utils import get_column_letter
        parts = _read_zip(src)
        out.hidden_found, raw_names = _xlsx_inventory(parts)
        names = _author_names(n for n in raw_names if not n.startswith("company:"))
        company = {n.split(":", 1)[1] for n in raw_names if n.startswith("company:")}
        try:
            wb = openpyxl.load_workbook(src, data_only=False, keep_vba=False, keep_links=False)
            cached = openpyxl.load_workbook(src, data_only=True, keep_vba=False, keep_links=False)
        except Exception as exc:  # noqa: BLE001
            raise DocumentError(f"this workbook could not be read ({type(exc).__name__})")
        if src.suffix.lower() == ".xlsm":
            out.notes.append("macros removed; the clean copy is a .xlsx")

        # Workbook-wide: nothing that is not a cell value survives.
        n_names = len(list(wb.defined_names))
        wb.defined_names.clear()
        for ws in wb.worksheets:
            n_names += len(getattr(ws, "defined_names", {}) or {})
            if getattr(ws, "defined_names", None):
                ws.defined_names.clear()
        _count(out.hidden_found, "defined names", n_names)
        wb._external_links = []
        # Chart sheets are pictures of data: removed, like charts.
        for cs in list(getattr(wb, "chartsheets", [])):
            wb.remove(cs)
            _count(out.removed, "chart sheets")
        try:
            wb.code_name = None
        except Exception:  # noqa: BLE001
            pass
        # Named styles and number formats can carry words ("Pt Abernathy" 0).
        for i, ns in enumerate(getattr(wb, "_named_styles", []), 1):
            if getattr(ns, "builtinId", None) is None and ns.name != "Normal":
                ns.name = f"Style {i}"
        fmts = getattr(wb, "_number_formats", None)
        if fmts is not None:
            for i, f in enumerate(list(fmts)):
                if '"' in f or "\\" in f:
                    fmts[i] = "General"
                    _count(out.removed, "number formats with words")
        wb.properties = DocumentProperties(creator="", lastModifiedBy="", title=None)
        try:
            wb.custom_doc_props = type(wb.custom_doc_props)()
        except Exception:  # noqa: BLE001
            pass

        taken: set[str] = set()
        title_slots = []
        passes: list[Pass] = []
        for ws in wb.worksheets:
            cws = cached[ws.title]
            if ws.sheet_state != "visible":
                _count(out.hidden_found, "hidden sheets")
                ws.sheet_state = "visible"
            for dim in list(ws.row_dimensions.values()) + list(ws.column_dimensions.values()):
                if dim.hidden:
                    _count(out.hidden_found, "hidden rows and columns")
                    dim.hidden = False
            n_hf = sum(1 for part in (ws.oddHeader, ws.oddFooter, ws.evenHeader, ws.evenFooter,
                                      ws.firstHeader, ws.firstFooter)
                       for x in (part.left, part.center, part.right) if (x.text or "").strip())
            _count(out.hidden_found, "headers and footers", n_hf)
            ws.HeaderFooter = HeaderFooter()
            from openpyxl.formatting.formatting import ConditionalFormattingList
            from openpyxl.worksheet.filters import AutoFilter
            n_cf = len(list(ws.conditional_formatting))
            if n_cf:
                _count(out.removed, "conditional formatting rules", n_cf)
            ws.conditional_formatting = ConditionalFormattingList()
            if ws.auto_filter.ref or ws.auto_filter.filterColumn:
                _count(out.removed, "filters")
            ws.auto_filter = AutoFilter()
            ws.sheet_properties.codeName = None
            _count(out.hidden_found, "data validations", len(ws.data_validations.dataValidation))
            ws.data_validations.dataValidation = []
            for name in list(ws.tables):
                del ws.tables[name]
                _count(out.removed, "table definitions")
            _count(out.removed, "pivot tables", len(getattr(ws, "_pivots", [])))
            ws._pivots = []
            _count(out.removed, "images and charts", len(getattr(ws, "_images", [])) + len(getattr(ws, "_charts", [])))
            ws._images = []
            ws._charts = []
            for row in ws.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and len(c.value) > MAX_CELL_CHARS:
                        raise DocumentError(f"a cell holds more than {MAX_CELL_CHARS:,} characters; "
                                            "it is too long to check as a table cell")
                    if c.comment is not None:
                        c.comment = None
                    if c.hyperlink is not None:
                        c.hyperlink = None
                        _count(out.removed, "links and external references")
                    if c.data_type == "f" or (isinstance(c.value, str) and c.value.startswith("=")):
                        # A formula becomes the value it last showed: the
                        # formula would recompute from cells that change.
                        c.value = cws[c.coordinate].value
                        _count(out.removed, "formulas turned into values")
                        if c.value is None:
                            _count(out.removed, "formulas with no saved value (left empty)")
                    if isinstance(c.value, str):
                        c.value = _sanitize_piece(_unescape_ooxml(c.value))
                    fmt = c.number_format or ""
                    if '"' in fmt or "\\" in fmt:
                        c.number_format = "General"
                        _count(out.removed, "number formats with words")

            hdr = _header_row(ws)
            headers = {}
            if hdr:
                for c in ws[hdr]:
                    t, _k = _cell_text(c.value)
                    if t:
                        headers[c.column] = re.sub(r"\s+", " ", t).strip()
            rows = [r for r in ws.iter_rows() if any(c.value not in (None, "") for c in r)]
            data_rows = [r for r in rows if not hdr or r[0].row != hdr]
            head = [r for r in rows if hdr and r[0].row == hdr]
            chunks = [data_rows[i:i + XLSX_CHUNK_ROWS] for i in range(0, len(data_rows), XLSX_CHUNK_ROWS)] or [[]]
            for k, chunk in enumerate(chunks):
                cv = Canvas()
                if k == 0:
                    cv.slot(("title", ws), _sanitize_piece(ws.title))
                    cv.sep("\n")
                    for r in head:
                        first = True
                        for c in r:
                            t, kind = _cell_text(c.value)
                            if t is None:
                                continue
                            if not first:
                                cv.sep("  |  ")
                            first = False
                            cv.slot(("cell", c, kind), _sanitize_piece(t))
                        cv.sep("\n")
                for r in chunk:
                    cv.sep("\n")
                    for c in r:
                        t, kind = _cell_text(c.value)
                        if t is None or t.strip() == "":
                            continue
                        label = headers.get(c.column) or f"Column {get_column_letter(c.column)}"
                        cv.sep(f"{label}: ")
                        cv.slot(("cell", c, kind), _sanitize_piece(t))
                        cv.sep("\n")
                text = cv.text
                if _sanitize_piece(text) != text:
                    raise _Stop("held", "the text could not be normalised in place")
                ps = _engine_pass(text, eng, names)
                passes.append(ps)
                out.results.append(ps.result)
                out.words_to_check += ps.words_to_check
                new_slots, _expected = cv.apply(ps.edits)
                for ref, old, new in zip(cv.slots, cv.slot_text, new_slots):
                    if new == old:
                        continue
                    if ref[0] == "title":
                        title_slots.append((ref[1], new))
                        continue
                    cell = ref[1]
                    cell.value = new
                    cell.number_format = "General"
                for key, n in _removed_types(ps.result).items():
                    _count(out.removed, key, n)
        for ws in wb.worksheets:
            taken.add(ws.title.casefold())
        for ws, new in title_slots:
            taken.discard(ws.title.casefold())
            ws.title = _safe_title(new, taken)
        # Cell comments were removed above; their count comes from the
        # inventory read from the original file ("cell notes").
        dst = dst.with_suffix(".xlsx")
        dst.parent.mkdir(parents=True, exist_ok=True)
        wb.save(dst)

        # Check: opens, and holds none of the removed strings anywhere.
        try:
            openpyxl.load_workbook(dst)
        except Exception as exc:  # noqa: BLE001
            raise _Stop("failed", f"the clean copy did not open ({type(exc).__name__})")
        out.sweep = sweep_zip(dst, set().union(*(p.secrets for p in passes)) | company,
                              set().union(*(p.names for p in passes)) | names)
        out.outputs = [dst]
        worst = next((p.result for p in passes if p.result.action.value == "review"), None)
        _settle(out, worst)
    except DocumentError as exc:
        out.status, out.reason = "failed", str(exc)
    except _Stop as st:
        out.status, out.reason = st.status, st.reason
        if st.result is not None:
            out.results.append(st.result)
    return out


# =====================================================================
# TABLES (CSV and TSV; also used for the other columns of a CSV note run)
# =====================================================================

def _header_values(row: list[str]) -> bool:
    vals = [v for v in row if (v or "").strip()]
    return bool(vals) and all(not re.fullmatch(r"[\d\s./:+()-]+", v) for v in vals)


def clean_table_rows(header: list[str] | None, rows: list[list[str]], eng: Engine,
                     *, skip: set[int] = frozenset(), names: set[str] = frozenset()) -> dict:
    """Every cell of a table through the engine, each row read as
    "Column: value" lines so a column name works as a field label. Columns
    in `skip` are left as they are (the caller handles them). Returns the
    new header, rows, the engine passes and counts."""
    header = list(header) if header else None
    rows = [list(r) for r in rows]
    new_header = list(header) if header else None
    passes: list[Pass] = []
    removed: dict[str, int] = {}
    words: list[dict] = []
    labels = {i: re.sub(r"\s+", " ", (h or "")).strip() for i, h in enumerate(header or [])}
    chunks = [list(range(i, min(i + XLSX_CHUNK_ROWS, len(rows))))
              for i in range(0, len(rows), XLSX_CHUNK_ROWS)] or [[]]
    for k, idxs in enumerate(chunks):
        cv = Canvas()
        if k == 0 and header:
            for j, h in enumerate(header):
                if j in skip or not (h or "").strip():
                    continue
                if cv.owner:
                    cv.sep("  |  ")
                cv.slot(("h", j), _sanitize_piece(h))
            cv.sep("\n")
        for i in idxs:
            cv.sep("\n")
            for j, v in enumerate(rows[i]):
                if j in skip or not (v or "").strip():
                    continue
                cv.sep(f"{labels.get(j) or f'Column {j + 1}'}: ")
                cv.slot(("c", i, j), _sanitize_piece(v))
                cv.sep("\n")
        text = cv.text
        if not text.strip():
            continue
        if _sanitize_piece(text) != text:
            raise _Stop("held", "the text could not be normalised in place")
        ps = _engine_pass(text, eng, set(names))
        passes.append(ps)
        words += ps.words_to_check
        new_slots, _ = cv.apply(ps.edits)
        for ref, old, new in zip(cv.slots, cv.slot_text, new_slots):
            if ref[0] == "h":
                new_header[ref[1]] = new
            elif new != old or rows[ref[1]][ref[2]] != old:
                rows[ref[1]][ref[2]] = new
        for key, n in _removed_types(ps.result).items():
            _count(removed, key, n)
    return {"header": new_header, "rows": rows, "passes": passes, "removed": removed,
            "words": words}


_FORMULA_START = re.compile(r"^(?:[=@]|[+-](?![\d.]))")


def decode_text(raw: bytes, *, cut: bool = False) -> str:
    """The text of a plain-text file, read the way it was written.

    UTF-8 first (with or without a byte-order mark), UTF-16 when the file
    says so, then Windows-1252 — the default of Windows Notepad and many
    older exports — and finally Latin-1, which accepts every byte.
    Decoding with "replace" broke names like "Søren Ægir" into fragments
    the engine could not read as a name. `cut`: the bytes are a prefix of
    the file, so a
    character split at the end is dropped rather than treated as a sign
    of another encoding."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="strict" if not cut else "ignore")
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        if cut and exc.start >= len(raw) - 3:
            return raw[:exc.start].decode("utf-8", errors="strict")
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def clean_csv(src: Path, dst: Path, eng: Engine) -> DocResult:
    import csv
    out = DocResult(source=str(src), kind="csv")
    try:
        raw = src.read_bytes()
        if b"\x00" in raw[:4096] and raw[:2] not in (b"\xff\xfe", b"\xfe\xff"):
            raise DocumentError("this file is not plain text")
        text = decode_text(raw)
        delim = "\t" if src.suffix.lower() == ".tsv" else ","
        csv.field_size_limit(50_000_000)
        try:
            data = list(csv.reader(io.StringIO(text), delimiter=delim))
        except csv.Error as exc:
            raise DocumentError(f"this table could not be read ({exc})")
        if not data:
            raise DocumentError("this file is empty")
        if any(len(v) > MAX_CELL_CHARS for row in data for v in row):
            raise DocumentError(f"a cell holds more than {MAX_CELL_CHARS:,} characters; it is too "
                                "long to check as a table cell")
        header = data[0] if _header_values(data[0]) else None
        body = data[1:] if header else data
        res = clean_table_rows(header, body, eng)
        for p in res["passes"]:
            out.results.append(p.result)
        out.words_to_check = res["words"]
        out.removed = res["removed"]
        # A cell that a spreadsheet would run as a formula is written as text.
        for row in res["rows"]:
            for j, v in enumerate(row):
                if _FORMULA_START.match(v or ""):
                    row[j] = "'" + v
                    _count(out.removed, "formulas turned into text")
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=delim, lineterminator="\n")
        if res["header"]:
            w.writerow(res["header"])
        w.writerows(res["rows"])
        dst = dst.with_suffix(src.suffix.lower())
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(buf.getvalue(), encoding="utf-8")
        secrets = set().union(*(p.secrets for p in res["passes"])) if res["passes"] else set()
        pnames = set().union(*(p.names for p in res["passes"])) if res["passes"] else set()
        out.sweep = sweep_texts({dst.name: buf.getvalue()}, secrets, pnames)
        out.outputs = [dst]
        worst = next((p.result for p in res["passes"] if p.result.action.value == "review"), None)
        _settle(out, worst)
    except DocumentError as exc:
        out.status, out.reason = "failed", str(exc)
    except _Stop as st:
        out.status, out.reason = st.status, st.reason
        if st.result is not None:
            out.results.append(st.result)
    return out


# =====================================================================
# PDF (text PDFs)
# =====================================================================

PDF_DPI = 170


def _pdf_inventory(path: Path) -> tuple[dict, set[str], int]:
    """Hidden content in a PDF, the names in its properties, and how many
    form fields hold a value."""
    from pypdf import PdfReader
    found: dict[str, int] = {}
    names: set[str] = set()
    try:
        r = PdfReader(str(path))
        if r.is_encrypted:
            try:
                ok = r.decrypt("")
            except Exception:  # noqa: BLE001
                ok = 0
            if not ok:
                raise DocumentError("this PDF is password-protected; remove the password and try again")
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocumentError(f"this PDF could not be read ({type(exc).__name__})")
    meta = r.metadata or {}
    for k in ("/Author", "/Title", "/Subject", "/Keywords", "/Creator", "/Producer"):
        v = meta.get(k)
        if v and str(v).strip():
            _count(found, "document properties")
            if k == "/Author":
                names.add(str(v))
    try:
        if r.xmp_metadata is not None:
            _count(found, "XMP metadata")
    except Exception:  # noqa: BLE001
        pass
    filled = 0
    for page in r.pages:
        annots = page.get("/Annots")
        if annots:
            try:
                n = len(annots.get_object())
            except Exception:  # noqa: BLE001
                n = 1
            _count(found, "annotations and notes", n)
    try:
        fields = r.get_fields() or {}
    except Exception:  # noqa: BLE001
        fields = {}
    _count(found, "form fields", len(fields))
    filled = sum(1 for f in fields.values() if str(f.get("/V", "") or "").strip() not in ("", "/Off"))
    try:
        att = r.attachments or {}
        _count(found, "attached files", len(att))
    except Exception:  # noqa: BLE001
        pass
    try:
        if r.outline:
            _count(found, "bookmarks", len(r.outline))
    except Exception:  # noqa: BLE001
        pass
    root = r.trailer.get("/Root", {})
    try:
        root = root.get_object()
        if "/OpenAction" in root or ("/Names" in root and "/JavaScript" in root["/Names"].get_object()):
            _count(found, "scripts")
    except Exception:  # noqa: BLE001
        pass
    return found, {n for n in names if n}, filled


def _single_letter(w: dict) -> bool:
    t = w.get("text") or ""
    return len(t) == 1 and t.isalnum()


def _letter_spaced_joins(ws: list[dict]) -> list[bool]:
    """For words sorted left to right on one line, whether each word
    continues the one before it. Letter-spaced text ("L I N D Q V I S T")
    and comb fields put every character in its own word; a run of three or
    more single characters, set closer together than about one and a half
    letters, is one word, and a gap clearly wider than the run's usual gap
    starts the next. A column of single digits in a table is set further
    apart and stays as it is."""
    from statistics import median
    join = [False] * len(ws)
    i = 0
    while i < len(ws):
        if not _single_letter(ws[i]):
            i += 1
            continue
        j = i
        while j + 1 < len(ws) and _single_letter(ws[j + 1]):
            j += 1
        if j - i >= 2:
            gaps = [ws[k + 1]["x0"] - ws[k]["x1"] for k in range(i, j)]
            width = median(ws[k]["x1"] - ws[k]["x0"] for k in range(i, j + 1))
            mg = median(gaps)
            if mg <= 1.5 * max(width, 1.0):
                for k in range(i, j):
                    if gaps[k - i] <= max(1.6 * mg, mg + 1.0):
                        join[k + 1] = True
        i = j + 1
    return join


def _pdf_canvas(pdf) -> tuple[Canvas, list[dict]]:
    """Every page's words as lines, each character tied to its box.
    Overprinted characters (a "bold" made by printing twice) are read once;
    letter-spaced words are read as words."""
    cv = Canvas()
    pages = []
    for pno, raw_page in enumerate(pdf.pages):
        if pno:
            cv.sep("\n\n")
        page = raw_page.dedupe_chars(tolerance=1)
        sideways = sum(1 for c in page.chars if not c.get("upright", True) and (c.get("text") or "").strip())
        words = page.extract_words(keep_blank_chars=False, use_text_flow=False, return_chars=True)
        lines: list[list[dict]] = []
        for w in sorted(words, key=lambda w: (round(w["top"] / 3), w["x0"])):
            if lines and abs(lines[-1][0]["top"] - w["top"]) <= 3:
                lines[-1].append(w)
            else:
                lines.append([w])
        comb = False
        for li, line in enumerate(lines):
            if li:
                cv.sep("\n")
            ws = sorted(line, key=lambda w: w["x0"])
            if _comb_line(ws):
                comb = True
            joins = _letter_spaced_joins(ws)
            for wi, w in enumerate(ws):
                if wi and not joins[wi]:
                    cv.sep(" ", droppable=True)
                for ch in w.get("chars") or []:
                    t = _sanitize_piece(ch.get("text") or "")
                    box = {"page": pno, "x0": ch["x0"], "x1": ch["x1"], "top": ch["top"],
                           "bottom": ch["bottom"]}
                    cv.slot(box, t)
        pages.append({"width": raw_page.width, "height": raw_page.height,
                      "images": list(raw_page.images), "chars": len(page.chars),
                      "bbox": raw_page.bbox, "sideways": sideways, "comb": comb,
                      "curves": len(raw_page.curves),
                      "mediabox": getattr(raw_page, "mediabox", raw_page.bbox),
                      "cropbox": getattr(raw_page, "cropbox", raw_page.bbox),
                      "rotation": getattr(raw_page, "rotation", 0),
                      "words": [w["text"] for w in words]})
    return cv, pages


def _comb_line(ws: list[dict]) -> bool:
    """A line set one character at a time — a form comb field or wide
    letter-spacing — where the engine would see loose single characters and
    keep them, while a reader sees whole words. Widening the gaps defeats a
    distance-based join, so any line that is MADE of single characters (four
    or more, and at least half the line) is treated as suspect and the page
    is held. A sentence has few single-character tokens next to its words,
    so it does not normally trip this."""
    singles = sum(1 for w in ws if _single_letter(w))
    return singles >= 4 and singles >= 0.5 * len(ws)


# ---------------------------------------------------------------------
# Page check: what a reader SEES against what the tool READ.
# ---------------------------------------------------------------------
# Text can be drawn as shapes, set as a picture inside a form, or encoded
# so that it shows one thing and extracts as another. The tool would not
# read it, and the flattened copy would show it. So every rendered page is
# read back with OCR, and a page showing words the tool never read is held
# for a person.

OCR_MIN_CONF = 60


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


def _near(a: str, b: str, limit: int) -> bool:
    """Edit distance of at most `limit` (small limits only)."""
    if abs(len(a) - len(b)) > limit:
        return False
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            best = min(best, cur[j])
        if best > limit:
            return False
        prev = cur
    return prev[-1] <= limit


def ocr_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:  # noqa: BLE001
        return False


def _ocr_words(img) -> list[str]:
    import os
    import pytesseract
    # One thread per page: tesseract's own threading made a page take
    # about 8.5 s instead of 1.8 s on a two-core machine (measured).
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    # pytesseract hands the page image (taken before any box is drawn) to
    # the Tesseract program through short-lived files in the system temp
    # folder, and reads its answer back the same way. They are deleted
    # afterwards, but a crash or power loss mid-page can leave one behind.
    data = pytesseract.image_to_data(img.convert("L"), output_type=pytesseract.Output.DICT,
                                     config="--psm 11")
    out = []
    for text, conf in zip(data.get("text", []), data.get("conf", [])):
        try:
            c = float(conf)
        except (TypeError, ValueError):
            c = -1
        if text and text.strip() and c >= OCR_MIN_CONF:
            out.append(text.strip())
    return out


def unread_words(seen: list[str], read: list[str]) -> list[str]:
    """Words OCR saw on the page whose exact form the tool did not read.

    Matching is deliberately strict: a word counts as read only when its
    letters-and-digits form exactly equals an extracted token. The looser
    rules this once had — a word accepted because it was a SUBSTRING of all
    the extracted words joined together, or within an edit or two of one —
    let text drawn as shapes slip through ("Lee" inside "asleep", "Moore"
    one letter from "more"). OCR's own slips are forgiven later, in
    `_page_check`, where the engine can tell a misread word from a name."""
    tokens = {_norm(w) for w in read}
    tokens.discard("")
    missing = []
    for w in seen:
        n = _norm(w)
        if len(n) < 3 or n in tokens:
            continue
        missing.append(w)
    return missing


def _page_check(unread: list[str], read: list[str], eng: Engine) -> bool:
    """True when words the tool did not read could be identifiers.

    A word close to an extracted token is treated as an OCR misread and
    forgiven — UNLESS the engine would remove it, so a name one letter off
    an ordinary word ("Moore" beside "more") is not excused. What remains is
    held when three or more words are unaccounted for, any carries three or
    more digits, or the engine would remove any of them."""
    tokens = {_norm(w) for w in read}
    tokens.discard("")
    # A word close to an extracted token is OCR's own misread of text the
    # tool DID read, so it is forgiven. Text the tool could not read at all —
    # drawn as shapes, or a comb field — is caught before this, by the curve
    # and comb guards; what reaches here and matches nothing extracted is a
    # word shown on the page whose true form the tool never saw.
    real = [w for w in unread if not any(_near(_norm(w), t, 1 if len(_norm(w)) < 8 else 2)
                                         for t in tokens)]
    if not real:
        return False
    if len(real) >= 3 or any(sum(ch.isdigit() for ch in w) >= 3 for w in real):
        return True
    return _engine_removes(eng, " ".join(real))


def _engine_removes(eng: Engine, text: str) -> bool:
    try:
        res = eng.evaluate(text, redaction_mode="mask")
    except Exception:  # noqa: BLE001
        return True
    return res.action.value == "blocked" or res.clean_text != text


def _label(d, box, text: str) -> None:
    """The placeholder, in white, as large as the box allows."""
    from PIL import ImageFont
    h, w = box[3] - box[1], box[2] - box[0]
    size = max(7, int(h * 0.72))
    while size >= 7:
        try:
            font = ImageFont.load_default(size=size)
        except TypeError:                      # an older Pillow: one size only
            font = ImageFont.load_default()
        l, t_, r, b = d.textbbox((0, 0), text, font=font)
        if r - l <= w - 4 or size == 7:
            d.text((box[0] + 2, box[1] + (h - (b - t_)) / 2 - t_), text, fill=(255, 255, 255), font=font)
            return
        size -= 1


def clean_pdf(src: Path, dst: Path, eng: Engine) -> DocResult:
    out = DocResult(source=str(src), kind="pdf")
    try:
        import pdfplumber
        import pypdfium2 as pdfium
        from PIL import ImageDraw
        found, raw_names, filled = _pdf_inventory(src)
        out.hidden_found = found
        names = _author_names(raw_names)
        try:
            pdf = pdfplumber.open(str(src))
        except Exception as exc:  # noqa: BLE001
            raise DocumentError(f"this PDF could not be read ({type(exc).__name__})")
        with pdf:
            cv, pages = _pdf_canvas(pdf)
        for p in pages:
            area = float(p["width"] * p["height"]) or 1.0
            img_area = sum(max(0.0, (i["x1"] - i["x0"]) * (i["bottom"] - i["top"])) for i in p["images"])
            if img_area > 0.5 * area:
                raise DocumentError("this PDF looks scanned (a page is mostly an image); typed PDFs "
                                    "only for now")
            if p["rotation"] not in (0, None) or tuple(p["cropbox"]) != tuple(p["mediabox"]):
                raise _Stop("held", "a page is rotated or cropped; its boxes need a person to check")
            if p["sideways"]:
                raise _Stop("held", "some text runs sideways or at an angle; a person should check it")
            if p["comb"]:
                raise _Stop("held", "a line is set one character at a time (a form comb field or "
                                    "letter-spacing); a person should check it")
            if p["curves"] >= 4:
                raise _Stop("held", "part of a page is drawn as shapes rather than text (it may be a "
                                    "logo, a signature, or text drawn as a picture); a person should "
                                    "check it")
        if not ocr_available():
            raise _Stop("held", "the page check (OCR) is not available on this computer, so the "
                                "pages could not be compared with what the tool read")
        if len(cv.text.strip()) < 20:
            raise DocumentError("no text could be read from this PDF; typed PDFs only for now")
        text = cv.text
        if _sanitize_piece(text) != text:
            raise _Stop("held", "the text could not be normalised in place")
        ps = _engine_pass(text, eng, names)
        out.results.append(ps.result)
        out.words_to_check = ps.words_to_check
        out.removed.update(_removed_types(ps.result))
        _slots, expected = cv.apply(ps.edits)

        # Boxes to cover: every stored character inside a removal, joined
        # per line.
        cover: dict[int, list[tuple[float, float, float, float, str]]] = {}
        for s, e, rep in ps.edits:
            by_line: dict[tuple[int, int], list[dict]] = {}
            for i in range(s, e):
                o = cv.owner[i]
                if o is None or o[0] == "drop":
                    continue
                b = cv.slots[o[0]]
                by_line.setdefault((b["page"], round(b["top"])), []).append(b)
            first = True
            for (pg, _t), bs in sorted(by_line.items()):
                cover.setdefault(pg, []).append((min(b["x0"] for b in bs), min(b["top"] for b in bs),
                                                 max(b["x1"] for b in bs), max(b["bottom"] for b in bs),
                                                 rep if first else ""))
                first = False

        doc = pdfium.PdfDocument(str(src))
        images = []
        unchecked: list[int] = []
        try:
            if len(doc) != len(pages):
                raise _Stop("failed", "the PDF's pages could not be counted the same way twice")
            for pno in range(len(doc)):
                page = doc[pno]
                scale = PDF_DPI / 72
                bmp = page.render(scale=scale, draw_annots=False, may_draw_forms=False)
                img = bmp.to_pil().convert("RGB")
                sx = img.width / float(pages[pno]["width"])
                sy = img.height / float(pages[pno]["height"])
                d = ImageDraw.Draw(img)
                for im in pages[pno]["images"]:
                    box = (im["x0"] * sx, im["top"] * sy, im["x1"] * sx, im["bottom"] * sy)
                    d.rectangle(box, fill=(255, 255, 255))
                    d.rectangle(box, outline=(170, 170, 170))
                    d.text((box[0] + 4, box[1] + 4), "[image removed]", fill=(120, 120, 120))
                    _count(out.removed, "images and objects")
                # What a reader would see, before any box is drawn, against
                # what the tool read.
                if _page_check(unread_words(_ocr_words(img), pages[pno]["words"]),
                               pages[pno]["words"], eng):
                    unchecked.append(pno + 1)
                for x0, top, x1, bottom, rep in cover.get(pno, []):
                    box = (x0 * sx - 3, top * sy - 3, x1 * sx + 3, bottom * sy + 3)
                    d.rectangle(box, fill=(0, 0, 0))
                    if rep:
                        _label(d, box, rep)
                images.append(img)
        finally:
            doc.close()
        dst_pdf = dst.with_suffix(".pdf")
        dst_pdf.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        images[0].save(buf, "PDF", save_all=True, append_images=images[1:], resolution=PDF_DPI,
                       title="Clean copy", author="", subject="", keywords="", creator="", producer="")
        dst_pdf.write_bytes(buf.getvalue())
        dst_txt = dst_pdf.with_name(dst_pdf.name + ".txt")
        dst_txt.write_text(expected, encoding="utf-8")
        if filled:
            out.notes.append(f"{filled} filled form field(s) were not carried over")

        # Check: the flattened copy carries no text at all, and neither copy
        # holds a removed string or a name from the file's properties.
        with pdfplumber.open(str(dst_pdf)) as check:
            leftover = "".join(p.extract_text() or "" for p in check.pages).strip()
        if leftover:
            raise _Stop("failed", "the flattened copy still carries text")
        secrets = ps.secrets
        from pypdf import PdfReader
        meta = PdfReader(str(dst_pdf)).metadata or {}
        out.sweep = sweep_texts({dst_pdf.name: dst_pdf.read_bytes().decode("latin-1"),
                                 dst_pdf.name + " (properties)": " ".join(str(v) for v in meta.values()),
                                 dst_txt.name: expected}, secrets, ps.names | names)
        out.outputs = [dst_pdf, dst_txt]
        _settle(out, ps.result)
        if unchecked:
            pg = ", ".join(map(str, unchecked))
            msg = (f"page {pg} shows words the tool could not read as text (drawn as shapes or "
                   f"pictures); a person should check {'it' if len(unchecked) == 1 else 'them'}")
            if len(unchecked) > 1:
                msg = msg.replace("page ", "pages ", 1)
            out.notes.append(msg)
            if out.status == "clean":
                out.status, out.reason = "held", msg
        if out.status == "clean" and filled:
            out.status, out.reason = "held", f"{filled} filled form field(s) were not carried over"
    except DocumentError as exc:
        out.status, out.reason = "failed", str(exc)
    except _Stop as st:
        out.status, out.reason = st.status, st.reason
        if st.result is not None:
            out.results.append(st.result)
    return out


# =====================================================================
# entry points
# =====================================================================

def clean_file(src: Path, dst: Path, eng: Engine) -> DocResult:
    """Clean one document into `dst` (its suffix is set by the kind). Any
    unexpected error fails the file, never the run."""
    try:
        return _clean_file(src, dst, eng)
    except Exception as exc:  # noqa: BLE001
        return DocResult(source=str(src), kind=src.suffix.lower().lstrip("."), status="failed",
                         reason=f"this file could not be cleaned safely ({type(exc).__name__})")


def _clean_file(src: Path, dst: Path, eng: Engine) -> DocResult:
    suf = src.suffix.lower()
    if suf in (".docx", ".docm"):
        return clean_docx(src, dst, eng)
    if suf in (".xlsx", ".xlsm"):
        return clean_xlsx(src, dst, eng)
    if suf == ".pdf":
        return clean_pdf(src, dst, eng)
    if suf in (".csv", ".tsv"):
        return clean_csv(src, dst, eng)
    return DocResult(source=str(src), kind=suf.lstrip("."), status="failed",
                     reason="this kind of file is not supported yet")


def read_text(src: Path) -> str:
    """The text a reader sees, for learning names before redacting (the
    whole-patient first pass). Hidden parts are not included."""
    suf = src.suffix.lower()
    try:
        if suf in (".docx", ".docm"):
            parts = _read_zip(src)
            main = _main_part(parts)
            root = _parse(parts[main])
            _drop_provenance(root)
            _clean_story(root, {})
            return _docx_canvas({main: root}, [main]).text
        if suf in (".xlsx", ".xlsm"):
            import openpyxl
            wb = openpyxl.load_workbook(src, data_only=True, read_only=True)
            lines = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    vals = [_cell_text(v)[0] for v in row]
                    lines.append("  ".join(v for v in vals if v))
            return "\n".join(lines)
        if suf == ".pdf":
            import pdfplumber
            with pdfplumber.open(str(src)) as pdf:
                return _pdf_canvas(pdf)[0].text
        if suf in (".csv", ".tsv"):
            return decode_text(src.read_bytes())
    except Exception:  # noqa: BLE001
        return ""
    return ""
