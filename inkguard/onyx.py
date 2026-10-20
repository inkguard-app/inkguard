"""
PSEUDONYMS — codes that carry forward.

WHAT A RESEARCHER NEEDS, AND WHAT [NAME] TAKES AWAY
----------------------------------------------------
Replacing every name with [NAME] removes the identity and also removes
the one thing a researcher needs from it: whether two mentions are the
SAME person. "Haddad was admitted... Haddad's daughter consented...
Dr. Wendermere reviewed" becomes three indistinguishable [NAME] tags, and a
study of who-did-what across a record is no longer possible.

A pseudonym keeps the relationship and drops the identity. Every mention
of one person gets one code, in every note of that patient's record:

    Patient: Aisha Haddad      ->  Patient: [PERSON-K7Q3F]
    Ms. Haddad tolerated...    ->  Ms. [PERSON-K7Q3F] tolerated...
    Signed: Dr. T. Wendermere  ->  Signed: Dr. [PROVIDER-2M8XD]

If the output is ever exposed, what is exposed is the code.

HOW THE CODE IS MADE, AND WHY EACH CHOICE
-----------------------------------------
RANDOM, NOT DERIVED. The visible code is drawn from a secure
random source the first time a person is seen, and is NOT computed from
the name. The HIPAA re-identification rule (45 CFR 164.514(c)(1)) asks
that a code be "not derived from or related to information about the
individual". HHS's guidance allows codes computed from patient data by a
keyed hash only under Expert Determination (its Q2.9); for Safe Harbor
it says an unkeyed hash is an identifying code (Q3.5) and never says a
keyed one is not. A random code avoids the question: nothing about the
person goes into it.

THE CODE TABLE. To give one person the same code every time, the first
code drawn is remembered in a local code table. The table is looked up by
an INDEX — a keyed HMAC of the patient scope and the canonical name —
and that index never appears in any output; it is the "mechanism for
re-identification" the rule says must not be disclosed, and it is not.
The table stores no names: only opaque index → code pairs, meaningless
without the root key. It is append-only (a code, once given, never
changes), sealed with an integrity tag, written atomically under a file
lock so two runs at once cannot lose an entry, and every code in it is
unique across the whole table — two different people can never share a
code by coincidence, which a hashed code could not promise. A missing,
unreadable or tampered table STOPS pseudonym mode; it never falls back to
computing codes from names.

KEYED. An unkeyed hash can be reversed by hashing a list of surnames, so
nothing here is unkeyed. The index is keyed for that reason: without the
root key the table reveals nothing.

CANONICAL. Mentions are resolved to one canonical name before coding:
"Haddad" alone maps to "Aisha Haddad" if that is the only full name in
the record containing it. If two people share the surname, "Haddad"
alone is ambiguous and keeps its own code rather than being silently
merged with one of them — a wrong merge would fabricate a relationship
in the research data.

SCOPED TO THE PATIENT. Codes are consistent within one patient's record
and independent across patients. Two different patients named John
Smith must not share a code: that would falsely link them, and codes
that link people ACROSS records are themselves a re-identification
vector.

OPAQUE. The visible code carries no hint of the name's origin. Grouping
codes by language — an Arabic-name family, a Japanese-name family — was
considered and rejected: a code saying "this name is Arabic" discloses a
proxy for ethnicity, religion and national origin, which is exactly the
category where exposure carries immigration and discrimination risk, and
it adds a quasi-identifier to the retained demographics. Script family is
still computed, because it is the right axis to MEASURE detection equity
on — but it goes only into the private operation log.

PERSISTENT KEY AND TABLE REQUIRED. Codes that change between runs do
not carry forward, which defeats the purpose silently. Same rule as date
shifting: the root key must be persistent, and the code table must have a
configured, persistent home (PHI_CODE_TABLE). The key and the table are
kept and backed up TOGETHER: either alone cannot carry codes forward.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import unicodedata
from dataclasses import dataclass, field

PERSON, PROVIDER, FAMILY = "PERSON", "PROVIDER", "FAMILY"
_ROLE_RANK = {PERSON: 0, FAMILY: 1, PROVIDER: 2}

_TITLE = re.compile(
    r"^(?:mr|mrs|ms|miss|mx|dr|prof|doctor|nurse|sr|jr)\.?\s+", re.IGNORECASE)
_PROVIDER_BEFORE = re.compile(
    r"\b(?:Dr|Doctor|NP|PA|RN|MD|DO|Prof)\.?\s*$|"
    r"\b(?:signed|dictated|attending|surgeon|physician|provider|"
    r"referring|reviewed)\b[^\n]{0,20}$",
    re.IGNORECASE,
)


def normalize_name(name: str) -> str:
    """Canonical form for coding: NFKC, titles removed, case-folded,
    punctuation stripped, whitespace collapsed."""
    n = unicodedata.normalize("NFKC", name).strip()
    n = _TITLE.sub("", n)
    n = re.sub(r"[.,;:'\"()\[\]]", " ", n)
    n = re.sub(r"\s+", " ", n).strip().casefold()
    return n


def script_family(name: str) -> str:
    """For the private QA log only. Never written into output.
    Resolved through the shared script table in the type registry
    (talc.script_of)."""
    from inkguard.talc import script_of
    for ch in name:
        fam = script_of(ch)
        if fam:
            return fam
    return "latin" if re.search(r"[A-Za-zÀ-ɏ]", name) else "other"


# ---------------------------------------------------------------------
# THE CODE TABLE — random codes, remembered locally, never derived.
#
# Format (v2): a JSON-lines file, append-only.
#   line 1   {"v": 2, "id": <table id>, "m": seal("hdr", id)}
#   line n   {"i": <index>, "c": <code>, "m": seal(prev m, index, code)}
# Each record's seal chains to the one before, so an edited, reordered,
# inserted or foreign line breaks verification. A new person costs one
# appended line, not a rewrite. A sibling WATERMARK records the most
# entries this computer has seen for each table, so a table that comes
# back shorter (a stale backup, a copy from another machine) is refused.
# ---------------------------------------------------------------------

CODE_LEN = 5
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"   # base32: same look as before
_TABLE_VERSION = 2
MAX_TABLE_BYTES = 1 << 30                        # refuse anything absurd before reading it
LOCK_TIMEOUT_S = 60.0
_HEX = set("0123456789abcdef")


class CodeTableError(RuntimeError):
    """The code table is not set up, cannot be read, or fails a check.
    Pseudonym mode stops; it never falls back to codes computed from names."""


def _valid_code(c) -> bool:
    return isinstance(c, str) and len(c) == CODE_LEN and set(c) <= set(_ALPHABET)


def _valid_hex(x, n: int) -> bool:
    return isinstance(x, str) and len(x) == n and set(x) <= _HEX


class _FileLock:
    """An exclusive, time-limited lock on a sidecar file. Each acquisition
    has its own handle (safe across threads); waiting longer than the limit
    raises instead of hanging."""

    def __init__(self, path):
        self.path = path

    def acquire(self):
        import os
        import time
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(str(self.path), flags, 0o600)
        except OSError as exc:
            raise CodeTableError(f"the code table's lock could not be opened ({type(exc).__name__})") from None
        deadline = time.monotonic() + LOCK_TIMEOUT_S
        try:
            try:
                import fcntl
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        return fd
                    except BlockingIOError:
                        pass
                    if time.monotonic() > deadline:
                        raise CodeTableError("the code table is busy (another run has held it too long)")
                    time.sleep(0.02)
            except ImportError:                        # Windows
                import errno
                import msvcrt
                while True:
                    try:
                        os.lseek(fd, 0, 0)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        return fd
                    except OSError as exc:
                        if exc.errno not in (errno.EACCES, errno.EDEADLK):
                            raise CodeTableError(f"the code table could not be locked ({type(exc).__name__})") from None
                    if time.monotonic() > deadline:
                        raise CodeTableError("the code table is busy (another run has held it too long)")
                    time.sleep(0.02)
        except BaseException:
            os.close(fd)
            raise

    @staticmethod
    def release(fd) -> None:
        import os
        try:
            try:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            except ImportError:
                import msvcrt
                os.lseek(fd, 0, 0)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


class CodeTable:
    """index -> random code, persisted locally, append-only.

    `key` is the pseudonym key (derived from the persistent root key). It
    keys the lookup index and the seals; it never touches a code. Open an
    existing table with CodeTable(path, key); create one only with
    create_code_table — a missing table is an error, never a fresh start.
    """

    def __init__(self, path, key: bytes):
        import threading
        from pathlib import Path
        self.path = Path(path)
        self._index_key = hmac.new(key, b"code-table-index-v1", hashlib.sha256).digest()
        self._seal_key = hmac.new(key, b"code-table-seal-v1", hashlib.sha256).digest()
        self._lock = _FileLock(self.path.with_name(self.path.name + ".lock"))
        self._mutex = threading.RLock()
        self.table_id: str | None = None
        self._codes: dict[str, str] = {}
        self._used: set[str] = set()
        self._offset = 0             # bytes verified, through the last complete line
        self._last_mac = ""
        if not self.path.exists():           # say WHY before touching any lock
            raise CodeTableError(missing_table_reason(self.path))
        with self._mutex:
            fd = self._lock.acquire()
            try:
                self._fresh()
                self._check_watermark()
            finally:
                _FileLock.release(fd)

    # -- the index: keyed, internal, never output -------------------------
    def _index(self, scope: str, canonical: str) -> str:
        return hmac.new(self._index_key, f"{scope}\x1f{canonical}".encode(),
                        hashlib.sha256).hexdigest()[:32]

    def _mac(self, *parts: str) -> str:
        return hmac.new(self._seal_key, "\x1f".join(parts).encode(), hashlib.sha256).hexdigest()

    # -- reading ------------------------------------------------------------
    def _fresh(self) -> None:
        """Verify and take in whatever has been appended since the last look.
        Call with the file lock (or at construction) held."""
        if not self.path.exists():
            raise CodeTableError(missing_table_reason(self.path))
        try:
            size = self.path.stat().st_size
            if size > MAX_TABLE_BYTES:
                raise CodeTableError("the code table is far larger than any real table; it was not read")
            if size < self._offset:
                raise CodeTableError("the code table became shorter while in use")
            if size == self._offset and self.table_id is not None:
                with open(self.path, "rb") as f:
                    head = f.readline(512)
                self._check_header(head)
                return
            with open(self.path, "rb") as f:
                if self.table_id is not None:
                    self._check_header(f.readline(512))
                f.seek(self._offset)
                chunk = f.read(size - self._offset)
            self._take(chunk)
        except CodeTableError:
            raise
        except Exception as exc:  # noqa: BLE001 — any read problem is a stop, never a guess
            raise CodeTableError(f"the code table could not be read ({type(exc).__name__})") from None

    def _check_header(self, raw: bytes) -> None:
        try:
            h = json.loads(raw)
        except ValueError:
            raise CodeTableError("the code table failed its integrity check (header)") from None
        if not isinstance(h, dict) or h.get("id") != self.table_id:
            raise CodeTableError("the code table was replaced by a different table while in use")

    def _take(self, chunk: bytes) -> None:
        pos = 0
        while True:
            nl = chunk.find(b"\n", pos)
            if nl < 0:
                break                          # a torn last line: not ours to trust; ignored
            line = chunk[pos:nl]
            pos = nl + 1
            try:
                rec = json.loads(line)
            except (ValueError, RecursionError):
                raise CodeTableError("the code table failed its integrity check (unreadable line)") from None
            if not isinstance(rec, dict):
                raise CodeTableError("the code table failed its integrity check (unexpected line)")
            if self.table_id is None:
                if (rec.get("v") != _TABLE_VERSION or not _valid_hex(rec.get("id"), 32)
                        or not hmac.compare_digest(str(rec.get("m", "")), self._mac("hdr", rec["id"]))):
                    raise CodeTableError("the code table failed its integrity check "
                                         "(damaged, edited by hand, or made under another key)")
                self.table_id = rec["id"]
                self._last_mac = rec["m"]
            else:
                i, c, m = rec.get("i"), rec.get("c"), rec.get("m")
                if not (_valid_hex(i, 32) and _valid_code(c) and isinstance(m, str)):
                    raise CodeTableError("the code table failed its integrity check (malformed entry)")
                if not hmac.compare_digest(m, self._mac(self._last_mac, i, c)):
                    raise CodeTableError("the code table failed its integrity check "
                                         "(an entry was changed, moved or inserted)")
                if i in self._codes or c in self._used:
                    raise CodeTableError("the code table failed its integrity check (a repeat)")
                self._codes[i] = c
                self._used.add(c)
                self._last_mac = m
            self._offset += len(line) + 1
        if self.table_id is None:
            raise CodeTableError("the code table failed its integrity check (no header)")

    # -- the watermark: this computer's memory of how long the table was ----
    def _check_watermark(self) -> None:
        seen = _read_watermark(self.path, self._seal_key).get(self.table_id)
        if seen is not None and len(self._codes) < seen:
            raise CodeTableError(
                f"the code table is older than the one this computer last used "
                f"({len(self._codes)} entries, previously {seen}); restore the newest backup, "
                f"or accept the restored table knowingly")

    def _raise_watermark(self) -> None:
        _write_watermark(self.path, self._seal_key, self.table_id, len(self._codes))

    # -- giving codes ---------------------------------------------------------
    def codes_for(self, scope: str, canonicals) -> dict[str, str]:
        """Codes for several people at once (one write for all new ones)."""
        want = {c: self._index(scope, c) for c in canonicals}
        with self._mutex:
            out = {c: self._codes[i] for c, i in want.items() if i in self._codes}
            if len(out) == len(want):          # entries never change: a hit is final
                return out
            fd = self._lock.acquire()
            try:
                self._fresh()
                lines, added = [], []
                for c, i in want.items():
                    if i in self._codes:
                        out[c] = self._codes[i]
                        continue
                    if any(i == a[0] for a in added):
                        out[c] = next(a[1] for a in added if a[0] == i)
                        continue
                    for _ in range(10_000):
                        code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LEN))
                        if code not in self._used and all(code != a[1] for a in added):
                            break
                    else:                                       # pragma: no cover
                        raise CodeTableError("the code table is full")
                    prev = added[-1][2] if added else self._last_mac
                    m = self._mac(prev, i, code)
                    added.append((i, code, m))
                    lines.append(json.dumps({"i": i, "c": code, "m": m},
                                            separators=(",", ":")) + "\n")
                    out[c] = code
                if added:
                    self._append("".join(lines).encode())
                    for i, code, m in added:
                        self._codes[i] = code
                        self._used.add(code)
                    self._last_mac = added[-1][2]
                    self._offset += len("".join(lines).encode())
                    self._raise_watermark()
                return out
            finally:
                _FileLock.release(fd)

    def code_for(self, scope: str, canonical: str) -> str:
        return self.codes_for(scope, [canonical])[canonical]

    def _append(self, data: bytes) -> None:
        import os
        try:
            with open(self.path, "r+b") as f:
                f.seek(self._offset)
                f.truncate()                   # drop a torn line left by a crash, never a verified one
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            _fsync_dir(self.path.parent)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise CodeTableError(f"the code table could not be saved ({type(exc).__name__})") from None

    def check(self) -> None:
        """Re-verify the table on disk now (for a health check)."""
        with self._mutex:
            fd = self._lock.acquire()
            try:
                self._fresh()
                self._check_watermark()
            finally:
                _FileLock.release(fd)

    def info(self) -> dict:
        """What a run report may say about the table: its id and size. Neither
        reveals anything about any person."""
        return {"id": (self.table_id or "")[:12], "entries": len(self._codes)}

    def __len__(self) -> int:
        return len(self._codes)

    def __repr__(self) -> str:
        return f"<CodeTable entries={len(self._codes)}>"


def _fsync_dir(d) -> None:
    import os
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return                                  # Windows: directories cannot be opened this way
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _watermark_path(path):
    return path.with_name(path.name + ".seen")


def _read_watermark(path, seal_key: bytes) -> dict:
    wp = _watermark_path(path)
    if not wp.exists():
        return {}
    try:
        data = json.loads(wp.read_text(encoding="utf-8"))
        seen = data["seen"]
        body = json.dumps(seen, sort_keys=True, separators=(",", ":"))
        ok = hmac.compare_digest(str(data.get("m", "")),
                                 hmac.new(seal_key, body.encode(), hashlib.sha256).hexdigest())
        if not ok or not all(_valid_hex(k, 32) and isinstance(v, int) and v >= 0
                             for k, v in seen.items()):
            raise ValueError
        return seen
    except Exception:  # noqa: BLE001
        raise CodeTableError("the code table's watermark failed its integrity check") from None


def _write_watermark(path, seal_key: bytes, table_id: str, count: int, *, force: bool = False) -> None:
    import os
    import tempfile
    import time
    seen = dict(_read_watermark(path, seal_key))
    if not force and seen.get(table_id, 0) >= count:
        return
    seen[table_id] = count
    body = json.dumps(seen, sort_keys=True, separators=(",", ":"))
    payload = json.dumps({"seen": seen,
                          "m": hmac.new(seal_key, body.encode(), hashlib.sha256).hexdigest()})
    wp = _watermark_path(path)
    fd, tmp = tempfile.mkstemp(prefix=".seen.", dir=str(wp.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(50):                # Windows: a reader may hold it briefly
            try:
                os.replace(tmp, wp)
                break
            except PermissionError:
                time.sleep(0.05 * (attempt + 1))
        else:
            raise CodeTableError("the code table's watermark could not be updated")
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def missing_table_reason(path) -> str:
    """Why there is no table, in words the doctor's IT person can act on."""
    try:
        has_history = _watermark_path(path).exists()
    except OSError:
        has_history = False
    if has_history:
        return ("the code table this computer used before is missing — restore it from the "
                "backup kept with the root key (creating a new one would give every person a new code)")
    return "no code table has been set up yet (first-time setup creates one)"


def _resolve_table_path(path):
    from pathlib import Path
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        raise CodeTableError("the code table location must be a full path, not a relative one")
    return p.resolve()


def create_code_table(path, key: bytes) -> "CodeTable":
    """First-time setup: make a new, empty, sealed table. Refuses if one is
    already there — a table is never silently replaced."""
    import os
    p = _resolve_table_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    seal_key = hmac.new(key, b"code-table-seal-v1", hashlib.sha256).digest()
    tid = secrets.token_hex(16)
    hdr = json.dumps({"v": _TABLE_VERSION, "id": tid,
                      "m": hmac.new(seal_key, f"hdr\x1f{tid}".encode(), hashlib.sha256).hexdigest()},
                     separators=(",", ":")) + "\n"
    try:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise CodeTableError("a code table already exists there; it was left untouched") from None
    with os.fdopen(fd, "wb") as f:
        f.write(hdr.encode())
        f.flush()
        os.fsync(f.fileno())
    _fsync_dir(p.parent)
    _write_watermark(p, seal_key, tid, 0)
    return code_table(key, p)


def accept_restored_table(key: bytes, path=None) -> dict:
    """The practice knowingly restored an older table (e.g. from backup after
    a disk failure). People coded after that backup will get new codes from
    now on. Record the restored size as the new watermark."""
    p = _resolve_table_path(path or _env_table_path())
    seal_key = hmac.new(key, b"code-table-seal-v1", hashlib.sha256).digest()
    t = CodeTable.__new__(CodeTable)               # open without the watermark check
    import threading
    t.path = p
    t._index_key = hmac.new(key, b"code-table-index-v1", hashlib.sha256).digest()
    t._seal_key = seal_key
    t._lock = _FileLock(p.with_name(p.name + ".lock"))
    t._mutex = threading.RLock()
    t.table_id, t._codes, t._used, t._offset, t._last_mac = None, {}, set(), 0, ""
    fd = t._lock.acquire()
    try:
        t._fresh()
        _write_watermark(p, seal_key, t.table_id, len(t._codes), force=True)
    finally:
        _FileLock.release(fd)
    _tables.clear()
    return t.info()


_tables: dict = {}


def _env_table_path():
    import os
    path = os.getenv("PHI_CODE_TABLE", "").strip()
    if not path:
        raise CodeTableError("pseudonym mode needs a persistent code table "
                             "(PHI_CODE_TABLE is not set)")
    return path


def code_table(key: bytes, path=None) -> CodeTable:
    """The process-wide code table at PHI_CODE_TABLE (or `path`). Refuses when
    no persistent location is configured — same rule as the root key — and
    when the table does not exist: a table is created only by setup."""
    p = _resolve_table_path(path or _env_table_path())
    tag = (str(p), hashlib.sha256(key).hexdigest())
    t = _tables.get(tag)
    if t is None:
        t = CodeTable(p, key)
        _tables[tag] = t
    return t


@dataclass
class Assignment:
    token: str            # "[PERSON-K7Q3F]"
    code: str             # "K7Q3F"
    role: str
    ambiguous: bool       # a lone component shared by several full names
    script: str           # private: for equity measurement only


@dataclass
class PseudonymMap:
    key: bytes
    scope: str
    known_full_names: set = field(default_factory=set)   # confirmed in the patient's other notes
    table: "CodeTable | None" = None                      # random codes; required to assign any

    def resolve(self, text: str, hits: list) -> dict[tuple[int, int], Assignment]:
        persons = [h for h in hits if h.entity_type == "PERSON"]
        norm = {(h.start, h.end): normalize_name(h.text) for h in persons}

        full_names = {n for n in norm.values() if " " in n}
        full_names |= {normalize_name(n) for n in self.known_full_names if " " in n}

        def canonical(n: str) -> tuple[str, bool]:
            if " " in n:
                return n, False
            owners = [f for f in full_names if n in f.split()]
            if len(owners) == 1:
                return owners[0], False
            return n, len(owners) > 1

        canon: dict[tuple[int, int], tuple[str, bool]] = {
            k: canonical(n) for k, n in norm.items()}

        # One role per canonical person: the strongest observed anywhere
        # in the record, so "Dr. Wendermere" and a later bare "Wendermere" agree.
        role_of: dict[str, str] = {}
        for h in persons:
            c, _ = canon[(h.start, h.end)]
            r = self._role(text, h)
            if _ROLE_RANK[r] > _ROLE_RANK.get(role_of.get(c, PERSON), 0):
                role_of[c] = r
            role_of.setdefault(c, r)

        if persons and self.table is None:
            raise CodeTableError("no code table: pseudonym codes are never computed from names")
        codes = self.table.codes_for(self.scope, {canon[(h.start, h.end)][0] for h in persons}) \
            if persons else {}

        out: dict[tuple[int, int], Assignment] = {}
        for h in persons:
            c, amb = canon[(h.start, h.end)]
            code = codes[c]
            role = role_of.get(c, PERSON)
            out[(h.start, h.end)] = Assignment(
                token=f"[{role}-{code}]", code=code, role=role,
                ambiguous=amb, script=script_family(h.text))
        return out

    @staticmethod
    def _role(text: str, h) -> str:
        if getattr(h, "section", "") == "SIGNATURE":
            return PROVIDER
        if _PROVIDER_BEFORE.search(text[max(0, h.start - 30):h.start]):
            return PROVIDER
        ev = getattr(h, "evidence", ()) or ()
        if any(e.startswith("attribution_adjacent") for e in ev):
            return PROVIDER
        if any(e.startswith("relation_adjacent") for e in ev):
            return FAMILY
        return PERSON


def pseudonym_key() -> bytes:
    """Derived from a PERSISTENT root key; refuses an ephemeral one."""
    from inkguard.date_shift import _get_keystore
    return _get_keystore().derive("pseudonym-v1")


def scope_for(patient_key: str | None, text: str, key: bytes) -> str:
    """Per-patient scope when the caller knows the patient; otherwise a
    per-document scope, so unrelated documents never share codes."""
    if patient_key:
        return "p:" + hmac.new(key, patient_key.encode(), hashlib.sha256).hexdigest()[:16]
    return "d:" + hmac.new(key, text.encode(), hashlib.sha256).hexdigest()[:16]
