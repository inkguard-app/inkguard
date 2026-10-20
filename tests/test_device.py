"""Retiring a computer: the work key and crypto-erase.

Retiring destroys the key the work is encrypted under (NIST SP 800-88
cryptographic erase), then the files; records copied back afterwards can
never be opened."""
from __future__ import annotations

import pytest

from inkguard import opal
from tests.test_opal import GOOD_PW, Clock, call, code, make, ok, ready, with_reviewer
from tests.test_restore import _link_phone, appdata, phone_code


@pytest.fixture(autouse=True)
def _fast_hashing(monkeypatch):
    monkeypatch.setattr(opal, "SCRYPT_N", 2 ** 10)


def test_work_is_encrypted_under_a_key_kept_outside_the_app_folder(tmp_path):
    svc, admin = ready(tmp_path)
    svc.work_put("batch1", b"Discharge summary for John Smith, MRN 448812")
    raw = (appdata(tmp_path) / "work" / "batch1.bin").read_bytes()
    assert b"John Smith" not in raw and b"448812" not in raw
    assert svc.work_get("batch1").startswith(b"Discharge summary")
    assert list((tmp_path / "keychain").glob("*-work.counter"))            # the key's only home
    drive = tmp_path / "usb"
    drive.mkdir()
    ok(call(svc, "backup_create", admin, folder=str(drive)))
    blob = next(drive.iterdir()).read_bytes()
    key_hex = next((tmp_path / "keychain").glob("*-work.counter")).read_text()
    assert key_hex.encode() not in blob                                    # never in a backup


def test_retire_this_computer_destroys_the_key_then_the_files(tmp_path):
    svc, admin = ready(tmp_path)
    svc.work_put("batch1", b"in-progress patient work")
    assert code(call(svc, "retire_here", admin, password=GOOD_PW)) == "APP-BAD-REQUEST"   # no RETIRE
    assert code(call(svc, "retire_here", admin, password="wrong password here",
                     confirm="RETIRE")) == "AUTH-FAILED"
    out = ok(call(svc, "retire_here", admin, password=GOOD_PW, confirm="RETIRE"))
    assert out["work_key_destroyed"] is True and out["work_files_removed"] == 1
    assert not list((tmp_path / "keychain").glob("*-work.counter"))
    assert not (appdata(tmp_path) / "work").exists()        # the whole work area, folders and all
    assert not (appdata(tmp_path) / "app_store.bin").exists()
    assert ok(call(svc, "status"))["state"] == "retired"
    assert code(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "DEVICE-RETIRED"
    assert "device_retired" in (appdata(tmp_path) / "security_log.jsonl").read_text()
    svc.close()
    again = make(tmp_path)
    assert ok(call(again, "status"))["state"] == "needs_restore"


def test_only_an_admin_with_the_phone_code_can_retire_it(tmp_path):
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    secret = _link_phone(svc, admin, wall)
    with_reviewer(svc, admin)
    wall.t += opal.TOTP_STEP_S
    rev = ok(call(svc, "sign_in", name="Jordan Lee", password=GOOD_PW))["session"]
    assert code(call(svc, "retire_here", rev, password=GOOD_PW, confirm="RETIRE")) == "ROLE-DENIED"
    assert code(call(svc, "retire_here", admin, password=GOOD_PW, confirm="RETIRE")) == "PHONE-CODE-NEEDED"
    ok(call(svc, "retire_here", admin, password=GOOD_PW, confirm="RETIRE",
            code=phone_code(secret, wall.t)))


def test_a_computer_nobody_can_sign_into_is_retired_with_recovery_and_phone_codes(tmp_path):
    wall = Clock(1_000_000.0)
    svc, admin = ready(tmp_path, wall=wall)
    args = dict(name="Dr. Sam Rivera", recovery_code=svc.recovery, confirm="RETIRE")
    secret = _link_phone(svc, admin, wall)
    wall.t += opal.TOTP_STEP_S
    assert code(call(svc, "retire_at_sign_in", **{**args, "recovery_code": "AAAAAA-BBBBBB-CCCCCC-DDDDDD"},
                     code=phone_code(secret, wall.t))) == "AUTH-FAILED"
    assert code(call(svc, "retire_at_sign_in", **args)) == "PHONE-CODE-NEEDED"   # phone linked: needed
    ok(call(svc, "retire_at_sign_in", **args, code=phone_code(secret, wall.t)))
    assert ok(call(svc, "status"))["state"] == "retired"


def test_an_admin_without_a_phone_retires_with_the_recovery_code_alone(tmp_path):
    svc, _ = ready(tmp_path)
    assert code(call(svc, "retire_at_sign_in", name="Dr. Sam Rivera", recovery_code=svc.recovery)) \
        == "APP-BAD-REQUEST"                                              # RETIRE not typed
    ok(call(svc, "retire_at_sign_in", name="Dr. Sam Rivera", recovery_code=svc.recovery, confirm="RETIRE"))


# ---- review findings (each one a test) --------------------------------------------------

def test_records_copied_back_after_a_retire_can_never_be_opened(tmp_path):
    svc, admin = ready(tmp_path)
    saved = (appdata(tmp_path) / "app_store.bin").read_bytes()
    ok(call(svc, "retire_here", admin, password=GOOD_PW, confirm="RETIRE"))
    svc.close()
    (appdata(tmp_path) / "app_store.bin").write_bytes(saved)               # put back by hand
    again = make(tmp_path)
    st = ok(call(again, "status"))
    assert st["state"] == "needs_restore"                                  # its key half is gone
    assert code(call(again, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW)) == "AUTH-FAILED"
