"""
opal — the app service: the secure core every screen talks to.

The desktop screens (Tauri) never touch files, keys or the engine. They send
small JSON requests over the process's own pipes — no network port exists —
and the service answers. Every safety rule lives HERE, not in the screens, so
a modified or misbehaving screen still cannot get past it:

  - THE PRACTICE: named once, at setup, by the person who sets the app up and
    takes responsibility for it. The name is printed on every review report.
    There is no licence, no account with anyone and no network: the app is
    free and open source (Apache-2.0) and works fully offline.
  - ACCOUNTS: local only. Passwords are slow salted hashes (scrypt); a PIN,
    if set, only unlocks a live session. Wrong guesses pause sign-in for that
    NAME (known or not, so nothing reveals which names exist); pauses decay
    after an hour of quiet and survive a restart. The first admin gets a
    one-time recovery code that signs in through a pause.
  - IDLE LOCK, enforced by the service: after the practice's lock time with
    no real user action — measured on both the steady clock and the wall
    clock, so a sleeping laptop wakes locked — every request except unlock,
    sign out, status and lock is refused. Status polling is not activity.
    Too many wrong unlocks end the session.
  - ROLES: admin, doctor, reviewer — each request checks its role.
  - STORE: one file encrypted with AES-GCM under a key derived from the
    practice's root key; a changed or foreign file is refused (APP-STORE).
  - SECURITY LOG: every sign-in, failure, lock, unlock and account
    change, hash-chained and owner-readable only (no patient data).
  - THE TWO VOUCH FOR EACH OTHER: the store records how long the log was and
    its last link; the log records every save of the store. If either comes
    back older, shorter, replaced or edited, an INTEGRITY ALERT is raised and
    only the practice admin can sign in until they acknowledge it.
  - TAMPER COUNTER: every save also moves a keyed (HMAC) counter kept OUTSIDE the
    app's folder, in the operating system's protected store (Windows
    Credential Manager, macOS Keychain). Copying old files back — records
    and log together — cannot roll it back, so a put-back older copy, a
    deleted counter, or records from another computer raise the alert.
  - RESTORE, ONE OFFICIAL DOOR: only when the records are lost or damaged
    (or on a new computer) — never over sound records. The person restoring
    proves they were an admin in the backup (name, password and, if linked,
    their phone code); they preview, then confirm within five minutes. The
    replaced log is kept as evidence; account bans and guess counters are
    carried forward.
  - PHONE CODES: an account can link an authenticator app (RFC 6238, works
    offline); once linked, sign-in and restore need its 6-digit code.
  - BATCH KEYS AND RETIRING A COMPUTER: each batch's working files are
    encrypted under that batch's own key, kept only in the OS protected
    store, never in a backup. Retiring a computer destroys those keys, the
    separate work key (used only by work_put/work_get) and this computer's
    half of the records key, so what they protected can no longer be read
    (the cryptographic-erase idea described in NIST SP 800-88), and then
    deletes the work and the records. It happens from the computer (admin
    password and phone code) or from the sign-in screen (recovery code and
    phone code).
  - ONE COPY AT A TIME: the service holds an exclusive lock on its folder.

Every error is a topaz support code with plain words. opal is a shell
module: it never imports the engine (the boundary test enforces this).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
import unicodedata
from pathlib import Path

from inkguard import granite, topaz

APP_DATA_NAME = "inkguard"             # the app's data folder on this computer
APP_DISPLAY_NAME = "Inkguard"     # what the phone's authenticator app shows
STORE_MAGIC = b"OPAL1"
IDLE_DEFAULT_MIN, IDLE_MIN, IDLE_MAX = 5, 1, 15
WALL_BACKWARDS_S = 5                   # the wall clock going back more than this locks
UNLOCK_TRIES = 5
THROTTLE_AFTER, THROTTLE_BASE_S, THROTTLE_MAX_S = 5, 30, 900
FAILURE_DECAY_S = 3600
MAX_FAILURE_ENTRIES = 1000
# NIST SP 800-63B-4 (final, Aug 2025): a password used as the single factor
# must be at least 15 characters; at least 64 must be allowed; no composition
# rules; refuse patterned and context words; at most 100 consecutive failures, then disable.
MIN_PASSWORD = 15
MAX_PASSWORD = 256
MAX_CONSECUTIVE_FAILURES = 100
# OWASP Password Storage Cheat Sheet: scrypt minimum N=2^17, r=8, p=1. The
# settings are stored with every hash, and an older hash is upgraded the next
# time its owner signs in.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 17, 8, 1
REVIEW_DAYS_DEFAULT, REVIEW_DAYS_MIN, REVIEW_DAYS_MAX = 14, 7, 30
# What a reviewer may say about a word the tool could not name, and what
# they may call it. Kept here so the screens and the service agree.
REVIEW_ANSWERS = frozenset({"remove", "fine", "unsure"})
REVIEW_KINDS = (("name", "a person's name"), ("date", "a date"), ("place", "a place or address"),
                ("contact", "a phone number, email or similar"), ("id", "a record or ID number"),
                ("other", "something else that identifies someone"))
REVIEW_ADD_TYPES = {"name": "PERSON", "date": "DATE_TIME", "place": "LOCATION",
                    "contact": "PHONE_NUMBER", "id": "UNIQUE_ID", "other": "OTHER_IDENTIFIER",
                    "unsure": "OTHER_IDENTIFIER"}
# What the tool thinks an unsure word might be, in words a person reads
# rather than a type name. Shown beside each one so the reviewer is
# answering a question, not decoding a label.
REVIEW_GUESSES = {
    "PERSON": "a person's name", "DATE_TIME": "a date", "LOCATION": "a place or address",
    "PHONE_NUMBER": "a phone number", "FAX_NUMBER": "a fax number",
    "EMAIL_ADDRESS": "an email address", "URL": "a web address",
    "UNIQUE_ID": "a record or ID number", "MRN": "a record number",
    "US_SSN": "a social security number", "ACCOUNT_NUMBER": "an account number",
    "OTHER_IDENTIFIER": "something that could identify someone",
}
REVIEW_ATTESTATION = ("I read every note in this batch myself and answered every word the tool "
                      "was unsure about. To the best of my knowledge what is blacked out leaves "
                      "nothing that identifies a patient.")
REVIEW_EXTENSION_DAYS = 7              # one per batch, logged
BATCH_BACKSTOP_DAYS = 30               # from the batch's start, whatever stage it is in
REMIND_AT_DAYS = (7, 3, 1)
RESTORE_CONFIRM_S = 300                # a restore preview must be confirmed within 5 minutes
TOTP_STEP_S, TOTP_DIGITS, TOTP_WINDOW = 30, 6, 1   # RFC 6238; one step either side for drift
PHONE_SETUP_S = 600                    # a started phone link must be confirmed within 10 minutes
ROLES = ("admin", "doctor", "reviewer")
MAX_REQUEST_BYTES = 1 << 20
# Requests allowed while the session is locked.
WHEN_LOCKED = frozenset({"unlock", "sign_out", "status", "lock_now", "me",
                         # A break covers the screen the same way the idle
                         # lock does, so coming back from one has to work
                         # while it is covered. It asks for the same password
                         # and refuses once the break has run past its grace.
                         "review_resume"})
# The oldest operating systems this app runs on. Below these, the parts it
# depends on (the OS protected store and the webview it draws the screens in)
# may be absent or behave differently, so it says so plainly and stops,
# rather than failing later in a way nobody can explain. The app opens no
# network connection, so no network library is among those parts. Windows 10
# 22H2 is past its regular end of support, so this floor is due for review.
# Stated here, in the one place, and listed in docs/SECURITY_STANDARDS.md.
MIN_WINDOWS = (10, 0, 19045)        # Windows 10 22H2
MIN_MACOS = (13, 0)                 # macOS 13 Ventura


def os_supported(platform: str | None = None, release=None) -> tuple[bool, str]:
    """(supported, what this computer is), without raising on an OS we have
    never heard of: an unknown system is allowed through and named, because
    refusing to run on something merely unfamiliar would be worse."""
    import platform as _p
    name = platform if platform is not None else sys.platform
    if name.startswith("win"):
        ver = release if release is not None else _p.version()
        parts = tuple(int(x) for x in str(ver).split(".")[:3] if x.isdigit())
        return (parts >= MIN_WINDOWS[:len(parts)] if parts else True), f"Windows {ver}"
    if name == "darwin":
        ver = release if release is not None else (_p.mac_ver()[0] or "")
        parts = tuple(int(x) for x in str(ver).split(".")[:2] if x.isdigit())
        return (parts >= MIN_MACOS[:len(parts)] if parts else True), f"macOS {ver}"
    return True, f"{name} {release if release is not None else _p.release()}"


_APP_WORDS = ("inkguard", "deidentifier", "password", "passphrase")


def _blocked() -> frozenset[str]:
    """Breached-password list. Not shipped: public breach lists carry real
    people's identifiers and crude strings, so the list was removed before the
    first release. A hashed list is planned; until then this returns no
    entries and the length, pattern and context rules below do the work."""
    return frozenset()


def _alnum(s: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKC", s).casefold() if ch.isalnum())


def _alnum_parts(s: str) -> list[str]:
    """The words of a name ("Dr. Sam Rivera" → sam, rivera), for context checks."""
    return [w for w in (_alnum(x) for x in s.replace("-", " ").split()) if len(w) >= 4]


def _patterned(pw: str) -> bool:
    """A repeated unit ("abcabcabc…", "aaaa…") or a straight run of characters
    ("123456789012345", "abcdefghijklmno") — guessable however long."""
    s = pw.casefold()
    for unit in range(1, len(s) // 2 + 1):
        if s == (s[:unit] * (len(s) // unit + 1))[:len(s)]:
            return True
    codes = [ord(c) for c in s]
    steps = {(b - a) for a, b in zip(codes, codes[1:])}
    digits_cycle = s.isdigit() and all((int(b) - int(a)) % 10 in (1, 9) for a, b in zip(s, s[1:])) \
        and len({(int(b) - int(a)) % 10 for a, b in zip(s, s[1:])}) == 1
    return steps in ({1}, {-1}) or digits_cycle


class ServiceError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def app_data_dir() -> Path:
    """The app's own folder for this user: PHI_APP_DATA (a full path) or the
    operating system's app-data location. Never inside a patient folder."""
    env = os.getenv("PHI_APP_DATA", "").strip()
    if env:
        p = Path(env).expanduser()
        if not p.is_absolute():
            raise ServiceError("APP-STORE")
        return p
    home = Path.home()
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / APP_DATA_NAME


def _norm_name(s) -> str:
    return " ".join(unicodedata.normalize("NFKC", s if isinstance(s, str) else "").split()).casefold()


def _scrypt(secret: bytes, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(secret, salt=salt, n=n, r=r, p=p, dklen=32,
                          maxmem=max(64 * 1024 * 1024, 256 * r * n))


def _hash_secret(secret: str, salt: bytes | None = None) -> dict:
    """A slow, salted hash with its settings stored beside it (PHC-style), so
    the work factor can be raised later without locking anyone out."""
    salt = salt or secrets.token_bytes(16)
    dk = _scrypt(secret.encode(), salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return {"alg": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
            "salt": salt.hex(), "hash": dk.hex()}


def _check_secret(secret, rec: dict | None) -> bool:
    secret = secret if isinstance(secret, str) else ""
    if not rec:
        _scrypt(b"x", b"0" * 16, SCRYPT_N, SCRYPT_R, SCRYPT_P)   # equal time when there is nothing to check
        return False
    n, r, p = rec.get("n", 2 ** 15), rec.get("r", 8), rec.get("p", 1)  # pre-0.16.1 hashes used 2^15
    dk = _scrypt(secret.encode(), bytes.fromhex(rec["salt"]), n, r, p)
    return hmac.compare_digest(dk.hex(), rec["hash"])


def _outdated(rec: dict | None) -> bool:
    return bool(rec) and (rec.get("n", 2 ** 15) < SCRYPT_N or rec.get("r", 8) < SCRYPT_R
                          or rec.get("p", 1) < SCRYPT_P)


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class _InstanceLock:
    """Held for the service's whole life: a second copy cannot start."""

    def __init__(self, path: Path):
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        self.fd = os.open(str(path), flags, 0o600)
        try:
            try:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except ImportError:                                   # Windows
                import msvcrt
                os.lseek(self.fd, 0, 0)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            os.close(self.fd)
            self.fd = None
            raise ServiceError("APP-ALREADY-RUNNING") from None

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            try:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except ImportError:
                import msvcrt
                os.lseek(self.fd, 0, 0)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        os.close(self.fd)
        self.fd = None


# ---------------------------------------------------------------------------
# The encrypted store
# ---------------------------------------------------------------------------

class SealedStore:
    """One JSON document, encrypted and authenticated (AES-GCM), written
    atomically. Any change outside the app makes it unreadable — on purpose."""

    AAD = b"opal-store-v1"

    def __init__(self, path: Path, key: bytes):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.path = path
        self._aead = AESGCM(key)

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            raw = self.path.read_bytes()
        except OSError:
            raise ServiceError("APP-STORE") from None
        return self.decode(raw)

    def decode(self, raw: bytes) -> dict:
        try:
            if not raw.startswith(STORE_MAGIC) or len(raw) < len(STORE_MAGIC) + 12 + 16:
                raise ValueError
            nonce = raw[len(STORE_MAGIC):len(STORE_MAGIC) + 12]
            doc = json.loads(self._aead.decrypt(nonce, raw[len(STORE_MAGIC) + 12:], self.AAD))
            if not isinstance(doc, dict) or doc.get("version") != 1:
                raise ValueError
            return doc
        except Exception:  # noqa: BLE001 — tampered, foreign key, truncated: all the same answer
            raise ServiceError("APP-STORE") from None

    def save(self, doc: dict) -> None:
        import tempfile
        self.path.parent.mkdir(parents=True, exist_ok=True)
        nonce = secrets.token_bytes(12)
        blob = STORE_MAGIC + nonce + self._aead.encrypt(
            nonce, json.dumps(doc, sort_keys=True).encode(), self.AAD)
        fd, tmp = tempfile.mkstemp(prefix=".store.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            for attempt in range(50):                 # Windows: a reader may hold it briefly
                try:
                    os.replace(tmp, self.path)
                    break
                except PermissionError:
                    time.sleep(0.05 * (attempt + 1))
            else:
                raise ServiceError("APP-STORE")
            _fsync_dir(self.path.parent)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


# ---------------------------------------------------------------------------
# The security log — hash-chained, owner-readable only, no patient data
# ---------------------------------------------------------------------------

class SecurityLog:
    def __init__(self, path: Path, key: bytes):
        self.path = path
        self._key = key
        self.macs: list[str] = []          # every link, in order
        self.max_gen = 0                    # highest store save recorded
        self.torn_trimmed = False
        self.ok = self._verify()
        if not self.ok:
            # Never overwrite evidence: keep the damaged log, start a new chain.
            stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.path.rename(self.path.with_name(f"{self.path.stem}.damaged-{stamp}{self.path.suffix}"))
            # max_gen keeps the value the scan found: the store must not go backwards.
            self.macs, self.max_gen = [], self.max_gen
            self.append("log_restarted", detail={"reason": "previous log failed verification"})
        elif self.torn_trimmed:
            self.append("torn_line_trimmed", detail={"reason": "an unfinished last line from a crash"})

    @property
    def head(self) -> str:
        return self.macs[-1] if self.macs else ""

    @property
    def count(self) -> int:
        return len(self.macs)

    def _mac(self, prev: str, body: dict) -> str:
        return _log_mac(self._key, prev, body)

    def _verify(self) -> bool:
        if not self.path.exists():
            return True
        try:
            data = self.path.read_bytes()
            if data and not data.endswith(b"\n"):
                # A crash mid-write leaves one unfinished line: trim it, never trust it.
                cut = data.rfind(b"\n") + 1
                with open(self.path, "r+b") as f:
                    f.truncate(cut)
                data = data[:cut]
                self.torn_trimmed = True
        except OSError:
            return False
        ok, self.macs, self.max_gen = _scan_log(data, self._key)
        return ok

    def append(self, event: str, uid: str | None = None, detail: dict | None = None) -> None:
        body = {"ts": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "event": event, "uid": uid, "detail": detail or {}, "prev": self.head}
        m = self._mac(self.head, body)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({**body, "m": m}, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        if event == "store_saved":
            self.max_gen = max(self.max_gen, int(body["detail"].get("gen", 0)))
        self.macs.append(m)

    def last_date(self) -> _dt.date | None:
        """The date of the newest entry (UTC), if any."""
        try:
            last = self.tail(1)
            return _dt.date.fromisoformat(last[0]["ts"][:10]) if last else None
        except (ValueError, KeyError, TypeError, OSError):
            return None

    def tail(self, n: int) -> list[dict]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        return [{k: v for k, v in json.loads(x).items() if k not in ("m", "prev")} for x in lines]


def _log_mac(key: bytes, prev: str, body: dict) -> str:
    return hmac.new(key, (prev + json.dumps(body, sort_keys=True)).encode(), hashlib.sha256).hexdigest()


def _scan_log(data: bytes, key: bytes, *, allow_torn: bool = False) -> tuple[bool, list[str], int]:
    """Check a security log's chain from its bytes, changing nothing.
    Returns (sound, links in order, highest store save recorded)."""
    if allow_torn and data and not data.endswith(b"\n"):
        data = data[:data.rfind(b"\n") + 1]
    macs, max_gen, prev = [], 0, ""
    try:
        for line in data.decode("utf-8").splitlines():
            rec = json.loads(line)
            m = rec.pop("m")
            if rec.get("prev") != prev or not hmac.compare_digest(m, _log_mac(key, prev, rec)):
                return False, macs, max_gen
            if rec.get("event") == "store_saved":
                max_gen = max(max_gen, int(rec.get("detail", {}).get("gen", 0)))
            macs.append(m)
            prev = m
    except Exception:  # noqa: BLE001
        return False, macs, max_gen
    return True, macs, max_gen


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _write_private(path: Path, data: bytes) -> None:
    """Write a whole file atomically, readable by its owner only."""
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".w.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


class AnchorUnavailable(Exception):
    """The operating system's protected store could not be reached."""


class KeychainAnchor:
    """The operating system's protected password store: Windows Credential
    Manager or the macOS Keychain (through the open-source `keyring`
    library, MIT). It lives outside the app's folder and outside any file
    backup of it."""

    def __init__(self, service: str | None = None):
        self.service = service or f"{APP_DATA_NAME}.records-counter"

    def _backend(self):
        try:
            import keyring
            kr = keyring.get_keyring()
        except Exception:  # noqa: BLE001
            raise AnchorUnavailable() from None
        if type(kr).__module__.startswith(("keyring.backends.fail", "keyring.backends.null")):
            raise AnchorUnavailable()
        return kr

    def read(self, account: str) -> str | None:
        kr = self._backend()
        try:
            return kr.get_password(self.service, account)
        except Exception:  # noqa: BLE001
            raise AnchorUnavailable() from None

    def write(self, account: str, value: str) -> None:
        kr = self._backend()
        try:
            kr.set_password(self.service, account, value)
        except Exception:  # noqa: BLE001
            raise AnchorUnavailable() from None

    def delete(self, account: str) -> None:
        kr = self._backend()
        try:
            if kr.get_password(self.service, account) is not None:
                kr.delete_password(self.service, account)
        except Exception:  # noqa: BLE001
            raise AnchorUnavailable() from None


class FolderAnchor:
    """Stand-in for the keychain in tests and development: a folder OUTSIDE
    the app's folder. Not used by the desktop build."""

    def __init__(self, folder):
        self.folder = Path(folder)

    def read(self, account: str) -> str | None:
        p = self.folder / f"{account}.counter"
        try:
            return p.read_text(encoding="ascii") if p.exists() else None
        except (OSError, UnicodeError):
            raise AnchorUnavailable() from None

    def write(self, account: str, value: str) -> None:
        try:
            _write_private(self.folder / f"{account}.counter", value.encode("ascii"))
        except OSError:
            raise AnchorUnavailable() from None

    def delete(self, account: str) -> None:
        try:
            (self.folder / f"{account}.counter").unlink(missing_ok=True)
        except OSError:
            raise AnchorUnavailable() from None


# ---------------------------------------------------------------------------
# Phone codes — RFC 6238 time-based one-time passwords (authenticator apps)
# ---------------------------------------------------------------------------

def _totp_at(secret: bytes, step: int) -> str:
    digest = hmac.new(secret, step.to_bytes(8, "big"), hashlib.sha1).digest()   # RFC 6238 default
    off = digest[-1] & 0x0F
    value = int.from_bytes(digest[off:off + 4], "big") & 0x7FFFFFFF
    return str(value % 10 ** TOTP_DIGITS).zfill(TOTP_DIGITS)


def _totp_match(secret: bytes, code, now: float, last_step: int) -> int | None:
    """The time step the code belongs to, or None. A step already used
    (or older) never counts again, so an overheard code cannot be replayed."""
    if not isinstance(code, str):
        return None
    code = code.replace(" ", "").strip()
    if len(code) != TOTP_DIGITS or not code.isdigit():
        return None
    now_step = int(now // TOTP_STEP_S)
    for step in range(now_step - TOTP_WINDOW, now_step + TOTP_WINDOW + 1):
        if step > last_step and hmac.compare_digest(_totp_at(secret, step), code):
            return step
    return None


def _b32(secret: bytes) -> str:
    import base64
    return base64.b32encode(secret).decode("ascii").rstrip("=")


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

class _Session:
    __slots__ = ("token", "uid", "mono", "wall", "locked", "tries", "pool_open")

    def __init__(self, uid: str, mono: float, wall: float):
        self.token = secrets.token_urlsafe(32)
        self.uid, self.mono, self.wall, self.locked, self.tries = uid, mono, wall, False, 0
        # True once this person has had patient work open in this session:
        # getting back in then asks for their phone code as well.
        self.pool_open = False


class Service:
    """One instance per running app. `handle(request) -> response`. Call
    `close()` (or use `with`) to release the folder for another instance."""

    def __init__(self, data_dir: Path | None = None, *, keystore=None, anchor=None,
                 clock=time.monotonic, wall=time.time, today=None):
        from inkguard.security import KeyStore
        ks = keystore or KeyStore()
        if getattr(ks, "ephemeral", False):
            raise ServiceError("APP-STORE")           # no persistent root key: nothing could be kept
        self.dir = Path(data_dir) if data_dir else app_data_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = _InstanceLock(self.dir / ".service.lock")
        try:
            self._store_base = ks.derive("app-store-v2")
            self._log_key = ks.derive("app-audit-v1")
            self._backup_key = ks.derive("app-backup-v1")
            self._fail_key = ks.derive("app-fail-names-v1")
            self._anchor_key = ks.derive("app-anchor-v1")
            # The OS protected store holds the tamper counter, the work key and
            # this computer's half of the records key.
            self._anchor = anchor if anchor is not None else KeychainAnchor()
            self._anchor_acct = hmac.new(self._anchor_key,
                                         os.path.normcase(str(self.dir.resolve())).encode(),
                                         hashlib.sha256).hexdigest()[:32]
            store_path = self.dir / "app_store.bin"
            share, damaged, existing = self._records_share(create=False), None, None
            if share is None and store_path.exists():
                # Records whose key half is not on this computer: retired here,
                # or copied from another computer. Never opened; set aside.
                damaged = self._set_aside_damaged_store(store_path, "could not be opened on this "
                                                        "computer (retired here, or copied from "
                                                        "another computer)")
            if share is None:
                share = self._records_share(create=True)
            self.store = SealedStore(store_path, hmac.new(self._store_base, share, hashlib.sha256).digest())
            if damaged is None:
                try:
                    existing = self.store.load()
                except ServiceError:
                    damaged = self._set_aside_damaged_store(store_path, "could not be read")
            self.log = SecurityLog(self.dir / "security_log.jsonl", self._log_key)
        except BaseException:
            self._lock.release()
            raise
        self.sessions: dict[str, _Session] = {}
        self._clock, self._wall = clock, wall
        self._today = today or (lambda: _dt.date.today())
        self._mutex = threading.RLock()
        self._fail_mono: dict[str, float] = {}        # pause end, steady clock (this run)
        self._fail_last_mono: dict[str, float] = {}   # last failure, steady clock (this run)
        self._pending_restore: dict | None = None
        self._closing = False
        self._worker: threading.Thread | None = None   # the batch being prepared, if any
        self._working_on: str | None = None
        self._export_thread: threading.Thread | None = None
        self._export_running: str | None = None        # the batch being exported, if any
        self._stop_work = threading.Event()
        self._pool_keys: dict[str, bytes] = {}         # batch keys held only while someone is here
        self._pool_paused: str | None = None           # why preparation is waiting, if it is
        self._review_opened: dict = {}                 # when each note was opened, for the record
        self._review_elapsed: dict = {}                # time put by when a screen locked
        self._retired = False
        self._phone_setup: dict[str, dict] = {}       # uid -> started link, this run only
        self._anchor_state, self._anchor_gen = "missing", None
        seen = self._anchor_read()
        self.doc = existing or self._fresh_doc()
        try:
            if existing is not None:
                self._check_integrity(seen)
                self._recover_exports()                # an export the app did not live through
                self._expire_batches()                 # at every start, before anyone signs in
            else:
                self._check_lost(seen, damaged)
        except BaseException:
            self._lock.release()
            raise

    @staticmethod
    def _fresh_doc() -> dict:
        return {"version": 1, "practice": None,
                "settings": {"idle_minutes": IDLE_DEFAULT_MIN,
                             "review_days": REVIEW_DAYS_DEFAULT},
                "accounts": {}, "failures": {}, "store_gen": 0,
                "log_count": 0, "log_head": "", "integrity_alert": None,
                "install": secrets.token_hex(8), "anchored": False, "batches": {}}

    def _records_share(self, create: bool) -> bytes | None:
        """This computer's half of the records key, kept only in the OS
        protected store. Retiring the computer destroys it, so a records file
        copied back afterwards — or copied to another computer — cannot be
        opened. An unreachable protected store stops the app (APP-STORE)."""
        acct = self._anchor_acct + "-records"
        try:
            raw = self._anchor.read(acct)
            if raw is None and create:
                raw = secrets.token_hex(32)
                self._anchor.write(acct, raw)
        except AnchorUnavailable:
            raise ServiceError("APP-STORE") from None
        try:
            return bytes.fromhex(raw) if raw else None
        except ValueError:
            return None

    def _set_aside_damaged_store(self, path: Path, why: str) -> str:
        """The records cannot be opened. If the security log still verifies
        under this root key, the key is right and the records file itself is
        damaged: keep it as evidence (never delete) and start in the
        needs-restore state. Otherwise the key may be wrong — change nothing."""
        try:
            data = (self.dir / "security_log.jsonl").read_bytes()
        except OSError:
            data = b""
        ok, macs, _ = _scan_log(data, self._log_key, allow_torn=True)
        if not (ok and macs):
            raise ServiceError("APP-STORE")
        target = path.with_name(f"app_store.damaged-{_stamp()}.bin")
        os.replace(path, target)
        self._set_aside_why = why
        return target.name

    # -- the tamper counter ----------------------------------------------------
    # The counter's fourth field is reserved and always written as "-".
    def _anchor_value(self, gen: int, last_seen: str | None, install: str,
                      spare: str | None = None) -> str:
        body = f"{gen}.{last_seen or '-'}.{install}.{spare or '-'}"
        return body + "." + hmac.new(self._anchor_key, body.encode(), hashlib.sha256).hexdigest()[:32]

    def _anchor_read(self) -> dict | None:
        """The keyed counter, or None. Sets the state: ok, missing,
        unavailable (the protected store could not be reached) or edited."""
        try:
            raw = self._anchor.read(self._anchor_acct)
        except AnchorUnavailable:
            self._anchor_state = "unavailable"
            return None
        if raw is None:
            self._anchor_state = "missing"
            return None
        try:
            gen, last, install, spare, mac = raw.strip().split(".")
            if not hmac.compare_digest(self._anchor_value(int(gen), None if last == "-" else last,
                                                          install, None if spare == "-" else spare),
                                       raw.strip()):
                raise ValueError
            if last != "-":
                _dt.date.fromisoformat(last)
        except (ValueError, TypeError):
            self._anchor_state = "edited"
            return None
        self._anchor_state, self._anchor_gen = "ok", int(gen)
        return {"gen": int(gen), "last_seen": None if last == "-" else last, "install": install}

    def _anchor_write(self, gen: int) -> None:
        """Forward only. A failure is logged once and shown to the admin;
        the next start raises the alert if the store is still unreachable."""
        if self._anchor_state == "unavailable":
            return
        if self._anchor_gen is not None and gen <= self._anchor_gen:
            return
        try:
            self._anchor.write(self._anchor_acct,
                               self._anchor_value(gen, self.doc.get("last_seen"), self.doc["install"]))
        except AnchorUnavailable:
            self._anchor_state = "unavailable"
            self.log.append("tamper_counter_unavailable")
            return
        self._anchor_gen, self._anchor_state = gen, "ok"

    def _check_lost(self, seen: dict | None, damaged: str | None) -> None:
        """No readable records. A brand-new install is fine; but if the log or
        this computer's tamper counter shows the app was used here, the records
        were lost — only a restore helps."""
        if damaged:
            why = getattr(self, "_set_aside_why", "could not be read")
            reasons = [f"the app's records {why} and were set aside as {damaged}"]
        elif self.log.max_gen > 0:
            reasons = ["the app's records are missing although the security log shows earlier use"]
        elif seen is not None or self._anchor_state == "edited":
            reasons = ["the app's records are missing although this computer's tamper counter "
                       "shows earlier use"]
        else:
            return
        on = self._today()
        last_ts = self.log.last_date()
        if last_ts:
            on = max(on, last_ts)                      # the clock cannot be set back past the log
        self.doc["records_lost"] = {"on": on.isoformat(), "reasons": reasons}
        self.doc["integrity_alert"] = {"reasons": reasons, "seen": _now_iso()}
        self.doc["store_gen"] = max(self.log.max_gen, seen["gen"] if seen else 0)
        self.log.append("integrity_alert", detail={"reasons": reasons})
        self._save()

    def close(self) -> None:
        """Stop any batch being prepared and let go of the folder — in that
        order. The folder lock is what stops a second copy of the app from
        writing the same records, so it is never released while the worker
        could still write."""
        self._stop_work.set()
        self._closing = True
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=300)                  # it stops between files
            if worker.is_alive():
                # Never release the folder to a second copy while this one is
                # still writing: the records and the log would be interleaved.
                self.log.append("batch_worker_would_not_stop")
                return
        self._lock.release()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- integrity: the store and the log vouch for each other -------------------
    def _check_integrity(self, seen: dict | None = None) -> None:
        reasons = []
        gen = self.doc.get("store_gen", 0)
        if self._anchor_state == "unavailable":
            reasons.append("this computer's protected tamper counter could not be reached, so an "
                           "older copy put back by hand cannot be ruled out")
        elif self._anchor_state == "edited":
            reasons.append("this computer's tamper counter for the app was edited")
        elif seen is None and self.doc.get("anchored"):
            reasons.append("this computer's tamper counter for the app is missing (the records were "
                           "copied from another computer, or the counter was deleted)")
        elif seen is not None and seen["install"] != self.doc["install"]:
            reasons.append("this computer's tamper counter belongs to other records (the records "
                           "were replaced)")
        elif seen is not None and seen["gen"] > gen:
            reasons.append("the app's records are older than this computer has seen (an older copy "
                           "was put back outside the app's restore)")
        n, head = self.doc.get("log_count", 0), self.doc.get("log_head", "")
        if not self.log.ok:
            reasons.append("the security log was edited")
        elif n and self.log.count < n:
            reasons.append("the security log is shorter than the app's records expect "
                           "(deleted or cut short)")
        elif n and self.log.macs[n - 1] != head:
            reasons.append("the security log was replaced")
        if self.log.ok and self.log.max_gen > self.doc.get("store_gen", 0):
            reasons.append("the app's records are older than the security log "
                           "(restored from an older copy)")
        if reasons:
            alert = {"reasons": reasons,
                     "seen": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            self.doc["integrity_alert"] = alert
            # Carry on from here: the alert is raised once and stays until the
            # admin acknowledges it, rather than firing again at every start.
            # Never below what this computer has seen.
            self.doc["store_gen"] = max(gen, self.log.max_gen, seen["gen"] if seen else 0)
            self.log.append("integrity_alert", detail={"reasons": reasons})
            self._save()

    # -- plumbing ---------------------------------------------------------------
    def _save(self) -> None:
        # Store first (recording the log as it stands), then the log records the
        # save, then the tamper counter moves: a crash between any two can
        # never look like a rollback.
        gen = self.doc.get("store_gen", 0) + 1
        self.doc["store_gen"] = gen
        self.doc["log_count"], self.doc["log_head"] = self.log.count, self.log.head
        self.store.save(self.doc)
        self.log.append("store_saved", detail={"gen": gen})
        self._anchor_write(gen)
        # "anchored" is recorded only once the counter exists (saved with the
        # next save), so a crash in between can never look like a deletion.
        if self._anchor_state == "ok" and not self.doc.get("anchored"):
            self.doc["anchored"] = True

    def _log(self, event: str, uid: str | None = None, detail: dict | None = None) -> None:
        """Record a security event and save, so the records vouch for it: a
        later cut of the log that removes it raises the integrity alert."""
        self.log.append(event, uid, detail)
        self._save()

    def _review_people(self) -> int:
        """Two people (two reviewers, one of whom may be the clinician who
        signs off) or three (two reviewers and a separate clinician)."""
        return 3 if self.doc["settings"].get("review_people") == 3 else 2

    def _practice(self) -> str:
        """The practice's name, as entered at setup."""
        return (self.doc.get("practice") or {}).get("name") or ""

    def _account(self, uid: str) -> dict:
        acc = self.doc["accounts"].get(uid)
        if not acc or acc.get("disabled"):
            raise ServiceError("SESSION-ENDED")
        return acc

    def _idle_expired(self, s: _Session) -> bool:
        idle = 60 * self.doc["settings"]["idle_minutes"]
        now_wall = self._wall()
        return (self._clock() - s.mono > idle or now_wall - s.wall > idle
                or now_wall < s.wall - WALL_BACKWARDS_S)

    def _session(self, token, op: str) -> _Session:
        s = self.sessions.get(token) if isinstance(token, str) else None
        if s is None:
            raise ServiceError("SESSION-ENDED")
        acc = self._account(s.uid)
        if not s.locked and self._idle_expired(s):
            s.locked = True
            self._close_pools("the screen locked", s.uid)
            self._log("auto_lock", s.uid)
        if s.locked and op not in WHEN_LOCKED:
            raise ServiceError("SESSION-LOCKED")
        if self.doc.get("integrity_alert") and "admin" not in acc["roles"] \
                and op not in WHEN_LOCKED:
            raise ServiceError("APP-INTEGRITY")
        return s

    def _reauth(self, s: _Session, acc: dict, secret) -> None:
        """Re-entering the password inside a session (to set a PIN, change the
        password, replace the recovery code) counts toward the same guess
        limits as signing in, and every miss is logged."""
        fid = self._fail_id(_norm_name(acc["name"]))
        self._fail_check(fid)
        if not _check_secret(secret, acc["pw"]):
            raise ServiceError(self._fail_record(fid, s.uid, "reauth_failed"))
        self._fail_clear(fid)

    def _phone_check(self, acc: dict, uid: str | None, fid: str, code, cap: bool = True) -> int | None:
        """Second factor: once a phone is linked, its current 6-digit code is
        needed too. A missing code only asks for it; a wrong or reused code
        counts as a failed guess on the same limits as the password."""
        link = acc.get("totp")
        if not link:
            return None
        if code in (None, ""):
            raise ServiceError("PHONE-CODE-NEEDED")
        step = _totp_match(bytes.fromhex(link["secret"]), code, self._wall(), link.get("last_step", -1))
        if step is None:
            raise ServiceError(self._fail_record(fid, uid, "phone_code_failed", cap=cap))
        link["last_step"] = step
        if uid is not None and acc is self.doc["accounts"].get(uid):
            self._save()                               # a used code is spent, even if a later step fails
        return step

    def _active(self, s: _Session) -> None:
        s.mono, s.wall = self._clock(), self._wall()

    def _need_role(self, s: _Session, *roles: str) -> dict:
        acc = self._account(s.uid)
        if not set(roles) & set(acc["roles"]):
            self._log("role_denied", s.uid, {"needs": sorted(roles)})
            raise ServiceError("ROLE-DENIED")
        return acc

    def _password_ok(self, pw, name: str = "") -> None:
        """NIST SP 800-63B-4: length 15–256, no composition rules, and not on
        the blocklist: context words (the person's name, the practice's name,
        the app's name) and repetitive or sequential strings. A breached-
        password list is planned, not shipped (see _blocked)."""
        if not isinstance(pw, str) or not MIN_PASSWORD <= len(pw) <= MAX_PASSWORD:
            raise ServiceError("PASSWORD-WEAK")
        folded = pw.casefold()
        if folded in _blocked() or _patterned(pw):
            raise ServiceError("PASSWORD-WEAK")
        flat = _alnum(pw)
        context = list(_APP_WORDS) + [_alnum(APP_DATA_NAME), _alnum(name)] + _alnum_parts(name)
        org = self._practice()
        if org:
            context += [_alnum(org)] + _alnum_parts(org)
        if any(len(w) >= 4 and w in flat for w in context) or len(set(folded)) <= 3:
            raise ServiceError("PASSWORD-WEAK")

    def _recovery_secret(self, given, name: str, password) -> str | None:
        """The admin's recovery secret: a passphrase they chose (checked like
        any password, and never the same as their password), or, if they
        chose none, a generated 96-bit code. Returns the generated code so it
        can be shown once; None when they chose their own."""
        if isinstance(given, str) and given.strip():
            if given.strip() == (password or "").strip():
                raise ServiceError("PASSWORD-WEAK")
            self._password_ok(given.strip(), name)
            return None
        return "-".join(secrets.token_hex(3).upper() for _ in range(4))

    def _public(self, uid: str, acc: dict) -> dict:
        from inkguard import pyrite
        badge = pyrite.badge_state(acc.get("badge"), today=self._today())
        return {"id": uid, "name": acc["name"], "roles": list(acc["roles"]),
                # "certified" here only means the practice badge is current
                # TODAY, worked out afresh rather than a flag set once and
                # never looked at again: a badge that has run out does not count.
                "certified": badge["certified"], "disabled": bool(acc.get("disabled")),
                "badge": badge,
                "has_pin": bool(acc.get("pin")), "has_phone": bool(acc.get("totp")),
                "password_disabled": bool(acc.get("pw_disabled"))}

    def _active_count(self) -> int:
        return sum(1 for a in self.doc["accounts"].values() if not a.get("disabled"))

    def _fail_id(self, norm: str) -> str:
        return hmac.new(self._fail_key, norm.encode(), hashlib.sha256).hexdigest()[:32]

    # -- the request door -------------------------------------------------------
    def handle(self, req) -> dict:
        rid = req.get("id") if isinstance(req, dict) else None
        if not (rid is None or isinstance(rid, str) or (isinstance(rid, int) and not isinstance(rid, bool))):
            rid = None
        try:
            if not isinstance(req, dict) or not isinstance(req.get("op"), str) \
                    or not isinstance(req.get("args", {}), (dict, type(None))):
                raise ServiceError("APP-BAD-REQUEST")
            fn = _OPS.get(req["op"])
            if fn is None:
                raise ServiceError("APP-BAD-REQUEST")
            if self._retired and req["op"] != "status":
                raise ServiceError("DEVICE-RETIRED")
            with self._mutex:
                result = fn(self, req.get("args") or {}, req.get("session"))
            return {"id": rid, "ok": True, "result": result}
        except ServiceError as e:
            return {"id": rid, "ok": False, "error": {"code": e.code, "message": topaz.message(e.code)}}
        except Exception:  # noqa: BLE001 — never a traceback to the screen
            return {"id": rid, "ok": False,
                    "error": {"code": "APP-BAD-REQUEST", "message": topaz.message("APP-BAD-REQUEST")}}

    # -- operations -------------------------------------------------------------
    def op_status(self, a, token):
        """State for the first screen. Never counted as user activity. Before
        sign-in it shows only what a sign-in screen needs."""
        if self._retired:
            return {"state": "retired"}
        fine, what = os_supported()
        if not fine:
            # Said on the first screen, before any account: there
            # is no point setting up an app that cannot run here.
            return {"state": "unsupported_os", "os": what, "code": "APP-OS-UNSUPPORTED",
                    "needs": {"windows": "Windows 10 22H2 or newer",
                              "macos": "macOS 13 Ventura or newer"}}
        self._expire_batches()                         # work out of time goes, signed in or not
        state = ("needs_restore" if self.doc.get("records_lost") and not self.doc["accounts"] else
                 "needs_setup" if not self.doc["accounts"] else "ready")
        s = self.sessions.get(token) if isinstance(token, str) else None
        out = {"state": state,
               "can_restore": not self.doc["accounts"],
               # With no accounts there is no admin to show it to: the first
               # screen explains plainly why a restore is needed.
               "records_lost_reasons": (list(self.doc["records_lost"]["reasons"])
                                        if state == "needs_restore" else None),
               "org": self._practice() or None,
               "integrity_alert": bool(self.doc.get("integrity_alert")),
               "idle_minutes": self.doc["settings"]["idle_minutes"],
               # The tidy-up tool needs no account, so the first screen can
               # offer it before any of this is set up.
               "tidy_available": True,
               "review_days": self.doc["settings"].get("review_days", REVIEW_DAYS_DEFAULT),
               "review_people": self._review_people(),
               "prepare_while_locked": bool(self.doc["settings"].get("prepare_while_locked", False)),
               "pages_per_part": self.doc["settings"].get("pages_per_part", granite.PAGES_PER_SITTING),
               "session_locked": (s.locked or self._idle_expired(s)) if s else None}
        acc = self.doc["accounts"].get(s.uid) if s else None
        if acc and "admin" in acc["roles"] and not (s.locked or self._idle_expired(s)):
            out["integrity_detail"] = self.doc.get("integrity_alert")
            out["security_log_ok"] = self.log.ok
            out["tamper_counter"] = self._anchor_state
            out["admins_without_phone"] = sorted(
                x["name"] for x in self.doc["accounts"].values()
                if "admin" in x["roles"] and not x.get("disabled") and not x.get("totp"))
        return out

    def op_setup_first_admin(self, a, token):
        if self.doc["accounts"]:
            raise ServiceError("ROLE-DENIED")
        lost = self.doc.get("records_lost")
        if a.get("attest_responsible") is not True:
            # The first admin attests they are responsible for how the app is
            # used at this practice; it is printed on every review report.
            raise ServiceError("ROLE-DENIED")
        name = a.get("name").strip() if isinstance(a.get("name"), str) else ""
        practice = a.get("practice").strip() if isinstance(a.get("practice"), str) else ""
        if not name or len(name) > 100 or not practice or len(practice) > 150:
            raise ServiceError("APP-BAD-REQUEST")
        self._password_ok(a.get("password"), name)
        recovery = self._recovery_secret(a.get("recovery"), name, a.get("password"))
        secret = recovery or a["recovery"].strip()
        uid = "U" + secrets.token_hex(4)
        # No badge by assertion, not even for the person who set the app up:
        # the practice batch is how a badge is earned, and they review under
        # the same rule as anyone they authorize. Signing a batch off is a
        # different job and needs no badge.
        self.doc["accounts"][uid] = {"name": name, "roles": ["admin", "doctor"], "certified": False,
                                     "pw": _hash_secret(a["password"]), "pin": None,
                                     "recovery": _hash_secret(secret), "responsible": True}
        self.doc["practice"] = {"name": practice, "since": _now_iso()}
        detail = {"practice": practice}
        if lost:
            detail["started_over_after_loss"] = {"lost_on": lost["on"]}
            self.doc.pop("records_lost", None)
        self.log.append("first_admin_created", uid, detail)
        self._save()
        # Shown ONCE, to be printed and kept with the practice's records.
        return {"id": uid, "recovery_code": recovery}

    # -- guessing limits (per name, known or not, so nothing reveals which exist)
    def _fail_check(self, fid: str, cap: bool = True) -> None:
        """Refuse before checking any secret if this name is paused (rate
        limit) or has reached NIST's cap of consecutive failures."""
        now = self._wall()
        fail = self.doc["failures"].get(fid)
        if not fail:
            return
        if cap and fail.get("consec", 0) >= MAX_CONSECUTIVE_FAILURES:
            raise ServiceError("AUTH-DISABLED")
        quiet_mono = self._clock() - self._fail_last_mono.get(fid, float("-inf"))
        if now - fail.get("last", 0) > FAILURE_DECAY_S and quiet_mono > FAILURE_DECAY_S:
            # Quiet for an hour by BOTH clocks (while running): the pause fades,
            # but the consecutive count toward the cap does not.
            fail["count"], fail["until"] = 0, 0
            return
        if now < fail.get("until", 0) or self._clock() < self._fail_mono.get(fid, 0):
            raise ServiceError("AUTH-THROTTLED")

    def _fail_record(self, fid: str, uid: str | None, event: str, cap: bool = True) -> str:
        now = self._wall()
        fail = self.doc["failures"].get(fid) or {}
        n, consec = fail.get("count", 0) + 1, fail.get("consec", 0) + 1
        wait = min(THROTTLE_MAX_S, THROTTLE_BASE_S * 2 ** (n - THROTTLE_AFTER)) if n >= THROTTLE_AFTER else 0
        self.doc["failures"][fid] = {"count": n, "consec": consec, "until": now + wait, "last": now}
        self._fail_last_mono[fid] = self._clock()
        if wait:
            self._fail_mono[fid] = self._clock() + wait
        if len(self.doc["failures"]) > MAX_FAILURE_ENTRIES:
            # Never evict a real account's counter or a live pause: a flood of
            # made-up names must not reset anyone's count.
            protected = {self._fail_id(_norm_name(x["name"])) for x in self.doc["accounts"].values()}
            protected |= {self._fail_id("recovery\x1f" + _norm_name(x["name"]))
                          for x in self.doc["accounts"].values()}
            evictable = sorted((k for k, v in self.doc["failures"].items()
                                if k not in protected and v.get("until", 0) <= now),
                               key=lambda k: self.doc["failures"][k].get("last", 0))
            for k in evictable[:len(self.doc["failures"]) - MAX_FAILURE_ENTRIES]:
                self.doc["failures"].pop(k, None)
        self.log.append(event, uid)
        code = "AUTH-FAILED"
        if cap and consec >= MAX_CONSECUTIVE_FAILURES:
            acc = self.doc["accounts"].get(uid) if uid else None
            if acc and not acc.get("pw_disabled"):
                acc["pw_disabled"] = True                 # NIST: disable after 100 in a row
                self.log.append("password_disabled_after_failures", uid)
            code = "AUTH-DISABLED"
        self._save()
        return code

    def _fail_clear(self, fid: str) -> None:
        self.doc["failures"].pop(fid, None)
        self._fail_mono.pop(fid, None)
        self._fail_last_mono.pop(fid, None)

    def op_sign_in(self, a, token):
        norm = _norm_name(a.get("name"))
        fid = self._fail_id(norm)
        uid = next((u for u, acc in self.doc["accounts"].items() if _norm_name(acc["name"]) == norm), None)
        acc = self.doc["accounts"].get(uid) if uid else None
        self._fail_check(fid)
        ok = _check_secret(a.get("password"), acc["pw"] if acc else None)
        if not ok or acc.get("disabled"):
            raise ServiceError(self._fail_record(fid, uid, "sign_in_failed"))
        if acc.get("pw_disabled"):
            raise ServiceError("AUTH-DISABLED")         # only an admin reset (or recovery) re-enables it
        self._phone_check(acc, uid, fid, a.get("code"))
        if self.doc.get("integrity_alert") and "admin" not in acc["roles"]:
            raise ServiceError("APP-INTEGRITY")
        self._fail_clear(fid)
        if _outdated(acc["pw"]):
            acc["pw"] = _hash_secret(a["password"])     # upgrade the work factor (OWASP)
            self.log.append("password_hash_upgraded", uid)
        s = _Session(uid, self._clock(), self._wall())
        self.sessions[s.token] = s
        oversight = self._since_last_sign_in(uid) if {"admin", "doctor"} & set(acc["roles"]) else None
        self.log.append("signed_in", uid)
        self._save()
        out = {"session": s.token, "user": self._public(uid, acc),
               "idle_minutes": self.doc["settings"]["idle_minutes"]}
        if oversight is not None:
            out["since_last_sign_in"] = oversight
        return out

    # What the practice's doctor/admin is told at sign-in: counts only, from
    # the security log — never names of patients, files or passwords.
    _OVERSIGHT = {
        "sign_in_failed": "failed sign-ins", "reauth_failed": "failed sign-ins",
        "phone_code_failed": "failed sign-ins", "recovery_failed": "failed recovery-code uses",
        "restore_sign_in_failed": "failed restore attempts",
        "password_disabled_after_failures": "passwords disabled after repeated failures",
        "integrity_alert": "integrity alerts", "role_denied": "actions refused for lack of role",
        "account_created": "accounts created", "account_disabled": "accounts disabled",
        "password_reset": "passwords reset by an admin", "admin_recovered_with_code": "recovery-code uses",
        "phone_linked": "phones linked", "phone_link_removed": "phone links removed",
        "backup_created": "backups made", "backup_verified": "backups checked",
        "backup_restored": "restores from backup", "code_table_restored": "code tables restored",
        "settings_changed": "settings changed", "tamper_counter_unavailable": "tamper-counter problems",
    }

    def _since_last_sign_in(self, uid: str) -> dict:
        try:
            lines = self.log.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {"since": None, "counts": {}}
        start, since = 0, None
        for i in range(len(lines) - 1, -1, -1):
            try:
                rec = json.loads(lines[i])
            except ValueError:
                continue
            if rec.get("event") == "signed_in" and rec.get("uid") == uid:
                start, since = i + 1, rec.get("ts")
                break
        counts: dict[str, int] = {}
        for line in lines[start:]:
            try:
                label = self._OVERSIGHT.get(json.loads(line).get("event"))
            except ValueError:
                continue
            if label:
                counts[label] = counts.get(label, 0) + 1
        return {"since": since, "counts": counts}

    def op_recover_admin(self, a, token):
        """Emergency access (the idea described in HIPAA 164.312(a)(2)(ii);
        this is not a compliance claim): an admin who is locked
        out — paused by someone's guessing, or with the password disabled
        after too many misses, or simply forgotten — uses the printed recovery
        code to set a new password. The code is replaced on use. Rate-limited
        like sign-in, on its own counter."""
        norm = _norm_name(a.get("name"))
        fid = self._fail_id("recovery\x1f" + norm)
        uid = next((u for u, acc in self.doc["accounts"].items() if _norm_name(acc["name"]) == norm), None)
        acc = self.doc["accounts"].get(uid) if uid else None
        # A 96-bit recovery code is rate-limited but never disabled: emergency
        # access must not be lockable by someone guessing.
        self._fail_check(fid, cap=False)
        given = a.get("recovery_code")
        given = given.strip() if isinstance(given, str) else ""
        rec = acc.get("recovery") if acc and "admin" in acc["roles"] and not acc.get("disabled") else None
        # A chosen passphrase is matched exactly; a generated code in any case.
        if not (_check_secret(given, rec) or _check_secret(given.upper(), rec)):
            raise ServiceError(self._fail_record(fid, uid, "recovery_failed", cap=False))
        self._password_ok(a.get("new_password"), acc["name"])
        new_code = self._recovery_secret(a.get("new_recovery"), acc["name"], a.get("new_password"))
        acc["pw"], acc["pin"] = _hash_secret(a["new_password"]), None
        acc["recovery"] = _hash_secret(new_code or a["new_recovery"].strip())
        acc.pop("pw_disabled", None)
        if acc.pop("totp", None):
            # The printed code is the way back from a lost phone: the old link
            # goes, and the admin links the new phone after signing in.
            self.log.append("phone_link_removed", uid, {"by": "recovery code"})
        self._fail_clear(fid)
        self._fail_clear(self._fail_id(norm))
        for t in [t for t, x in self.sessions.items() if x.uid == uid]:
            self.sessions.pop(t)
        self.log.append("admin_recovered_with_code", uid)
        self._save()
        return {"recovery_code": new_code}

    def op_me(self, a, token):
        """The signed-in person, for the screens: name, roles, PIN and phone
        status. Not counted as activity."""
        s = self._session(token, "me")
        return self._public(s.uid, self._account(s.uid))

    def op_sign_out(self, a, token):
        s = self.sessions.pop(token, None) if isinstance(token, str) else None
        if s:
            self._log("signed_out", s.uid)
        return {}

    def _close_pools(self, why: str, uid: str | None = None) -> None:
        """Close every pool: the app lets go of the batch keys it is holding,
        so nothing of the patients' can be read until someone signs back in.
        Work being prepared pauses here too, unless the practice has chosen
        to let it carry on while the screen is locked."""
        self._pool_keys.clear()
        # Time away is not time spent reading, but time already spent is not
        # thrown away: what each open note has taken so far is put by, and
        # the clock starts again when it is opened after signing back in.
        # Only this person's notes stop — another reviewer may still be at work.
        for key in [k for k in self._review_opened if uid is None or k[0] == uid]:
            started = self._review_opened.pop(key)
            self._review_elapsed[key] = self._review_elapsed.get(key, 0.0) + \
                max(0.0, min(3600.0, self._clock() - started))
        if not self.doc["settings"].get("prepare_while_locked", False):
            self._pool_paused = why

    def op_lock_now(self, a, token):
        s = self._session(token, "lock_now")
        if not s.locked:
            s.locked = True
            self._close_pools("the screen was locked", s.uid)
            self._log("locked", s.uid)
        return {}

    def op_touch(self, a, token):
        """The screen reports a real user action (a click or key), at most
        every few seconds. Only this — never status polling — keeps a session
        from locking; it cannot reopen a locked session."""
        s = self._session(token, "touch")
        self._active(s)
        return {}

    def op_unlock(self, a, token):
        s = self._session(token, "unlock")
        if not s.locked:
            return {}
        acc = self._account(s.uid)
        pin, pw = a.get("pin"), a.get("password")
        ok = (_check_secret(pin, acc.get("pin")) if isinstance(pin, str) and acc.get("pin")
              else _check_secret(pw, acc["pw"]))
        # Patient work was open on this screen, so getting back to it asks for
        # the code on this person's own phone as well, if they linked one.
        if ok and s.pool_open and acc.get("totp"):
            self._phone_check(acc, s.uid, self._fail_id(_norm_name(acc["name"])), a.get("code"))
        if not ok:
            s.tries += 1
            self._log("unlock_failed", s.uid)
            if s.tries >= UNLOCK_TRIES:
                self.sessions.pop(s.token, None)
                self._log("session_ended_after_failed_unlocks", s.uid)
                raise ServiceError("SESSION-ENDED")
            raise ServiceError("AUTH-FAILED")
        s.locked, s.tries = False, 0
        s.pool_open = False
        self._pool_paused = None                       # work waiting on the lock carries on
        if isinstance(pin, str) and acc.get("pin") and _outdated(acc["pin"]):
            acc["pin"] = _hash_secret(pin)               # upgrade the work factor (OWASP)
        self._active(s)
        self._log("unlocked", s.uid)
        return {}

    def op_set_pin(self, a, token):
        s = self._session(token, "set_pin")
        acc = self._account(s.uid)
        self._reauth(s, acc, a.get("password"))
        pin = a.get("pin") if isinstance(a.get("pin"), str) else ""
        if not (pin.isascii() and pin.isdigit()) or not 4 <= len(pin) <= 8 or len(set(pin)) == 1 \
                or pin in "0123456789" or pin in "9876543210":
            raise ServiceError("PASSWORD-WEAK")
        acc["pin"] = _hash_secret(pin)
        self._active(s)
        self.log.append("pin_set", s.uid)
        self._save()
        return {}

    def op_change_password(self, a, token):
        s = self._session(token, "change_password")
        acc = self._account(s.uid)
        self._reauth(s, acc, a.get("old"))
        self._password_ok(a.get("new"), acc["name"])
        acc["pw"] = _hash_secret(a["new"])
        self._active(s)
        self.log.append("password_changed", s.uid)
        self._save()
        return {}

    def op_recovery_code_new(self, a, token):
        """A new recovery code for the signed-in admin (the old one stops
        working). Needs the admin's password."""
        s = self._session(token, "recovery_code_new")
        acc = self._need_role(s, "admin")
        self._reauth(s, acc, a.get("password"))
        recovery = self._recovery_secret(a.get("recovery"), acc["name"], a.get("password"))
        acc["recovery"] = _hash_secret(recovery or a["recovery"].strip())
        self._active(s)
        self.log.append("recovery_code_replaced", s.uid)
        self._save()
        return {"recovery_code": recovery}

    def op_account_create(self, a, token):
        s = self._session(token, "account_create")
        self._need_role(s, "admin")
        name = a.get("name").strip() if isinstance(a.get("name"), str) else ""
        roles = a.get("roles")
        if not name or len(name) > 100 or not isinstance(roles, list) or not roles \
                or not all(isinstance(r, str) for r in roles) or not set(roles) <= set(ROLES):
            raise ServiceError("APP-BAD-REQUEST")
        if any(_norm_name(x["name"]) == _norm_name(name) for x in self.doc["accounts"].values()):
            raise ServiceError("APP-BAD-REQUEST")
        self._password_ok(a.get("password"), name)
        uid = "U" + secrets.token_hex(4)
        self.doc["accounts"][uid] = {"name": name, "roles": sorted(set(roles)),
                                     "certified": False, "pw": _hash_secret(a["password"]), "pin": None}
        self._active(s)
        self.log.append("account_created", s.uid, {"account": uid, "roles": sorted(set(roles))})
        self._save()
        return {"id": uid}

    def op_account_list(self, a, token):
        s = self._session(token, "account_list")
        self._need_role(s, "admin")
        self._active(s)
        return {"accounts": [self._public(u, x) for u, x in self.doc["accounts"].items()],
                "active": self._active_count()}

    def op_account_disable(self, a, token):
        s = self._session(token, "account_disable")
        self._need_role(s, "admin")
        uid = a.get("id")
        acc = self.doc["accounts"].get(uid) if isinstance(uid, str) else None
        if not acc:
            raise ServiceError("APP-BAD-REQUEST")
        admins = [u for u, x in self.doc["accounts"].items() if "admin" in x["roles"] and not x.get("disabled")]
        if admins == [uid]:
            raise ServiceError("ROLE-DENIED")       # never lock the practice out of its own app
        acc["disabled"] = True
        for t in [t for t, x in self.sessions.items() if x.uid == uid]:
            self.sessions.pop(t)
        self._active(s)
        self.log.append("account_disabled", s.uid, {"account": uid})
        self._save()
        return {}

    def op_reset_password(self, a, token):
        s = self._session(token, "reset_password")
        self._need_role(s, "admin")
        uid = a.get("id")
        acc = self.doc["accounts"].get(uid) if isinstance(uid, str) else None
        if not acc:
            raise ServiceError("APP-BAD-REQUEST")
        self._password_ok(a.get("password"), acc["name"])
        acc["pw"], acc["pin"] = _hash_secret(a["password"]), None
        acc.pop("pw_disabled", None)
        self._fail_clear(self._fail_id(_norm_name(acc["name"])))
        for t in [t for t, x in self.sessions.items() if x.uid == uid]:
            self.sessions.pop(t)
        self._active(s)
        self.log.append("password_reset", s.uid, {"account": uid})
        self._save()
        return {}

    def op_settings_set(self, a, token):
        s = self._session(token, "settings_set")
        self._need_role(s, "admin")
        changed = {}
        if a.get("review_people") is not None:
            if a["review_people"] not in (2, 3) or isinstance(a["review_people"], bool):
                raise ServiceError("APP-BAD-REQUEST")
            self.doc["settings"]["review_people"] = changed["review_people"] = a["review_people"]
        if isinstance(a.get("prepare_while_locked"), bool):
            self.doc["settings"]["prepare_while_locked"] = changed["prepare_while_locked"] = \
                a["prepare_while_locked"]
        for key, lo, hi in (("idle_minutes", IDLE_MIN, IDLE_MAX),
                            ("review_days", REVIEW_DAYS_MIN, REVIEW_DAYS_MAX),
                            ("pages_per_part", granite.MIN_PART_PAGES, 1000)):
            v = a.get(key)
            if v is None:
                continue
            if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                raise ServiceError("APP-BAD-REQUEST")
            self.doc["settings"][key] = changed[key] = v
        if not changed:
            raise ServiceError("APP-BAD-REQUEST")
        self._active(s)
        self.log.append("settings_changed", s.uid, changed)
        self._save()
        return {}

    def op_security_log(self, a, token):
        s = self._session(token, "security_log")
        self._need_role(s, "admin")
        self._active(s)
        n = a.get("last", 50)
        n = n if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 1000 else 50
        return {"ok": self.log.ok, "events": self.log.tail(n)}

    def op_integrity_acknowledge(self, a, token):
        """The admin has read the integrity alert and checked the accounts and
        the security log. The acknowledgement itself is logged."""
        s = self._session(token, "integrity_acknowledge")
        self._need_role(s, "admin")
        alert = self.doc.get("integrity_alert")
        if not alert:
            return {}
        self.doc["integrity_alert"] = None
        self._active(s)
        self.log.append("integrity_acknowledged", s.uid, {"reasons": alert.get("reasons", [])})
        self._save()
        return {}

    # -- batches: the work in hand ---------------------------------------------
    #
    # The practice's own files stay where they are. Everything the app makes
    # while working on them lives in that batch's work area, encrypted under
    # a key of its own held in the protected store, so finishing the batch
    # destroys the key first and the rest becomes unreadable at once.
    # The app's records keep only counts, stages and dates — never a path,
    # a name or a word of patient text — so a backup of them holds none.

    def _batch_key(self, bid: str, create: bool = False) -> bytes:
        held = self._pool_keys.get(bid)
        if held is not None:
            return held
        acct = f"{self._anchor_acct}-batch-{bid}"
        try:
            raw = self._anchor.read(acct)
            if raw is None and create:
                raw = secrets.token_hex(32)
                self._anchor.write(acct, raw)
        except AnchorUnavailable:
            raise ServiceError("APP-STORE") from None
        if not raw:
            raise ServiceError("BATCH-UNREADABLE")     # its key is gone: nothing can be read
        key = bytes.fromhex(raw)
        self._pool_keys[bid] = key
        return key

    def _batch_work(self, bid: str, create: bool = False):
        from inkguard import granite
        return granite.WorkArea(self.dir / "work" / bid, self._batch_key(bid, create))

    def _erase_batch(self, bid: str, why: str, uid: str | None = None, *,
                     keep_record: bool = False) -> dict:
        """Destroy the batch's key first (everything it made becomes
        unreadable at once), then delete the files and drop the record."""
        from inkguard import granite
        # Count what the pool holds BEFORE destroying its key: the closing
        # count is what shows the pool was emptied, not merely abandoned.
        pool = None
        try:
            pool = self._batch_pool_counts(bid)
        except Exception:  # noqa: BLE001 — its key may already be gone
            pass
        key_gone = True
        self._pool_keys.pop(bid, None)                 # forget it here as well as there
        try:
            self._anchor.delete(f"{self._anchor_acct}-batch-{bid}")
        except AnchorUnavailable:
            key_gone = False
        removed = 0
        area = self.dir / "work" / bid
        if area.exists():
            for f in sorted(area.rglob("*"), reverse=True):
                try:
                    f.unlink() if f.is_file() else f.rmdir()
                    removed += 1
                except OSError:
                    pass
            try:
                area.rmdir()
            except OSError:
                pass
        rec = self.doc.setdefault("batches", {}).pop(bid, None)
        if keep_record and rec is not None:
            # A finished batch leaves a CLOSED RECORD behind: the dates, the
            # counts, who signed, and the fact that the pool was emptied.
            # Nothing of the patients' survives — no folder, no paths, no
            # text, and no key to read what was deleted — but the practice
            # can still see that this batch happened and how it ended.
            self.doc["batches"][bid] = {
                "stage": "signed_off", "closed": True, "closed_at": _now_iso(),
                "created": rec.get("created"), "by": rec.get("by"),
                "profile": rec.get("profile"), "reviewers": rec.get("reviewers", []),
                "counts": rec.get("counts", {}), "helpers": rec.get("helpers", []),
                "confirmed": rec.get("confirmed"), "part_label": rec.get("part_label"),
                "signed_off": rec.get("signed_off"),
                "signed_off_by_doctor": rec.get("signed_off_by_doctor"),
                "small_numbers": rec.get("small_numbers"),
                "small_rulings": rec.get("small_rulings"),
                "export": rec.get("export"), "pool_closed": pool,
            }
        self.log.append("batch_erased", uid, {"batch": bid, "why": why,
                                              "key_destroyed": key_gone, "files_removed": removed,
                                              "stage": (rec or {}).get("stage"), "pool": pool})
        self._save()
        return {"erased": True, "key_destroyed": key_gone, "files_removed": removed}

    def _start_doctor_clock(self, rec: dict) -> None:
        """The doctor's time to sign off starts when the reviewers finish,
        not when the batch was created: the practice sets how long (7-30
        days, 14 by default) and each batch may be given one extra week."""
        days = self.doc["settings"].get("review_days", REVIEW_DAYS_DEFAULT)
        rec["due"] = (self._today() + _dt.timedelta(days=days)).isoformat()
        rec["reviews_finished"] = _now_iso()

    def _set_stage(self, bid: str, rec: dict, stage: str) -> None:
        if stage == "ready_for_doctor" and not rec.get("due"):
            self._start_doctor_clock(rec)
        rec["stage"] = stage

    def _batch_pool_counts(self, bid: str) -> dict:
        """What the pool holds right now: how much went in, how much came out
        clean, and how much is still inside. Counts only — never a name."""
        from inkguard import granite
        led = granite.WorkArea.ledger(self._batch_work(bid))
        c = led.get("counts", {})
        return {"files_in": c.get("files_in", 0), "notes_in": c.get("note_in", 0),
                "notes_out": c.get("note_out", 0), "erased": c.get("note_erased", 0),
                "in_pool": led.get("in_pool", 0)}

    def _batch_days_left(self, rec: dict) -> int | None:
        """Whole days until this batch's work is erased, or None when the
        doctor's clock has not started (the reviewers are still working)."""
        ends = [d for d in (rec.get("due"), rec.get("backstop")) if d]
        if not ends:
            return None
        return (min(_dt.date.fromisoformat(d) for d in ends) - self._today()).days

    def _expire_batches(self) -> None:
        """Work nobody finished in time is erased, as the practice was told.
        The practice's own files are untouched: the batch can be run again."""
        for bid, rec in list(self.doc.get("batches", {}).items()):
            if rec.get("stage") == "signed_off":
                continue
            left = self._batch_days_left(rec)
            if left is not None and left < 0 and bid not in (self._working_on,
                                                             self._export_running):
                self._erase_batch(bid, "its time ran out before it was signed off")

    def _batch_public(self, bid: str, rec: dict) -> dict:
        people = self.doc.get("accounts", {})
        name = lambda u: (people.get(u) or {}).get("name") or "—"      # noqa: E731
        return {"id": bid, "stage": rec.get("stage"), "created": rec.get("created"),
                "created_by": name(rec.get("by")), "profile": rec.get("profile"),
                "reviewers": [name(u) for u in rec.get("reviewers", [])],
                "counts": rec.get("counts", {}), "progress": rec.get("progress", {}),
                "pages": rec.get("counts", {}).get("pages", 0),
                "due": rec.get("due"), "backstop": rec.get("backstop"),
                "days_left": self._batch_days_left(rec), "extended": bool(rec.get("extended")),
                "error": rec.get("error"), "running": bid == self._working_on,
                "part_label": rec.get("part_label"), "closed": bool(rec.get("closed")),
                "export": {k: v for k, v in (rec.get("export") or {}).items() if k != "by"},
                "confirmed": rec.get("confirmed"), "helpers": list(rec.get("helpers", [])),
                "signed_off": {k: {"name": v["name"], "at": v["at"]}
                               for k, v in (rec.get("signed_off") or {}).items()},
                "reviews_in": len(rec.get("signed_off") or {}),
                "paused": self._pool_paused if bid == self._working_on else None}

    def _work_on(self, bid: str, folder: str) -> None:
        """Prepare a batch in the background: the guard, then the engine, one
        patient at a time. The window keeps answering throughout; progress is
        recorded as each patient is finished, so closing the app and coming
        back carries on from there rather than starting again."""
        from inkguard import granite
        try:
            with self._mutex:
                rec = self.doc.get("batches", {}).get(bid)
                if rec is None:
                    return
                profile = rec.get("profile", "safe_harbor")
                work = self._batch_work(bid)
            folder_p = Path(folder)
            items = granite.load_items(work)
            if not items:
                items, summary = granite.take_stock(folder_p, (rec or {}).get("part"))
                if not summary["to_read"]:
                    raise granite.BatchError("BATCH-NOTHING-READ")
                granite.save_items(work, items)
                work.note("files_in", summary["files"], {"patients": summary["patients"]})
                with self._mutex:
                    rec = self.doc.get("batches", {}).get(bid)
                    if rec is None:
                        return
                    rec["counts"] = granite.counts(items)
                    rec["progress"] = {"done": 0, "total": len(items), "patients_done": 0,
                                       "patients": summary["patients"], "seconds_left": None}
                    rec["stage"] = "processing"
                    self._save()
            by_patient: dict[int, list] = {}
            for it in items:
                by_patient.setdefault(it.patient, []).append(it)
            next_note = 1 + max([int(i.note_id[1:]) for i in items if i.note_id] or [0])
            started = time.monotonic()
            chars_done = 0
            for pi in sorted(by_patient):
                if self._stop_work.is_set():
                    return
                while self._pool_paused and not self._stop_work.is_set():
                    time.sleep(0.5)                    # the screen is locked: nothing is opened
                if self._stop_work.is_set():
                    return
                group = by_patient[pi]
                if all(i.outcome != "pending" for i in group):
                    continue                            # already done in an earlier sitting
                todo = []
                for it in group:
                    if it.outcome != "pending":
                        continue
                    if it.kind == "system":
                        it.outcome = "system"
                    elif it.kind in ("image", "other", "link"):
                        it.outcome = "set_aside"
                    elif granite.guard(folder_p, it):
                        todo.append(it)
                notes, info = granite.prepare_patient(
                    folder_p, todo, patient_key=f"{bid}-p{pi}", profile=profile,
                    first_note=next_note, should_stop=self._stop_work.is_set)
                if info.get("stopped"):
                    return
                next_note += len(notes)
                chars_done += info.get("chars", 0)
                with self._mutex:
                    if bid not in self.doc.get("batches", {}):
                        return                          # cancelled or erased meanwhile
                    for n in notes:
                        work.put("note-" + n["id"], n)
                    if notes:
                        work.note("note_in", len(notes))
                    granite.save_items(work, items)
                    rec = self.doc["batches"][bid]
                    rec["counts"] = granite.counts(items)
                    done = sum(1 for i in items if i.outcome != "pending")
                    rate = chars_done / max(0.001, time.monotonic() - started)
                    left = sum(i.size for i in items if i.outcome == "pending")
                    rec["progress"] = {"done": done, "total": len(items),
                                       "patients_done": pi, "patients": len(by_patient),
                                       "seconds_left": int(left / rate) if rate > 1 else None}
                    self._save()
            with self._mutex:
                if bid in self.doc.get("batches", {}):
                    rec = self.doc["batches"][bid]
                    rec["counts"] = granite.counts(items)
                    rec["progress"] = {"done": len(items), "total": len(items),
                                       "patients_done": len(by_patient), "patients": len(by_patient),
                                       "seconds_left": 0}
                    rec["stage"] = "ready_for_review" if rec["counts"].get("prepared") else "failed"
                    if rec["stage"] == "failed":
                        rec["error"] = "BATCH-NOTHING-READ"
                    self.log.append("batch_prepared", None,
                                    {"batch": bid, "counts": rec["counts"]})
                    self._save()
        except BaseException as exc:  # noqa: BLE001 — a batch must never take the service down
            with self._mutex:
                rec = self.doc.get("batches", {}).get(bid)
                if rec is not None:
                    rec["stage"], rec["error"] = "failed", getattr(exc, "code", "BATCH-FAILED")
                    self.log.append("batch_failed", None, {"batch": bid, "code": rec["error"]})
                    self._save()
        finally:
            with self._mutex:
                if self._working_on == bid:
                    self._working_on, self._worker = None, None
            self._start_next_waiting()

    def _start_next_waiting(self) -> None:
        """Begin the next part that is waiting its turn, if any."""
        with self._mutex:
            if self._working_on or self._closing:
                return
            self._stop_work.clear()                    # a cancel stopped one batch, not the rest
            for nid, nrec in sorted(self.doc.get("batches", {}).items(),
                                    key=lambda kv: kv[1].get("created", "")):
                if nrec.get("stage") != "preparing":
                    continue
                try:
                    folder = (self._batch_work(nid).get("folder") or {}).get("path")
                except ServiceError:
                    continue
                if folder and Path(folder).is_dir():
                    try:
                        self._start_worker(nid, folder)
                    except ServiceError:
                        return
                    return

    def _start_worker(self, bid: str, folder: str) -> None:
        if self._working_on:
            raise ServiceError("BATCH-BUSY")
        if self._worker is not None and self._worker.is_alive():
            raise ServiceError("BATCH-BUSY")          # the last one is still stopping
        self._stop_work.clear()
        self._working_on = bid
        self._worker = threading.Thread(target=self._work_on, args=(bid, folder), daemon=True)
        self._worker.start()

    # What the doctor confirms before a batch starts. These decide where
    # responsibility sits if the work is ever questioned, so each is said in
    # one plain line, ticked on its own, and recorded with the batch.
    CARE_POINTS = (
        ("alone", "Only the people named on this batch will see these notes."),
        ("helpers", "Anyone who helps will be named on this batch."),
        ("sitting", "Each reviewer finishes their part in one sitting where they can."),
        ("authority", "This practice may use these records, and these people may see them."),
    )

    def op_care_points(self, a, token):
        """The points the doctor confirms before starting a batch."""
        self._session(token, "care_points")
        return {"points": [{"id": k, "text": t} for k, t in self.CARE_POINTS]}

    def op_batch_assess(self, a, token):
        """Look at a folder before anything is started: what is in it, how
        long the reading will take, and how it should be split so each
        reviewer's part fits a sitting. Nothing is opened or changed."""
        s = self._session(token, "batch_assess")
        self._need_role(s, "doctor", "admin")
        folder = a.get("folder")
        try:
            got = granite.assess(Path(folder) if isinstance(folder, str) else Path("."),
                                 per_part=self.doc["settings"].get("pages_per_part",
                                                                   granite.PAGES_PER_SITTING))
        except granite.BatchError as e:
            raise ServiceError(e.code) from None
        self._active(s)
        # Said as a floor — "at least this long" — so nobody is told a job is
        # shorter than it is. Each reviewer reads the whole part.
        for p in got["parts"]:
            p["at_least_minutes"] = granite.at_least_minutes(p["pages"])
        got["at_least_minutes"] = granite.at_least_minutes(got["pages"])
        got["one_part"] = len(got["parts"]) == 1
        # What the reviewers are checking against, with the test sets named.
        got["accuracy"] = granite.ACCURACY
        return got

    def op_batch_create(self, a, token):
        """Take stock of a folder and start preparing it. Nothing in the
        folder is moved, changed or deleted — now or ever."""
        from inkguard import granite
        s = self._session(token, "batch_create")
        acc = self._need_role(s, "doctor", "admin")
        self._expire_batches()
        folder = a.get("folder")
        profile = a.get("profile") or "safe_harbor"
        if profile not in ("safe_harbor", "limited_data_set"):
            raise ServiceError("APP-BAD-REQUEST")
        reviewers = a.get("reviewers")
        if not isinstance(reviewers, list) or len(reviewers) != 2 or reviewers[0] == reviewers[1]:
            raise ServiceError("BATCH-REVIEWERS")
        from inkguard import pyrite
        for uid in reviewers:
            r = self.doc["accounts"].get(uid) if isinstance(uid, str) else None
            if not r or r.get("disabled") or not ({"reviewer", "doctor"} & set(r["roles"])):
                raise ServiceError("BATCH-REVIEWERS")
        # Two people or three, as the practice chose (Settings). With three,
        # the clinician who signs off is never one of the two reviewers; with
        # two, the clinician may be one of them, and the review report says
        # which arrangement was used.
        if s.uid in reviewers and self._review_people() == 3:
            raise ServiceError("BATCH-REVIEWERS")
        for uid in reviewers:
            # Who may review at all is settled above. THEN the practice badge:
            # nobody's first look at a real patient's notes should also be
            # their first look at this tool, and a badge that has run out
            # does not count.
            if not pyrite.badge_state(self.doc["accounts"][uid].get("badge"),
                                      today=self._today())["certified"]:
                raise ServiceError("BATCH-NOT-CERTIFIED")
        # THEN the phone. Coming back to open patient work asks for the
        # person's own phone code as well as their password when they have
        # linked a phone (see op_unlock); someone with no phone linked is
        # asked for the password only. Phones are required only of admins,
        # the accounts that can change everything, and are optional for
        # everyone else. The check is made here, while the batch is being
        # set up and nobody is mid-note, rather than at the lock screen.
        for uid in set(reviewers + [s.uid]):
            who = self.doc["accounts"].get(uid) or {}
            if "admin" in who.get("roles", []) and not who.get("totp"):
                raise ServiceError("BATCH-NO-PHONE")
        # Confirmed point by point, not one blanket tick: each is recorded
        # with the batch and printed in the review report.
        confirmed = a.get("confirmed")
        if not isinstance(confirmed, list) or {k for k, _ in self.CARE_POINTS} - set(confirmed):
            raise ServiceError("BATCH-NOT-CONFIRMED")
        queued = self._working_on or (self._worker is not None and self._worker.is_alive())
        # A quick look only, so the doctor hears about a wrong folder at once.
        # Taking full stock (hashing every file) happens in the background,
        # where it cannot hold up the window.
        part = a.get("part")
        if part is not None:
            if not (isinstance(part, dict) and isinstance(part.get("from"), int)
                    and isinstance(part.get("to"), int) and 1 <= part["from"] <= part["to"]
                    and isinstance(part.get("fingerprint"), str) and part["fingerprint"]):
                raise ServiceError("APP-BAD-REQUEST")
            part = {"from": part["from"], "to": part["to"], "fingerprint": part["fingerprint"]}
        try:
            granite.first_look(Path(folder) if isinstance(folder, str) else Path("."))
        except granite.BatchError as e:
            raise ServiceError(e.code) from None
        bid = granite.new_id()
        today = self._today()
        self.doc.setdefault("batches", {})[bid] = {
            "created": _now_iso(), "by": s.uid, "stage": "preparing", "profile": profile,
            "reviewers": list(reviewers), "counts": {}, "progress": {"done": 0, "total": 0,
                                                                     "patients_done": 0, "patients": 0,
                                                                     "seconds_left": None},
            "backstop": (today + _dt.timedelta(days=BATCH_BACKSTOP_DAYS)).isoformat(),
            "due": None, "extended": False, "error": None,
            "part": part, "part_label": a.get("part_label") if isinstance(a.get("part_label"), str) else None,
            "confirmed": {"by": s.uid, "at": _now_iso(),
                          "points": sorted({k for k, _ in self.CARE_POINTS})},
            "helpers": [], "people": self._review_people()}
        try:
            work = self._batch_work(bid, create=True)
            # The folder path is patient information (a folder is often named
            # after a person), so it lives in the pool and nowhere else.
            work.put("folder", {"path": str(folder)})
            work.note("pool_opened", 1, {"by": s.uid})
        except BaseException:
            # Never leave a key or a path behind for a batch that does not exist.
            self.doc["batches"].pop(bid, None)
            self._erase_batch_quietly(bid)
            raise
        self._active(s)
        self.log.append("batch_created", s.uid, {"batch": bid, "profile": profile,
                                                 "confirmed": sorted({k for k, _ in self.CARE_POINTS})})
        self._save()
        # One batch is prepared at a time; the parts of a large folder wait
        # their turn and start on their own as each one finishes.
        if not queued:
            try:
                self._start_worker(bid, str(folder))
            except ServiceError:
                queued = True                          # another just began: this one waits its turn
        return {**self._batch_public(bid, self.doc["batches"][bid]), "created_by_name": acc["name"],
                "queued": bool(queued)}

    def _erase_batch_quietly(self, bid: str) -> None:
        """Destroy a half-made batch's key and folder without a record entry."""
        self._pool_keys.pop(bid, None)
        try:
            self._anchor.delete(f"{self._anchor_acct}-batch-{bid}")
        except AnchorUnavailable:
            pass
        area = self.dir / "work" / bid
        if area.exists():
            for f in sorted(area.rglob("*"), reverse=True):
                try:
                    f.unlink() if f.is_file() else f.rmdir()
                except OSError:
                    pass
            try:
                area.rmdir()
            except OSError:
                pass

    def _batch_mine(self, s: _Session, bid: str, rec: dict) -> None:
        """A batch belongs to the doctor who started it. Another doctor does
        not reach into it; an admin can, and it is logged either way."""
        acc = self._account(s.uid)
        if rec.get("by") != s.uid and "admin" not in acc["roles"]:
            self._log("role_denied", s.uid, {"needs": ["the doctor who started this batch"]})
            raise ServiceError("ROLE-DENIED")

    def op_batch_resume(self, a, token):
        """Carry on preparing a batch that was interrupted (the app closed, or
        the computer went to sleep). It picks up where it stopped."""
        s = self._session(token, "batch_resume")
        self._need_role(s, "doctor", "admin")
        bid, rec = self._batch(a)
        self._batch_mine(s, bid, rec)
        if rec["stage"] not in ("processing", "failed") or bid == self._working_on:
            raise ServiceError("APP-BAD-REQUEST")
        folder = (self._batch_work(bid).get("folder") or {}).get("path")
        if not folder or not Path(folder).is_dir():
            raise ServiceError("BATCH-FOLDER")
        rec["stage"], rec["error"] = "processing", None
        self._active(s)
        self._save()
        self._start_worker(bid, folder)
        return self._batch_public(bid, rec)

    def _batch(self, a) -> tuple[str, dict]:
        bid = a.get("batch")
        rec = self.doc.get("batches", {}).get(bid) if isinstance(bid, str) else None
        if rec is None:
            raise ServiceError("BATCH-GONE")
        return bid, rec

    def op_batch_list(self, a, token):
        s = self._session(token, "batch_list")
        self._expire_batches()
        mine = self._account(s.uid)
        out = []
        for bid, rec in sorted(self.doc.get("batches", {}).items(),
                               key=lambda kv: kv[1].get("created", ""), reverse=True):
            if not ({"admin", "doctor"} & set(mine["roles"])) and s.uid not in rec.get("reviewers", []):
                continue                                # a reviewer sees only their own work
            row = self._batch_public(bid, rec)
            if s.uid in rec.get("reviewers", []) and not ({"admin", "doctor"} & set(mine["roles"])):
                row.pop("signed_off", None)            # the other reviewer's work is not theirs to see
                row.pop("reviews_in", None)
            if s.uid in rec.get("reviewers", []):
                slot = self._slot_of(rec, s.uid)
                ready = rec["stage"] in ("ready_for_review", "in_review")
                signed = bool((rec.get("signed_off") or {}).get(slot))
                done = 0
                if ready and not signed:
                    try:
                        done = len(self._review_state(bid, rec, slot)["done"])
                    except (ServiceError, Exception):    # noqa: BLE001 — the row still shows
                        done = 0
                row["my_review"] = {"slot": slot, "can_review": ready and not signed,
                                    "signed": signed, "done": done}
            out.append(row)
        return {"batches": out, "review_days": self.doc["settings"].get("review_days",
                                                                       REVIEW_DAYS_DEFAULT)}

    def op_batch_status(self, a, token):
        """One batch in detail, including every file that was not read and
        why. No paths and no patient text — those stay in the work area."""
        from inkguard import granite
        s = self._session(token, "batch_status")
        self._expire_batches()
        bid, rec = self._batch(a)
        mine = self._account(s.uid)
        if not ({"admin", "doctor"} & set(mine["roles"])) and s.uid not in rec.get("reviewers", []):
            raise ServiceError("ROLE-DENIED")
        out = self._batch_public(bid, rec)
        if not ({"admin", "doctor"} & set(mine["roles"])):
            out.pop("signed_off", None)
            out.pop("reviews_in", None)
        if rec.get("closed"):
            # The pool is gone: there is nothing to open and nothing to read.
            out["closed"] = True
            out["set_aside"] = []
            out["pool"] = rec.get("pool_closed") or {"in_pool": 0}
            out["remind"] = False
            return out
        s.pool_open = True
        # A batch whose key is gone cannot be worked on; say so plainly
        # rather than showing a status that looks fine.
        try:
            items = granite.load_items(self._batch_work(bid))
        except granite.BatchError as e:
            raise ServiceError(e.code) from None
        out["set_aside"] = granite.set_aside_rows(items)
        out["remind"] = bool(rec.get("due")) and out["days_left"] in REMIND_AT_DAYS
        out["pool"] = self._batch_pool_counts(bid)
        return out

    def op_batch_extend(self, a, token):
        """One extension per batch, for a doctor who needs longer. Logged, so
        it cannot quietly become the habit."""
        s = self._session(token, "batch_extend")
        self._need_role(s, "doctor", "admin")
        bid, rec = self._batch(a)
        self._batch_mine(s, bid, rec)
        if rec.get("extended") or not rec.get("due"):
            raise ServiceError("BATCH-NO-EXTENSION")
        rec["due"] = (_dt.date.fromisoformat(rec["due"])
                      + _dt.timedelta(days=REVIEW_EXTENSION_DAYS)).isoformat()
        rec["extended"] = True
        self._active(s)
        self._log("batch_extended", s.uid, {"batch": bid, "due": rec["due"]})
        return self._batch_public(bid, rec)

    def op_batch_helper(self, a, token):
        """Name someone who helped with this batch without being one of its
        two reviewers — a student, an assistant, a colleague asked a question.
        The record then shows who actually did the work, which is the point
        of naming them. Helpers are added, never removed."""
        s = self._session(token, "batch_helper")
        self._need_role(s, "doctor", "admin", "reviewer")
        bid, rec = self._batch(a)
        mine = self._account(s.uid)
        if rec.get("by") != s.uid and "admin" not in mine["roles"] \
                and s.uid not in rec.get("reviewers", []):
            raise ServiceError("ROLE-DENIED")
        name = a.get("name").strip() if isinstance(a.get("name"), str) else ""
        what = a.get("what").strip() if isinstance(a.get("what"), str) else ""
        if not name or len(name) > 100 or len(what) > 200:
            raise ServiceError("APP-BAD-REQUEST")
        rec.setdefault("helpers", []).append({"name": name, "what": what,
                                              "named_by": s.uid, "at": _now_iso()})
        self._active(s)
        self._log("batch_helper_named", s.uid, {"batch": bid})
        return {"helpers": rec["helpers"]}

    def op_batch_cancel(self, a, token):
        """Stop and erase a batch. The practice's own files are untouched."""
        s = self._session(token, "batch_cancel")
        self._need_role(s, "doctor", "admin")
        bid, _rec = self._batch(a)
        self._batch_mine(s, bid, _rec)
        if bid == self._export_running:
            # The files are being written right now. Cancelling here would
            # destroy the key under the export and leave released files with
            # no record of them.
            raise ServiceError("BATCH-BUSY")
        if _rec.get("closed"):
            raise ServiceError("BATCH-GONE")
        # Erasing happens now, holding the lock the worker needs to write
        # anything: it finds the batch gone at its next step and stops.
        if bid == self._working_on:
            self._stop_work.set()
        self._active(s)
        # RESTART: the practice may wipe a batch and start again. What had
        # been done up to that point is written into a restart report first,
        # without any patient text, and kept.
        why = str(a.get("reason") or "").strip()[:500]
        report = self._restart_report(bid, _rec, s.uid, why)
        out = self._erase_batch(bid, "cancelled by the practice", s.uid)
        self.doc.setdefault("restarts", []).append(
            {"batch": bid, "at": _now_iso(), "by": s.uid, "report": report,
             "key_destroyed": out.get("key_destroyed"), "files_removed": out.get("files_removed")})
        self._log("batch_restart_report", s.uid, {"batch": bid})
        self._save()
        return {**out, "report": report}

    def _restart_report(self, bid: str, rec: dict, uid: str, why: str) -> str:
        """Everything that had happened to a batch before it was wiped, as
        counts, dates and names of the people who worked on it. No patient,
        no file name, no note text."""
        people = self.doc.get("accounts", {})
        name = lambda u: (people.get(u) or {}).get("name") or "—"      # noqa: E731
        counts = rec.get("counts", {}) or {}
        L = ["# Restart report", "",
             f"**Batch** {bid}  ", f"**Wiped** {_now_iso()} by **{name(uid)}**  ",
             f"**Reason given** {why or 'none given'}", "",
             "The batch was wiped at the practice's request so the work could start again. "
             "Its key was destroyed first, then its working files. The practice's own files "
             "were not touched. This report holds no patient information.", "",
             "## How far it had got", "",
             f"- Created {rec.get('created') or '—'} by {name(rec.get('by'))}; profile "
             f"{rec.get('profile') or 'safe_harbor'}",
             f"- Stage reached: {rec.get('stage') or '—'}",
             f"- Notes: {counts.get('notes', counts.get('prepared', 0))}; pages: {counts.get('pages', 0)}"]
        for slot, uid_r in zip(("R1", "R2"), rec.get("reviewers", [])):
            try:
                st = self._review_state(bid, rec, slot)
                L.append(f"- Reviewer {slot} ({name(uid_r)}): {len(st['done'])} of "
                         f"{len(st['notes'])} notes confirmed")
            except Exception:  # noqa: BLE001 — the pool may be unreadable
                L.append(f"- Reviewer {slot} ({name(uid_r)}): progress could not be read")
        signed = rec.get("signed_off") or {}
        L.append(f"- Reviewers who had signed off: {len(signed)}"
                 + (" (" + ", ".join(v.get("name", "—") for v in signed.values()) + ")" if signed else ""))
        L.append(f"- The doctor's rulings recorded: {len(rec.get('rulings') or {})}")
        L.append(f"- Small-numbers rulings recorded: {len(rec.get('small_rulings') or {})}")
        ex = rec.get("export") or {}
        L.append(f"- Release: {ex.get('stage') or 'not started'}")
        L += ["", "Everything above is in the security log too. A new batch starts from the "
              "practice's own files, with nothing carried over from this one."]
        from inkguard.fluorite import VERIFY_NOTICE
        L += ["", f"> {VERIFY_NOTICE}"]
        return "\n".join(L) + "\n"

    def op_restart_reports(self, a, token):
        """The restart reports this practice has made: who wiped what, when
        and why, and how far it had got."""
        s = self._session(token, "restart_reports")
        self._need_role(s, "doctor", "admin")
        return {"reports": [{k: r.get(k) for k in ("batch", "at", "report", "key_destroyed",
                                                    "files_removed")}
                            for r in self.doc.get("restarts", [])]}

    # -- review: two people, one note at a time, neither seeing the other -----

    def _slot_of(self, rec: dict, uid: str) -> str:
        """Which of the two reviewer places this person holds on this batch."""
        try:
            return granite.REVIEWER_SLOTS[rec["reviewers"].index(uid)]
        except (ValueError, KeyError, IndexError):
            raise ServiceError("ROLE-DENIED") from None

    def _review_state(self, bid: str, rec: dict, slot: str) -> dict:
        """Where this reviewer has got to: the notes in order, which are
        done, and which comes next."""
        from inkguard import granite
        work = self._batch_work(bid)
        items = granite.load_items(work)
        nids = [i.note_id for i in items if i.note_id]
        done, left, pages_left = [], [], 0
        for nid in nids:
            st = granite.load_decision(work, slot, nid)
            (done if st.get("confirmed_at") else left).append(nid)
        for nid in left:
            try:
                pages_left += granite.load_note(work, nid).get("pages", 1)
            except granite.BatchError:
                pass
        return {"work": work, "notes": nids, "done": done, "left": left, "pages_left": pages_left}

    def _pace(self, uid: str) -> float:
        """Seconds a page takes this person, from their own finished notes.
        Used only to say 'about N minutes left'."""
        p = self.doc.get("pace", {}).get(uid)
        if not p or not p.get("pages"):
            return 70.0                                # a steady first guess
        return max(10.0, min(600.0, p["seconds"] / p["pages"]))

    def _note_pack(self, work, rec: dict, slot: str, nid: str, state: dict) -> dict:
        from inkguard import granite
        n = granite.load_note(work, nid)
        st = granite.load_decision(work, slot, nid)
        place = state["notes"].index(nid) + 1
        return {
            "note": nid, "place": place, "of": len(state["notes"]),
            "pages": n.get("pages", 1), "patient": n.get("patient"),
            "status": n.get("status"), "block_reason": n.get("block_reason", ""),
            "text": n.get("text", ""),
            "removals": n.get("removals", []), "generalized": n.get("generalized", []),
            "questions": [dict(q, word=n.get("text", "")[q["start"]:q["end"]],
                               guess=REVIEW_GUESSES.get(q.get("entity"), "something that could "
                                                        "identify someone"),
                               why=("a word it could not place" if q.get("kind") == "not_understood"
                                    else "a word it suspected but was not sure about"))
                          for q in n.get("questions", [])],
            "rare": n.get("rare", []),
            "decision": {k: st.get(k) for k in ("keep", "unsure", "add", "answers", "kinds",
                                                "words", "fb") if st.get(k)},
            "confirmed": bool(st.get("confirmed_at")),
            "kinds": [{"id": k, "label": v} for k, v in REVIEW_KINDS],
            "answers": sorted(REVIEW_ANSWERS),
        }

    def op_review_open(self, a, token):
        """Start, or come back to, this reviewer's part of a batch. Says how
        far they have got and roughly how long the rest will take, from their
        own measured pace."""
        s = self._session(token, "review_open")
        bid, rec = self._batch(a)
        slot = self._slot_of(rec, s.uid)
        if rec["stage"] not in ("ready_for_review", "in_review"):
            raise ServiceError("REVIEW-NOT-READY")
        s.pool_open = True
        state = self._review_state(bid, rec, slot)
        if rec["stage"] == "ready_for_review":
            rec["stage"] = "in_review"
            self._save()
        self._active(s)
        left = state["left"]
        return {"batch": bid, "slot": slot, "done": len(state["done"]), "of": len(state["notes"]),
                "next": left[0] if left else None, "pages_left": state["pages_left"],
                "minutes_left": int(state["pages_left"] * self._pace(s.uid) / 60) if left else 0,
                "at_least_minutes": granite.at_least_minutes(state["pages_left"]),
                "pages": rec.get("counts", {}).get("pages", 0),
                "accuracy": granite.ACCURACY,
                "signed_off": bool(rec.get("signed_off", {}).get(slot)),
                "certified": bool(self._account(s.uid).get("certified")),
                # Every note of theirs, in order, with whether it is
                # confirmed — so a reviewer who realises on note nine that
                # they answered note four wrongly can go back to it before
                # they sign off. After sign-off the answers can no longer be
                # changed; that is the point of signing.
                "notes": [{"id": nid, "place": i + 1, "done": nid in state["done"]}
                          for i, nid in enumerate(state["notes"])]}

    def op_review_note(self, a, token):
        """One note to work through: its words, what the tool marked, and
        every word it could not name. The other reviewer's answers are never
        part of this — each person decides on their own."""
        from inkguard import granite
        s = self._session(token, "review_note")
        bid, rec = self._batch(a)
        slot = self._slot_of(rec, s.uid)
        if rec["stage"] not in ("ready_for_review", "in_review"):
            raise ServiceError("REVIEW-NOT-READY")
        s.pool_open = True
        state = self._review_state(bid, rec, slot)
        nid = a.get("note") or (state["left"][0] if state["left"] else None)
        if nid not in state["notes"]:
            raise ServiceError("REVIEW-NOTE-GONE")
        try:
            pack = self._note_pack(state["work"], rec, slot, nid, state)
        except granite.BatchError as e:
            raise ServiceError(e.code) from None
        # How long this note is open is measured here, not sent by the
        # screen: a review that was never really read is a defect we check
        # for. Opening the same note again keeps the original start, so
        # looking back at it does not quietly reset the clock.
        self._review_opened.setdefault((s.uid, bid, nid), self._clock())
        self._active(s)
        return pack

    def op_review_save(self, a, token):
        """Keep this note's answers. Called as the reviewer works and when
        they move on, so a long batch is never held in a screen."""
        from inkguard import granite
        s = self._session(token, "review_save")
        bid, rec = self._batch(a)
        slot = self._slot_of(rec, s.uid)
        if rec["stage"] not in ("ready_for_review", "in_review"):
            raise ServiceError("REVIEW-NOT-READY")
        if rec.get("signed_off", {}).get(slot):
            raise ServiceError("REVIEW-SIGNED")
        s.pool_open = True
        work = self._batch_work(bid)
        nid = a.get("note")
        state = self._review_state(bid, rec, slot)
        if nid not in state["notes"]:
            raise ServiceError("REVIEW-NOTE-GONE")
        try:
            note = granite.load_note(work, nid)
            st = granite.load_decision(work, slot, nid)
            if st.get("confirmed_at"):
                # A confirmed note is closed. Reopening it to change answers
                # after the fact would make the sign-off untrue.
                raise granite.BatchError("REVIEW-NOTE-CONFIRMED", "this note is already confirmed")
            new = granite.check_decision(note, a.get("decision"), REVIEW_ADD_TYPES, REVIEW_ANSWERS)
        except granite.BatchError as e:
            raise ServiceError(e.code) from None
        opened = self._review_opened.pop((s.uid, bid, nid), None)
        spent = float(st.get("seconds", 0.0))
        spent += self._review_elapsed.pop((s.uid, bid, nid), 0.0)   # before the screen locked
        if opened is not None:
            spent += max(0.0, min(3600.0, self._clock() - opened))
        st.update(new)
        st["seconds"] = round(spent, 1)
        confirm = a.get("confirm") is True
        if confirm:
            missing = granite.unanswered(note, st)
            if missing:
                raise ServiceError("REVIEW-UNANSWERED")
            st["confirmed_at"] = _now_iso()
            # Counted once per note: a pace built from re-confirmations would
            # make the estimate meaningless.
            pace = self.doc.setdefault("pace", {}).setdefault(s.uid, {"pages": 0, "seconds": 0.0})
            pace["pages"] += note.get("pages", 1)
            pace["seconds"] += spent
        granite.save_decision(work, slot, nid, st)
        self._active(s)
        if confirm:
            self._save()
        state = self._review_state(bid, rec, slot)
        return {"saved": True, "confirmed": bool(st.get("confirmed_at")),
                "done": len(state["done"]), "of": len(state["notes"]),
                "next": state["left"][0] if state["left"] else None,
                "minutes_left": int(state["pages_left"] * self._pace(s.uid) / 60)}

    # -- the review sitting: one task, breaks on the record --------------------
    #
    # While someone is reviewing, the screen is a room of its own. They may
    # blur it and take a break, and they must come back and finish; the only
    # other way out is an emergency exit that closes the pool and signs them
    # out, and says plainly on the record that they left part way. Breaks are not discouraged — a tired reviewer is the
    # danger — they are simply counted, because the sitting is what the
    # attestation is about.

    #: A blurred screen keeps the pool open this long. Past it the ordinary
    #: idle lock takes over, which releases the batch's key and asks for the
    #: phone code as well on the way back.
    PAUSE_GRACE_MIN = 5

    def op_review_pause(self, a, token):
        """Blur the screen for a break. The notes are hidden at once; the
        pool stays open for a few minutes so coming back is quick, and after
        that the ordinary lock takes it."""
        s = self._session(token, "review_pause")
        bid, rec = self._batch(a)
        slot = self._slot_of(rec, s.uid)
        st = rec.setdefault("sitting", {}).setdefault(slot, {"pauses": 0, "paused_s": 0.0,
                                                             "away": 0, "blocked": 0})
        if not st.get("paused_at"):
            st["pauses"] = int(st.get("pauses") or 0) + 1
            st["paused_at"] = self._clock()
        s.locked = True                                # the screen is covered
        self._log("review_paused", s.uid, {"batch": bid, "slot": slot,
                                           "pauses": st["pauses"]})
        self._save()
        return {"paused": True, "grace_minutes": self.PAUSE_GRACE_MIN}

    def op_review_resume(self, a, token):
        """Back from a break: their own password. If the break ran past the
        grace the pool was closed by the idle lock, and the ordinary unlock
        (password and their phone code) applies instead."""
        s = self._session(token, "review_resume")
        bid, rec = self._batch(a)
        slot = self._slot_of(rec, s.uid)
        acc = self._account(s.uid)
        st = (rec.get("sitting") or {}).get(slot) or {}
        began = st.get("paused_at")
        long_break = began is not None and (self._clock() - began) > self.PAUSE_GRACE_MIN * 60
        if long_break or self._idle_expired(s):
            # Not ours to wave through: this is the real lock now.
            raise ServiceError("SESSION-LOCKED")
        self._reauth(s, acc, a.get("password"))
        if began is not None:
            st["paused_s"] = float(st.get("paused_s") or 0) + (self._clock() - began)
            st.pop("paused_at", None)
        s.locked = False
        self._active(s)
        self._log("review_resumed", s.uid, {"batch": bid, "slot": slot})
        self._save()
        return {"paused": False}

    def op_review_away(self, a, token):
        """The window lost focus while a note was on screen, or a copy, print
        or save was blocked. Counted, never scolded: it goes on the record as
        a fact about the sitting, which is what the attestation covers."""
        s = self._session(token, "review_away")
        bid, rec = self._batch(a)
        slot = self._slot_of(rec, s.uid)
        what = a.get("what")
        if what not in ("away", "blocked"):
            raise ServiceError("APP-BAD-REQUEST")
        st = rec.setdefault("sitting", {}).setdefault(slot, {"pauses": 0, "paused_s": 0.0,
                                                             "away": 0, "blocked": 0})
        st[what] = int(st.get(what) or 0) + 1
        self._save()
        return {what: st[what]}

    def op_review_leave(self, a, token):
        """The emergency exit: out of the pool and signed out, in one action.

        Nothing is lost — every answer so far is saved — and nothing is
        hidden: the record says this reviewer left part way through, with
        how far they had got."""
        s = self._session(token, "review_leave")
        bid, rec = self._batch(a)
        slot = self._slot_of(rec, s.uid)
        state = None
        try:
            state = self._review_state(bid, rec, slot)
        except ServiceError:
            pass
        done = len(state["done"]) if state else None
        of = len(state["notes"]) if state else None
        st = rec.setdefault("sitting", {}).setdefault(slot, {"pauses": 0, "paused_s": 0.0,
                                                             "away": 0, "blocked": 0})
        st["left"] = int(st.get("left") or 0) + 1
        st["left_at"] = _now_iso()
        st["left_after"] = done
        self._close_pools("a reviewer left the pool", s.uid)
        self._log("review_left", s.uid, {"batch": bid, "slot": slot, "done": done, "of": of})
        self._save()
        # Signed out, in the same breath: the pool is closed and the session
        # is gone, so the next thing on screen is the sign-in screen.
        if isinstance(token, str):
            self.sessions.pop(token, None)
        return {"left": True, "done": done, "of": of}

    def op_review_reopen(self, a, token):
        """Take back a confirmed note to change the answer, before sign-off.

        A confirmed note is closed, and it stays closed by accident — but a
        reviewer who realises on note nine that they answered note four
        wrongly must be able to put it right, because they are about to sign
        a statement saying every note is right to the best of their
        knowledge, and signing that over a known mistake would make the
        statement untrue.

        So reopening is deliberate, one note at a time, never after
        sign-off, and it is COUNTED: how often a reviewer goes back is a
        quality signal we keep, not a fault we hide."""
        from inkguard import granite
        s = self._session(token, "review_reopen")
        bid, rec = self._batch(a)
        slot = self._slot_of(rec, s.uid)
        if rec["stage"] not in ("ready_for_review", "in_review"):
            raise ServiceError("REVIEW-NOT-READY")
        if (rec.get("signed_off") or {}).get(slot):
            raise ServiceError("REVIEW-SIGNED")
        s.pool_open = True
        work = self._batch_work(bid)
        nid = a.get("note")
        if nid not in [i.note_id for i in granite.load_items(work) if i.note_id]:
            raise ServiceError("REVIEW-NO-NOTE")
        try:
            st = granite.load_decision(work, slot, nid)
        except granite.BatchError as e:
            raise ServiceError(e.code) from None
        if not st.get("confirmed_at"):
            return {"reopened": True, "note": nid}    # already open; nothing to do
        st.pop("confirmed_at", None)
        st["reopened"] = int(st.get("reopened") or 0) + 1
        st["reopened_at"] = _now_iso()
        granite.save_decision(work, slot, nid, st)
        rec["reopened"] = int(rec.get("reopened") or 0) + 1
        self._save()
        self._active(s)
        self._log("review_reopened", s.uid, {"batch": bid, "slot": slot, "note": nid,
                                             "times": st["reopened"]})
        return {"reopened": True, "note": nid, "times": st["reopened"]}

    def op_review_sign_off(self, a, token):
        """The reviewer's part is finished and attested. When both have
        signed, the batch goes to the doctor and their clock starts."""
        s = self._session(token, "review_sign_off")
        bid, rec = self._batch(a)
        slot = self._slot_of(rec, s.uid)
        acc = self._account(s.uid)
        if rec["stage"] not in ("ready_for_review", "in_review"):
            raise ServiceError("REVIEW-NOT-READY")
        if a.get("attest") is not True:
            raise ServiceError("REVIEW-NOT-ATTESTED")
        state = self._review_state(bid, rec, slot)
        if state["left"]:
            raise ServiceError("REVIEW-UNFINISHED")
        # Every note read again at the moment of signing: the statement says
        # every unsure word was answered, so it is checked, not assumed.
        work = state["work"]
        for nid in state["notes"]:
            if granite.unanswered(granite.load_note(work, nid),
                                  granite.load_decision(work, slot, nid)):
                raise ServiceError("REVIEW-UNANSWERED")
        signed = rec.setdefault("signed_off", {})
        if signed.get(slot):
            raise ServiceError("REVIEW-SIGNED")
        signed[slot] = {"by": s.uid, "name": acc["name"], "at": _now_iso(),
                        "attestation": REVIEW_ATTESTATION,
                        "certified": bool(acc.get("certified"))}
        both = all(signed.get(x) for x in granite.REVIEWER_SLOTS)
        if both:
            self._set_stage(bid, rec, "ready_for_doctor")
        self._active(s)
        self._log("review_signed_off", s.uid, {"batch": bid, "slot": slot, "both": both})
        # Their own part only: whether the other reviewer has finished, and
        # when, is not theirs to know while they are both still deciding.
        return {"signed_off": True}

    # -- feedback, after the files are released --------------------------------
    #
    # Asked once, at the end of a batch, and never skipped quietly: the
    # screen puts the form in the way and the doctor either sends it or says
    # not this time. The report is built to hold no patient details (amber):
    # what went wrong is built only from the support codes, and the doctor's
    # own words are scrubbed and then re-scanned, so a field that still looks
    # like it holds patient details is blanked and named rather than saved.
    # It is SAVED to a folder, never posted: nothing in this app reaches the
    # network on its own.

    def op_feedback_open(self, a, token):
        """The questions, and what the report would carry if sent."""
        from inkguard import amber, chert
        s = self._session(token, "feedback_open")
        bid, rec = self._batch(a)
        self._need_role(s, "doctor", "admin")
        fr = amber.build(support_events=self._support_events(rec),
                         run_shape=self._run_shape(rec), os_name=sys.platform)
        self._active(s)
        return {"batch": bid, "went_wrong": fr.what_went_wrong,
                "run_shape": fr.run_shape,
                "issues": [{"id": k, "label": v}
                           for k, v in sorted(chert.FEEDBACK_ISSUES.items(), key=lambda x: x[1])],
                "asked": bool(rec.get("feedback"))}

    def op_feedback_save(self, a, token):
        """Finish the report and write it where the doctor chose. Their own
        words are scrubbed first; anything that still looks like patient
        detail is blanked, named, and the report is not marked ready."""
        from inkguard import amber, chert
        s = self._session(token, "feedback_save")
        bid, rec = self._batch(a)
        self._need_role(s, "doctor", "admin")
        text = a.get("note")
        sugg = a.get("suggestion")
        for v in (text, sugg):
            if v is not None and (not isinstance(v, str) or len(v) > chert.FEEDBACK_TEXT_MAX):
                raise ServiceError("APP-BAD-REQUEST")
        contact = a.get("contact") if isinstance(a.get("contact"), dict) else {}
        issues = a.get("issues") or []
        if not isinstance(issues, list) or not all(
                isinstance(x, str) and x in chert.FEEDBACK_ISSUES for x in issues):
            raise ServiceError("APP-BAD-REQUEST")
        # Only the ticked list and the support codes are free of patient
        # details by construction: they carry no words of the doctor's own.
        # The doctor's own words are scrubbed and re-scanned like any field.
        ticked = "; ".join(chert.FEEDBACK_ISSUES[x] for x in dict.fromkeys(issues))
        fr = amber.build(support_events=self._support_events(rec),
                         run_shape=self._run_shape(rec),
                         note=((f"What went wrong: {ticked}.\n" if ticked else "") + (text or "")),
                         suggestion=sugg or "", contact=contact, os_name=sys.platform)
        out = a.get("out") or ((rec.get("export") or {}).get("out") or "")
        if not out:
            raise ServiceError("RUN-OUTPUT-WRITE")
        if not fr.ready:
            # Fail closed, and say which field and why — the doctor can
            # rephrase it. Nothing is written.
            return {"saved": False, "blocked": fr.blocked_fields,
                    "why": "that wording still looked like it held patient details, so nothing "
                           "was written. Say it in general terms and it will go."}
        try:
            where = amber.save(fr, Path(out))
        except OSError:
            raise ServiceError("RUN-OUTPUT-WRITE") from None
        rec["feedback"] = {"at": _now_iso(), "by": s.uid, "report_id": fr.report_id,
                           "sent": False, "scrubbed": fr.scrub_counts,
                           "issues": list(dict.fromkeys(issues))}
        self._log("feedback_saved", s.uid, {"batch": bid, "report": fr.report_id,
                                            "scrubbed": fr.scrub_counts,
                                            "issues": list(dict.fromkeys(issues))})
        self._save()
        self._active(s)
        # The same checked text that was written is handed back, so the screen
        # can offer it to the doctor's OWN email app or a GitHub issue. This
        # app never sends anything: the doctor sees it there and decides.
        return {"saved": True, "files": where, "report_id": fr.report_id,
                "scrubbed": fr.scrub_counts, "text": amber.render_markdown(fr)}

    def op_feedback_skip(self, a, token):
        """Not this time. Recorded, because being asked is the point; the
        answer is always the doctor's own."""
        s = self._session(token, "feedback_skip")
        bid, rec = self._batch(a)
        self._need_role(s, "doctor", "admin")
        rec["feedback"] = {"at": _now_iso(), "by": s.uid, "declined": True}
        self._log("feedback_declined", s.uid, {"batch": bid})
        self._save()
        self._active(s)
        return {"asked": True}

    def _support_events(self, rec: dict) -> list[dict]:
        """The batch's own support codes, as counts. No paths, no text."""
        return [{"code": code, "count": n} for code, n in sorted((rec.get("codes") or {}).items())]

    def _run_shape(self, rec: dict) -> dict:
        c = rec.get("counts") or {}
        ex = rec.get("export") or {}
        return {"found": c.get("files", 0), "deidentified": ex.get("records", 0),
                "held": c.get("failed", 0), "set_aside": c.get("set_aside", 0),
                "failed": c.get("failed", 0), "patients": c.get("patients", 0)}

    # -- the practice batch, and the badge it earns ----------------------------
    #
    # Nobody's first look at a real patient's notes should also be their
    # first look at this tool, so a reviewer works through invented notes on
    # the real review screen first and is marked against a known answer key
    # (pyrite). Two of those notes hold identifiers the engine does not
    # mark: a reviewer who only ticks what the tool found does not pass,
    # because that is the reviewer a two-person review cannot carry. The
    # badge lasts a year and the review report prints the date it was
    # earned.

    def op_practice_open(self, a, token):
        """The practice notes, redacted by the engine exactly as a real
        note would be, with the words it is unsure about to answer."""
        from inkguard import chert, contract, pyrite
        s = self._session(token, "practice_open")
        acc = self._account(s.uid)
        state = pyrite.badge_state(acc.get("badge"), today=self._today())
        out = []
        for note in pyrite.NOTES:
            res = contract.deidentify(note.text, profile="safe_harbor", mode="mask")
            payload, sanitized = chert.note_payload(note.text, res.raw, False)
            out.append({
                "note": note.id, "text": sanitized, "status": payload["status"],
                "removals": payload.get("removals", []),
                "generalized": payload.get("generalized", []),
                "questions": [dict(q, word=sanitized[q["start"]:q["end"]],
                                   guess=REVIEW_GUESSES.get(q.get("entity"),
                                                            "something that could identify "
                                                            "someone"))
                              for q in payload.get("questions", [])],
                "rare": payload.get("rare", []),
            })
        self._active(s)
        return {"notes": out, "of": len(out), "badge": state,
                "kinds": [{"id": k, "label": v} for k, v in REVIEW_KINDS],
                "answers": sorted(REVIEW_ANSWERS),
                "about": ("These people do not exist. Every name, number and address in them "
                          "was invented for this exercise, and the tool already knows every "
                          "answer. Two of the notes hold something identifying that the tool "
                          "does not mark — only reading finds those."),
                "needs": {"planted": pyrite.MUST_CATCH_PLANTED,
                          "months": pyrite.BADGE_MONTHS}}

    def op_practice_mark(self, a, token):
        """Mark an attempt and, if it passes, record the badge.

        What is marked is what the person's own decisions WOULD HAVE
        RELEASED, not which boxes they ticked."""
        from inkguard import chert, contract, pyrite
        s = self._session(token, "practice_mark")
        acc = self._account(s.uid)
        given = a.get("decisions")
        if not isinstance(given, dict):
            raise ServiceError("APP-BAD-REQUEST")
        finished, unanswered, own = {}, 0, {}
        for note in pyrite.NOTES:
            d = given.get(note.id)
            if not isinstance(d, dict):
                continue
            res = contract.deidentify(note.text, profile="safe_harbor", mode="mask")
            payload, sanitized = chert.note_payload(note.text, res.raw, False)
            if payload["status"] != "ready":
                continue
            full = dict(payload, id=note.id, text=sanitized)
            # The SAME validation a real review gets. Without it an answer of
            # "banana" counts as answered and silently means "leave it in",
            # and there is no cap on how many marks one note may carry.
            try:
                d = granite.check_decision(full, d, REVIEW_ADD_TYPES, REVIEW_ANSWERS)
            except granite.BatchError as e:
                raise ServiceError(e.code) from None
            unanswered += len(granite.unanswered(full, d))
            # One reviewer, so their own answers decide both votes: this is
            # practice, not a release.
            items, _ = chert.note_items(full, d, d, sanitized)
            spans, _outcome = chert.final_spans(full, items, {}, sanitized)
            finished[note.id] = chert._render(sanitized, spans, False)
            # What THIS PERSON took out, as opposed to what the engine did.
            # The planted identifiers only count when they are in here.
            own[note.id] = [sanitized[sp["start"]:sp["end"]]
                            for sp in spans if sp.get("layer") == "review"]
        m = pyrite.mark(finished, unanswered=unanswered, own=own)
        if m.passed:
            acc["badge"] = pyrite.badge(m, today=self._today())
            acc.pop("certified", None)             # the badge is the record; the old flag is dropped
            self._log("practice_passed", s.uid, {"planted_found": len(m.planted_found),
                                                 "over_removed": len(m.over_removed)})
        else:
            acc.setdefault("practice_tries", 0)
            acc["practice_tries"] += 1
            self._log("practice_not_passed", s.uid,
                      {"wrongly_kept": len(m.wrongly_kept),
                       "planted_found": len(m.planted_found), "unanswered": m.unanswered})
        self._save()
        self._active(s)
        out = m.public()
        out["badge"] = pyrite.badge_state(acc.get("badge"), today=self._today())
        return out

    # -- the tidy-up tool ------------------------------------------------------
    #
    # Works even before the app is set up: a practice can always reorder
    # files it already holds as redacted, so the order no longer matches an
    # appointment book. It checks the files first and refuses to reorder
    # anything that still looks identified, because a tidy folder must never
    # be mistaken for a redacted one. Once the app is set up it needs a
    # signed-in doctor or admin, so the work has a name against it in the
    # log; before setup it runs without one and is logged as such.

    def _tidy_caller(self, token):
        """Who may use the tidy-up tool.

        Signed in, it is the practice's own doctors and admins. On a computer
        with NO ACCOUNTS AT ALL it needs no session: before setup there is
        nobody to sign in as. It reads and copies the practice's own files on
        their own computer, takes nothing away, and is written to the
        security log either way."""
        if token is None and not self.doc["accounts"]:
            return None
        s = self._session(token, "tidy_look")
        self._need_role(s, "doctor", "admin")
        return s

    def op_tidy_look(self, a, token):
        """Read a folder and say whether its check allows it to be reordered.
        Counts only."""
        from inkguard import dolomite
        s = self._tidy_caller(token)
        try:
            found = dolomite.look(Path(a.get("folder") or ""))
        except dolomite.TidyError as e:
            raise ServiceError(e.code) from None
        if s is not None:
            self._active(s)
        self._log("tidy_looked", s.uid if s else None,
                  {"files": found.files, "with_identifiers": found.with_identifiers,
                   "unchecked": found.unchecked + found.unreadable,
                   "before_setup": s is None})
        return found.public()

    def op_tidy_run(self, a, token):
        """Copy the files into a new folder in a new order. The check runs
        again here: a folder can change between looking and tidying."""
        from inkguard import dolomite
        s = self._tidy_caller(token)
        try:
            out = dolomite.tidy(Path(a.get("folder") or ""), Path(a.get("out") or ""),
                                order=a.get("order") or "shuffled")
        except dolomite.TidyError as e:
            raise ServiceError(e.code) from None
        except OSError:
            raise ServiceError("TIDY-WRITE") from None
        if s is not None:
            self._active(s)
        self._log("tidied", s.uid if s else None,
                  {"files": out["files"], "order": out["order"], "before_setup": s is None})
        return out

    # -- settling a batch and taking the clean copies out ----------------------
    #
    # Both reviewers have signed off. Now the responsible clinician reads every
    # note that had a disagreement, decides it, answers the small-numbers
    # warnings, chooses the shapes, and signs. Only then do the clean copies
    # leave the pool — and the moment they are written and verified, the
    # pool's key is destroyed and everything else inside is deleted.

    def _doctor_of(self, bid: str, rec: dict, s) -> str:
        """Only a doctor or admin settles a batch, and only on a batch whose
        reviews are both in."""
        mine = self._account(s.uid)
        if not ({"doctor", "admin"} & set(mine["roles"])):
            raise ServiceError("ROLE-DENIED")
        if rec.get("closed") or rec.get("stage") not in ("ready_for_doctor", "settling"):
            raise ServiceError("EXPORT-NOT-SETTLED")
        return mine["name"]

    def _settle_state(self, bid: str, rec: dict) -> dict:
        """Every note, with what the clinician still has to decide."""
        from inkguard import granite, halite
        work = self._batch_work(bid)
        items = granite.load_items(work)
        rows, left = [], []
        for it in items:
            if not it.note_id:
                continue
            n = granite.load_note(work, it.note_id)
            if n.get("status") != "ready":
                rows.append({"note": it.note_id, "patient": n.get("patient", 0),
                             "status": "blocked", "to_decide": 0})
                continue
            d1 = granite.load_decision(work, "R1", it.note_id)
            d2 = granite.load_decision(work, "R2", it.note_id)
            b = halite.bucket(n, d1, d2)
            ruled = (rec.get("rulings") or {}).get(it.note_id) or {}
            undecided = [i for i in b if ruled.get(i["key"]) not in ("keep", "remove")]
            rows.append({"note": it.note_id, "patient": n.get("patient", 0), "status": "ready",
                         "pages": n.get("pages", 1), "to_decide": len(undecided),
                         "decided": len(b) - len(undecided), "items": len(b)})
            if undecided:
                left.append(it.note_id)
        return {"work": work, "items": items, "notes": rows, "left": left}

    def op_settle_open(self, a, token):
        """The clinician's own screen: what is left to decide in this batch."""
        from inkguard import halite
        s = self._session(token, "settle_open")
        bid, rec = self._batch(a)
        name = self._doctor_of(bid, rec, s)
        self._batch_mine(s, bid, rec)
        s.pool_open = True
        st = self._settle_state(bid, rec)
        if rec.get("stage") == "ready_for_doctor":
            self._set_stage(bid, rec, "settling")
            self._save()
        self._active(s)
        return {"batch": bid, "doctor": name, "notes": st["notes"], "left": st["left"],
                "to_decide": sum(r["to_decide"] for r in st["notes"]),
                "attestation": halite.DOCTOR_ATTESTATION,
                "shapes": [{"id": k, "label": v} for k, v in halite.SHAPES.items()],
                "arrangements": [{"id": k, "label": halite._ARRANGEMENT_SAID[k]}
                                 for k in halite.ARRANGEMENTS],
                "days_left": self._batch_days_left(rec)}

    def op_settle_note(self, a, token):
        """One note as the clinician reads it: the whole note, and each item
        the two reviewers did not agree on, with both of their answers."""
        from inkguard import granite, halite
        s = self._session(token, "settle_note")
        bid, rec = self._batch(a)
        self._doctor_of(bid, rec, s)
        self._batch_mine(s, bid, rec)
        s.pool_open = True
        work = self._batch_work(bid)
        nid = a.get("note")
        items = granite.load_items(work)
        nids = [i.note_id for i in items if i.note_id]
        if nid not in nids:
            raise ServiceError("REVIEW-NO-NOTE")
        n = granite.load_note(work, nid)
        if n.get("status") != "ready":
            return {"note": nid, "status": "blocked",
                    "block_reason": n.get("block_reason", "")}
        d1 = granite.load_decision(work, "R1", nid)
        d2 = granite.load_decision(work, "R2", nid)
        text = n.get("text", "")
        bucket = [dict(b, word=text[b["start"]:b["end"]],
                       guess=REVIEW_GUESSES.get(b.get("entity"), "something that could "
                                                "identify someone"))
                  for b in halite.bucket(n, d1, d2, text)]
        self._active(s)
        return {"note": nid, "status": "ready", "place": nids.index(nid) + 1, "of": len(nids),
                "patient": n.get("patient"), "text": text,
                "removals": n.get("removals", []), "generalized": n.get("generalized", []),
                "bucket": bucket, "rare": n.get("rare", []),
                "ruled": (rec.get("rulings") or {}).get(nid) or {}}

    def op_settle_rule(self, a, token):
        """Save the clinician's decisions on one note. Saved as they go, so a
        locked screen or a closed window never loses the work."""
        from inkguard import granite, halite
        s = self._session(token, "settle_rule")
        bid, rec = self._batch(a)
        self._doctor_of(bid, rec, s)
        self._batch_mine(s, bid, rec)
        work = self._batch_work(bid)
        nid = a.get("note")
        given = a.get("rulings")
        if not isinstance(given, dict):
            raise ServiceError("APP-BAD-REQUEST")
        n = granite.load_note(work, nid)
        d1 = granite.load_decision(work, "R1", nid)
        d2 = granite.load_decision(work, "R2", nid)
        keys = {b["key"]: b for b in halite.bucket(n, d1, d2, n.get("text", ""))}
        out = dict((rec.setdefault("rulings", {}).get(nid) or {}))
        for key, fate in given.items():
            if key not in keys:
                raise ServiceError("APP-BAD-REQUEST")
            if fate not in ("keep", "remove"):
                raise ServiceError("APP-BAD-REQUEST")
            if fate == "keep" and keys[key]["locked"]:
                # Both reviewers removed it, or the tool marked it as certain.
                # Nobody can put it back, the clinician included.
                raise ServiceError("EXPORT-LOCKED")
            out[key] = fate
        rec.setdefault("rulings", {})[nid] = out
        rec["settled_seconds"] = float(rec.get("settled_seconds") or 0) + float(
            min(max(a.get("seconds") or 0, 0), 3600))
        self._active(s)
        self._save()
        left = [k for k in keys if out.get(k) not in ("keep", "remove")]
        return {"note": nid, "left": len(left), "decided": len(keys) - len(left)}

    def op_settle_check(self, a, token):
        """The small-numbers check over the whole batch, once every
        disagreement is decided. Nothing has left the pool at this point."""
        from inkguard import halite
        s = self._session(token, "settle_check")
        bid, rec = self._batch(a)
        self._doctor_of(bid, rec, s)
        self._batch_mine(s, bid, rec)
        s.pool_open = True
        settled, trouble = self._settle_all(bid, rec)
        warn_at = int(self.doc["settings"].get("small_numbers_warn_at", halite.DEFAULT_WARN_AT))
        check = halite.small_numbers(settled, warn_at=warn_at)
        rec["small_numbers"] = dict(check)         # note ids, counts and labels only
        self._save()
        self._active(s)
        return {"batch": bid, **check, "held": trouble,
                "ready": sum(1 for x in settled if x.status == "ready"),
                "rulings": ["generalize", "withhold", "accept"],
                "ruled": rec.get("small_rulings") or {}}

    def _settle_all(self, bid: str, rec: dict):
        """Every note settled with both reviews and the clinician's rulings.
        Raises EXPORT-NOT-SETTLED if anything is still undecided."""
        from inkguard import chert, granite, halite
        work = self._batch_work(bid)
        items = granite.load_items(work)
        lds = rec.get("profile") == "limited_data_set"
        out, trouble = [], []
        for it in items:
            if not it.note_id:
                continue
            n = granite.load_note(work, it.note_id)
            d1 = granite.load_decision(work, "R1", it.note_id)
            d2 = granite.load_decision(work, "R2", it.note_id)
            dn = {"rulings": (rec.get("rulings") or {}).get(it.note_id) or {}}
            try:
                s = halite.settle_note(n, d1, d2, dn, lds=lds)
            except chert.ReviewError:
                raise ServiceError("EXPORT-NOT-SETTLED") from None
            if s.status != "ready":
                trouble.append({"id": s.id, "reason": s.reason})
            out.append(s)
        return out, trouble

    def op_settle_small(self, a, token):
        """The clinician's ruling on each small-numbers warning."""
        from inkguard import halite
        s = self._session(token, "settle_small")
        bid, rec = self._batch(a)
        self._doctor_of(bid, rec, s)
        self._batch_mine(s, bid, rec)
        given = a.get("rulings")
        if not isinstance(given, dict):
            raise ServiceError("APP-BAD-REQUEST")
        out = dict(rec.get("small_rulings") or {})
        # A warning is about a CONDITION, and the condition sits in every
        # note the check listed. A ruling given for one of them is recorded
        # for all of them: holding back one note of a patient who is singled
        # out by a condition in three of their notes would do nothing.
        sn = rec.get("small_numbers") or {}
        groups = ([row["notes"] for row in (sn.get("odd_one_out") or [])]
                  + [row["notes"] for row in (sn.get("rare_in_world") or [])])
        for nid, r in given.items():
            if r not in halite.RULINGS:
                raise ServiceError("EXPORT-RULING")
            for group in groups:
                if nid in group:
                    for other in group:
                        out[other] = r
                    break
            else:
                out[nid] = r
        rec["small_rulings"] = out
        self._save()
        self._active(s)
        return {"ruled": out}

    def op_export(self, a, token):
        """Write the chosen shapes, verify them, then empty the pool.

        Runs in the background: a big batch takes minutes, and the window
        must keep answering. Progress is a stage name only — the screen
        shows motion, not a commentary."""
        from inkguard import halite
        s = self._session(token, "export")
        bid, rec = self._batch(a)
        name = self._doctor_of(bid, rec, s)
        self._batch_mine(s, bid, rec)
        if a.get("attest") is not True:
            raise ServiceError("EXPORT-NOT-ATTESTED")
        shapes = [x for x in (a.get("shapes") or []) if x in halite.SHAPES]
        if not shapes:
            raise ServiceError("EXPORT-NO-SHAPE")
        arrangement = a.get("arrangement") or "shuffled"
        if arrangement not in halite.ARRANGEMENTS:
            raise ServiceError("EXPORT-ARRANGEMENT")
        # How removals look in the FINAL files: typed ([NAME], [DATE]) or
        # opaque (every one [REMOVED]). Chosen here, at
        # release, because only signed-off files may be opaque.
        markers = a.get("markers") or "typed"
        if markers not in ("typed", "opaque"):
            raise ServiceError("EXPORT-ARRANGEMENT")
        out_dir = a.get("out") or ""
        if not out_dir:
            raise ServiceError("RUN-OUTPUT-WRITE")
        # Everything is decided before anything is written, and the small
        # numbers must have been looked at: a warning nobody answered stops
        # the export rather than leaving quietly.
        settled, _trouble = self._settle_all(bid, rec)
        check = halite.small_numbers(
            settled, warn_at=int(self.doc["settings"].get("small_numbers_warn_at",
                                                          halite.DEFAULT_WARN_AT)))
        ruled = rec.get("small_rulings") or {}
        # The report states the check that was ENFORCED, so it is stored
        # again here rather than left as it was when the clinician first
        # looked.
        rec["small_numbers"] = dict(check)
        for row in check["odd_one_out"] + check["rare_in_world"]:
            if not any(ruled.get(nid) for nid in row["notes"]):
                raise ServiceError("EXPORT-SMALL-NUMBERS")
        if self._export_running:
            raise ServiceError("BATCH-BUSY")
        # The folder is checked HERE, while the clinician is looking at the
        # screen: a refusal they only see minutes later, after the work has
        # gone to a background thread, is a refusal they cannot act on.
        work = self._batch_work(bid)
        came_from = (work.get("folder") or {}).get("path") or ""
        try:
            halite.check_destination(Path(came_from), Path(out_dir))
        except halite.ExportError as e:
            raise ServiceError(e.code) from None
        rec["export"] = {"stage": "working", "by": s.uid, "doctor": name,
                         "shapes": shapes, "arrangement": arrangement, "markers": markers,
                         "started": _now_iso(), "out": None, "error": None}
        rec["signed_off_by_doctor"] = {"by": s.uid, "name": name, "at": _now_iso(),
                                       "attestation": halite.DOCTOR_ATTESTATION}
        self._set_stage(bid, rec, "exporting")
        self._log("export_started", s.uid, {"batch": bid, "shapes": shapes,
                                            "arrangement": arrangement})
        self._save()
        self._export_running = bid
        t = threading.Thread(target=self._export_work,
                             args=(bid, Path(out_dir), shapes, arrangement, s.uid, markers),
                             daemon=True)
        t.start()
        self._export_thread = t
        return {"started": True, "batch": bid}

    def op_export_status(self, a, token):
        """Where the export has got to. A stage, never a running count."""
        s = self._session(token, "export_status")
        bid, rec = self._batch(a)
        mine = self._account(s.uid)
        if not ({"doctor", "admin"} & set(mine["roles"])):
            raise ServiceError("ROLE-DENIED")      # the release is the clinician's own business
        self._batch_mine(s, bid, rec)
        e = dict(rec.get("export") or {})
        e.pop("by", None)
        self._active(s)
        return {"batch": bid, "stage": rec.get("stage"), "export": e}

    def _export_work(self, bid: str, out_dir, shapes, arrangement, uid: str,
                     markers: str = "typed") -> None:
        from inkguard import contract
        with contract.opaque_scope(markers == "opaque"):
            return self._export_work_inner(bid, out_dir, shapes, arrangement, uid, markers)

    def _export_work_inner(self, bid: str, out_dir, shapes, arrangement, uid: str,
                           markers: str) -> None:
        from inkguard import granite, halite
        try:
            with self._mutex:
                rec = self.doc.get("batches", {}).get(bid)
                if rec is None:
                    return
                work = self._batch_work(bid)
                folder = (work.get("folder") or {}).get("path") or ""
                profile = rec.get("profile", "safe_harbor")
                settled, trouble = self._settle_all(bid, rec)
                halite.apply_small_numbers(settled, rec.get("small_rulings") or {})
                items = granite.load_items(work)
                facts = self._export_facts(bid, rec, settled, trouble)
                facts["markers"] = markers
            result = halite.export(Path(folder), items, settled, shapes=shapes,
                                   arrangement=arrangement, out_dir=Path(out_dir),
                                   facts=facts, profile=profile,
                                   erase=lambda: self._erase_for_export(bid, uid))
        except ServiceError as e:
            self._export_failed(bid, e.code)
            return
        except halite.ExportError as e:
            self._export_failed(bid, e.code)
            return
        except Exception:                              # noqa: BLE001
            if os.environ.get("PHI_DEBUG_EXPORT"):
                import traceback; traceback.print_exc()
            self._export_failed(bid, "EXPORT-WRITE")
            return
        with self._mutex:
            self._export_running = None
            rec = self.doc.get("batches", {}).get(bid)
            if rec is not None:
                rec["export"] = {"stage": "done", "finished": _now_iso(),
                                 "shapes": shapes, "arrangement": arrangement, "markers": markers,
                                 "out": str(out_dir), "records": result["records"],
                                 "zips": {k: v["sha256"] for k, v in result["zips"].items()},
                                 "error": None}
                rec["stage"] = "signed_off"
                rec.pop("rulings", None)               # the decisions went into the report
                self._log("export_finished", uid,
                          {"batch": bid, "records": result["records"], "shapes": shapes,
                           "erased": result["erased"]})
                self._save()

    def _recover_exports(self) -> None:
        """An export stage with no thread behind it is an export the app did
        not live through — a power cut or a quit part way. Nothing was
        destroyed (the pool is emptied only after every shape is read back),
        so the batch goes back to the clinician to release again rather than
        sitting in a stage it can never leave."""
        for bid, rec in list(self.doc.get("batches", {}).items()):
            if rec.get("stage") == "exporting" and not rec.get("closed"):
                rec["stage"] = "settling"
                rec["export"] = {**(rec.get("export") or {}), "stage": "stopped",
                                 "error": "EXPORT-INTERRUPTED", "finished": _now_iso()}
                rec.pop("signed_off_by_doctor", None)
                self.log.append("export_interrupted", None, {"batch": bid})
        self._save()

    def _export_failed(self, bid: str, code: str) -> None:
        with self._mutex:
            self._export_running = None
            rec = self.doc.get("batches", {}).get(bid)
            if rec is None:
                return
            rec["export"] = {**(rec.get("export") or {}), "stage": "stopped", "error": code,
                             "finished": _now_iso()}
            # The signature went with the release that did not happen; the
            # clinician signs again when they release it.
            rec.pop("signed_off_by_doctor", None)
            # Nothing was destroyed: the batch goes back to the clinician so
            # they can try again.
            rec["stage"] = "settling"
            self._log("export_stopped", None, {"batch": bid, "code": code})
            self._save()

    def _erase_for_export(self, bid: str, uid: str) -> dict:
        """Called by halite once every shape is written and read back.

        This runs ON THE EXPORT THREAD, so it takes the service's lock: it
        rewrites the records and saves them, and a request being answered in
        the window does the same."""
        with self._mutex:
            out = self._erase_batch(bid, "its clean copies were taken out and the pool emptied",
                                    uid, keep_record=True)
        return {"files": out.get("files_removed", 0),
                "key_destroyed": _now_iso() if out.get("key_destroyed")
                else "could not be confirmed"}

    def _export_facts(self, bid: str, rec: dict, settled, trouble) -> dict:
        """Everything the review report states, gathered in one place. No
        patient text: the report names people, counts and decisions."""
        from inkguard import chert, contract, granite, halite
        people = self.doc.get("accounts", {})
        name = lambda u: (people.get(u) or {}).get("name") or "—"     # noqa: E731
        work = self._batch_work(bid)
        items = granite.load_items(work)
        c = granite.counts(items)
        prof = contract.profile_info(rec.get("profile", "safe_harbor"))
        ready = [x for x in settled if x.status == "ready"]
        d = {"engine_kept": 0, "bucket": 0, "bucket_removed": 0, "bucket_kept": 0,
             "marks": 0, "questions": 0, "spelling_slips": 0}
        # Spelling slips: each was a question both reviewers had to answer
        # before confirming the note; counted here so the
        # report records that they were seen.
        for x in settled:
            try:
                d["spelling_slips"] += sum(1 for q in granite.load_note(work, x.id).get("questions", [])
                                           if q.get("reason") == "spelling_slip")
            except granite.BatchError:
                pass
        guesses: dict[str, str] = {}
        for x in settled:
            for key, fate in (x.outcome or {}).items():
                if key.startswith("m"):
                    d["marks"] += fate == "remove"
                elif key.startswith("q"):
                    d["questions"] += 1
                else:
                    d["engine_kept"] += fate == "remove"
        for x in ready:
            for w in x.unmask:
                guesses.setdefault(w, "a word the tool was unsure about")
        for nid, ruled in (rec.get("rulings") or {}).items():
            for _k, fate in ruled.items():
                d["bucket"] += 1
                d["bucket_removed"] += fate == "remove"
                d["bucket_kept"] += fate == "keep"
        from inkguard import __version__
        responsible = next((x["name"] for x in people.values() if x.get("responsible")), None)
        return {
            "batch": bid, "app": "Inkguard", "version": __version__,
            "practice": (f"{self._practice()} — {responsible}" if responsible
                         else self._practice() or None),
            "doctor": (rec.get("signed_off_by_doctor") or {}).get("name") or "—",
            "people": rec.get("people") or 3,
            "reviewers": {slot: name(rec["reviewers"][i])
                          for i, slot in enumerate(granite.REVIEWER_SLOTS)
                          if i < len(rec.get("reviewers", []))},
            # The badge each reviewer held, as it stood when the batch ran.
            # Not a claim about them today: a claim about who did this work.
            "badges": {slot: (people.get(rec["reviewers"][i]) or {}).get("badge") or {}
                       for i, slot in enumerate(granite.REVIEWER_SLOTS)
                       if i < len(rec.get("reviewers", []))},
            "helpers": list(rec.get("helpers", [])),
            "points": [t for _k, t in self.CARE_POINTS],
            "reviewer_attestation": REVIEW_ATTESTATION,
            "profile": prof["profile"], "citation": prof["citation"],
            "counts": {"files": c["files"], "patients": c["patients"], "notes": len(settled),
                       "released": len(ready),
                       "held_by_tool": sum(1 for x in settled
                                           if x.status == "held" and not x.withheld_by_doctor),
                       "withheld": sum(1 for x in settled if x.withheld_by_doctor),
                       "blocked": sum(1 for x in settled if x.status == "blocked"),
                       "set_aside": c["set_aside"] + c["failed"] + c["system"]},
            "decisions": d, "guesses": guesses,
            "set_aside_rows": granite.set_aside_rows(items),
            "held_rows": trouble,
            "small_numbers": rec.get("small_numbers") or halite.small_numbers(settled),
            "small_rulings": rec.get("small_rulings") or {},
            "accuracy": granite.ACCURACY,
            "codes": dict(rec.get("codes") or {}),
        }

    # -- the work key and retiring a computer ------------------------------------
    @property
    def _work_dir(self) -> Path:
        return self.dir / "work"

    def _work_aead(self, create: bool):
        """The work key lives ONLY in the OS protected store — never in the
        records, the log or a backup — so destroying it makes every file
        written by work_put unreadable at once. Batches do not use it: each
        has its own key (see _batch_key)."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        acct = self._anchor_acct + "-work"
        try:
            raw = self._anchor.read(acct)
            if raw is None and create:
                raw = secrets.token_hex(32)
                self._anchor.write(acct, raw)
        except AnchorUnavailable:
            raise ServiceError("APP-STORE") from None
        if raw is None:
            raise ServiceError("APP-STORE")
        return AESGCM(bytes.fromhex(raw))

    def work_put(self, name: str, data: bytes) -> None:
        """Keep a piece of in-progress work, encrypted. `name` is an internal
        id (never a patient file name)."""
        if not name.isalnum() or len(name) > 64:
            raise ServiceError("APP-BAD-REQUEST")
        nonce = secrets.token_bytes(12)
        _write_private(self._work_dir / f"{name}.bin",
                       b"WORK1" + nonce + self._work_aead(True).encrypt(nonce, data, name.encode()))

    def work_get(self, name: str) -> bytes:
        try:
            raw = (self._work_dir / f"{name}.bin").read_bytes()
            return self._work_aead(False).decrypt(raw[5:17], raw[17:], name.encode())
        except ServiceError:
            raise
        except Exception:  # noqa: BLE001
            raise ServiceError("APP-STORE") from None

    def _crypto_erase(self, how: str, uid: str | None = None) -> dict:
        """Retire this computer: destroy the keys first (the work key, this
        computer's half of the records key and every batch key, so what was
        in progress can no longer be read), then delete the work files and
        the app's records. The security log stays (it holds no patient data,
        passwords or keys) and records the retirement. To use the app here
        again, restore from a backup."""
        self._stop_work.set()                          # a batch being prepared stops now
        worker = self._worker
        if worker is not None and worker.is_alive():
            self._mutex.release()                      # the worker needs it to finish its step
            try:
                worker.join(timeout=120)
            finally:
                self._mutex.acquire()
        key_destroyed = records_key_destroyed = True
        try:
            self._anchor.delete(self._anchor_acct + "-work")
        except AnchorUnavailable:
            key_destroyed = False
        try:
            self._anchor.delete(self._anchor_acct + "-records")   # copied-back records stay unreadable
        except AnchorUnavailable:
            records_key_destroyed = False
        batches_erased = 0
        self._pool_keys.clear()
        for bid in list(self.doc.get("batches", {})):
            try:
                self._anchor.delete(f"{self._anchor_acct}-batch-{bid}")
            except AnchorUnavailable:
                key_destroyed = False
            self.doc["batches"].pop(bid, None)
            batches_erased += 1
        removed = 0
        # The whole work area, batches and all: deepest first, so the
        # folders go too and nothing is left behind to puzzle over.
        if self._work_dir.exists():
            for f in sorted(self._work_dir.rglob("*"), reverse=True):
                try:
                    f.unlink() if f.is_file() else f.rmdir()
                    removed += 1
                except OSError:
                    pass
            try:
                self._work_dir.rmdir()
            except OSError:
                pass
        table = self.code_table_path()
        table_removed = False
        if table is not None and table.parent.resolve() == self.dir.resolve():
            # Only the copy kept inside the app's own folder; a table the
            # practice keeps elsewhere (a shared drive) is theirs to manage.
            for t in (table, table.with_name(table.name + ".seen")):
                try:
                    t.unlink(missing_ok=True)
                    table_removed = True
                except OSError:
                    pass
        self.log.append("device_retired", uid, {"how": how,
                                                "batches_erased": batches_erased,
                                                "work_key_destroyed": key_destroyed,
                                                "records_key_destroyed": records_key_destroyed,
                                                "code_table_removed": table_removed,
                                                "work_files_removed": removed})
        try:
            self.store.path.unlink(missing_ok=True)
        except OSError:
            pass
        self.sessions.clear()
        self._retired = True
        return {"retired": True, "work_key_destroyed": key_destroyed,
                "records_key_destroyed": records_key_destroyed, "work_files_removed": removed,
                "complete": key_destroyed and records_key_destroyed}

    def op_retire_here(self, a, token):
        """The "Retire this computer" button: admin password, phone code (if
        linked) and the word RETIRE typed out."""
        s = self._session(token, "retire_here")
        acc = self._need_role(s, "admin")
        self._reauth(s, acc, a.get("password"))
        self._phone_check(acc, s.uid, self._fail_id(_norm_name(acc["name"])), a.get("code"))
        if a.get("confirm") != "RETIRE":
            raise ServiceError("APP-BAD-REQUEST")
        return self._crypto_erase("retired on this computer by an admin", uid=s.uid)

    def op_retire_at_sign_in(self, a, token):
        """For a computer nobody can sign into: an admin's printed recovery
        code AND their linked phone's current code. Rate-limited, never
        disabled (same counter as the recovery code)."""
        norm = _norm_name(a.get("name"))
        fid = self._fail_id("recovery\x1f" + norm)
        uid = next((u for u, x in self.doc["accounts"].items() if _norm_name(x["name"]) == norm), None)
        acc = self.doc["accounts"].get(uid) if uid else None
        self._fail_check(fid, cap=False)
        code = a.get("recovery_code")
        code = code.strip() if isinstance(code, str) else ""
        ok = (acc is not None and "admin" in acc["roles"] and not acc.get("disabled")
              and (_check_secret(code, acc.get("recovery"))
                   or _check_secret(code.upper(), acc.get("recovery"))))
        if not ok:
            raise ServiceError(self._fail_record(fid, uid, "retire_failed", cap=False))
        self._phone_check(acc, uid, fid, a.get("code"), cap=False)   # if a phone is linked
        if a.get("confirm") != "RETIRE":
            raise ServiceError("APP-BAD-REQUEST")
        return self._crypto_erase("retired from the sign-in screen with the recovery code", uid=uid)

    # -- phone codes (authenticator app, RFC 6238) -------------------------------
    def op_phone_link_begin(self, a, token):
        """Start linking an authenticator app: re-enter the password; the
        screen shows the returned link as a QR code to scan. Nothing changes
        until the first code from the phone is confirmed."""
        from urllib.parse import quote
        s = self._session(token, "phone_link_begin")
        acc = self._account(s.uid)
        self._reauth(s, acc, a.get("password"))
        # Moving to a new phone needs the current phone's code (a lost phone
        # goes through an admin or the recovery code instead).
        self._phone_check(acc, s.uid, self._fail_id(_norm_name(acc["name"])), a.get("code"))
        secret = secrets.token_bytes(20)
        self._phone_setup[s.uid] = {"secret": secret, "until": self._clock() + PHONE_SETUP_S}
        self._active(s)
        b32 = _b32(secret)
        label = quote(f"{APP_DISPLAY_NAME}:{acc['name']}")
        return {"secret": b32, "digits": TOTP_DIGITS, "period": TOTP_STEP_S,
                "uri": f"otpauth://totp/{label}?secret={b32}&issuer={quote(APP_DISPLAY_NAME)}"
                       f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP_S}"}

    def op_phone_link_confirm(self, a, token):
        s = self._session(token, "phone_link_confirm")
        acc = self._account(s.uid)
        pend = self._phone_setup.get(s.uid)
        if not pend or self._clock() > pend["until"]:
            self._phone_setup.pop(s.uid, None)
            raise ServiceError("PHONE-LINK-EXPIRED")
        fid = self._fail_id(_norm_name(acc["name"]))
        self._fail_check(fid)
        step = _totp_match(pend["secret"], a.get("code"), self._wall(), -1)
        if step is None:
            raise ServiceError(self._fail_record(fid, s.uid, "phone_code_failed"))
        self._phone_setup.pop(s.uid, None)
        acc["totp"] = {"secret": pend["secret"].hex(), "last_step": step, "linked": _now_iso()}
        self._active(s)
        self._log("phone_linked", s.uid)
        return {"linked": True}

    def op_phone_unlink(self, a, token):
        """Remove a phone link: your own (password and current code), or —
        for an admin — someone else's who lost their phone (admin password)."""
        s = self._session(token, "phone_unlink")
        me = self._account(s.uid)
        target = a.get("id") if isinstance(a.get("id"), str) else s.uid
        if target != s.uid:
            self._need_role(s, "admin")
        acc = self.doc["accounts"].get(target)
        if not acc:
            raise ServiceError("APP-BAD-REQUEST")
        self._reauth(s, me, a.get("password"))
        if target == s.uid:
            self._phone_check(me, s.uid, self._fail_id(_norm_name(me["name"])), a.get("code"))
        if acc.pop("totp", None) is None:
            return {"removed": False}
        self._active(s)
        self._log("phone_link_removed", s.uid, {"account": target})
        return {"removed": True}


    # -- backup and the restore drill (the idea in HIPAA 164.308(a)(7)) -----
    @staticmethod
    def code_table_path() -> Path | None:
        """Where the pseudonym code table lives: the practice's chosen full
        path (PHI_CODE_TABLE) or the default in the user's app-data folder —
        the same place the batch tool uses."""
        table = os.getenv("PHI_CODE_TABLE", "").strip()
        if table:
            p = Path(table).expanduser()
            return p if p.is_absolute() else None
        saved = os.environ.pop("PHI_APP_DATA", None)       # the default ignores the store override
        try:
            return app_data_dir() / "code_table.json"
        finally:
            if saved is not None:
                os.environ["PHI_APP_DATA"] = saved

    def _backup_files(self) -> dict[str, Path]:
        # The records go in as their saved contents (records.json), protected by
        # the backup's own encryption: the backup must open on a NEW computer,
        # which never had this computer's half of the records key.
        files = {"security_log.jsonl": self.log.path}
        table = self.code_table_path()
        if table and table.exists():
            files["code_table.json"] = table
            seen = table.with_name(table.name + ".seen")
            if seen.exists():
                files["code_table.json.seen"] = seen
        return files

    def _backup_aead(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(self._backup_key)

    def op_backup_create(self, a, token):
        """Write one encrypted backup file to a folder the admin chooses (an
        external drive, say): the app's records, its security log and the
        pseudonym code table. Readable only with the practice's root key,
        which is NOT inside it and is backed up separately."""
        import io
        import zipfile
        s = self._session(token, "backup_create")
        self._need_role(s, "admin")
        folder = a.get("folder")
        dest = Path(folder) if isinstance(folder, str) and folder.strip() else None
        if dest is None or not dest.is_absolute() or not dest.is_dir():
            raise ServiceError("BACKUP-FAILED")
        try:
            dest.resolve().relative_to(self.dir.resolve())
            raise ServiceError("BACKUP-FAILED")      # a backup beside the original protects nothing
        except ValueError:
            pass
        files = self._backup_files()
        manifest = {"created": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "store_gen": self.doc.get("store_gen", 0), "log_entries": self.log.count,
                    "files": {}}
        buf = io.BytesIO()
        try:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                saved = self.store.load() or {}
                blobs = {"records.json": json.dumps(saved, sort_keys=True).encode(),
                         **{name: p.read_bytes() for name, p in files.items()}}
                for name, data in blobs.items():
                    manifest["files"][name] = hashlib.sha256(data).hexdigest()
                    z.writestr(name, data)
                z.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            nonce = secrets.token_bytes(12)
            blob = b"OPALBAK1" + nonce + self._backup_aead().encrypt(nonce, buf.getvalue(), b"opal-backup-v1")
            name = "backup-" + manifest["created"].replace(":", "").replace("-", "") + ".phibak"
            out = dest / name
            fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
        except ServiceError:
            raise
        except Exception:  # noqa: BLE001
            raise ServiceError("BACKUP-FAILED") from None
        digest = hashlib.sha256(blob).hexdigest()
        self._active(s)
        self._log("backup_created", s.uid, {"file": name, "sha256": digest,
                                                   "includes": sorted(manifest["files"])})
        return {"file": str(out), "sha256": digest, "includes": sorted(manifest["files"]),
                "code_table": "included" if "code_table.json" in files else "not in use"}

    def _open_backup(self, path) -> dict:
        """Decrypt a backup, check every file against its fingerprint and run
        the drill on it in memory — changing nothing: the records open under
        this practice's key, the log's chain holds, the two agree, and the
        code table (if included) verifies."""
        import io
        import tempfile
        import zipfile
        p = Path(path) if isinstance(path, str) and path.strip() else None
        try:
            raw = p.read_bytes() if p and p.is_absolute() and p.is_file() else b""
            if not raw.startswith(b"OPALBAK1"):
                raise ValueError
            plain = self._backup_aead().decrypt(raw[8:20], raw[20:], b"opal-backup-v1")
            with zipfile.ZipFile(io.BytesIO(plain)) as z:
                manifest = json.loads(z.read("manifest.json"))
                contents = {n: z.read(n) for n in manifest["files"]}
            for n, data in contents.items():
                if hashlib.sha256(data).hexdigest() != manifest["files"][n]:
                    raise ValueError
            doc = json.loads(contents["records.json"])
            if not isinstance(doc, dict) or doc.get("version") != 1:
                raise ValueError
            ok, macs, max_gen = _scan_log(contents["security_log.jsonl"], self._log_key)
        except Exception:  # noqa: BLE001 — damaged, foreign key, not a backup: one answer
            raise ServiceError("BACKUP-INVALID") from None
        n = doc.get("log_count", 0)
        if not (ok and len(macs) >= n and (n == 0 or macs[n - 1] == doc.get("log_head"))
                and max_gen <= doc.get("store_gen", 0)):
            raise ServiceError("BACKUP-INVALID")
        table = None
        if "code_table.json" in contents:
            from inkguard import contract
            with tempfile.TemporaryDirectory(prefix="restore-drill-") as tmp:
                t = Path(tmp) / "code_table.json"
                t.write_bytes(contents["code_table.json"])
                if contract.check_code_table(t):
                    raise ServiceError("BACKUP-INVALID")
            table = "verified"
        return {"path": p, "sha256": hashlib.sha256(raw).hexdigest(), "manifest": manifest,
                "contents": contents, "doc": doc, "log_entries": len(macs), "log_macs": macs,
                "code_table": table}

    def op_backup_verify(self, a, token):
        """The restore drill: check that a backup is complete and consistent
        without touching the live data. Putting a backup back INTO use is
        restore_preview / restore_confirm, open only when records are lost."""
        s = self._session(token, "backup_verify")
        self._need_role(s, "admin")
        b = self._open_backup(a.get("file"))
        result = {"created": b["manifest"]["created"], "includes": sorted(b["contents"]),
                  "accounts": len(b["doc"].get("accounts", {})), "log_entries": b["log_entries"],
                  "code_table": b["code_table"] or
                  "not in this backup (pseudonym mode not in use when it was made)"}
        self._active(s)
        self._log("backup_verified", s.uid, {"file": b["path"].name, "created": b["manifest"]["created"]})
        result["ok"] = True
        return result

    # -- restore: one official door, only for lost or damaged records -------
    def _restore_admin(self, b: dict, a: dict) -> tuple[str, dict]:
        """The person restoring proves they were an admin in the backup: their
        name, their password as it was then and, if their phone was linked,
        its current code. Rate-limited like sign-in, on its own counter."""
        norm = _norm_name(a.get("name"))
        fid = self._fail_id("restore\x1f" + norm)
        accounts = b["doc"].get("accounts", {})
        uid = next((u for u, x in accounts.items() if _norm_name(x.get("name")) == norm), None)
        acc = accounts.get(uid) if uid else None
        ok = _check_secret(a.get("password"), acc["pw"] if acc else None)
        if not ok or "admin" not in acc.get("roles", []) or acc.get("disabled") or acc.get("pw_disabled"):
            raise ServiceError(self._fail_record(fid, None, "restore_sign_in_failed", cap=False))
        step = self._phone_check(acc, None, fid, a.get("code"), cap=False)
        self._fail_clear(fid)
        return uid, step

    def _tightenings_since(self, b: dict) -> list[tuple[str, str]]:
        """Security tightenings this computer's surviving log recorded AFTER
        the backup was made (same chain only): accounts disabled, passwords
        disabled or changed, phone links removed. A restore never undoes them."""
        bm = b["log_macs"]
        if not bm or self.log.count < len(bm) or self.log.macs[:len(bm)] != bm:
            return []
        out = []
        try:
            lines = self.log.path.read_text(encoding="utf-8").splitlines()[len(bm):]
        except OSError:
            return []
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ev, d = rec.get("event"), rec.get("detail") or {}
            who = d.get("account") or rec.get("uid")
            if not isinstance(who, str):
                continue
            if ev == "account_disabled":
                out.append(("disabled", who))
            elif ev in ("password_disabled_after_failures", "password_changed", "password_reset",
                        "admin_recovered_with_code"):
                out.append(("password", who))
            elif ev == "phone_link_removed":
                out.append(("phone", who))
        return out

    def _restored_doc(self, b: dict, restorer: str) -> tuple[dict, list[str]]:
        """The backup's records, with what must never go backwards carried
        forward from this computer: guess counters, the save counter, and
        every security tightening recorded since the backup."""
        new = json.loads(json.dumps(b["doc"]))
        cur, notes = self.doc, []
        accounts = new.get("accounts", {})
        tightened = set()
        for kind, who in self._tightenings_since(b):
            acc = accounts.get(who)
            if not acc:
                continue
            if kind == "disabled":
                acc["disabled"] = True
            elif kind == "password" and who != restorer:
                acc["pw_disabled"] = True              # its old password must not come back to life
            elif kind == "phone":
                acc.pop("totp", None)
            tightened.add(acc.get("name", who))
        if tightened:
            notes.append("security changes made after the backup are kept (accounts disabled, "
                         "passwords needing an admin reset, phone links removed): "
                         + ", ".join(sorted(tightened)))
        fails = new.setdefault("failures", {})
        for fid, f in cur.get("failures", {}).items():
            if f.get("consec", 0) >= fails.get(fid, {}).get("consec", 0):
                fails[fid] = f
        new["install"], new["anchored"] = cur["install"], cur.get("anchored", False)
        lost = cur.get("records_lost")
        # After a loss the alert stays until an admin has looked at it.
        new["integrity_alert"] = ({"reasons": list(lost["reasons"]) + [
            f"restored from the backup made {b['manifest']['created']}"], "seen": _now_iso()}
            if lost else None)
        for k in ("records_lost",):
            new.pop(k, None)
        # A backup carries a batch's counts but never its key or its work, so
        # the batches in it cannot be opened here. They are not brought back;
        # the practice's own files are untouched and the work can be redone.
        dropped = len(new.pop("batches", {}) or {})
        new["batches"] = {}
        if dropped:
            notes.append(f"{dropped} batch(es) in that backup are not restored: their working "
                         "copies were erased with their keys. Start them again from your files.")
        new["store_gen"] = max(cur.get("store_gen", 0), new.get("store_gen", 0),
                               self.log.max_gen, self._anchor_gen or 0)
        new.setdefault("settings", {"idle_minutes": IDLE_DEFAULT_MIN})
        notes.append("everyone signs in with the password they had when the backup was made")
        return new, notes

    def _code_table_plan(self, b: dict) -> str:
        if b["code_table"] is None:
            return "not in this backup"
        from inkguard import contract
        path = self.code_table_path()
        if path is None:
            return "not restored: no code table location is set"
        if path.exists() and contract.check_code_table(path) is None:
            return "the current code table on this computer is sound and is kept"
        return "will be restored"

    def _restore_code_table(self, b: dict) -> None:
        from inkguard import contract
        try:
            contract.code_table_restore(self.code_table_path(), b["contents"]["code_table.json"])
        except Exception:  # noqa: BLE001
            raise ServiceError("BACKUP-INVALID") from None

    def op_restore_preview(self, a, token):
        """Step 1 of 2. Only when there are no records to protect (lost,
        damaged, or a new computer). Checks the backup, checks who is
        restoring, and shows exactly what will happen. Changes nothing."""
        if self.doc["accounts"]:
            raise ServiceError("RESTORE-NOT-NEEDED")
        self._fail_check(self._fail_id("restore\x1f" + _norm_name(a.get("name"))), cap=False)
        b = self._open_backup(a.get("file"))
        uid, step = self._restore_admin(b, a)
        new, notes = self._restored_doc(b, uid)
        confirm = secrets.token_urlsafe(24)
        self._pending_restore = {"confirm": confirm, "sha256": b["sha256"], "file": str(b["path"]),
                                 "uid": uid, "step": step,
                                 "until": self._clock() + RESTORE_CONFIRM_S}
        self._log("restore_previewed", None, {"file": b["path"].name, "backup_sha256": b["sha256"],
                                              "backup_created": b["manifest"]["created"]})
        active = [{"name": x["name"], "roles": list(x["roles"])}
                  for x in new["accounts"].values() if not x.get("disabled")]
        return {"confirm": confirm, "confirm_within_s": RESTORE_CONFIRM_S,
                "backup_created": b["manifest"]["created"], "accounts": active,
                "log_entries": b["log_entries"], "practice": (new.get("practice") or {}).get("name"),
                "code_table": self._code_table_plan(b), "notes": notes}

    def op_restore_confirm(self, a, token):
        """Step 2 of 2: put the previewed backup into use. The backup is read
        and checked again (it must be the same file, unchanged); the log being
        replaced is kept beside it as evidence; the restore is recorded."""
        pend, self._pending_restore = self._pending_restore, None     # single use
        given = a.get("confirm")
        if (not pend or not isinstance(given, str) or not hmac.compare_digest(given, pend["confirm"])
                or self._clock() > pend["until"]):
            raise ServiceError("RESTORE-EXPIRED")
        if self.doc["accounts"]:
            raise ServiceError("RESTORE-NOT-NEEDED")
        b = self._open_backup(pend["file"])
        if b["sha256"] != pend["sha256"]:
            raise ServiceError("RESTORE-EXPIRED")
        new, notes = self._restored_doc(b, pend["uid"])
        link = new["accounts"].get(pend["uid"], {}).get("totp")
        if link and pend["step"] is not None:
            link["last_step"] = max(link.get("last_step", -1), pend["step"])   # the preview's code is spent
        failed_attempts = sum(1 for e in self.log.tail(self.log.count or 1)
                              if e.get("event") == "restore_sign_in_failed")
        table = self._code_table_plan(b)
        if table == "will be restored":
            self._restore_code_table(b)                 # before the records: refusal changes nothing
            table = "restored"
        replaced = None
        if self.log.path.exists() and self.log.count:
            archive = self.log.path.with_name(f"security_log.replaced-{_stamp()}.jsonl")
            replaced = {"file": archive.name, "entries": self.log.count, "last_link": self.log.head[:16]}
            os.replace(self.log.path, archive)
        _write_private(self.log.path, b["contents"]["security_log.jsonl"])
        self.log = SecurityLog(self.log.path, self._log_key)
        lost = self.doc.get("records_lost")
        self.doc = new
        self.sessions.clear()
        self._fail_mono.clear()
        self._fail_last_mono.clear()
        self._phone_setup.clear()
        self.log.append("backup_restored", pend["uid"], {
            "file": b["path"].name, "backup_sha256": b["sha256"],
            "backup_created": b["manifest"]["created"],
            "backup_store_gen": b["doc"].get("store_gen", 0), "replaced_log": replaced,
            "records_lost": lost["reasons"] if lost else None, "code_table": table,
            "failed_restore_attempts_before": failed_attempts})
        self._save()
        return {"restored": True, "backup_created": b["manifest"]["created"],
                "code_table": table, "replaced_log": replaced["file"] if replaced else None,
                "notes": notes}

    def op_code_table_restore(self, a, token):
        """The records are sound but the pseudonym code table is lost or
        damaged: an admin (password, and phone code if linked) puts the
        backup's table back. Refused while the current table is sound."""
        s = self._session(token, "code_table_restore")
        acc = self._need_role(s, "admin")
        self._reauth(s, acc, a.get("password"))
        self._phone_check(acc, s.uid, self._fail_id(_norm_name(acc["name"])), a.get("code"))
        b = self._open_backup(a.get("file"))
        plan = self._code_table_plan(b)
        if plan != "will be restored":
            raise ServiceError("RESTORE-NOT-NEEDED" if "sound" in plan else "BACKUP-INVALID")
        self._restore_code_table(b)
        self._active(s)
        self._log("code_table_restored", s.uid, {"file": b["path"].name,
                                                 "backup_created": b["manifest"]["created"]})
        return {"restored": True}


_OPS = {name[3:]: fn for name, fn in vars(Service).items() if name.startswith("op_")}


def _reject_constant(name):
    raise ValueError(f"{name} is not allowed")


def serve(stdin=None, stdout=None, service: Service | None = None) -> int:
    """One JSON request per line in, one JSON response per line out. A line
    longer than the limit is refused without being held in memory."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        svc = service or Service()
    except ServiceError as e:
        stdout.write(json.dumps({"id": None, "ok": False, "error": {
            "code": e.code, "message": topaz.message(e.code)}}) + "\n")
        stdout.flush()
        return 1
    bad = {"id": None, "ok": False, "error": {"code": "APP-BAD-REQUEST",
                                               "message": topaz.message("APP-BAD-REQUEST")}}
    # Read raw bytes and decode each line as UTF-8 ourselves: on Windows a
    # piped stdin would otherwise be read in the local code page, so an
    # accented name could garble or stop the service.
    src = getattr(stdin, "buffer", stdin)
    nl = b"\n" if isinstance(src.read(0), bytes) else "\n"
    try:
        while True:
            line = src.readline(MAX_REQUEST_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_REQUEST_BYTES and not line.endswith(nl):
                while True:                           # discard the rest of the oversized line
                    rest = src.readline(MAX_REQUEST_BYTES + 1)
                    if not rest or rest.endswith(nl):
                        break
                resp = bad
            else:
                try:
                    text = line.decode("utf-8") if isinstance(line, bytes) else line
                    req = json.loads(text, parse_constant=_reject_constant)
                except (ValueError, RecursionError):   # includes a line that is not UTF-8
                    req = None
                resp = svc.handle(req)
            try:
                out = json.dumps(resp, allow_nan=False)
            except ValueError:
                out = json.dumps(bad)
            stdout.write(out + "\n")
            stdout.flush()
    finally:
        if service is None:
            svc.close()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
