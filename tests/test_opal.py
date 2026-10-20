"""The app service (inkguard/opal.py): every safety rule is enforced by the
service itself, so a modified screen cannot get around it. Each defect an
independent adversarial review found has a test here."""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil

import pytest

from inkguard import opal
from inkguard.security import KeyStore

TODAY = dt.date(2026, 10, 5)
GOOD_PW = "blue heron at dawn"
PRACTICE = "Lakeside Family Practice"
SETUP = dict(name="Dr. Sam Rivera", password=GOOD_PW, practice=PRACTICE, attest_responsible=True)


@pytest.fixture(autouse=True)
def _fast_hashing(monkeypatch):
    """Tests use a cheap work factor; test_production_hashing_meets_owasp pins
    the real one."""
    monkeypatch.setattr(opal, "SCRYPT_N", 2 ** 10)




class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make(tmp_path, *, root=b"r" * 32, today=TODAY, clock=None, wall=None, folder="appdata",
         keychain="keychain"):
    return opal.Service(tmp_path / folder, keystore=KeyStore(root_key=root),
                        anchor=opal.FolderAnchor(tmp_path / keychain),
                        clock=clock or Clock(), wall=wall or Clock(), today=lambda: today)


#: The phone secret the batch fixtures link (tests/test_granite.link_phones).
#: Real accounts get a random one; a fixed one here lets a test sign in the
#: way a person with their phone in hand does.
FIXTURE_PHONE_SECRET = "ab" * 20


def phone_now(svc, secret: str = FIXTURE_PHONE_SECRET) -> str:
    """The code that phone would be showing right now."""
    return opal._totp_at(bytes.fromhex(secret), int(svc._wall() // opal.TOTP_STEP_S))


def call(svc, op, session=None, **args):
    resp = svc.handle({"id": 1, "op": op, "args": args, "session": session})
    # A fixture that linked a phone makes sign-in ask for the code, as it
    # should. Rather than spell that out at a hundred call sites, the helper
    # does what the person would: read the code off the phone and try again.
    # The clock is nudged a step first, because a code is good once (RFC 6238
    # replay protection) and fixtures sign in several times in a row.
    if (op == "sign_in" and not resp["ok"]
            and resp["error"]["code"] == "PHONE-CODE-NEEDED" and "code" not in args
            # ONLY for the fixture's own stub phone. A test that linked a
            # real one through the flow is testing the code itself, and this
            # helper must not answer for it.
            and any((acc.get("totp") or {}).get("secret") == FIXTURE_PHONE_SECRET
                    for acc in svc.doc.get("accounts", {}).values()
                    if acc.get("name") == args.get("name"))):
        wall = getattr(svc, "_wall", None)
        if isinstance(getattr(wall, "t", None), float):
            # Past any step this account has already used: a code is good
            # once, and a fixture may have signed in on this clock already.
            used = -1
            for acc in svc.doc.get("accounts", {}).values():
                if acc.get("name") == args.get("name"):
                    used = (acc.get("totp") or {}).get("last_step", -1)
            wall.t = max(wall.t + opal.TOTP_STEP_S, (used + 1) * opal.TOTP_STEP_S)
        return svc.handle({"id": 1, "op": op, "session": session,
                           "args": {**args, "code": phone_now(svc)}})
    return resp


def ok(resp):
    assert resp["ok"], resp
    return resp["result"]


def code(resp):
    assert not resp["ok"], resp
    return resp["error"]["code"]




def ready(tmp_path, **kw):
    svc = make(tmp_path, **kw)
    first = ok(call(svc, "setup_first_admin", **SETUP))
    svc.recovery = first["recovery_code"]
    tok = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    return svc, tok


def with_reviewer(svc, admin):
    ok(call(svc, "account_create", admin, name="Jordan Lee", roles=["reviewer"], password=GOOD_PW))


# ---- first run -----------------------------------------------------------------

def test_first_run_names_the_practice_and_an_attested_admin(tmp_path):
    svc = make(tmp_path)
    assert ok(call(svc, "status"))["state"] == "needs_setup"            # nothing set up yet
    no_attest = {k: v for k, v in SETUP.items() if k != "attest_responsible"}
    assert code(call(svc, "setup_first_admin", **no_attest)) == "ROLE-DENIED"
    assert code(call(svc, "setup_first_admin", **{**SETUP, "practice": ""})) == "APP-BAD-REQUEST"
    assert code(call(svc, "setup_first_admin", **{**SETUP, "password": "short"})) == "PASSWORD-WEAK"
    first = ok(call(svc, "setup_first_admin", **SETUP))
    assert len(first["recovery_code"]) == 27
    assert ok(call(svc, "status"))["state"] == "ready"
    assert code(call(svc, "setup_first_admin", **{**SETUP, "name": "Mallory"})) == "ROLE-DENIED"

def test_before_sign_in_status_shows_only_what_a_sign_in_screen_needs(tmp_path):
    svc, admin = ready(tmp_path)
    pre = ok(call(svc, "status"))
    assert pre["org"] == PRACTICE
    assert "integrity_detail" not in pre and "security_log_ok" not in pre
    assert "integrity_detail" in ok(call(svc, "status", admin))

# ---- sign-in, guessing, enumeration ----------------------------------------------

def test_wrong_name_and_wrong_password_look_the_same(tmp_path):
    svc, _ = ready(tmp_path)
    assert code(call(svc, "sign_in", name="Nobody", password=GOOD_PW)) == "AUTH-FAILED"
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password="wrong one!!")) == "AUTH-FAILED"
    ok(call(svc, "sign_in", name="  dr.  sam RIVERA ", password=GOOD_PW))


def test_unknown_names_are_paused_exactly_like_real_ones(tmp_path):
    svc, _ = ready(tmp_path)
    for name in ("Nobody Here", "Dr. Sam Rivera"):
        for _ in range(opal.THROTTLE_AFTER):
            assert code(call(svc, "sign_in", name=name, password="wrong guess!")) == "AUTH-FAILED"
        assert code(call(svc, "sign_in", name=name, password="wrong guess!")) == "AUTH-THROTTLED"


def test_guessing_is_paused_and_the_pause_survives_a_restart(tmp_path):
    wall = Clock()
    svc, _ = ready(tmp_path, wall=wall)
    for _ in range(opal.THROTTLE_AFTER):
        call(svc, "sign_in", name="Dr. Sam Rivera", password="wrong guess!")
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "AUTH-THROTTLED"
    svc.close()
    again = make(tmp_path, wall=wall)
    assert code(call(again, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "AUTH-THROTTLED"
    wall.t += opal.THROTTLE_BASE_S + 1
    ok(call(again, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))


def test_a_pause_cannot_be_skipped_by_moving_the_wall_clock(tmp_path):
    wall, clock = Clock(), Clock()
    svc, _ = ready(tmp_path, wall=wall, clock=clock)
    for _ in range(opal.THROTTLE_AFTER):
        call(svc, "sign_in", name="Dr. Sam Rivera", password="wrong guess!")
    wall.t += 3600 * 2                                       # clock pushed forward two hours
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "AUTH-THROTTLED"


def test_the_recovery_code_gets_the_admin_back_in_while_someone_keeps_guessing(tmp_path):
    svc, _ = ready(tmp_path)
    for _ in range(opal.THROTTLE_AFTER + 3):
        call(svc, "sign_in", name="Dr. Sam Rivera", password="wrong guess!")
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "AUTH-THROTTLED"
    assert code(call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code="AAAAAA-BBBBBB-CCCCCC-DDDDDD",
                     new_password="quiet orchard under snow")) == "AUTH-FAILED"
    new = ok(call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code=svc.recovery.lower(),
                  new_password="quiet orchard under snow"))["recovery_code"]
    assert new != svc.recovery
    tok = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password="quiet orchard under snow"))["session"]
    assert code(call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code=svc.recovery,
                     new_password="another long sentence here")) == "AUTH-FAILED"   # old code is spent
    events = [e["event"] for e in ok(call(svc, "security_log", tok))["events"]]
    assert "admin_recovered_with_code" in events


def test_a_reviewer_cannot_use_the_admin_recovery_path(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    assert code(call(svc, "recover_admin", name="Jordan Lee", recovery_code=svc.recovery,
                     new_password="quiet orchard under snow")) == "AUTH-FAILED"


def test_failures_fade_after_an_hour_of_quiet(tmp_path):
    wall, clock = Clock(), Clock()
    svc, _ = ready(tmp_path, wall=wall, clock=clock)
    for _ in range(opal.THROTTLE_AFTER - 1):
        call(svc, "sign_in", name="Dr. Sam Rivera", password="wrong guess!")
    wall.t += opal.FAILURE_DECAY_S + 1
    clock.t += opal.FAILURE_DECAY_S + 1
    for _ in range(opal.THROTTLE_AFTER - 1):                 # a fresh count, not a pause
        assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password="wrong guess!")) == "AUTH-FAILED"
    ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))


# ---- the idle lock is the service's, not the screen's ------------------------------

def test_idle_lock_refuses_work_until_unlocked(tmp_path):
    clock, wall = Clock(), Clock()
    svc, tok = ready(tmp_path, clock=clock, wall=wall)
    clock.t += 60 * opal.IDLE_DEFAULT_MIN + 1
    assert code(call(svc, "account_list", tok)) == "SESSION-LOCKED"
    assert ok(call(svc, "status", tok))["session_locked"] is True
    assert code(call(svc, "unlock", tok, password="not it at all")) == "AUTH-FAILED"
    ok(call(svc, "unlock", tok, password=GOOD_PW))
    ok(call(svc, "account_list", tok))


def test_a_sleeping_laptop_wakes_locked(tmp_path):
    clock, wall = Clock(), Clock()
    svc, tok = ready(tmp_path, clock=clock, wall=wall)
    wall.t += 3 * 3600                     # lid closed for 3 hours: the steady clock did not move
    assert code(call(svc, "account_list", tok)) == "SESSION-LOCKED"


def test_a_wall_clock_turned_back_locks_the_session(tmp_path):
    clock, wall = Clock(), Clock()
    svc, tok = ready(tmp_path, clock=clock, wall=wall)
    wall.t -= 60
    assert code(call(svc, "account_list", tok)) == "SESSION-LOCKED"


def test_status_polling_never_keeps_a_session_alive_but_real_actions_do(tmp_path):
    clock, wall = Clock(), Clock()
    svc, tok = ready(tmp_path, clock=clock, wall=wall)
    for _ in range(10):
        clock.t += 60; wall.t += 60
        call(svc, "status", tok)
    assert code(call(svc, "account_list", tok)) == "SESSION-LOCKED"
    svc2, tok2 = ready(tmp_path / "b", clock=clock, wall=wall)
    for _ in range(10):
        clock.t += 60; wall.t += 60
        ok(call(svc2, "touch", tok2))
    ok(call(svc2, "account_list", tok2))


def test_touch_cannot_reopen_a_locked_session(tmp_path):
    svc, tok = ready(tmp_path)
    ok(call(svc, "lock_now", tok))
    assert code(call(svc, "touch", tok)) == "SESSION-LOCKED"


def test_too_many_wrong_unlocks_end_the_session(tmp_path):
    svc, tok = ready(tmp_path)
    ok(call(svc, "lock_now", tok))
    for _ in range(opal.UNLOCK_TRIES - 1):
        assert code(call(svc, "unlock", tok, password="guess guess!")) == "AUTH-FAILED"
    assert code(call(svc, "unlock", tok, password="guess guess!")) == "SESSION-ENDED"
    assert code(call(svc, "unlock", tok, password=GOOD_PW)) == "SESSION-ENDED"


def test_a_pin_unlocks_but_weak_pins_are_refused(tmp_path):
    svc, tok = ready(tmp_path)
    assert code(call(svc, "set_pin", tok, pin="4826", password="wrong pass!!")) == "AUTH-FAILED"
    for weak in ("1111", "1234", "9876", "12a4", "123", "١٢٣٤٥"):
        assert code(call(svc, "set_pin", tok, pin=weak, password=GOOD_PW)) == "PASSWORD-WEAK"
    ok(call(svc, "set_pin", tok, pin="4826", password=GOOD_PW))
    ok(call(svc, "lock_now", tok))
    assert code(call(svc, "unlock", tok, pin="4825")) == "AUTH-FAILED"
    ok(call(svc, "unlock", tok, pin="4826"))


# ---- roles --------------------------------------------------------

def test_roles_are_enforced(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    rev = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    for op, args in (("account_create", dict(name="X Y", roles=["admin"], password=GOOD_PW)),
                     ("account_list", {}), ("settings_set", dict(idle_minutes=15)),
                     ("security_log", {}),
                     ("integrity_acknowledge", {}), ("recovery_code_new", dict(password=GOOD_PW))):
        assert code(call(svc, op, rev, **args)) == "ROLE-DENIED", op
    ok(call(svc, "account_create", admin, name="Priya Nand", roles=["reviewer"], password=GOOD_PW))
    ok(call(svc, "account_create", admin, name="Fourth Person", roles=["reviewer"], password=GOOD_PW))


def test_disabling_ends_sessions_and_the_last_admin_cannot_be_removed(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    rid = [a for a in ok(call(svc, "account_list", admin))["accounts"] if a["name"] == "Jordan Lee"][0]["id"]
    rev = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    ok(call(svc, "account_disable", admin, id=rid))
    assert code(call(svc, "touch", rev)) == "SESSION-ENDED"
    assert code(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW)) == "AUTH-FAILED"
    me = [a for a in ok(call(svc, "account_list", admin))["accounts"] if "admin" in a["roles"]][0]["id"]
    assert code(call(svc, "account_disable", admin, id=me)) == "ROLE-DENIED"


# ---- the store, the log, and the two vouching for each other ---------------------------

def test_store_holds_no_readable_names_or_passwords(tmp_path):
    svc, _ = ready(tmp_path)
    raw = (tmp_path / "appdata" / "app_store.bin").read_bytes()
    for s in (b"Rivera", b"heron", b"Lakeside"):
        assert s not in raw


def test_a_changed_store_or_a_different_root_key_is_refused(tmp_path):
    svc, _ = ready(tmp_path)
    svc.close()
    p = tmp_path / "appdata" / "app_store.bin"
    with pytest.raises(opal.ServiceError) as e:
        make(tmp_path, root=b"q" * 32)
    assert e.value.code == "APP-STORE"
    assert p.exists() and not list(p.parent.glob("app_store.damaged-*"))   # wrong key: nothing moved
    raw = bytearray(p.read_bytes())
    raw[-5] ^= 1
    p.write_bytes(bytes(raw))
    # Right key, damaged records: set aside as evidence; a restore, or
    # starting over as a new setup, gets the practice going again.
    again = make(tmp_path)
    assert list(p.parent.glob("app_store.damaged-*.bin"))
    st = ok(call(again, "status"))
    assert st["state"] == "needs_restore" and st["integrity_alert"] is True
    # Starting over is allowed and recorded: the old records stay set aside,
    # and their key half is not what a new setup uses.
    ok(call(again, "setup_first_admin", **{**SETUP, "name": "New Admin"}))
    assert any(e["event"] == "first_admin_created" and "started_over_after_loss" in (e.get("detail") or {})
               for e in again.log.tail(50))


def test_no_persistent_root_key_means_no_service():
    class Ephemeral:
        ephemeral = True
    with pytest.raises(opal.ServiceError) as e:
        opal.Service(keystore=Ephemeral())
    assert e.value.code == "APP-STORE"


def test_only_one_copy_can_run_at_a_time(tmp_path):
    svc = make(tmp_path)
    with pytest.raises(opal.ServiceError) as e:
        make(tmp_path)
    assert e.value.code == "APP-ALREADY-RUNNING"
    svc.close()
    make(tmp_path).close()


def test_restoring_an_older_copy_of_the_records_raises_an_alert(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    store = tmp_path / "appdata" / "app_store.bin"
    shutil.copy(store, tmp_path / "old_store.bin")
    rid = [a for a in ok(call(svc, "account_list", admin))["accounts"] if a["name"] == "Jordan Lee"][0]["id"]
    ok(call(svc, "account_disable", admin, id=rid))
    svc.close()
    shutil.copy(tmp_path / "old_store.bin", store)          # the "disabled" account comes back
    again = make(tmp_path)
    assert ok(call(again, "status"))["integrity_alert"] is True
    assert code(call(again, "sign_in", name="Jordan Lee", password=GOOD_PW)) == "APP-INTEGRITY"
    tok = ok(call(again, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    detail = ok(call(again, "status", tok))["integrity_detail"]
    assert "older" in " ".join(detail["reasons"])
    ok(call(again, "integrity_acknowledge", tok))
    again.close()
    third = make(tmp_path)
    assert ok(call(third, "status"))["integrity_alert"] is False     # raised once, not every start


@pytest.mark.parametrize("damage", ["delete", "truncate", "edit"])
def test_a_deleted_cut_or_edited_security_log_raises_an_alert(tmp_path, damage):
    svc, tok = ready(tmp_path)
    call(svc, "sign_in", name="Dr. Sam Rivera", password="wrong guess!")
    ok(call(svc, "lock_now", tok))
    svc.close()
    logp = tmp_path / "appdata" / "security_log.jsonl"
    lines = logp.read_text().splitlines()
    if damage == "delete":
        logp.unlink()
    elif damage == "truncate":
        logp.write_text("\n".join(lines[:-4]) + "\n")
    else:
        rec = json.loads(lines[2]); rec["event"] = "nothing_happened"
        lines[2] = json.dumps(rec, sort_keys=True)
        logp.write_text("\n".join(lines) + "\n")
    again = make(tmp_path)
    assert ok(call(again, "status"))["integrity_alert"] is True
    if damage == "edit":
        assert list((tmp_path / "appdata").glob("security_log.damaged-*.jsonl"))


def test_deleting_the_records_is_not_mistaken_for_a_fresh_install(tmp_path):
    svc, _ = ready(tmp_path)
    svc.close()
    (tmp_path / "appdata" / "app_store.bin").unlink()
    again = make(tmp_path)
    assert ok(call(again, "status"))["integrity_alert"] is True


def test_a_crash_mid_write_is_not_mistaken_for_tampering(tmp_path):
    svc, _ = ready(tmp_path)
    svc.close()
    logp = tmp_path / "appdata" / "security_log.jsonl"
    with open(logp, "a") as f:
        f.write('{"event": "torn')
    again = make(tmp_path)
    assert again.log.ok is True and ok(call(again, "status"))["integrity_alert"] is False
    assert "torn_line_trimmed" in logp.read_text()


def test_security_log_is_private_and_holds_no_secrets(tmp_path):
    svc, tok = ready(tmp_path)
    call(svc, "sign_in", name="Dr. Sam Rivera", password="wrong guess!")
    ok(call(svc, "lock_now", tok))
    ok(call(svc, "unlock", tok, password=GOOD_PW))
    logp = tmp_path / "appdata" / "security_log.jsonl"
    if os.name == "posix":
        assert logp.stat().st_mode & 0o077 == 0
    text = logp.read_text()
    assert "heron" not in text and "wrong guess" not in text and svc.recovery not in text
    events = [e["event"] for e in ok(call(svc, "security_log", tok))["events"]]
    for ev in ("signed_in", "sign_in_failed", "locked", "unlocked"):
        assert ev in events


# ---- the pipe protocol ---------------------------------------------------------------

def test_the_pipe_protocol_survives_hostile_input(tmp_path):
    svc = make(tmp_path)
    lines = ["not json\n", "[" * 100_000 + "\n",
             '{"id": NaN, "op": "status"}\n',
             json.dumps({"id": 7, "op": "drop_tables"}) + "\n",
             json.dumps({"id": 8, "op": "status", "args": []}) + "\n",
             "x" * (opal.MAX_REQUEST_BYTES + 10) + "\n",
             json.dumps({"id": 9, "op": "status"}) + "\n"]
    out = io.StringIO()
    opal.serve(io.StringIO("".join(lines)), out, service=svc)
    text = out.getvalue()
    resps = [json.loads(x) for x in text.splitlines()]
    assert len(resps) == 7 and "Traceback" not in text and "NaN" not in text
    assert all(r["error"]["code"] == "APP-BAD-REQUEST" for r in resps[:6])
    assert resps[6]["id"] == 9 and resps[6]["result"]["state"] == "needs_setup"


# ---- industry standards: NIST SP 800-63B-4 and OWASP ------------------------------------

def test_production_hashing_meets_owasp(monkeypatch):
    monkeypatch.undo()                                   # drop the tests' cheap setting
    assert (opal.SCRYPT_N, opal.SCRYPT_R, opal.SCRYPT_P) == (2 ** 17, 8, 1)
    rec = opal._hash_secret("a long enough passphrase")
    assert rec["n"] == 2 ** 17 and opal._check_secret("a long enough passphrase", rec)


def test_an_older_hash_is_upgraded_at_the_next_sign_in(tmp_path, monkeypatch):
    svc, _ = ready(tmp_path)                                  # hashed at the test's cheap setting
    uid = next(iter(svc.doc["accounts"]))
    assert svc.doc["accounts"][uid]["pw"]["n"] == 2 ** 10
    monkeypatch.setattr(opal, "SCRYPT_N", 2 ** 11)            # the standard is raised
    ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))
    assert svc.doc["accounts"][uid]["pw"]["n"] == 2 ** 11
    ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))   # still works after upgrade


@pytest.mark.parametrize("pw", [
    "fourteen chars",                       # under 15
    "passwordpassword",                     # breached list
    "123456789012345",                      # sequential
    "abcabcabcabcabcabc",                   # repeated unit
    "aaaaaaaaaaaaaaaaaa",                   # one character
    "sam rivera is great",                  # the person's name
    "lakeside forever and ever",            # the practice's name
    "my inkguard passphrase",               # the app's name
])
def test_nist_password_rules(tmp_path, pw):
    svc, admin = ready(tmp_path)
    assert code(call(svc, "change_password", admin, old=GOOD_PW, new=pw)) == "PASSWORD-WEAK"


def test_nist_allows_long_passphrases_and_no_composition_rules(tmp_path):
    svc, admin = ready(tmp_path)
    ok(call(svc, "change_password", admin, old=GOOD_PW, new="all lowercase words no digits"))
    ok(call(svc, "change_password", admin, old="all lowercase words no digits", new="x" * 3 + "y" * 2 + " " + "z" * 60 + "q w e r t y u"))


def _guess_until(svc, name, n, wall, clock):
    last = None
    for _ in range(n):
        r = call(svc, "sign_in", name=name, password="wrong guess!")
        last = r["error"]["code"]
        wall.t += opal.THROTTLE_MAX_S + 1                     # wait out each pause
        clock.t += opal.THROTTLE_MAX_S + 1
    return last


def test_one_hundred_consecutive_misses_disable_the_password(tmp_path):
    wall, clock = Clock(), Clock()
    svc, admin = ready(tmp_path, wall=wall, clock=clock)
    with_reviewer(svc, admin)
    assert _guess_until(svc, "Jordan Lee", opal.MAX_CONSECUTIVE_FAILURES, wall, clock) == "AUTH-DISABLED"
    assert code(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW)) == "AUTH-DISABLED"
    # Unknown names behave the same, so nothing reveals which names exist.
    assert _guess_until(svc, "Nobody Here", opal.MAX_CONSECUTIVE_FAILURES, wall, clock) == "AUTH-DISABLED"
    # The admin resets it (NIST: force a change on evidence of compromise).
    admin = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    rid = [a for a in ok(call(svc, "account_list", admin))["accounts"] if a["name"] == "Jordan Lee"][0]["id"]
    ok(call(svc, "reset_password", admin, id=rid, password="new kettle on the stove"))
    ok(call(svc, "sign_in", name="Jordan Lee", password="new kettle on the stove"))


def test_a_disabled_admin_password_is_recovered_with_the_recovery_code(tmp_path):
    wall, clock = Clock(), Clock()
    svc, _ = ready(tmp_path, wall=wall, clock=clock)
    _guess_until(svc, "Dr. Sam Rivera", opal.MAX_CONSECUTIVE_FAILURES, wall, clock)
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "AUTH-DISABLED"
    ok(call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code=svc.recovery,
            new_password="quiet orchard under snow"))
    ok(call(svc, "sign_in", name="Dr. Sam Rivera", password="quiet orchard under snow"))


# ---- a long run of real time ----------------------------------------

def test_thirty_days_of_real_use_keep_working(tmp_path):
    clock, wall = Clock(), Clock()
    day = [TODAY]
    svc = opal.Service(tmp_path / "appdata", keystore=KeyStore(root_key=b"r" * 32), clock=clock,
                       anchor=opal.FolderAnchor(tmp_path / "keychain"),
                       wall=wall, today=lambda: day[0])
    ok(call(svc, "setup_first_admin", **SETUP))
    tok = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    for hour in range(1, 30 * 24 + 1):                        # thirty days of real time
        clock.t += 3600; wall.t += 3600
        day[0] = TODAY + dt.timedelta(days=hour // 24)
        if not call(svc, "touch", tok)["ok"]:
            tok = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
        ok(call(svc, "account_list", tok))
    assert ok(call(svc, "status"))["state"] == "ready"


# ---- backup and the restore drill (HIPAA 164.308(a)(7)) -------------------------------------

def test_backup_and_restore_drill(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    drive = tmp_path / "usb"
    drive.mkdir()
    made = ok(call(svc, "backup_create", admin, folder=str(drive)))
    raw = open(made["file"], "rb").read()
    assert b"Rivera" not in raw and b"Lakeside" not in raw     # encrypted
    if os.name == "posix":
        assert os.stat(made["file"]).st_mode & 0o077 == 0
    drill = ok(call(svc, "backup_verify", admin, file=made["file"]))
    assert drill["ok"] and drill["accounts"] == 2 and drill["log_entries"] > 5
    assert "code_table" in drill and drill["code_table"] == "verified"
    events = [e["event"] for e in ok(call(svc, "security_log", admin))["events"]]
    assert "backup_created" in events and "backup_verified" in events


def test_a_damaged_or_foreign_backup_fails_the_drill(tmp_path):
    svc, admin = ready(tmp_path)
    drive = tmp_path / "usb"
    drive.mkdir()
    made = ok(call(svc, "backup_create", admin, folder=str(drive)))
    damaged = drive / "damaged.phibak"
    b = bytearray(open(made["file"], "rb").read()); b[-3] ^= 1
    damaged.write_bytes(bytes(b))
    assert code(call(svc, "backup_verify", admin, file=str(damaged))) == "BACKUP-INVALID"
    svc.close()
    other = make(tmp_path, root=b"z" * 32, folder="other")
    ok(call(other, "setup_first_admin", **SETUP))
    t = ok(call(other, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    assert code(call(other, "backup_verify", t, file=made["file"])) == "BACKUP-INVALID"


def test_a_backup_must_leave_the_apps_own_folder_and_only_admins_make_one(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    assert code(call(svc, "backup_create", admin, folder=str(tmp_path / "appdata"))) == "BACKUP-FAILED"
    assert code(call(svc, "backup_create", admin, folder="relative/path")) == "BACKUP-FAILED"
    assert code(call(svc, "backup_create", admin, folder=str(tmp_path / "missing"))) == "BACKUP-FAILED"
    rev = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    assert code(call(svc, "backup_create", rev, folder=str(tmp_path))) == "ROLE-DENIED"


# ---- controls-matrix audit findings (each one a test) -------------------------------

def test_guessing_recovery_codes_never_locks_out_the_admin(tmp_path):
    wall, clock = Clock(), Clock()
    svc, _ = ready(tmp_path, wall=wall, clock=clock)
    for _ in range(opal.MAX_CONSECUTIVE_FAILURES + 5):
        r = call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code="AAAAAA-BBBBBB-CCCCCC-DDDDDD",
                 new_password="quiet orchard under snow")
        assert r["error"]["code"] in ("AUTH-FAILED", "AUTH-THROTTLED")
        wall.t += opal.THROTTLE_MAX_S + 1; clock.t += opal.THROTTLE_MAX_S + 1
    ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))       # password not disabled
    ok(call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code=svc.recovery,
            new_password="quiet orchard under snow"))                     # the real code still works


def test_a_flood_of_made_up_names_cannot_reset_a_real_counter(tmp_path, monkeypatch):
    monkeypatch.setattr(opal, "MAX_FAILURE_ENTRIES", 20)
    wall, clock = Clock(), Clock()
    svc, _ = ready(tmp_path, wall=wall, clock=clock)
    for _ in range(opal.THROTTLE_AFTER):
        call(svc, "sign_in", name="Dr. Sam Rivera", password="wrong guess!")
    for i in range(60):
        call(svc, "sign_in", name=f"made up name {i}", password="wrong guess!")
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "AUTH-THROTTLED"


def test_cutting_recent_security_events_from_the_log_raises_an_alert(tmp_path):
    svc, tok = ready(tmp_path)
    ok(call(svc, "lock_now", tok))
    call(svc, "unlock", tok, password="guess guess!")
    call(svc, "unlock", tok, password="guess guess!")
    svc.close()
    logp = tmp_path / "appdata" / "security_log.jsonl"
    # Rewriting the chain needs the key, so an attacker can only cut the tail.
    full = logp.read_text().splitlines()
    cut = next(i for i, x in enumerate(full) if json.loads(x)["event"] == "unlock_failed")
    logp.write_text("\n".join(full[:cut]) + "\n")
    again = make(tmp_path)
    assert ok(call(again, "status"))["integrity_alert"] is True


def test_reentering_a_password_inside_a_session_is_limited_and_logged(tmp_path):
    svc, tok = ready(tmp_path)
    for _ in range(opal.THROTTLE_AFTER):
        assert code(call(svc, "set_pin", tok, pin="4826", password="wrong one, sorry")) == "AUTH-FAILED"
    assert code(call(svc, "set_pin", tok, pin="4826", password=GOOD_PW)) == "AUTH-THROTTLED"
    events = [e["event"] for e in ok(call(svc, "security_log", tok, last=200))["events"]]
    assert events.count("reauth_failed") == opal.THROTTLE_AFTER


def test_role_denials_are_logged(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    rev = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    call(svc, "account_list", rev)
    log = ok(call(svc, "security_log", admin, last=200))["events"]
    assert any(e["event"] == "role_denied" for e in log)

def test_a_symbol_passphrase_is_allowed_and_a_reset_clears_the_pin(tmp_path):
    svc, admin = ready(tmp_path)
    ok(call(svc, "change_password", admin, old=GOOD_PW, new="#!@%&*()[]{}<>?~"))
    with_reviewer(svc, admin)
    rev = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    ok(call(svc, "set_pin", rev, pin="4826", password=GOOD_PW))
    rid = [a for a in ok(call(svc, "account_list", admin))["accounts"] if a["name"] == "Jordan Lee"][0]["id"]
    ok(call(svc, "reset_password", admin, id=rid, password="new kettle on the stove"))
    acc = [a for a in ok(call(svc, "account_list", admin))["accounts"] if a["id"] == rid][0]
    assert acc["has_pin"] is False


def test_backup_says_plainly_when_no_code_table_is_in_use(tmp_path, monkeypatch):
    monkeypatch.setenv("PHI_CODE_TABLE", str(tmp_path / "no_table_here.json"))
    svc, admin = ready(tmp_path)
    drive = tmp_path / "usb"; drive.mkdir()
    made = ok(call(svc, "backup_create", admin, folder=str(drive)))
    assert made["code_table"] == "not in use"
    assert "not in this backup" in ok(call(svc, "backup_verify", admin, file=made["file"]))["code_table"]


def test_the_pipe_reads_utf8_whatever_the_computers_code_page(tmp_path):
    """Windows pipes are read in the local code page unless the service reads
    bytes itself; an accented name must never garble or stop the service."""
    svc = make(tmp_path)

    class Pipe:                                      # what a Windows text stdin would wrap
        def __init__(self, data):
            self.buffer = io.BytesIO(data)

        def read(self, n=-1):
            raise AssertionError("the service must read the raw bytes")

    lines = [json.dumps({"id": 1, "op": "status", "args": {"x": "Álvaro Zoë"}}, ensure_ascii=False).encode(),
             b"\xff\xfe not text", json.dumps({"id": 2, "op": "status"}).encode()]
    out = io.StringIO()
    opal.serve(stdin=Pipe(b"\n".join(lines) + b"\n"), stdout=out, service=svc)
    answers = [json.loads(x) for x in out.getvalue().splitlines()]
    assert answers[0]["ok"] and answers[1]["error"]["code"] == "APP-BAD-REQUEST" and answers[2]["ok"]


# ---------------------------------------------------------------------------
# The supported operating systems
# ---------------------------------------------------------------------------

def test_an_operating_system_older_than_the_app_supports_is_said_plainly(tmp_path):
    svc = make(tmp_path)
    assert opal.os_supported("win32", "10.0.19045")[0] is True
    assert opal.os_supported("win32", "6.2.9200")[0] is False       # Windows 8
    assert opal.os_supported("darwin", "13.4")[0] is True
    assert opal.os_supported("darwin", "10.15.7")[0] is False       # Catalina
    # An OS we have never heard of is allowed through and named, not refused.
    fine, what = opal.os_supported("plan9", "4")
    assert fine is True and "plan9" in what
    svc.close()


def test_the_first_screen_stops_on_an_unsupported_system(tmp_path, monkeypatch):
    svc = make(tmp_path)
    monkeypatch.setattr(opal, "os_supported", lambda *a, **k: (False, "Windows 6.2.9200"))
    st = ok(call(svc, "status"))
    assert st["state"] == "unsupported_os" and st["code"] == "APP-OS-UNSUPPORTED"
    assert "6.2.9200" in st["os"] and "Windows 10" in st["needs"]["windows"]
    svc.close()


# ---- a recovery passphrase the admin chooses ---------------------------------------------

def test_the_admin_may_choose_their_own_recovery_passphrase(tmp_path):
    svc = make(tmp_path)
    mine = "the old lighthouse keeps its own time"
    assert code(call(svc, "setup_first_admin", **{**SETUP, "recovery": GOOD_PW})) == "PASSWORD-WEAK"
    assert code(call(svc, "setup_first_admin", **{**SETUP, "recovery": "short"})) == "PASSWORD-WEAK"
    first = ok(call(svc, "setup_first_admin", **{**SETUP, "recovery": mine}))
    assert first["recovery_code"] is None                       # nothing to show: they chose it
    # It is matched exactly, and using it means choosing the next one.
    assert code(call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code=mine.upper(),
                     new_password="quiet orchard under snow")) == "AUTH-FAILED"
    again = "a second sentence kept in the safe"
    out = ok(call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code=mine,
                  new_password="quiet orchard under snow", new_recovery=again))
    assert out["recovery_code"] is None
    assert code(call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code=mine,
                     new_password="another fine long password")) == "AUTH-FAILED"   # the old one is spent
    log = (tmp_path / "appdata" / "security_log.jsonl").read_text()
    assert mine not in log and again not in log
