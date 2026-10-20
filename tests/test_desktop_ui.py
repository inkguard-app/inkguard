"""The desktop screens, driven by a real browser against the real secure core. A robot clicks through every flow as a person would: first-run
setup (practice, account, recovery passphrase), sign-in with a phone code, the lock
screen, people, backups, the security log, restore after lost records and
retiring the computer. The screens reach the core exactly as in the desktop
app — one JSON line per request — through an in-process binding; no network
port is opened and the pages are served from memory."""
from __future__ import annotations

import base64
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest

from inkguard import opal
from tests.test_opal import GOOD_PW, SETUP, TODAY, Clock, call, make, ok

pw = pytest.importorskip("playwright.sync_api")
UI = Path(__file__).resolve().parent.parent / "desktop" / "ui"
ORIGIN = "http://app.test"
#: How long a screen may wait for a batch to be prepared. Preparing even a
#: few notes loads the engine and reads every file, and when the whole suite
#: is running on one machine that takes minutes — so this is generous on
#: purpose. A release suite that fails at random is worse than a slow one.
PREPARED = 900_000


@pytest.fixture(autouse=True)
def _fast_hashing(monkeypatch):
    monkeypatch.setattr(opal, "SCRYPT_N", 2 ** 10)


@pytest.fixture(scope="module")
def _playwright():
    with pw.sync_playwright() as p:
        yield p


@pytest.fixture
def browser(_playwright):
    """A FRESH browser for every test. One browser shared by the whole file
    gathered pages left open by failed tests until, around the twentieth,
    it stopped answering and every later test failed with it (found
    2026-10-17). A second or so per test buys tests that cannot affect
    each other."""
    b = _playwright.chromium.launch()
    yield b
    try:
        for ctx in b.contexts:
            ctx.close()
        b.close()
    except Exception:                               # noqa: BLE001 — a wedged browser
        pass                                        # must not hang the run


class App:
    """A browser page wired to one service."""

    def __init__(self, browser, svc):
        self.svc = svc
        self.problems: list[str] = []
        self.page = browser.new_page()
        self.page.on("console", lambda m: self.problems.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.problems.append(str(e)))
        self.page.on("dialog", lambda d: (self.problems.append("dialog: " + d.message), d.dismiss()))
        self.page.route(ORIGIN + "/**", self._serve)
        self.page.expose_function("__opal_test", self._opal)
        self.opened: list[tuple[str, str]] = []           # what "send" handed outside
        self.page.expose_function("__open_test", lambda how, text: self.opened.append((how, text)))

    def _serve(self, route):
        rel = urlparse(route.request.url).path.lstrip("/") or "index.html"
        p = (UI / rel).resolve()
        if UI not in p.parents or not p.is_file():
            return route.fulfill(status=404, body="")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if p.suffix == ".js":
            ctype = "text/javascript"
        route.fulfill(path=str(p), content_type=ctype)

    def _opal(self, line):
        return json.dumps(self.svc.handle(json.loads(line)))

    def open(self):
        self.page.goto(ORIGIN + "/index.html")
        return self.page

    def close(self):
        self.page.close()


RECOVERY = "the old lighthouse keeps its own time"


def set_up(page, svc):
    """First run, as a person does it: practice, account, a recovery
    passphrase they choose, then that passphrase shown back to keep."""
    page.get_by_label("Your practice, lab or team").fill("Lakeside Family Practice")
    page.get_by_role("button", name="Next").click()
    page.get_by_label("Your full name").fill("Dr. Sam Rivera")
    page.get_by_label("Password", exact=True).fill(GOOD_PW)
    page.get_by_label("Type the password again").fill(GOOD_PW)
    page.locator("#attest").check()
    page.get_by_role("button", name="Next").click()
    page.get_by_label("Recovery passphrase", exact=True).fill(RECOVERY)
    page.get_by_label("Type it again").fill(RECOVERY)
    page.get_by_role("button", name="Create my account").click()
    shown = page.locator(".bigcode").inner_text().strip()
    assert shown == RECOVERY                             # shown back to write down or photograph
    cont = page.get_by_role("button", name="Continue to sign in")
    assert cont.is_disabled()                          # not until they say it is kept safe
    page.locator("#kept").check()
    cont.click()
    return shown


def sign_out(page):
    """Signing out asks once, and never for a password."""
    page.locator("header.appbar").get_by_role("button", name="Sign out").click()
    page.locator(".dialog").get_by_role("button", name="Sign out").click()


def sign_in(page, name="Dr. Sam Rivera", password=GOOD_PW, svc=None):
    """Sign in as a person would — including reading the code off their phone
    when their account has one linked, which every account on a batch must."""
    page.get_by_label("Name").fill(name)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    if svc is None:
        return
    box = page.get_by_label("Code from your phone")
    try:
        box.wait_for(timeout=4000)
    except Exception:                               # noqa: BLE001 — no phone on this account
        return
    from tests.test_opal import phone_now
    svc._wall.t += opal.TOTP_STEP_S                 # a code is good once (RFC 6238)
    box.fill(phone_now(svc))
    page.get_by_role("button", name="Sign in").click()


def wait_prepared(page, svc, timeout_ms: int = PREPARED) -> None:
    """Wait for a batch to be ready for review, and FAIL FAST if it never
    will be.

    A raw 15-minute wait on a heading turns any refused batch into a
    quarter-hour of nothing, which is no use to whoever runs this suite. So
    this watches the service's own record as well: a batch that stopped, or
    a batch that was never created at all, raises at once and says why."""
    import time
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        if page.get_by_role("heading", name="Ready for review").count():
            return
        recs = svc.doc.get("batches", {})
        if not recs and page.get_by_text("Support code:").count():
            raise AssertionError("the batch was refused: "
                                 + page.locator(".error, .callout-warn").first.inner_text())
        for bid, rec in recs.items():
            if rec.get("stage") == "failed":
                raise AssertionError(f"batch {bid} stopped: {rec.get('error')}")
        worker = getattr(svc, "_worker", None)
        idle = recs and (worker is None or not worker.is_alive())
        if idle and any(r.get("stage") in ("preparing", "processing") for r in recs.values()):
            idle_since = idle_since if "idle_since" in locals() else time.time()
            if time.time() - idle_since > 10:
                # Nothing is working on it and nothing will: say so now.
                stages = {b: (r.get("stage"), r.get("progress")) for b, r in recs.items()}
                raise AssertionError(f"no one is preparing the batch; stages: {stages}; "
                                     f"stop flag set: {svc._stop_work.is_set()}")
        else:
            idle_since = time.time()
        page.wait_for_timeout(500)
    stages = {b: r.get("stage") for b, r in svc.doc.get("batches", {}).items()}
    raise AssertionError(f"no batch became ready for review in time; stages: {stages}")


@pytest.fixture
def fresh(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    yield app, svc
    app.close()
    svc.close()


def test_first_run_setup_through_to_home(fresh):
    app, svc = fresh
    page = app.open()
    page.get_by_role("heading", name="Set up this computer").wait_for()
    set_up(page, svc)
    page.get_by_text("Set up. Sign in with your new account.").wait_for()
    sign_in(page, svc=svc)
    page.get_by_role("heading", name=re.compile(r"^Good (morning|afternoon|evening), Sam$")).wait_for()
    assert page.get_by_text("Since your last sign-in").is_visible()
    assert page.get_by_text("Lakeside Family Practice").first.is_visible()   # the practice, as named
    assert page.get_by_text("Licence").count() == 0                           # nothing to buy or renew
    text = page.locator("main").inner_text()
    assert "null" not in text and "undefined" not in text, text[:300]          # no leaked JS values
    assert app.problems == []


def test_wrong_password_shows_plain_words_and_a_support_code(fresh):
    app, svc = fresh
    page = app.open()
    set_up(page, svc)
    sign_in(page, password="not the password at all")
    alert = page.get_by_role("alert")
    alert.wait_for()
    assert "Support code: AUTH-FAILED" in alert.inner_text()
    assert app.problems == []


def test_lock_screen_hides_everything_and_unlocks_with_the_password(fresh):
    app, svc = fresh
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("button", name="Lock").click()
    overlay = page.locator(".lock-overlay")
    overlay.wait_for()
    assert overlay.bounding_box()["height"] >= page.viewport_size["height"] - 1
    page.locator(".lock-overlay").get_by_label("Password", exact=True).fill("wrong password guess")
    page.locator(".lock-overlay").get_by_role("button", name="Unlock").click()
    page.locator(".lock-overlay [role=alert]").wait_for()
    page.locator(".lock-overlay").get_by_label("Password", exact=True).fill(GOOD_PW)
    page.locator(".lock-overlay").get_by_role("button", name="Unlock").click()
    overlay.wait_for(state="detached")
    assert app.problems == []


def test_the_core_locks_an_idle_session_and_the_screen_follows(browser, tmp_path):
    clock, wall = Clock(), Clock()
    svc = make(tmp_path, clock=clock, wall=wall)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("heading", name=re.compile("^Good")).wait_for()
    clock.t += 6 * 60; wall.t += 6 * 60                          # six idle minutes (default lock: 5)
    page.get_by_role("button", name="My account").click()
    page.locator(".lock-overlay").wait_for()
    app.close(); svc.close()


def test_people_and_the_security_log_and_text_is_never_markup(fresh):
    app, svc = fresh
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("button", name="Settings").click()
    page.get_by_label("Full name").fill('<img src=x onerror="alert(1)">Jordan')
    page.get_by_label("Temporary password").fill("lantern by the quiet river")
    page.get_by_role("button", name="Add person").click()
    page.get_by_role("cell", name='<img src=x onerror="alert(1)">Jordan').wait_for()
    assert page.locator("main img").count() == 0                   # shown as text, never as markup
    page.get_by_role("tab", name="Security log").click()
    page.get_by_role("cell", name="Person added").wait_for()
    assert app.problems == []


def test_linking_a_phone_then_signing_in_with_its_code(browser, tmp_path):
    wall = Clock(1_000_000.0)
    svc = make(tmp_path, wall=wall)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("button", name="My account").click()
    card = page.locator("section", has=page.get_by_role("heading", name="Phone codes"))
    card.get_by_label("Password").fill(GOOD_PW)
    card.get_by_role("button", name="Link my phone").click()
    page.locator("svg.qr").wait_for()
    secret = card.locator("code").inner_text().strip()
    raw = base64.b32decode(secret + "=" * (-len(secret) % 8))
    card.get_by_label("Code from your phone").fill(opal._totp_at(raw, int(wall.t // 30)))
    card.get_by_role("button", name="Finish linking").click()
    page.get_by_text("Linked").wait_for()
    sign_out(page)
    wall.t += 30
    sign_in(page, svc=svc)
    page.get_by_label("Code from your phone").wait_for()
    page.get_by_label("Code from your phone").fill(opal._totp_at(raw, int(wall.t // 30)))
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("heading", name=re.compile("^Good")).wait_for()
    assert app.problems == []
    app.close(); svc.close()


def test_backup_and_its_restore_drill_from_the_screens(fresh, tmp_path):
    app, svc = fresh
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    drive = tmp_path / "usb"; drive.mkdir()
    page.get_by_role("button", name="Settings").click()
    page.get_by_role("tab", name="Backups").click()
    card = page.locator("section", has=page.get_by_role("heading", name="Make a backup"))
    card.get_by_label("Save the backup to").fill(str(drive))
    card.get_by_role("button", name="Make backup").click()
    card.get_by_text("Backup saved").wait_for()
    made = next(drive.iterdir())
    drill = page.locator("section", has=page.get_by_role("heading", name="Check a backup (restore drill)"))
    drill.get_by_label("Backup file").fill(str(made))
    drill.get_by_role("button", name="Check backup").click()
    drill.locator(".notice", has_text="is complete:").wait_for()
    assert app.problems == []


def test_lost_records_are_restored_from_the_screens(browser, tmp_path):
    svc = make(tmp_path)
    ok(call(svc, "setup_first_admin", **SETUP))
    tok = ok(call(svc, "sign_in", name="Dr. Sam Rivera", password=GOOD_PW))["session"]
    drive = tmp_path / "usb"; drive.mkdir()
    backup = ok(call(svc, "backup_create", tok, folder=str(drive)))["file"]
    svc.close()
    (tmp_path / "appdata" / "app_store.bin").unlink()                  # the disaster
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    page.get_by_role("heading", name="This computer's app records need restoring").wait_for()
    page.get_by_role("button", name="Restore from a backup").click()
    page.get_by_label("Backup file").fill(backup)
    page.get_by_label("Admin name").fill("Dr. Sam Rivera")
    page.get_by_label("Password (as it was then)").fill(GOOD_PW)
    page.get_by_role("button", name="Check this backup").click()
    page.get_by_role("heading", name="Check before restoring").wait_for()
    page.get_by_role("button", name="Restore now").click()
    page.get_by_role("heading", name="Restored").wait_for()
    page.get_by_role("button", name="Continue to sign in").click()
    sign_in(page, svc=svc)
    page.get_by_role("button", name="Review the alert").click()        # the admin looks at the restore
    page.get_by_role("button", name="I have looked into it").click()
    page.get_by_text("Alert acknowledged").wait_for()
    assert app.problems == []
    app.close(); svc.close()


def test_retiring_the_computer_from_settings(fresh):
    app, svc = fresh
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("button", name="Settings").click()
    page.get_by_role("tab", name="This computer").click()
    zone = page.locator("section.danger-zone")
    zone.get_by_label("Your password").fill(GOOD_PW)
    zone.get_by_label("To confirm, type RETIRE").fill("RETIRE")
    zone.get_by_role("button", name="Retire this computer").click()
    page.get_by_role("heading", name="This computer has been retired").wait_for()
    assert app.problems == []


def test_the_screens_carry_no_inline_script_or_style_and_load_nothing_remote():
    html = (UI / "index.html").read_text()
    assert "script-src 'self'" in html and "unsafe-inline" not in html
    for f in list(UI.rglob("*.js")) + list(UI.rglob("*.html")) + list(UI.rglob("*.css")):
        text = f.read_text()
        assert not re.search(r"\bfetch\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|import\(", text), f
        if f.parent.name == "vendor":
            continue                                      # third-party: comments may cite web pages
        assert not re.search(r"https?://(?!www\.w3\.org/2000/svg|ipc\.localhost)", text), f
        if f.suffix == ".js":
            assert "innerHTML" not in text and "eval(" not in text, f


# ---- independent review findings, each one a test ----------------------------------

def _signed_in_with_reviewer(page, svc):
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("heading", name=re.compile("^Good")).wait_for()
    page.get_by_role("button", name="Settings").click()
    page.get_by_label("Full name").fill("Jordan Lee")
    page.get_by_label("Temporary password").fill("lantern by the quiet river")
    page.get_by_label("Temporary password").press("Enter")                 # Enter submits
    page.get_by_role("cell", name="Jordan Lee").wait_for()


def test_a_dialog_never_outlives_the_session_and_the_app_behind_it_is_inert(browser, tmp_path):
    clock, wall = Clock(), Clock()
    svc = make(tmp_path, clock=clock, wall=wall)
    app = App(browser, svc)
    page = app.open()
    _signed_in_with_reviewer(page, svc)
    page.get_by_role("row", name=re.compile("Jordan Lee")).get_by_role("button", name="Reset password").click()
    page.locator(".dialog").wait_for()
    assert page.locator("#app").evaluate("el => el.inert") is True        # nothing behind is reachable
    clock.t += 6 * 60; wall.t += 6 * 60                                     # the core locks meanwhile
    page.locator(".dialog").get_by_label("New temporary password").fill("a brand new temporary one")
    page.locator(".dialog").get_by_role("button", name="Reset password").click()
    page.locator(".lock-overlay").wait_for()
    assert page.locator(".scrim").count() == 0                              # the dialog went with it
    page.locator(".lock-overlay").get_by_role("button", name="Sign out").click()
    page.get_by_role("button", name="Sign in").wait_for()
    assert page.locator(".scrim, .lock-overlay").count() == 0
    assert page.locator("#app").evaluate("el => el.inert") is False
    app.close(); svc.close()


def test_locking_an_ended_session_goes_to_sign_in_not_a_stuck_lock_screen(fresh):
    app, svc = fresh
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("heading", name=re.compile("^Good")).wait_for()
    svc.sessions.clear()                                                    # e.g. the app restarted
    page.get_by_role("button", name="Lock").click()
    page.get_by_role("button", name="Sign in").wait_for()
    assert page.locator(".lock-overlay").count() == 0


def test_settings_say_plainly_there_is_nothing_to_renew_and_two_or_three_people_is_a_choice(fresh):
    app, svc = fresh
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("button", name="Settings").click()
    page.get_by_role("tab", name="This computer").click()
    page.get_by_text("There is no account with anyone").wait_for()
    assert page.get_by_role("tab", name="The business").count() == 0
    page.get_by_role("tab", name="Security", exact=True).click()
    page.locator("#people3").check()
    page.locator("section", has=page.get_by_role("heading", name="Who reviews a batch")) \
        .get_by_role("button", name="Save").click()
    page.get_by_text("Saved.").wait_for()
    assert svc.doc["settings"]["review_people"] == 3
    page.get_by_role("tab", name="Security log").click()
    page.get_by_role("cell", name="Signed in").first.wait_for()
    assert app.problems == []


def test_the_records_lost_screen_says_why_before_anyone_signs_in(browser, tmp_path):
    svc = make(tmp_path)
    ok(call(svc, "setup_first_admin", **SETUP))
    svc.close()
    (tmp_path / "appdata" / "app_store.bin").unlink()
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    page.get_by_text(re.compile("missing although")).wait_for()
    app.close(); svc.close()


def test_an_answer_out_of_order_is_never_trusted(browser, tmp_path):
    class Shuffled(App):
        def _opal(self, line):
            resp = json.loads(super()._opal(line))
            resp["id"] = 999999                                             # somebody else's answer
            return json.dumps(resp)

    svc = make(tmp_path)
    app = Shuffled(browser, svc)
    page = app.open()
    page.get_by_text("APP-SERVICE-STOPPED").wait_for()
    app.close(); svc.close()


def test_the_browser_menu_and_reload_print_save_shortcuts_are_off(fresh):
    app, svc = fresh
    page = app.open()
    page.get_by_role("heading", name="Set up this computer").wait_for()
    prevented = page.evaluate("""() => {
        const out = [];
        for (const [key, ctrl] of [["r", true], ["p", true], ["s", true], ["F5", false]]) {
            const e = new KeyboardEvent("keydown", {key, ctrlKey: ctrl, bubbles: true, cancelable: true});
            document.body.dispatchEvent(e); out.push(e.defaultPrevented);
        }
        const m = new MouseEvent("contextmenu", {bubbles: true, cancelable: true});
        document.body.dispatchEvent(m); out.push(m.defaultPrevented);
        return out;
    }""")
    assert all(prevented)


# ---- batches, through the screens --------------------------------------------

def _folder_of_notes(tmp_path):
    from tests.test_granite import a_folder
    return a_folder(tmp_path)


def _add_reviewers(page, svc=None):
    page.get_by_role("button", name="Settings").click()
    for name in ("Jordan Lee", "Casey Park"):
        page.get_by_label("Full name").fill(name)
        page.get_by_label("Temporary password").fill("lantern by the quiet river")
        page.get_by_role("button", name="Add person").click()
        page.get_by_role("cell", name=name).wait_for()
    page.get_by_role("button", name="Home").click()
    if svc is not None:
        _certify(svc)


def _certify(svc):
    """As if both reviewers had worked through the practice batch and linked
    their phones, which they must before a real one (tests/test_practice.py
    and tests/test_export.py drive both for real; these tests are about
    other screens)."""
    import datetime as _dt
    from tests.test_granite import link_phones
    link_phones(svc)
    for uid, acc in svc.doc["accounts"].items():
        acc["badge"] = {"earned": svc._today().isoformat(),
                        "until": (svc._today() + _dt.timedelta(days=200)).isoformat(),
                        "planted_found": 3, "planted_of": 3, "over_removed": 0, "notes": 4}
        acc["certified"] = True
    svc._save()


def _look(page):
    """The folder is looked at as soon as it is entered (one screen):
    leaving the field shows what it holds in place."""
    page.get_by_label("Folder").press("Tab")
    page.locator(".dialog .found .callout, .dialog .found .error").first.wait_for(timeout=120_000)


def _tick_and_start(page):
    """The four care points are ticked on the same screen, then one button
    starts the work."""
    for box in page.locator(".dialog .care-point input[type=checkbox]").all():
        box.check()
    page.locator(".dialog").get_by_role("button", name="Start preparing").click()


def _start_batch(page, folder, svc=None):
    # A practice that runs long batches turns on "keep preparing while the
    # screen is locked"; without it the idle lock pauses the work and waits
    # for the person to come back, which is the design but means a browser left watching for minutes waits for ever.
    if svc is not None:
        svc.doc["settings"]["prepare_while_locked"] = True
        svc._save()
    page.get_by_role("button", name="New batch").click()
    page.get_by_label("Folder").fill(str(folder))
    page.get_by_label("First reviewer").select_option(label="Jordan Lee")
    page.get_by_label("Second reviewer").select_option(label="Casey Park")
    # The folder is measured as soon as it is entered, and the size is
    # shown before anyone commits to it.
    _look(page)
    _tick_and_start(page)


def test_a_batch_runs_from_the_screens_and_leaves_the_files_alone(browser, tmp_path):
    from tests.test_granite import snapshot
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    folder = _folder_of_notes(tmp_path)
    before = snapshot(folder)
    page.get_by_text("No batches yet").wait_for()
    _start_batch(page, folder, svc)
    page.get_by_role("heading", name="Batch", exact=True).wait_for()
    wait_prepared(page, svc)
    assert snapshot(folder) == before                       # the practice's files, untouched
    body = page.locator("main").inner_text()
    assert "3 notes are ready" in body
    assert "Files not read (3)" in body                     # image, old .doc, system file
    assert "image or scan" in body
    page.get_by_role("button", name="Back to Home").click()
    page.get_by_role("cell", name="Ready for review").wait_for()
    assert app.problems == []
    app.close(); svc.close()


def test_the_new_batch_screen_refuses_a_doctor_as_their_own_reviewer(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    page.get_by_role("button", name="New batch").click()
    names = page.get_by_label("First reviewer").locator("option").all_inner_texts()
    assert "Dr. Sam Rivera" not in names                    # never offered as their own reviewer
    page.get_by_label("Folder").fill(str(tmp_path / "nowhere"))
    page.get_by_label("First reviewer").select_option(label="Jordan Lee")
    page.get_by_label("Second reviewer").select_option(label="Casey Park")
    # A folder that is not there is said at once, in place, and nothing can
    # start from it.
    _look(page)
    page.get_by_text("Support code: BATCH-FOLDER").wait_for()
    start = page.locator(".dialog").get_by_role("button", name="Start preparing")
    assert start.is_disabled()

    # With a real folder the button holds until every care point is ticked.
    page.get_by_label("Folder").fill(str(_folder_of_notes(tmp_path)))
    _look(page)
    boxes = page.locator(".dialog .care-point input[type=checkbox]")
    assert boxes.count() == 4
    assert start.is_disabled()                              # the barrier holds until each is ticked
    for i in range(boxes.count() - 1):
        boxes.nth(i).check()
    assert start.is_disabled()
    boxes.nth(boxes.count() - 1).check()
    assert not start.is_disabled()
    app.close(); svc.close()


def test_cancelling_a_batch_from_the_screen_erases_its_work(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    _start_batch(page, _folder_of_notes(tmp_path), svc)
    wait_prepared(page, svc)
    bid = list(svc.doc["batches"])[0]
    page.locator("section.danger-zone").get_by_role("button", name="Wipe and start again").click()
    page.get_by_role("heading", name="Restart report").wait_for()     # what had been done, kept
    page.get_by_role("button", name="Done").click()
    page.get_by_text("No batches yet").wait_for()
    assert not (svc.dir / "work" / bid).exists()
    assert svc._anchor.read(f"{svc._anchor_acct}-batch-{bid}") is None
    assert app.problems == []
    app.close(); svc.close()


def test_no_overlay_ever_leaves_the_app_unusable_behind_it(browser, tmp_path):
    """A dialog, a barrier or the lock screen makes everything behind it
    inert; when it goes, the app must come back to life."""
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    inert = lambda: page.locator("#app").evaluate("el => el.inert")      # noqa: E731
    assert not inert()
    folder = _folder_of_notes(tmp_path)
    page.get_by_role("button", name="New batch").click()
    assert inert()
    page.get_by_label("Folder").fill(str(folder))
    _look(page)
    assert inert()                                                        # still the one screen
    page.locator(".dialog").get_by_role("button", name="Cancel").click()
    assert not inert()                                                    # walking away leaves it usable
    page.get_by_role("button", name="New batch").click()
    page.get_by_label("Folder").fill(str(folder))
    page.get_by_label("First reviewer").select_option(label="Jordan Lee")
    page.get_by_label("Second reviewer").select_option(label="Casey Park")
    _look(page)
    _tick_and_start(page)
    page.get_by_role("heading", name="Batch", exact=True).wait_for()
    assert not inert()                                                    # both overlays gone
    assert page.locator(".scrim").count() == 0
    wait_prepared(page, svc)
    page.locator("section.danger-zone").get_by_role("button", name="Wipe and start again").click()
    page.get_by_role("heading", name="Restart report").wait_for()     # what had been done, kept
    page.get_by_role("button", name="Done").click()
    page.get_by_text("No batches yet").wait_for()
    assert app.problems == []
    app.close(); svc.close()


def test_the_pool_panel_shows_what_is_inside_the_batch(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    _start_batch(page, _folder_of_notes(tmp_path), svc)
    wait_prepared(page, svc)
    pool = page.locator("section", has=page.get_by_role("heading", name="Inside this batch"))
    text = pool.inner_text()
    assert "Files taken in" in text and "Still inside" in text
    assert "Clean copies taken out" in text
    page.get_by_label("Their name").fill("Robin Shah")
    page.get_by_label("What they helped with").fill("read the scanned pages aloud")
    page.get_by_role("button", name="Name a helper").click()
    page.get_by_text("Robin Shah — read the scanned pages aloud").wait_for()
    assert app.problems == []
    app.close(); svc.close()


# ---- the review screen, through the real screens ------------------------------

def test_a_reviewer_works_through_notes_on_a_screen_with_nothing_else_on_it(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    _start_batch(page, _folder_of_notes(tmp_path), svc)
    wait_prepared(page, svc)
    sign_out(page)
    page.get_by_role("button", name="Sign in").wait_for()

    sign_in(page, name="Jordan Lee", password="lantern by the quiet river", svc=svc)
    page.get_by_role("button", name="Review", exact=True).first.click()
    page.get_by_text("Note 1 of 3").wait_for()
    # The review screen is the whole window: no app bar, no tabs, no dashboard.
    assert page.locator("header.appbar").count() == 0
    assert page.get_by_role("button", name="Settings").count() == 0
    assert page.get_by_role("button", name="Home").count() == 0
    assert page.locator(".note-text").inner_text().strip()          # the note itself, in full

    for _ in range(3):
        page.get_by_text(re.compile(r"^Note \d of 3$")).wait_for()
        for card in page.locator("li.question").all():
            card.get_by_role("button", name="Take it out").click()
        page.get_by_role("button", name="Save and next").click()
    page.get_by_role("heading", name="That is every note").wait_for()
    go = page.get_by_role("button", name="Sign off my review")
    assert go.is_disabled()                                          # not until the statement is read
    page.locator("label.attest input").check()
    go.click()
    # Their part only: the screen never says whether the other reviewer has
    # finished, or who they are.
    page.get_by_text("Signed off. Thank you.").wait_for()
    # Who else is on the batch is no secret; whether they have finished is.
    shown = page.locator("body").inner_text().lower()
    for leak in ("waiting for the other", "both reviews", "signed off at", "has signed"):
        assert leak not in shown
    assert svc.doc["batches"][list(svc.doc["batches"])[0]]["signed_off"]["R1"]["name"] == "Jordan Lee"
    assert app.problems == []
    app.close(); svc.close()


def test_the_unsure_words_show_the_tools_guess_and_take_the_reviewers_own(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    _start_batch(page, _folder_of_notes(tmp_path), svc)
    wait_prepared(page, svc)
    sign_out(page)
    sign_in(page, name="Casey Park", password="lantern by the quiet river", svc=svc)
    page.get_by_role("button", name="Review", exact=True).first.click()
    page.get_by_text("Note 1 of 3").wait_for()
    cards = page.locator("li.question")
    if cards.count() == 0:
        app.close(); svc.close()
        pytest.skip("this note left the tool in no doubt")
    first = cards.first
    assert "the tool thinks:" in first.inner_text()                  # its guess, in plain words
    first.get_by_role("button", name="Leave it in").click()
    first.locator("select").select_option(label="a place or address")
    first.get_by_placeholder("Or say it in your own words").fill("the name of their village")
    page.get_by_role("button", name="Save and next").click()
    page.get_by_text("Note 2 of 3").wait_for()
    bid = list(svc.doc["batches"])[0]
    from inkguard import granite
    nid = [i.note_id for i in granite.load_items(svc._batch_work(bid)) if i.note_id][0]
    st = granite.load_decision(svc._batch_work(bid), "R2", nid)
    assert "fine" in st["answers"].values() and "place" in st["kinds"].values()
    assert "village" in list(st["words"].values())[0]
    assert app.problems == []
    app.close(); svc.close()


# ---- the clinician settles and releases, through the real screens --------------

def _review_as(page, who: str, svc=None) -> None:
    """One reviewer's whole pass on the screens: take everything out, sign."""
    sign_out(page)
    page.get_by_role("button", name="Sign in").wait_for()
    sign_in(page, name=who, password="lantern by the quiet river", svc=svc)
    page.get_by_role("button", name="Review", exact=True).first.click()
    page.get_by_text("Note 1 of 3").wait_for()
    for _ in range(3):
        page.get_by_text(re.compile(r"^Note \d of 3$")).wait_for()
        for card in page.locator("li.question").all():
            card.get_by_role("button", name="Take it out").click()
        page.get_by_role("button", name="Save and next").click()
    page.get_by_role("heading", name="That is every note").wait_for()
    page.locator("label.attest input").check()
    page.get_by_role("button", name="Sign off my review").click()
    page.get_by_text("Signed off. Thank you.").wait_for()


def test_the_clinician_settles_the_batch_and_releases_the_files(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    _start_batch(page, _folder_of_notes(tmp_path), svc)
    wait_prepared(page, svc)
    _review_as(page, "Jordan Lee", svc)
    _review_as(page, "Casey Park", svc)

    sign_out(page)
    page.get_by_role("button", name="Sign in").wait_for()
    sign_in(page, svc=svc)
    page.get_by_role("button", name="Settle", exact=True).first.click()

    # The settle screen is the whole window too: it shows patient words.
    page.wait_for_timeout(500)
    assert page.locator("header.appbar").count() == 0
    while page.locator("header.review-bar .where").inner_text().startswith("Note"):
        for card in page.locator("li.question").all():
            card.get_by_role("button", name="Take it out").click()
        page.get_by_role("button", name="Save and next").click()
        page.wait_for_timeout(300)

    # The small-numbers check, then the shapes.
    page.get_by_role("heading", name="What could still single someone out").wait_for()
    for card in page.locator("li.question").all():
        card.get_by_role("button", name="Accept it on the record").click()
    go = page.get_by_role("button", name="Choose how the files come back")
    assert not go.is_disabled()
    go.click()

    page.get_by_role("heading", name="Choose what you take away").wait_for()
    release = page.get_by_role("button", name="Release the files")
    assert release.is_disabled()                       # not until the folder and statement are in
    out = tmp_path / "finished"
    page.get_by_placeholder("Full path of the folder for the finished files").fill(str(out))
    page.locator("label.attest input").check()
    assert not release.is_disabled()
    release.click()

    page.get_by_role("heading", name="Finished").wait_for(timeout=PREPARED)
    assert (out / "Research copy.zip").exists()

    # The feedback form is in the way, and the answer is either to send it or
    # to say not this time.
    page.get_by_text("Anything wrong this time?").wait_for()
    page.get_by_label("missed something").check()
    page.get_by_placeholder("Anything the tool got wrong, in general terms").fill(
        "it took out a medicine name it should have left in")
    page.get_by_role("button", name="Send this back").click()
    page.get_by_text("The report is saved beside your files").wait_for()
    # Sending is the doctor's own act: the report goes to THEIR email program
    # (or a GitHub issue in their browser), with the same checked text.
    page.get_by_role("button", name="Email it to the project").click()
    page.get_by_text("Your email program should now show the report").wait_for()
    how, text = app.opened[-1]
    assert how == "email" and "medicine name" in text
    for word in ("Martha", "Ellison", "Okonkwo", "4481203"):
        assert word not in text
    page.get_by_role("button", name="Done").click()
    saved = sorted(out.rglob("feedback_report_*.md"))
    assert saved, "the report is written beside the files"
    body = saved[0].read_text()
    assert "missed something" in body and "medicine name" in body
    for word in ("Martha", "Ellison", "Okonkwo", "4481203"):
        assert word not in body
    # The pool is gone; a closed record stays so the practice can see it
    # happened.
    bid = list(svc.doc["batches"])[0]
    assert svc.doc["batches"][bid]["closed"] is True
    assert app.problems == []
    app.close(); svc.close()


def test_the_clinician_cannot_release_without_signing_the_statement(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    _start_batch(page, _folder_of_notes(tmp_path), svc)
    wait_prepared(page, svc)
    _review_as(page, "Jordan Lee", svc)
    _review_as(page, "Casey Park", svc)
    sign_out(page)
    page.get_by_role("button", name="Sign in").wait_for()
    sign_in(page, svc=svc)
    page.get_by_role("button", name="Settle", exact=True).first.click()
    page.wait_for_timeout(500)
    while page.locator("header.review-bar .where").inner_text().startswith("Note"):
        for card in page.locator("li.question").all():
            card.get_by_role("button", name="Take it out").click()
        page.get_by_role("button", name="Save and next").click()
        page.wait_for_timeout(300)
    page.get_by_role("heading", name="What could still single someone out").wait_for()
    for card in page.locator("li.question").all():
        card.get_by_role("button", name="Accept it on the record").click()
    page.get_by_role("button", name="Choose how the files come back").click()
    page.get_by_role("heading", name="Choose what you take away").wait_for()
    page.get_by_placeholder("Full path of the folder for the finished files").fill(str(tmp_path / "x"))
    assert page.get_by_role("button", name="Release the files").is_disabled()
    assert app.problems == []
    app.close(); svc.close()


# ---- the assessment, before anyone commits to a folder -------------------------

def test_the_folder_is_measured_before_a_batch_starts(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    folder = _folder_of_notes(tmp_path)
    page.get_by_role("button", name="New batch").click()
    page.get_by_label("Folder").fill(str(folder))
    page.get_by_label("First reviewer").select_option(label="Jordan Lee")
    page.get_by_label("Second reviewer").select_option(label="Casey Park")
    _look(page)
    said = page.locator(".dialog").inner_text()
    assert "patient" in said and "pages" in said
    # A floor, never a ceiling: the screen says "at least", never "about X
    # at most" or a single flat number.
    assert "at least" in said.lower()
    assert "at most" not in said.lower()
    assert "0 minutes" not in said, "a floor that rounds to nothing says nothing"
    # Files that cannot be read are named as such rather than left out quietly.
    assert "cannot be read" in said
    # The measured accuracy is there with its test sets, and never a perfect
    # score, because that would teach a reviewer the tool catches everything.
    page.get_by_text("What the tool is measured at").click()
    shown = page.locator(".dialog").inner_text()
    assert "90.5%" in shown and "98.7%" not in shown
    assert "90.5%" in shown and "Messy notes" in shown          # the hard figure, first
    assert "100%" not in shown
    # Nothing has been started: looking changes nothing.
    assert svc.doc.get("batches", {}) == {}
    assert app.problems == []
    app.close(); svc.close()


def _big_folder(tmp_path):
    """Three patients with enough pages between them to need more than one
    sitting, so the split is real rather than contrived."""
    from inkguard import granite
    root = tmp_path / "Big practice"
    body = ("Clinic letter. The patient attends regularly and remains stable on "
            "current treatment. Reviewed today; plan unchanged. ") * 60
    for n in range(3):
        d = root / f"patient-{n + 1}"
        d.mkdir(parents=True)
        for f in range(4):
            (d / f"note-{f + 1}.txt").write_text(body, encoding="utf-8")
    return root


def test_a_folder_bigger_than_one_sitting_is_split_on_whole_patients(browser, tmp_path):
    svc = make(tmp_path)
    # A practice whose reviewers take shorter sittings. The floor the engine
    # enforces (MIN_PART_PAGES) still applies, so this is a real split.
    svc.doc["settings"]["pages_per_part"] = 20
    svc._save()
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    folder = _big_folder(tmp_path)
    page.get_by_role("button", name="New batch").click()
    page.get_by_label("Folder").fill(str(folder))
    page.get_by_label("First reviewer").select_option(label="Jordan Lee")
    page.get_by_label("Second reviewer").select_option(label="Casey Park")
    _look(page)
    said = page.locator(".dialog").inner_text()
    assert "never through a patient" in said
    assert "Part 1 of" in said
    _tick_and_start(page)
    page.get_by_role("heading", name="Batch", exact=True).wait_for()
    bid = list(svc.doc["batches"])[0]
    rec = svc.doc["batches"][bid]
    assert rec["part"]["from"] == 1 and rec["part"]["fingerprint"]
    assert rec["part_label"].startswith("Part 1 of")
    assert app.problems == []
    app.close(); svc.close()


# ---- the practice batch, through the real screens ------------------------------

def test_a_reviewer_must_pass_the_practice_batch_before_a_real_one(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    # Added, but not certified: the practice batch is how that is earned.
    page.get_by_role("button", name="Settings").click()
    for name in ("Jordan Lee", "Casey Park"):
        page.get_by_label("Full name").fill(name)
        page.get_by_label("Temporary password").fill("lantern by the quiet river")
        page.get_by_role("button", name="Add person").click()
        page.get_by_role("cell", name=name).wait_for()
    page.get_by_role("button", name="Home").click()
    # Both have linked their phones, so the badge is the only thing missing:
    # the screen stops someone without a phone before this point, and the
    # service's certified check is the backstop proven here.
    from tests.test_granite import link_phones
    link_phones(svc)

    folder = _folder_of_notes(tmp_path)
    page.get_by_role("button", name="New batch").click()
    page.get_by_label("Folder").fill(str(folder))
    page.get_by_label("First reviewer").select_option(
        label="Jordan Lee — has not passed the practice batch")
    page.get_by_label("Second reviewer").select_option(
        label="Casey Park — has not passed the practice batch")
    _look(page)
    _tick_and_start(page)
    page.get_by_text("Support code: BATCH-NOT-CERTIFIED").wait_for()
    assert svc.doc.get("batches", {}) == {}
    assert app.problems == []
    app.close(); svc.close()


def test_the_practice_batch_marks_a_reviewer_who_only_ticks_the_tools_marks(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("button", name="Settings").click()
    page.get_by_label("Full name").fill("Jordan Lee")
    page.get_by_label("Temporary password").fill("lantern by the quiet river")
    page.get_by_role("button", name="Add person").click()
    page.get_by_role("cell", name="Jordan Lee").wait_for()
    sign_out(page)
    page.get_by_role("button", name="Sign in").wait_for()
    sign_in(page, name="Jordan Lee", password="lantern by the quiet river", svc=svc)

    page.get_by_role("button", name="Start the practice batch").click()
    page.get_by_role("heading", name="Practice batch").wait_for()
    said = page.locator(".panel").inner_text()
    assert "do not exist" in said                       # said plainly, before they begin
    page.get_by_role("button", name="Begin").click()

    from inkguard import pyrite
    for i in range(len(pyrite.NOTES)):
        page.get_by_text(re.compile(rf"^Practice note {i + 1} of ")).wait_for()
        # The lazy pass: answer the tool's questions, mark nothing of their own.
        for card in page.locator("li.question").all():
            card.get_by_role("button", name="Take it out").click()
        name = "Finish and mark" if i + 1 == len(pyrite.NOTES) else "Next note"
        page.get_by_role("button", name=name).click()

    page.get_by_role("heading", name="Not this time").wait_for(timeout=120_000)
    shown = page.locator(".panel").inner_text()
    assert "reading is the job" in shown
    # The marking never quotes the answer back at them.
    for word in ("Tranche", "hardware shop"):
        assert word not in shown
    assert svc.doc["accounts"][[u for u, a in svc.doc["accounts"].items()
                                if a["name"] == "Jordan Lee"][0]].get("badge") is None
    assert app.problems == []
    app.close(); svc.close()


# ---- the free tidy-up tool, through the real screens ---------------------------

def test_the_free_tool_is_reachable_before_setup(browser, tmp_path):
    """Someone who has only downloaded the app can use the tidy-up tool
    without setting anything up."""
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    page.get_by_label("Your practice, lab or team").wait_for()
    page.get_by_role("button", name="Tidy up a folder").click()
    page.get_by_role("heading", name="Tidy up a folder").wait_for()

    src = tmp_path / "already clean"
    src.mkdir()
    for n in range(3):
        (src / f"note-{n}.txt").write_text("Patient has hypertension. Reviewed in clinic.\n",
                                           encoding="utf-8")
    page.get_by_placeholder("Folder the files are in now").fill(str(src))
    page.get_by_placeholder("Folder for the tidied copies").fill(str(tmp_path / "tidied"))
    page.get_by_role("button", name="Check these files first").click()
    page.get_by_text("nothing identifying was found").wait_for()
    page.get_by_role("button", name="Make tidied copies").click()
    page.get_by_text("copied in a new order").wait_for()
    names = sorted(p.name for p in (tmp_path / "tidied").iterdir())
    assert len(names) == 4 and "What this folder is.md" in names
    assert not any(n.startswith("note-") for n in names), "new names, new order"
    # And the way back to setting up is one click.
    page.get_by_role("button", name="Back to setting up").click()
    page.get_by_label("Your practice, lab or team").wait_for()
    assert app.problems == []
    app.close(); svc.close()


def test_the_free_tool_refuses_files_that_still_look_identified(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    page.get_by_label("Your practice, lab or team").wait_for()
    page.get_by_role("button", name="Tidy up a folder").click()
    src = tmp_path / "not clean"
    src.mkdir()
    (src / "note.txt").write_text("Martha Ellison, MRN 4481203, telephone 555-0147.\n",
                                  encoding="utf-8")
    page.get_by_placeholder("Folder the files are in now").fill(str(src))
    page.get_by_placeholder("Folder for the tidied copies").fill(str(tmp_path / "tidied"))
    page.get_by_role("button", name="Check these files first").click()
    page.get_by_text("still holds something identifying").wait_for()
    shown = page.locator("body").inner_text()
    assert "a record number" in shown or "a person's name" in shown
    for word in ("Martha", "Ellison", "4481203", "555-0147"):
        assert word not in shown, "the check never quotes what it found"
    assert page.get_by_role("button", name="Make tidied copies").is_disabled()
    assert not (tmp_path / "tidied").exists()
    assert app.problems == []
    app.close(); svc.close()


def test_the_new_batch_screen_names_who_still_needs_a_phone(browser, tmp_path):
    """Coming back to open patient work needs a code from that person's own
    phone, so nobody goes on a batch without one. The screen says who, by
    name, instead of refusing after they have chosen (audit, 2026-10-10)."""
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    set_up(page, svc)
    sign_in(page, svc=svc)
    page.get_by_role("button", name="Settings").click()
    for name in ("Jordan Lee", "Casey Park"):
        page.get_by_label("Full name").fill(name)
        page.get_by_label("Temporary password").fill("lantern by the quiet river")
        page.get_by_role("button", name="Add person").click()
        page.get_by_role("cell", name=name).wait_for()
    page.get_by_role("button", name="Home").click()
    page.get_by_role("button", name="New batch").click()
    page.get_by_text("no phone linked yet").wait_for()
    shown = page.locator(".dialog").inner_text()
    assert "2 people have" in shown and "any authenticator app" in shown
    # And neither of them can be chosen until they have one.
    for who in ("Jordan Lee", "Casey Park"):
        option = page.locator("option", has_text=who).first
        assert option.is_disabled()
    assert app.problems == []
    app.close(); svc.close()


# ---- the reading room: breaks, veils and the way out --------------------------

def _into_review(page, svc, tmp_path, who="Jordan Lee"):
    set_up(page, svc)
    sign_in(page, svc=svc)
    _add_reviewers(page, svc)
    _start_batch(page, _folder_of_notes(tmp_path), svc)
    wait_prepared(page, svc)
    sign_out(page)
    page.get_by_role("button", name="Sign in").wait_for()
    sign_in(page, name=who, password="lantern by the quiet river", svc=svc)
    page.get_by_role("button", name="Review", exact=True).first.click()
    page.get_by_text("Note 1 of 3").wait_for()


def test_the_review_screen_offers_a_break_and_nothing_else_to_wander_off_to(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    _into_review(page, svc, tmp_path)
    # The only ways off this screen are a break and the emergency exit.
    assert page.get_by_role("button", name="Take a break").count() == 1
    assert page.get_by_role("button", name="Save and sign out").count() == 1
    assert page.get_by_role("button", name="Save and close").count() == 0
    assert page.locator("header.appbar").count() == 0

    page.get_by_role("button", name="Take a break").click()
    page.get_by_role("heading", name="Taking a break").wait_for()
    # The notes are veiled behind it, and their place is kept.
    assert page.locator(".review-main.veiled").count() == 1
    page.get_by_placeholder("Your password").fill("not the right one")
    page.get_by_role("button", name="Back to the notes").click()
    page.get_by_text("That password did not match").wait_for()
    page.get_by_placeholder("Your password").fill("lantern by the quiet river")
    page.get_by_role("button", name="Back to the notes").click()
    page.get_by_text("Note 1 of 3").wait_for()
    assert page.locator(".review-main.veiled").count() == 0
    bid = list(svc.doc["batches"])[0]
    assert svc.doc["batches"][bid]["sitting"]["R1"]["pauses"] == 1
    assert app.problems == []
    app.close(); svc.close()


def test_the_emergency_exit_closes_the_notes_and_signs_them_out(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    _into_review(page, svc, tmp_path)
    page.get_by_role("button", name="Save and sign out").click()
    page.get_by_role("heading", name="Save and sign out").wait_for()
    shown = page.locator(".pause-card").inner_text()
    assert "Everything you have decided is saved" in shown
    assert "stopped part way" in shown
    page.get_by_role("button", name="Keep going").click()
    page.get_by_text("Note 1 of 3").wait_for()        # a break is not a trap either way

    page.get_by_role("button", name="Save and sign out").click()
    page.get_by_role("button", name="Save and sign me out").click()
    page.get_by_role("button", name="Sign in").wait_for()
    bid = list(svc.doc["batches"])[0]
    st = svc.doc["batches"][bid]["sitting"]["R1"]
    assert st["left"] == 1 and st["left_at"]
    assert not svc._pool_keys, "the pool closed behind them"
    assert app.problems == []
    app.close(); svc.close()


def test_copying_the_note_is_refused_and_recorded(browser, tmp_path):
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    _into_review(page, svc, tmp_path)
    page.keyboard.press("Control+c")
    page.wait_for_timeout(600)
    bid = list(svc.doc["batches"])[0]
    assert svc.doc["batches"][bid]["sitting"]["R1"]["blocked"] >= 1
    assert app.problems == []
    app.close(); svc.close()


def test_the_reading_room_leaves_nothing_behind_when_it_closes(browser, tmp_path):
    """Whatever the screen pins to the window on the way in, it takes down on
    the way out — every way out. Going in and out repeatedly must leave the
    app exactly as it found it, or the standing instructions pile up and the
    app gets slower and stranger the longer it stays open (2026-10-11)."""
    svc = make(tmp_path)
    app = App(browser, svc)
    page = app.open()
    _into_review(page, svc, tmp_path)
    assert page.evaluate("document.body.dataset.room") == "open"
    assert page.locator(".veil").count() == 1

    # In and out three times, by the ordinary route.
    for _ in range(3):
        page.get_by_role("button", name="Take a break").click()
        page.get_by_role("heading", name="Taking a break").wait_for()
        page.get_by_placeholder("Your password").fill("lantern by the quiet river")
        page.get_by_role("button", name="Back to the notes").click()
        page.get_by_text("Note 1 of 3").wait_for()
    # One room, one veil — never a second copy of either.
    assert page.locator(".veil").count() == 1
    assert page.evaluate("document.body.dataset.room") == "open"

    # And out for good: nothing of the room is left on the window.
    page.get_by_role("button", name="Save and sign out").click()
    page.get_by_role("button", name="Save and sign me out").click()
    page.get_by_role("button", name="Sign in").wait_for()
    assert page.evaluate("document.body.dataset.room") is None
    assert page.locator(".veil").count() == 0
    # The sign-in screen behaves like a sign-in screen: nothing is swallowing
    # its clicks and nothing is blocking its keys.
    page.get_by_label("Name").fill("Dr. Sam Rivera")
    assert page.get_by_label("Name").input_value() == "Dr. Sam Rivera"
    assert app.problems == []
    app.close(); svc.close()
