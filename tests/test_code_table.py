"""The pseudonym code table (inkguard/onyx.py): random codes, never derived.

HIPAA's re-identification rule asks that a code be "not derived from or
related to information about the individual" (45 CFR 164.514(c)(1)). These
tests hold the design to that, and to what pseudonyms promise researchers:
one person, one code, every time; different people, different codes; nothing
about a person on disk; and a fail-closed STOP — never a silent fresh start,
never a fallback to name-derived codes — when the table is missing, older
than it should be, damaged, or under the wrong key. Each defect an
independent adversarial review found has a test here.
"""
from __future__ import annotations

import json
import os
import shutil
import threading

import pytest

from inkguard import contract, onyx
from inkguard.config import settings
from inkguard.policy import evaluate

KEY = b"k" * 32
NAMES = [f"name{i} surname{i}" for i in range(20)]


def new(tmp_path, name="t.json", key=KEY):
    return onyx.create_code_table(tmp_path / name, key)


@pytest.fixture
def engine_table(tmp_path, monkeypatch):
    """A clean table for one test, wired into the engine."""
    path = tmp_path / "engine_table.json"
    monkeypatch.setenv("PHI_CODE_TABLE", str(path))
    onyx._tables.clear()
    onyx.create_code_table(path, onyx.pseudonym_key())
    yield path
    onyx._tables.clear()


@pytest.fixture(autouse=True)
def _isolate_cache():
    yield
    onyx._tables.clear()


# ---- the code is not derived from the person ---------------------------

def test_same_person_gets_unrelated_codes_in_two_independent_tables(tmp_path):
    a, b = new(tmp_path, "a.json"), new(tmp_path, "b.json")
    same = sum(a.code_for("p:1", n) == b.code_for("p:1", n) for n in NAMES)
    assert same <= 1          # chance agreement is ~1 in 33 million per name


def test_codes_have_the_familiar_shape(tmp_path):
    code = new(tmp_path).code_for("p:1", "aisha haddad")
    assert len(code) == onyx.CODE_LEN and set(code) <= set(onyx._ALPHABET)


# ---- one person, one code, every time ----------------------------------

def test_same_person_same_code_within_a_table_and_after_a_restart(tmp_path):
    t1 = new(tmp_path)
    first = {n: t1.code_for("p:1", n) for n in NAMES}
    assert all(t1.code_for("p:1", n) == c for n, c in first.items())
    t2 = onyx.CodeTable(tmp_path / "t.json", KEY)          # a new process, same table
    assert all(t2.code_for("p:1", n) == c for n, c in first.items())


def test_different_scopes_and_people_never_share_a_code(tmp_path):
    t = new(tmp_path)
    a, b = t.code_for("p:1", "john smith"), t.code_for("p:2", "john smith")
    assert a != b
    codes = [t.code_for(f"p:{i % 7}", f"person {i}") for i in range(2000)]
    assert len(t) == 2002 and len(set(t._codes.values())) == len(t)
    assert a not in codes and b not in codes


def test_one_document_is_one_write(tmp_path):
    t = new(tmp_path)
    size0 = (tmp_path / "t.json").stat().st_size
    out = t.codes_for("p:1", ["a one", "b two", "c three"])
    assert len(set(out.values())) == 3
    lines = (tmp_path / "t.json").read_bytes()[size0:].count(b"\n")
    assert lines == 3


# ---- nothing about the person on disk ----------------------------------

def test_table_on_disk_holds_no_names(tmp_path):
    t = new(tmp_path)
    for n in ("aisha haddad", "tobias mbeki", "hani sharbatly"):
        t.code_for("p:1", n)
    raw = ((tmp_path / "t.json").read_text() + (tmp_path / "t.json.seen").read_text()).lower()
    for part in ("aisha", "haddad", "mbeki", "sharbatly", "p:1"):
        assert part not in raw


# ---- missing, older, damaged, foreign: STOP ----------------------------

def test_a_table_is_never_created_implicitly(tmp_path):
    with pytest.raises(onyx.CodeTableError, match="no code table has been set up"):
        onyx.CodeTable(tmp_path / "none.json", KEY)


def test_a_missing_table_that_existed_before_says_restore(tmp_path):
    new(tmp_path).code_for("p:1", "aisha haddad")
    os.remove(tmp_path / "t.json")
    with pytest.raises(onyx.CodeTableError, match="missing — restore"):
        onyx.CodeTable(tmp_path / "t.json", KEY)


def test_create_never_replaces_an_existing_table(tmp_path):
    new(tmp_path)
    with pytest.raises(onyx.CodeTableError, match="already exists"):
        new(tmp_path)


def test_an_older_table_is_refused_until_knowingly_accepted(tmp_path):
    t = new(tmp_path)
    for n in NAMES[:5]:
        t.code_for("p:1", n)
    shutil.copy(tmp_path / "t.json", tmp_path / "backup.json")
    for n in NAMES[5:]:
        t.code_for("p:1", n)
    shutil.copy(tmp_path / "backup.json", tmp_path / "t.json")   # restore a stale backup
    with pytest.raises(onyx.CodeTableError, match="older than the one this computer last used"):
        onyx.CodeTable(tmp_path / "t.json", KEY)
    info = onyx.accept_restored_table(KEY, tmp_path / "t.json")
    assert info["entries"] == 5
    assert len(onyx.CodeTable(tmp_path / "t.json", KEY)) == 5


def _rewrite(path, fn):
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join(fn(lines)))


def test_a_changed_code_is_refused(tmp_path):
    t = new(tmp_path)
    t.code_for("p:1", "aisha haddad")
    def change(lines):
        rec = json.loads(lines[1]); rec["c"] = "AAAAA"
        return [lines[0], json.dumps(rec) + "\n"]
    _rewrite(tmp_path / "t.json", change)
    with pytest.raises(onyx.CodeTableError, match="integrity"):
        onyx.CodeTable(tmp_path / "t.json", KEY)


def test_reordered_or_dropped_entries_are_refused(tmp_path):
    t = new(tmp_path)
    for n in NAMES[:3]:
        t.code_for("p:1", n)
    _rewrite(tmp_path / "t.json", lambda L: [L[0], L[2], L[1], L[3]])
    with pytest.raises(onyx.CodeTableError):
        onyx.CodeTable(tmp_path / "t.json", KEY)
    shutil.copy(tmp_path / "t.json", tmp_path / "x")
    _rewrite(tmp_path / "t.json", lambda L: [L[0], L[2], L[1], L[3]])   # put back
    _rewrite(tmp_path / "t.json", lambda L: [L[0], L[2], L[3]])         # drop the first entry
    with pytest.raises(onyx.CodeTableError):
        onyx.CodeTable(tmp_path / "t.json", KEY)


def test_table_made_under_another_key_is_refused(tmp_path):
    new(tmp_path).code_for("p:1", "aisha haddad")
    with pytest.raises(onyx.CodeTableError):
        onyx.CodeTable(tmp_path / "t.json", b"z" * 32)


def test_a_table_swapped_under_a_running_process_is_refused(tmp_path):
    live = new(tmp_path)
    live.code_for("p:1", "aisha haddad")
    other = new(tmp_path, "other.json")
    for n in NAMES:
        other.code_for("p:1", n)
    shutil.copy(tmp_path / "other.json", tmp_path / "t.json")
    with pytest.raises(onyx.CodeTableError):
        live.code_for("p:1", "someone new")


@pytest.mark.parametrize("junk", [b"[]\n", b"null\n", b"1\n", b'"x"\n', b"[" * 200_000 + b"\n", b"\xff\xfe\n"])
def test_malformed_tables_give_the_table_error_not_a_crash(tmp_path, junk):
    (tmp_path / "t.json").write_bytes(junk)
    with pytest.raises(onyx.CodeTableError):
        onyx.CodeTable(tmp_path / "t.json", KEY)


def test_an_absurdly_large_table_is_not_read(tmp_path, monkeypatch):
    new(tmp_path)
    monkeypatch.setattr(onyx, "MAX_TABLE_BYTES", 10)
    with pytest.raises(onyx.CodeTableError, match="larger"):
        onyx.CodeTable(tmp_path / "t.json", KEY)


def test_a_relative_location_is_refused(monkeypatch):
    monkeypatch.setenv("PHI_CODE_TABLE", "relative/table.json")
    with pytest.raises(onyx.CodeTableError, match="full path"):
        onyx.code_table(KEY)


# ---- crashes and failures never hand out an unsaved code ----------------

def test_a_torn_last_line_is_ignored_then_repaired(tmp_path):
    t = new(tmp_path)
    a = t.code_for("p:1", "aisha haddad")
    with open(tmp_path / "t.json", "ab") as f:
        f.write(b'{"i":"00')                                 # a crash mid-write
    t2 = onyx.CodeTable(tmp_path / "t.json", KEY)
    assert t2.code_for("p:1", "aisha haddad") == a
    t2.code_for("p:1", "new person")                         # next append repairs the tail
    assert len(onyx.CodeTable(tmp_path / "t.json", KEY)) == 2


def test_a_failed_save_hands_out_no_code(tmp_path, monkeypatch):
    t = new(tmp_path)
    real = os.fsync
    def boom(fd):
        raise MemoryError
    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(onyx.CodeTableError):
        t.code_for("p:1", "aisha haddad")
    monkeypatch.setattr(os, "fsync", real)
    # Whatever the next call returns is exactly what the file holds.
    c = t.code_for("p:1", "aisha haddad")
    assert onyx.CodeTable(tmp_path / "t.json", KEY).code_for("p:1", "aisha haddad") == c


def test_a_held_lock_times_out_instead_of_hanging(tmp_path, monkeypatch):
    t = new(tmp_path)
    monkeypatch.setattr(onyx, "LOCK_TIMEOUT_S", 0.2)
    fd = t._lock.acquire()                                   # another run holds the table
    try:
        with pytest.raises(onyx.CodeTableError, match="busy"):
            t.code_for("p:1", "x y")
    finally:
        onyx._FileLock.release(fd)


# ---- concurrency: two runs, or many threads, lose nothing ---------------

def test_one_shared_instance_is_thread_safe(tmp_path):
    t = new(tmp_path)
    errors, results = [], {}

    def work(k):
        try:
            for i in range(150):
                results[(k, i)] = t.code_for("p:1", f"t{k} n{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ths = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    [x.start() for x in ths]; [x.join() for x in ths]
    assert not errors
    final = onyx.CodeTable(tmp_path / "t.json", KEY)
    assert len(final) == 600 and len(set(final._codes.values())) == 600


def test_two_instances_lose_no_entry_and_agree(tmp_path):
    a = new(tmp_path)
    b = onyx.CodeTable(tmp_path / "t.json", KEY)
    shared = [f"shared {i}" for i in range(50)]
    out_a, out_b = {}, {}

    def work(t, out, own):
        for n in shared + own:
            out[n] = t.code_for("p:1", n)

    ta = threading.Thread(target=work, args=(a, out_a, [f"a {i}" for i in range(150)]))
    tb = threading.Thread(target=work, args=(b, out_b, [f"b {i}" for i in range(150)]))
    ta.start(); tb.start(); ta.join(); tb.join()
    final = onyx.CodeTable(tmp_path / "t.json", KEY)
    assert len(final) == 350
    assert all(out_a[n] == out_b[n] for n in shared)


# ---- the engine ---------------------------------------------------------

def test_engine_stops_when_the_table_is_tampered(engine_table):
    r = evaluate("Patient: Hani Sharbatly", patient_key="p001", redaction_mode="pseudonym")
    assert "[PERSON-" in r.clean_text
    def change(lines):
        rec = json.loads(lines[1]); rec["c"] = "CCCCC"
        return [lines[0], json.dumps(rec) + "\n"] + lines[2:]
    _rewrite(engine_table, change)
    onyx._tables.clear()
    r2 = evaluate("Patient: Hani Sharbatly", patient_key="p001", redaction_mode="pseudonym")
    assert r2.action.value == "blocked" and r2.clean_text == ""


def test_engine_stops_without_a_configured_table(monkeypatch):
    monkeypatch.delenv("PHI_CODE_TABLE", raising=False)
    onyx._tables.clear()
    r = evaluate("Patient: Hani Sharbatly", patient_key="p001", redaction_mode="pseudonym")
    assert r.action.value == "blocked" and r.clean_text == ""
    assert contract.pseudonym_ready() is not None


def test_mask_mode_needs_no_table(monkeypatch):
    monkeypatch.delenv("PHI_CODE_TABLE", raising=False)
    r = evaluate("Patient: Hani Sharbatly", patient_key="p001")
    assert r.action.value != "blocked" and "Sharbatly" not in r.clean_text


def test_health_check_rereads_the_table(engine_table):
    assert contract.pseudonym_ready() is None
    engine_table.write_text("garbage\n")
    assert contract.pseudonym_ready() is not None


# ---- name-derived "hash" codes never appear by accident -----------------

def test_a_global_hash_setting_cannot_override_an_explicit_mode(monkeypatch, engine_table):
    monkeypatch.setattr(settings, "redaction_mode", "hash")
    m = evaluate("Patient: Hani Sharbatly MRN: 99812", patient_key="p001", redaction_mode="mask")
    p = evaluate("Patient: Hani Sharbatly MRN: 99812", patient_key="p001", redaction_mode="pseudonym")
    import re
    for r in (m, p):
        assert not re.search(r"<[A-Z_]+_[0-9a-f]{8}>", r.clean_text), r.clean_text


def test_hash_mode_is_refused_without_an_expert_determination(monkeypatch):
    monkeypatch.delenv("PHI_ALLOW_DERIVED_CODES", raising=False)
    r = evaluate("Patient: Hani Sharbatly", redaction_mode="hash")
    assert r.action.value == "blocked" and r.clean_text == ""
    monkeypatch.setenv("PHI_ALLOW_DERIVED_CODES", "expert-determination")
    r2 = evaluate("Patient: Hani Sharbatly", redaction_mode="hash")
    assert "<PERSON_" in r2.clean_text and "Sharbatly" not in r2.clean_text


# ---- the batch tool says so once, with a support code -------------------

def _run_cli(tmp_path, monkeypatch, table, *extra):
    from inkguard import cli
    monkeypatch.setenv("PHI_CODE_TABLE", str(table))
    onyx._tables.clear()
    src = tmp_path / "in"
    src.mkdir(exist_ok=True)
    (src / "n.txt").write_text("Patient: Hani Sharbatly")
    return cli.main([str(src), "-o", str(tmp_path / "out"), "--names", "pseudonym", "-q", *extra])


def test_batch_tool_refuses_a_damaged_table_before_reading_any_file(tmp_path, monkeypatch):
    table = tmp_path / "t.json"
    table.write_text("[]\n")
    with pytest.raises(SystemExit) as exc:
        _run_cli(tmp_path, monkeypatch, table)
    assert "RUN-PSEUDONYM-SETUP" in str(exc.value)
    assert not any((tmp_path / "out").rglob("*.txt")) if (tmp_path / "out").exists() else True


def test_batch_tool_needs_explicit_first_time_setup(tmp_path, monkeypatch):
    table = tmp_path / "fresh" / "t.json"
    with pytest.raises(SystemExit) as exc:
        _run_cli(tmp_path, monkeypatch, table)
    assert "RUN-PSEUDONYM-SETUP" in str(exc.value) and "first-time" in str(exc.value)
    _run_cli(tmp_path, monkeypatch, table, "--new-code-table")
    rep = json.loads((tmp_path / "out" / ".inkguard" / "audit_trail.json").read_text())
    assert rep["names"]["code_table"]["entries"] >= 1
    assert "[PERSON-" in next((tmp_path / "out").rglob("*n.txt")).read_text()
