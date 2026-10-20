"""
zircon — the intake guard.

Every file a user drops in is inspected HERE, before the redaction engine or
the document reader (calcite) ever opens it. The engine's job is to find and
remove patient information; it trusts that what it is handed is a real,
ordinary document. zircon is what earns that trust: it checks that a file is
what its name claims, that it hides no bomb or trap in the places a document
format allows, and that it carries no runnable or booby-trapped content a
cleaned copy could still pass on.

It works in three bands, outside the engine:

  1. The signature. The first bytes of a file say what it really is. If a
     ".pdf" does not begin like a PDF, or a ".docx" is not a ZIP, the name
     is a lie and the file is refused. This stops a renamed program and the
     simplest file that is two formats at once (a .pdf that begins as a ZIP).
     It is a quick byte-level screen for the common forms; calcite's rebuild
     of every file it keeps is the main protection.

  2. The container. Word and Excel files are ZIPs; a ZIP can be a bomb (a
     few megabytes that unpack to terabytes), can carry entries whose names
     climb out of the folder ("../"), or can be encrypted so nothing can be
     checked. Each of these is measured and capped. A ZIP inside the document
     is not opened here (calcite drops embedded files).

  3. The content. Documents can carry things that run or reach out: a PDF
     that launches a program or opens an attachment; an Office file with
     macros, embedded programs (OLE), ActiveX, or a template fetched from a
     web address; XML that defines entities to exhaust memory or read local
     files (billion-laughs, XXE). zircon separates these into two kinds —
     the ones a cleaned copy removes anyway (macros, ActiveX, embedded
     objects: calcite rebuilds from an allow-list, so they do not survive),
     which are NOTED, and the ones that must not be opened at all (a launch
     action, an embedded program, an encrypted part, a bomb), which are
     REFUSED.

By default the inspection runs in the same process as the rest of the tool,
with no sandbox: the checks only read ZIP listings and size-capped byte
ranges, never a full parse. An optional mode (`sandbox=True`, not used by
0.25.0) runs the deeper check in a separate process with memory and CPU-time
limits, so that a file built to crash or hang a parser stops only that
process. That mode sets no network restriction of its own; the inspection
code makes no network calls either way.

Nothing here reads patient content, and nothing it writes to its event log
names a person: every outcome is a short reason and a stable support code
(ZC-…) so that whoever supports the software can read the log and help
without seeing patient data.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

# What the intake guard will hand on to be read. Everything else is set
# aside, not refused: an image or an old-format file is not dangerous, just
# not something the tool reads (cli._classify decides that). zircon's REFUSE
# is reserved for files that are a lie about their type or carry something
# that must not be opened.
READABLE = {".docx", ".docm", ".xlsx", ".xlsm", ".pdf", ".csv", ".tsv", ".txt", ".text", ".md"}
OFFICE_ZIP = {".docx", ".docm", ".xlsx", ".xlsm"}

# Container caps (shared intent with calcite's own, kept here so the guard
# can refuse a bomb before calcite opens it).
MAX_ENTRIES = 5_000
MAX_UNZIPPED = 300_000_000
MAX_RATIO = 200                      # unpacked : packed, above which it is a bomb
MAX_NESTING = 3                      # not yet used: no nesting check is made

VERDICT_OK = "ok"
VERDICT_SET_ASIDE = "set_aside"      # keep, flag, do not read (not dangerous)
VERDICT_REFUSE = "refuse"           # do not open: a lie about its type, or a trap


@dataclass
class Verdict:
    status: str = VERDICT_OK
    code: str = ""                   # a stable ZC-… support code, no patient data
    reason: str = ""                 # a short, factual, non-accusatory sentence
    notes: list = field(default_factory=list)   # active content that cleaning removes

    @property
    def ok(self) -> bool:
        return self.status == VERDICT_OK


# ---------------------------------------------------------------------
# 1. The signature — is the file what its name says?
# ---------------------------------------------------------------------
# (code, matcher) pairs. A matcher takes the first bytes and returns True.

def _sig_pdf(h: bytes) -> bool:
    return h[:5] == b"%PDF-" or h[:5].lstrip(b"\x00\r\n ") .startswith(b"%PDF")


def _sig_zip(h: bytes) -> bool:
    return h[:2] in (b"PK",)


_EXECUTABLE_SIGS = {
    b"MZ": "a Windows program",
    b"\x7fELF": "a Linux program",
    b"\xca\xfe\xba\xbe": "a program",
    b"\xcf\xfa\xed\xfe": "a macOS program",
    b"#!": "a script",
}
# Container formats that hide other files from the operating system's own
# "downloaded from the internet" warning. Refused on sight.
_CONTAINER_SIGS = {
    b"\x4c\x00\x00\x00": "a Windows shortcut (.lnk)",
    b"MSCF": "a cabinet archive",
    b"Rar!": "a RAR archive",
    b"7z\xbc\xaf": "a 7-Zip archive",
}


def _looks_like(h: bytes, suf: str) -> tuple[bool, str]:
    """Does the content match the extension? Returns (match, what_it_is)."""
    if suf == ".pdf":
        return _sig_pdf(h), "a PDF"
    if suf in OFFICE_ZIP:
        return _sig_zip(h), "a Word or Excel file"
    # Text kinds: anything that is not binary. A NUL byte in the first chunk
    # is the simplest, most reliable "this is not text" signal.
    if suf in {".csv", ".tsv", ".txt", ".text", ".md"}:
        if h[:2] in (b"\xff\xfe", b"\xfe\xff"):      # UTF-16 with its byte-order mark
            return True, "a text file"
        return b"\x00" not in h[:8192], "a text file"
    return True, ""


def check_signature(path: Path, suf: str) -> Verdict:
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError as exc:
        return Verdict(VERDICT_SET_ASIDE, "ZC-READ", f"this file could not be read ({type(exc).__name__})")
    for sig, what in _EXECUTABLE_SIGS.items():
        if head.startswith(sig):
            return Verdict(VERDICT_REFUSE, "ZC-EXEC",
                           f"this file is {what}, not a document; it was not opened")
    for sig, what in _CONTAINER_SIGS.items():
        if head.startswith(sig):
            return Verdict(VERDICT_REFUSE, "ZC-CONTAINER",
                           f"this file is {what}; unpack it and add the documents inside instead")
    # The old binary Office format and password-protected OOXML both begin
    # with the compound-file signature. Either way it is not a readable
    # .docx/.xlsx: ask for an unprotected, modern copy.
    if suf in OFFICE_ZIP and head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return Verdict(VERDICT_REFUSE, "ZC-OFFICE-OLD",
                       "this file is password-protected or in the old binary format; save an "
                       "unprotected .docx or .xlsx and try again")
    match, what = _looks_like(head, suf)
    if not match:
        return Verdict(VERDICT_REFUSE, "ZC-MISMATCH",
                       f"this file is named {suf} but its contents are not {what}; it was not opened")
    # A PDF that also opens as a ZIP, or vice versa: a polyglot. Refuse.
    if suf == ".pdf" and _sig_zip(head):
        return Verdict(VERDICT_REFUSE, "ZC-POLYGLOT",
                       "this file is two formats at once; it was not opened")
    return Verdict()


# ---------------------------------------------------------------------
# 2. The container — Office ZIP checks
# ---------------------------------------------------------------------

_TRAVERSAL = re.compile(r"(^|/)\.\.(/|$)")


def check_zip(path: Path, depth: int = 0) -> Verdict:
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ENTRIES:
                return Verdict(VERDICT_REFUSE, "ZC-ZIP-COUNT",
                               "this file has far more parts than a document has; it was not opened")
            packed = max(1, path.stat().st_size)
            total = 0
            for info in infos:
                name = info.filename
                if name.startswith("/") or _TRAVERSAL.search(name) or (len(name) > 1 and name[1] == ":"):
                    return Verdict(VERDICT_REFUSE, "ZC-ZIP-PATH",
                                   "part of this file is addressed outside the document; it was not opened")
                if info.flag_bits & 0x1:
                    return Verdict(VERDICT_REFUSE, "ZC-ZIP-ENCRYPTED",
                                   "part of this file is encrypted and cannot be checked; save an "
                                   "unprotected copy and try again")
                total += info.file_size
                if total > MAX_UNZIPPED or total > MAX_RATIO * packed:
                    return Verdict(VERDICT_REFUSE, "ZC-ZIP-BOMB",
                                   "this file unpacks to far more than its size suggests; it was not opened")
            return Verdict()
    except zipfile.BadZipFile:
        return Verdict(VERDICT_REFUSE, "ZC-ZIP-BROKEN", "this file is damaged or not a real document; "
                       "it was not opened")
    except OSError as exc:
        return Verdict(VERDICT_SET_ASIDE, "ZC-READ", f"this file could not be read ({type(exc).__name__})")


# ---------------------------------------------------------------------
# 3. The content
# ---------------------------------------------------------------------
# Office: what a cleaned copy removes anyway is NOTED; what must not be
# opened is REFUSED.

# Refuse: an embedded program, a remote (web) relationship a cleaned copy
# would still carry a reference to, or a DTD/entity (XXE / billion-laughs).
_OFFICE_REFUSE = [
    (re.compile(rb"<!DOCTYPE|<!ENTITY"), "ZC-XML-ENTITY",
     "this file defines XML entities, which documents do not need and which can be used to overload a "
     "reader; it was not opened"),
    (re.compile(rb"TargetMode\s*=\s*[\"']External[\"'][^>]*Target\s*=\s*[\"'](?:https?|ftp|file)://",
                re.IGNORECASE),
     "ZC-REMOTE", "this file refers to content at an outside address; it was not opened"),
    (re.compile(rb"Target\s*=\s*[\"'](?:https?|ftp|file)://[^\"']*"
                rb"(?:dotm|dotx|xlam|template)", re.IGNORECASE),
     "ZC-REMOTE-TEMPLATE", "this file loads a template from another location (a web address or another "
     "file) when opened; it was not opened"),
]
# Note (cleaning removes these): macros, ActiveX, OLE objects, DDE.
_OFFICE_NOTE = [
    (re.compile(r"vbaProject\.bin$|/vbaData\.xml$"), "ZC-MACRO", "macros (removed)"),
    (re.compile(r"/activeX/"), "ZC-ACTIVEX", "ActiveX controls (removed)"),
    (re.compile(r"/embeddings/|oleObject", re.IGNORECASE), "ZC-OLE", "embedded objects (removed)"),
]
_DDE = re.compile(rb"DDEAUTO|\bDDE\b", re.IGNORECASE)


def inspect_office(path: Path) -> Verdict:
    v = check_zip(path)
    if not v.ok:
        return v
    notes: list[str] = []
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                name = info.filename
                for rx, code, note in _OFFICE_NOTE:
                    if rx.search(name):
                        notes.append(note)
                # Look inside the XML parts (not binaries) for refuse markers.
                if name.endswith((".xml", ".rels")):
                    try:
                        data = z.read(info)
                    except Exception:  # noqa: BLE001
                        return Verdict(VERDICT_REFUSE, "ZC-ZIP-BROKEN",
                                       "part of this file could not be read; it was not opened")
                    for rx, code, reason in _OFFICE_REFUSE:
                        if rx.search(data):
                            return Verdict(VERDICT_REFUSE, code, reason)
                    if _DDE.search(data):
                        notes.append("field commands (removed)")
    except zipfile.BadZipFile:
        return Verdict(VERDICT_REFUSE, "ZC-ZIP-BROKEN", "this file is damaged or not a real document; "
                       "it was not opened")
    except OSError as exc:
        return Verdict(VERDICT_SET_ASIDE, "ZC-READ", f"this file could not be read ({type(exc).__name__})")
    return Verdict(notes=sorted(set(notes)))


# PDF: markers that run or reach out. A scriptable PDF is flattened to images
# by calcite anyway, so most are NOTED; a launch action or an embedded
# program is REFUSED (never opened).
_PDF_REFUSE = [
    (re.compile(rb"/Launch\b"), "ZC-PDF-LAUNCH",
     "this PDF is set to launch a program when opened; it was not opened"),
    (re.compile(rb"/EmbeddedFiles?\b|/Filespec\b"), "ZC-PDF-EMBED",
     "this PDF carries an attached file; it was not opened — open the attachment separately if you need it"),
]
_PDF_NOTE = [
    (re.compile(rb"/JavaScript\b|/JS\b"), "ZC-PDF-JS", "page scripts (removed by flattening)"),
    (re.compile(rb"/OpenAction\b|/AA\b"), "ZC-PDF-ACTION", "open actions (removed by flattening)"),
    (re.compile(rb"/RichMedia\b|/Movie\b|/Sound\b"), "ZC-PDF-MEDIA", "embedded media (removed by flattening)"),
    (re.compile(rb"/XFA\b"), "ZC-PDF-XFA", "an XFA form (flattened)"),
]


MAX_SCAN_BYTES = 200_000_000         # a typed clinical PDF is far smaller


def inspect_pdf(path: Path) -> Verdict:
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            return Verdict(VERDICT_REFUSE, "ZC-PDF-HUGE",
                           "this PDF is far larger than a typed document; it was not opened")
        data = path.read_bytes()
    except OSError as exc:
        return Verdict(VERDICT_SET_ASIDE, "ZC-READ", f"this file could not be read ({type(exc).__name__})")
    for rx, code, reason in _PDF_REFUSE:
        if rx.search(data):
            return Verdict(VERDICT_REFUSE, code, reason)
    notes = [note for rx, code, note in _PDF_NOTE if rx.search(data)]
    return Verdict(notes=sorted(set(notes)))


# ---------------------------------------------------------------------
# The optional sandbox (off by default): run an inspection in a separate
# process with memory and CPU-time limits, so a file built to crash or hang a
# parser stops only that process.
# ---------------------------------------------------------------------

def _worker(fn, path_str, q):
    try:
        import resource
        # Half a gigabyte of address space and 20 CPU seconds: enough for any
        # real document, not enough for a bomb to do damage.
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, resource.RLIM_INFINITY))
        resource.setrlimit(resource.RLIMIT_CPU, (20, resource.RLIM_INFINITY))
    except Exception:  # noqa: BLE001  (not POSIX, or not permitted)
        pass
    try:
        v = fn(Path(path_str))
        q.put((v.status, v.code, v.reason, v.notes))
    except MemoryError:
        q.put((VERDICT_REFUSE, "ZC-OVERLOAD", "this file used far more memory than a document should "
               "while being checked; it was not opened", []))
    except Exception as exc:  # noqa: BLE001
        q.put((VERDICT_REFUSE, "ZC-PARSE", f"this file could not be checked safely ({type(exc).__name__}); "
               "it was not opened", []))


def _sandboxed(fn, path: Path, timeout: float = 30.0) -> Verdict:
    import multiprocessing as mp
    # fork where it exists (Linux): fast, and the child inherits the process
    # so limits apply at once. Mac and Windows have only spawn, which needs
    # the inspection function to be importable — ours are module-level, so it
    # works there too.
    methods = mp.get_all_start_methods()
    method = "fork" if "fork" in methods else "spawn"
    try:
        ctx = mp.get_context(method)
        q = ctx.Queue()
        p = ctx.Process(target=_worker, args=(fn, str(path), q))
        p.start()
        p.join(timeout)
        if p.is_alive():
            p.terminate()
            p.join(5)
            return Verdict(VERDICT_REFUSE, "ZC-TIMEOUT",
                           "this file took too long to check; it was not opened")
        if q.empty():
            return Verdict(VERDICT_REFUSE, "ZC-CRASH",
                           "this file stopped the checker unexpectedly; it was not opened")
        status, code, reason, notes = q.get()
        return Verdict(status, code, reason, list(notes))
    except Exception:  # noqa: BLE001  (no fork/spawn here) — fall back to in-process
        try:
            return fn(path)
        except Exception as exc:  # noqa: BLE001
            return Verdict(VERDICT_REFUSE, "ZC-PARSE",
                           f"this file could not be checked safely ({type(exc).__name__}); it was not opened")


# ---------------------------------------------------------------------
# The front door.
# ---------------------------------------------------------------------

def inspect(path: Path, sandbox: bool = False) -> Verdict:
    """Inspect one file before anything else opens it. Returns a Verdict:
    ok (hand it on), set_aside (keep and flag, do not read), or refuse (a lie
    about its type, or a trap — do not open).

    The checks are bounded by design — ZIP metadata and capped, size-limited
    reads, never a full parse of untrusted structure — so they are safe to
    run in-process, which is the default. `sandbox=True` runs the deep check
    in a separate, memory- and time-limited process as well; it is meant for
    a packaged app (desktop app, in development) that runs the guard on its
    own, before the heavy engine is loaded, where forking is cheap. 0.25.0
    does not turn it on. On a process that has already loaded the
    engine, forking per file is expensive, so the default stays in-process."""
    suf = path.suffix.lower()
    if suf not in READABLE:
        # Not something zircon gates; cli._classify decides (image, old
        # Office, other) and it is set aside there, never opened by the engine.
        return Verdict()
    sig = check_signature(path, suf)
    if not sig.ok:
        return sig
    if suf in OFFICE_ZIP:
        deep = inspect_office
    elif suf == ".pdf":
        deep = inspect_pdf
    else:
        return Verdict()                 # a plain text / CSV file: signature was enough
    return _sandboxed(deep, path) if sandbox else deep(path)
