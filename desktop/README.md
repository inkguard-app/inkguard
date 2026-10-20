# The desktop app

> Applies to the desktop app, in development and not part of 0.25.0. Version
> 0.25.0 is the command line and a small window; it writes ordinary, unencrypted
> files that you keep on an encrypted disk.

Three parts, each with one job:

| Part | Where | Job |
|---|---|---|
| The screens | `desktop/ui/` | Plain HTML, CSS and JavaScript (no build step, no web libraries except a vendored MIT QR-code drawer). They show what the core says and send what the person does. They hold no keys and no patient data; the session token lives in memory only. |
| The wrapper | `desktop/src-tauri/` | A Tauri 2 program (Rust). Opens the operating system's own webview on the screens, refuses to navigate anywhere else, and relays each request from the screens to the core as one line over the core's stdin/stdout. Its own code opens no network port and makes no decisions. |
| The secure core | `inkguard/opal.py`, started by `desktop/opal_service.py` | Every rule: accounts, roles, the idle lock, records, backups, retiring. Packaged per platform as the `opal-service` program that ships beside the wrapper. |

Security settings that matter: a strict content security policy (no inline
script or style, nothing loaded from anywhere but the app, no network
connections from the screens), a frozen JavaScript prototype, navigation
locked to the app, the screens may only call the core and open a file or
folder picker (no shell, file system or network permissions). One exception,
chosen by the person: a finished feedback report can be handed to their own
email program, addressed to the project's feedback address, which is fixed
when the app is built. The person reads it there and sends it themselves.

## Building

Installers are built by `.github/workflows/desktop.yml` on Windows and macOS
build machines (run it by hand): it tests the core,
packages it with PyInstaller as the `opal-service` folder, and builds `.msi`/`.exe` and
`.dmg` installers with the Tauri CLI. Signing needs an Apple Developer
account (macOS) and a Windows signing service, kept in the repository's
encrypted secrets; without them the installers work but the operating system
shows an unknown-publisher warning.

## Running from source (development)

```
cd desktop/src-tauri
cargo build
PHI_ROOT_KEY=<64 hex> PHI_APP_DATA=/tmp/deid DEID_DEV_ANCHOR_DIR=/tmp/deid-keychain \
  DEID_CORE_DEV=$(pwd)/../dev/run-core.sh ./target/debug/inkguard-desktop
```

`desktop/dev/run-core.sh` runs the core from the source tree for debug
builds only: a release build ignores `DEID_CORE_DEV` and runs the packaged
core from its own `core/` folder (PyInstaller `--onedir`, placed there by the
release workflow). `DEID_DEV_ANCHOR_DIR` stands in for the protected key
store on a Linux build box; a packaged core ignores it.

## Tests

`tests/test_desktop_ui.py` drives the real screens in a real browser against
the real core — first-run setup (practice, account, recovery passphrase), sign-in with
a phone code, the lock screen, people, backups and the restore drill,
restore after lost records, retiring — with no network port: pages are
served from memory and requests go through an in-process binding, one JSON
line each, exactly as in the app.
