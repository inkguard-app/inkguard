"""Disaster recovery, the tamper counter and phone codes.

The counter closes the last known gap: putting BOTH the records and the
security log back from the same old copy used to look like an honest
restore. Now the only way back is the official restore door, which is open
only when records are lost or damaged, and everything else is caught."""
from __future__ import annotations

import datetime as dt
import json
import shutil

import pytest

from inkguard import contract, onyx, opal
from tests.test_opal import (GOOD_PW, TODAY, Clock, call, code, make, ok, ready, with_reviewer, SETUP)

NEW_PW = "quiet orchard under snow"


@pytest.fixture(autouse=True)
def _fast_hashing(monkeypatch):
    monkeypatch.setattr(opal, "SCRYPT_N", 2 ** 10)


def appdata(tmp_path):
    return tmp_path / "appdata"


def backup(svc, tok, tmp_path):
    drive = tmp_path / "usb"
    drive.mkdir(exist_ok=True)
    return ok(call(svc, "backup_create", tok, folder=str(drive)))["file"]


def events(tmp_path, folder="appdata"):
    return [json.loads(x)["event"] for x in (tmp_path / folder / "security_log.jsonl").read_text().splitlines()]


def phone_code(secret_b32: str, wall: float, step_offset: int = 0) -> str:
    import base64
    pad = "=" * (-len(secret_b32) % 8)
    return opal._totp_at(base64.b32decode(secret_b32 + pad), int(wall // opal.TOTP_STEP_S) + step_offset)


# ---- the tamper counter --------------------------------------------------------------------

def test_putting_back_records_and_log_together_is_caught(tmp_path):
    """The gap this release closes."""
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    old = tmp_path / "old"
    old.mkdir()
    for n in ("app_store.bin", "security_log.jsonl"):
        shutil.copy(appdata(tmp_path) / n, old / n)
    rid = [a for a in ok(call(svc, "account_list", admin))["accounts"] if a["name"] == "Jordan Lee"][0]["id"]
    ok(call(svc, "account_disable", admin, id=rid))
    svc.close()
    for n in ("app_store.bin", "security_log.jsonl"):        # both, from the same old copy
        shutil.copy(old / n, appdata(tmp_path) / n)
    again = make(tmp_path)
    assert ok(call(again, "status"))["integrity_alert"] is True
    assert code(call(again, "sign_in", name="Jordan Lee", password=GOOD_PW)) == "APP-INTEGRITY"
    tok = ok(call(again, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    st = ok(call(again, "status", tok))
    assert "older than this computer has seen" in " ".join(st["integrity_detail"]["reasons"])
    assert st["tamper_counter"] == "ok"
    ok(call(again, "integrity_acknowledge", tok))
    again.close()
    assert ok(call(make(tmp_path), "status"))["integrity_alert"] is False   # raised once


@pytest.mark.parametrize("damage", ["deleted", "edited", "other_computer"])
def test_a_missing_edited_or_foreign_counter_is_caught(tmp_path, damage):
    svc, _ = ready(tmp_path)
    svc.close()
    counter = next(c for c in (tmp_path / "keychain").glob("*.counter") if "-" not in c.stem)
    if damage == "deleted":
        counter.unlink()
        again = make(tmp_path)
        want = "missing"
    elif damage == "edited":
        parts = counter.read_text().split(".")
        parts[0] = str(int(parts[0]) + 1000)
        counter.write_text(".".join(parts))
        again = make(tmp_path)
        want = "edited"
    else:
        # Records copied by hand to another folder or computer: their key half
        # is not there, so they are never opened: they are set aside and a restore is needed.
        shutil.copytree(appdata(tmp_path), tmp_path / "copied")
        (tmp_path / "copied" / ".service.lock").unlink(missing_ok=True)
        again = make(tmp_path, folder="copied")
        st = ok(call(again, "status"))
        assert st["state"] == "needs_restore" and st["integrity_alert"] is True
        assert code(call(again, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "AUTH-FAILED"
        assert list((tmp_path / "copied").glob("app_store.damaged-*.bin"))
        return
    assert ok(call(again, "status"))["integrity_alert"] is True
    tok = ok(call(again, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    assert want in " ".join(ok(call(again, "status", tok))["integrity_detail"]["reasons"])


def test_an_unreachable_keychain_stops_the_app_and_changes_nothing(tmp_path):
    """This computer's half of the records key lives in the protected store:
    without it the records cannot be opened, so the app stops safely."""
    svc, _ = ready(tmp_path)
    svc.close()

    class Down:
        def read(self, account):
            raise opal.AnchorUnavailable()

        def write(self, account, value):
            raise opal.AnchorUnavailable()

    from inkguard.security import KeyStore
    with pytest.raises(opal.ServiceError) as e:
        opal.Service(appdata(tmp_path), keystore=KeyStore(root_key=b"r" * 32), anchor=Down(),
                     clock=Clock(), wall=Clock(), today=lambda: TODAY)
    assert e.value.code == "APP-STORE"
    assert (appdata(tmp_path) / "app_store.bin").exists()                 # nothing moved
    again = make(tmp_path)                                                # and nothing left locked
    assert ok(call(again, "status"))["integrity_alert"] is False




def test_records_whose_counter_was_never_planted_start_quietly(tmp_path):
    """The counter could not be written at first (the store was busy): it is
    planted at the next save without an alarm."""
    real = opal.FolderAnchor(tmp_path / "keychain")

    class CounterDown:
        def read(self, account):
            if "-" not in account:
                raise opal.AnchorUnavailable()
            return real.read(account)

        def write(self, account, value):
            if "-" not in account:
                raise opal.AnchorUnavailable()
            real.write(account, value)

        def delete(self, account):
            real.delete(account)

    from inkguard.security import KeyStore
    svc = opal.Service(appdata(tmp_path), keystore=KeyStore(root_key=b"r" * 32), anchor=CounterDown(),
                       clock=Clock(), wall=Clock(), today=lambda: TODAY)
    ok(call(svc, "setup_first_admin", **SETUP))
    assert svc.doc["anchored"] is False
    svc.close()
    again = make(tmp_path)
    assert ok(call(again, "status"))["integrity_alert"] is False
    ok(call(again, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))       # the next save plants it
    assert again.doc["anchored"] is True


# ---- the official restore door -------------------------------------------------------------

def test_the_restore_door_is_shut_while_the_records_are_sound(tmp_path):
    svc, admin = ready(tmp_path)
    f = backup(svc, admin, tmp_path)
    r = call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW)
    assert code(r) == "RESTORE-NOT-NEEDED"


def test_lost_records_come_back_through_the_official_door(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    f = backup(svc, admin, tmp_path)
    call(svc, "sign_in", name="Jordan Lee", password="a wrong guess here")
    svc.close()
    (appdata(tmp_path) / "app_store.bin").unlink()                       # the records file is lost
    svc = make(tmp_path)
    st = ok(call(svc, "status"))
    assert st["state"] == "needs_restore" and st["can_restore"] is True
    # Proving who is restoring: an admin in the backup, password as it was then.
    assert code(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera",
                     password="not the password")) == "AUTH-FAILED"
    assert code(call(svc, "restore_preview", file=f, name="Jordan Lee",
                     password=GOOD_PW)) == "AUTH-FAILED"                  # not an admin
    pre = ok(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW))
    assert {a["name"] for a in pre["accounts"]} == {"Dr. Sam Rivera", "Jordan Lee"}
    assert pre["notes"] and pre["confirm_within_s"] == opal.RESTORE_CONFIRM_S
    done = ok(call(svc, "restore_confirm", confirm=pre["confirm"]))
    assert done["restored"] is True and done["replaced_log"].startswith("security_log.replaced-")
    assert (appdata(tmp_path) / done["replaced_log"]).exists()          # kept as evidence
    assert "backup_restored" in events(tmp_path)
    tok = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    st = ok(call(svc, "status", tok))
    assert st["integrity_alert"] is True                                  # an admin looks at it
    assert "restored from the backup" in " ".join(st["integrity_detail"]["reasons"])
    ok(call(svc, "integrity_acknowledge", tok))
    svc.close()
    assert ok(call(make(tmp_path), "status"))["integrity_alert"] is False   # counter agrees


def test_a_damaged_records_file_is_set_aside_and_restored(tmp_path):
    svc, admin = ready(tmp_path)
    f = backup(svc, admin, tmp_path)
    svc.close()
    p = appdata(tmp_path) / "app_store.bin"
    p.write_bytes(b"ransomware was here")
    svc = make(tmp_path)
    assert list(appdata(tmp_path).glob("app_store.damaged-*.bin"))
    pre = ok(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW))
    ok(call(svc, "restore_confirm", confirm=pre["confirm"]))
    ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))


def test_a_new_computer_is_set_up_from_the_backup(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    f = backup(svc, admin, tmp_path)
    svc.close()
    new = make(tmp_path, folder="newpc", keychain="newpc-keychain")
    assert ok(call(new, "status"))["state"] == "needs_setup"
    pre = ok(call(new, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW))
    ok(call(new, "restore_confirm", confirm=pre["confirm"]))
    ok(call(new, "sign_in", name="Jordan Lee", password=GOOD_PW))
    assert ok(call(new, "status"))["state"] == "ready"


def test_a_restore_must_be_confirmed_in_time_once_with_the_same_file(tmp_path):
    clock = Clock()
    svc, admin = ready(tmp_path, clock=clock)
    f = backup(svc, admin, tmp_path)
    svc.close()
    (appdata(tmp_path) / "app_store.bin").unlink()
    svc = make(tmp_path, clock=clock)
    pre = ok(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW))
    clock.t += opal.RESTORE_CONFIRM_S + 1
    assert code(call(svc, "restore_confirm", confirm=pre["confirm"])) == "RESTORE-EXPIRED"
    pre = ok(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW))
    assert code(call(svc, "restore_confirm", confirm="guessed")) == "RESTORE-EXPIRED"
    assert code(call(svc, "restore_confirm", confirm=pre["confirm"])) == "RESTORE-EXPIRED"  # single use
    pre = ok(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW))
    raw = open(f, "rb").read()
    open(f, "wb").write(raw + b"\0")                                      # swapped after the preview
    assert code(call(svc, "restore_confirm", confirm=pre["confirm"])) in ("RESTORE-EXPIRED",
                                                                           "BACKUP-INVALID")


def test_without_a_backup_starting_over_is_allowed_and_flagged(tmp_path):
    svc, _ = ready(tmp_path)
    svc.close()
    (appdata(tmp_path) / "app_store.bin").unlink()
    (appdata(tmp_path) / "security_log.jsonl").unlink()                   # everything wiped
    svc = make(tmp_path)
    assert ok(call(svc, "status"))["state"] == "needs_restore"           # the counter remembers
    first = ok(call(svc, "setup_first_admin", **{**SETUP, "password": NEW_PW}))
    assert first["recovery_code"]
    tok = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=NEW_PW))["session"]
    assert ok(call(svc, "status", tok))["integrity_alert"] is True       # still shown to the admin


def test_a_backup_made_under_another_root_key_is_refused(tmp_path):
    svc, admin = ready(tmp_path)
    f = backup(svc, admin, tmp_path)
    svc.close()
    other_key = make(tmp_path, root=b"z" * 32, folder="otherkey", keychain="otherkey-chain")
    assert code(call(other_key, "restore_preview", file=f, name="Dr. Sam Rivera",
                     password=GOOD_PW)) == "BACKUP-INVALID"


def test_a_lost_code_table_is_restored_only_when_it_is_actually_lost(tmp_path, monkeypatch):
    table = tmp_path / "practice" / "code_table.json"
    table.parent.mkdir()
    monkeypatch.setenv("PHI_CODE_TABLE", str(table))
    contract.pseudonym_setup()
    svc, admin = ready(tmp_path)
    f = backup(svc, admin, tmp_path)
    assert code(call(svc, "code_table_restore", admin, file=f, password=GOOD_PW)) == "RESTORE-NOT-NEEDED"
    table.unlink()
    onyx._tables.clear()
    assert contract.check_code_table(table) is not None
    ok(call(svc, "code_table_restore", admin, file=f, password=GOOD_PW))
    assert contract.check_code_table(table) is None
    assert "code_table_restored" in events(tmp_path)


# ---- phone codes ---------------------------------------------------------------------------

def test_rfc6238_test_vector(monkeypatch):
    monkeypatch.setattr(opal, "TOTP_DIGITS", 8)
    assert opal._totp_at(b"12345678901234567890", 59 // 30) == "94287082"
    assert opal._totp_at(b"12345678901234567890", 1111111109 // 30) == "07081804"


def _link_phone(svc, tok, wall):
    start = ok(call(svc, "phone_link_begin", tok, password=GOOD_PW))
    assert start["uri"].startswith("otpauth://totp/") and "secret=" in start["uri"]
    assert code(call(svc, "phone_link_confirm", tok, code="000000")) in ("AUTH-FAILED",) \
        or phone_code(start["secret"], wall.t) == "000000"
    ok(call(svc, "phone_link_confirm", tok, code=phone_code(start["secret"], wall.t)))
    return start["secret"]


def test_a_linked_phone_is_needed_to_sign_in_and_codes_cannot_be_replayed(tmp_path):
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    secret = _link_phone(svc, admin, wall)
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "PHONE-CODE-NEEDED"
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW, code="12345")) == "AUTH-FAILED"
    wall.t += opal.TOTP_STEP_S
    c = phone_code(secret, wall.t)
    ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW, code=c))
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW, code=c)) == "AUTH-FAILED"
    wall.t += opal.TOTP_STEP_S
    ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW, code=phone_code(secret, wall.t)))
    st_admins = ok(call(svc, "status", admin))
    assert "admins_without_phone" not in st_admins or st_admins["admins_without_phone"] == []


def test_a_wrong_phone_code_counts_toward_the_same_guess_limits(tmp_path):
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    _link_phone(svc, admin, wall)
    seen = [code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW, code="999999"))
            for _ in range(opal.THROTTLE_AFTER)]
    assert seen[0] == "AUTH-FAILED" and seen[-1] == "AUTH-THROTTLED"      # same limits as passwords
    assert "phone_code_failed" in events(tmp_path)


def test_a_restore_needs_the_phone_code_of_a_linked_admin(tmp_path):
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    secret = _link_phone(svc, admin, wall)
    f = backup(svc, admin, tmp_path)
    svc.close()
    (appdata(tmp_path) / "app_store.bin").unlink()
    wall.t += opal.TOTP_STEP_S
    svc = make(tmp_path, wall=wall)
    assert code(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera",
                     password=GOOD_PW)) == "PHONE-CODE-NEEDED"
    pre = ok(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW,
                  code=phone_code(secret, wall.t)))
    ok(call(svc, "restore_confirm", confirm=pre["confirm"]))


def test_a_lost_phone_is_handled_by_the_recovery_code_or_an_admin(tmp_path):
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    _link_phone(svc, admin, wall)
    with_reviewer(svc, admin)
    rev = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    _link_phone(svc, rev, wall)
    rid = [a for a in ok(call(svc, "account_list", admin))["accounts"] if a["name"] == "Jordan Lee"][0]["id"]
    assert ok(call(svc, "phone_unlink", admin, id=rid, password=GOOD_PW))["removed"] is True
    ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))                      # no code now
    ok(call(svc, "recover_admin", name="Dr. Sam Rivera", recovery_code=svc.recovery, new_password=NEW_PW))
    ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=NEW_PW))                   # link removed
    ev = events(tmp_path)
    assert ev.count("phone_link_removed") == 2 and ev.count("phone_linked") == 2


def test_the_phone_secret_is_never_stored_readably_or_logged(tmp_path):
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    secret = _link_phone(svc, admin, wall)
    import base64
    raw_secret = base64.b32decode(secret + "=" * (-len(secret) % 8))
    for f in ("app_store.bin", "security_log.jsonl"):
        data = (appdata(tmp_path) / f).read_bytes()
        assert raw_secret.hex().encode() not in data and secret.encode() not in data


# ---- the doctor's oversight at sign-in ----------------------------------------------------

def test_the_doctor_sees_what_happened_since_their_last_sign_in(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    for _ in range(3):
        call(svc, "sign_in", name="Jordan Lee", password="a wrong guess here")
    backup(svc, admin, tmp_path)
    again = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))
    counts = again["since_last_sign_in"]["counts"]
    assert counts["failed sign-ins"] == 3 and counts["backups made"] == 1
    assert counts["accounts created"] == 1
    rev = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))
    assert "since_last_sign_in" not in rev                                # reviewers don't get it


# ---- review findings (each one a test) ------------------------------------------------------

def test_moving_to_a_new_phone_needs_the_current_phones_code(tmp_path):
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    secret = _link_phone(svc, admin, wall)
    assert code(call(svc, "phone_link_begin", admin, password=GOOD_PW)) == "PHONE-CODE-NEEDED"
    wall.t += opal.TOTP_STEP_S
    ok(call(svc, "phone_link_begin", admin, password=GOOD_PW, code=phone_code(secret, wall.t)))


def test_guessing_at_a_restore_never_shuts_the_door_for_good(tmp_path):
    wall, clock = Clock(), Clock()
    svc, admin = ready(tmp_path, wall=wall, clock=clock)
    f = backup(svc, admin, tmp_path)
    svc.close()
    (appdata(tmp_path) / "app_store.bin").unlink()
    svc = make(tmp_path, wall=wall, clock=clock)
    for _ in range(opal.MAX_CONSECUTIVE_FAILURES + 3):
        r = call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password="a wrong guess here")
        assert r["error"]["code"] in ("AUTH-FAILED", "AUTH-THROTTLED")
        wall.t += opal.THROTTLE_MAX_S + 1; clock.t += opal.THROTTLE_MAX_S + 1
    ok(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW))


def test_a_restore_never_undoes_security_changes_made_after_the_backup(tmp_path):
    svc, admin = ready(tmp_path)
    with_reviewer(svc, admin)
    ok(call(svc, "account_create", admin, name="Casey Park", roles=["doctor"], password=GOOD_PW))
    f = backup(svc, admin, tmp_path)
    ids = {a["name"]: a["id"] for a in ok(call(svc, "account_list", admin))["accounts"]}
    ok(call(svc, "account_disable", admin, id=ids["Jordan Lee"]))           # disabled after the backup
    ok(call(svc, "reset_password", admin, id=ids["Casey Park"], password=NEW_PW))
    svc.close()
    (appdata(tmp_path) / "app_store.bin").unlink()
    svc = make(tmp_path)
    pre = ok(call(svc, "restore_preview", file=f, name="Dr. Sam Rivera", password=GOOD_PW))
    assert "Jordan Lee" not in {a["name"] for a in pre["accounts"]}
    ok(call(svc, "restore_confirm", confirm=pre["confirm"]))
    assert code(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW)) == "AUTH-FAILED"
    assert code(call(svc, "sign_in", name="Casey Park", password=GOOD_PW)) == "AUTH-DISABLED"
