// The desktop wrapper. It does two things only:
//
//  1. Shows the screens (desktop/ui) in the operating system's own webview,
//     which may navigate nowhere but the app itself.
//  2. Starts the secure core (the bundled `opal-service` program) as a child
//     process and relays each request from the screens to it as ONE line on
//     its stdin, returning ONE line from its stdout. This code opens no
//     network port; requests are relayed one at a time, off the window's thread, so
//     the window never freezes while the core works.
//
// Every safety rule — accounts, roles, the idle lock, records —
// is enforced inside the core, so this wrapper holds no secrets and makes no
// decisions. If the core stops or does not answer in time, it is ended and
// the next request starts a fresh one; the screens then see the session has
// ended and ask the person to sign in.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};

/// The core refuses longer requests too; this stops a runaway screen early.
const MAX_LINE: usize = 1 << 20;
/// The longest any single request may take (a large backup or restore). A
/// core that has not answered by then is ended rather than waited on forever.
const REPLY_DEADLINE: Duration = Duration::from_secs(180);

struct Pipe {
    child: Child,
    stdin: Option<ChildStdin>,
    lines: Receiver<Result<Vec<u8>, ()>>,
}

impl Drop for Pipe {
    fn drop(&mut self) {
        // Close the core's input first: its read loop ends and it exits
        // cleanly, releasing its folder lock. Kill only if it has not gone.
        drop(self.stdin.take());
        for _ in 0..60 {
            if let Ok(Some(_)) = self.child.try_wait() {
                return;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

struct Core {
    pipe: Mutex<Option<Pipe>>,
}

fn core_path(app: &AppHandle) -> io::Result<PathBuf> {
    // Development builds only: run the core from the source tree.
    #[cfg(debug_assertions)]
    if let Ok(p) = std::env::var("DEID_CORE_DEV") {
        return Ok(PathBuf::from(p));
    }
    // Release: the core is a folder of files bundled with the app.
    let name = if cfg!(windows) { "opal-service.exe" } else { "opal-service" };
    let dir = app
        .path()
        .resource_dir()
        .map_err(|_| io::Error::new(io::ErrorKind::NotFound, "no resource folder"))?;
    Ok(dir.join("core").join(name))
}

fn spawn_core(app: &AppHandle) -> io::Result<Pipe> {
    let mut cmd = Command::new(core_path(app)?);
    cmd.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = cmd.spawn()?;
    let stdin = child.stdin.take().ok_or_else(|| io::Error::from(io::ErrorKind::BrokenPipe))?;
    let stdout = child.stdout.take().ok_or_else(|| io::Error::from(io::ErrorKind::BrokenPipe))?;
    // A reader thread hands over one complete line at a time, so a reply can
    // be waited on with a deadline.
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        loop {
            let mut buf = Vec::new();
            match (&mut reader).take(MAX_LINE as u64 + 1).read_until(b'\n', &mut buf) {
                Ok(n) if n > 0 && buf.ends_with(b"\n") => {
                    if tx.send(Ok(buf)).is_err() {
                        return;
                    }
                }
                _ => {
                    let _ = tx.send(Err(())); // stopped, cut short, or too long
                    return;
                }
            }
        }
    });
    Ok(Pipe { child, stdin: Some(stdin), lines: rx })
}

fn exchange(pipe: &mut Pipe, line: &str) -> Result<String, ()> {
    let stdin = pipe.stdin.as_mut().ok_or(())?;
    stdin
        .write_all(line.as_bytes())
        .and_then(|_| stdin.write_all(b"\n"))
        .and_then(|_| stdin.flush())
        .map_err(|_| ())?;
    match pipe.lines.recv_timeout(REPLY_DEADLINE) {
        Ok(Ok(mut buf)) => {
            while matches!(buf.last(), Some(b'\n') | Some(b'\r')) {
                buf.pop();
            }
            String::from_utf8(buf).map_err(|_| ())
        }
        Ok(Err(())) | Err(RecvTimeoutError::Timeout) | Err(RecvTimeoutError::Disconnected) => Err(()),
    }
}

fn relay(app: &AppHandle, line: &str) -> Result<String, String> {
    if line.is_empty() || line.len() > MAX_LINE || line.contains('\n') || line.contains('\r') {
        return Err("request refused".into());
    }
    let core = app.state::<Core>();
    let mut guard = core.pipe.lock().unwrap_or_else(|p| p.into_inner());
    if guard.is_none() {
        *guard = Some(spawn_core(app).map_err(|_| "the secure core could not start".to_string())?);
    }
    let pipe = guard.as_mut().expect("started above");
    match exchange(pipe, line) {
        Ok(resp) => Ok(resp),
        Err(()) => {
            *guard = None; // ended; the next request starts a fresh core
            Err("the secure core stopped".into())
        }
    }
}

/// One request line in, one response line out — never on the window's thread.
#[tauri::command]
async fn opal(line: String, app: AppHandle) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || relay(&app, &line))
        .await
        .map_err(|_| "the secure core stopped".to_string())?
}

// ---------------------------------------------------------------------------
// Feedback, sent by the person, never by the app.
//
// The app's own code makes no network connections. When a doctor chooses to
// send a feedback report, this hands the already-checked text to their own
// email program (a mailto: link), where they read it and press send
// themselves, or do not. The one destination, an email address, is fixed
// here; the screens supply the text, but never an address or a link.

const FEEDBACK_EMAIL: &str = "inkguard.project@gmail.com";
/// Long links are cut by some email programs and by Windows (about 2,000
/// characters), so the text is shortened and points to the full report,
/// which is already saved beside the released files.
const MAX_BODY: usize = 1500;

fn not_set(s: &str) -> bool {
    s.is_empty() || s.contains("{{")
}

/// Percent-encode everything but the unreserved characters (RFC 3986).
fn encode(s: &str) -> String {
    let mut out = String::with_capacity(s.len() * 3);
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{:02X}", b)),
        }
    }
    out
}

fn shorten(text: &str) -> String {
    if text.chars().count() <= MAX_BODY {
        return text.to_string();
    }
    let cut: String = text.chars().take(MAX_BODY).collect();
    format!("{cut}\n\n(The full report is saved beside the released files; please attach it.)")
}

fn feedback_link(how: &str, text: &str) -> Result<String, String> {
    let body = encode(&shorten(text));
    match how {
        "email" if !not_set(FEEDBACK_EMAIL) => Ok(format!(
            "mailto:{FEEDBACK_EMAIL}?subject={}&body={body}",
            encode("Feedback report")
        )),
        // GitHub issues are public, so the app never opens one with a report
        // in it. Reports go by email only.
        "issue" => Err("reports are sent by email only".into()),
        "email" => Err("this copy of the app has no feedback address set".into()),
        _ => Err("unknown destination".into()),
    }
}

/// Ask the operating system to open the link in the person's own program.
fn open_outside(link: &str) -> io::Result<()> {
    #[cfg(target_os = "macos")]
    let mut cmd = Command::new("open");
    #[cfg(target_os = "windows")]
    let mut cmd = {
        // rundll32 hands the link straight to the default handler, with no
        // command shell in between to reinterpret its characters.
        let mut c = Command::new("rundll32");
        c.arg("url.dll,FileProtocolHandler");
        c
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut cmd = Command::new("xdg-open");
    cmd.arg(link).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    cmd.spawn().map(|_| ())
}

#[tauri::command]
fn send_feedback(how: String, text: String) -> Result<(), String> {
    if text.len() > MAX_LINE {
        return Err("the report is too long".into());
    }
    let link = feedback_link(&how, &text)?;
    open_outside(&link).map_err(|_| "your email program could not be opened".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encoding_leaves_nothing_that_could_end_the_link_early() {
        let e = encode("a&b=c?d#e f\n%");
        assert_eq!(e, "a%26b%3Dc%3Fd%23e%20f%0A%25");
    }

    #[test]
    fn long_text_is_shortened_and_says_where_the_rest_is() {
        let s = shorten(&"x".repeat(5000));
        assert!(s.chars().count() < MAX_BODY + 120);
        assert!(s.contains("full report is saved"));
    }

    #[test]
    fn only_the_two_fixed_destinations_exist() {
        assert!(feedback_link("file", "hi").is_err());
        assert!(feedback_link("https://example.org", "hi").is_err());
    }
}

fn main() {
    tauri::Builder::default()
        // A second copy of the app brings the open window forward instead:
        // two copies would fight over the same records.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.unminimize();
                let _ = w.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .manage(Core { pipe: Mutex::new(None) })
        .invoke_handler(tauri::generate_handler![opal, send_feedback])
        .setup(|app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Inkguard")
                .inner_size(1280.0, 820.0)
                .min_inner_size(960.0, 640.0)
                // The screens never leave the app: no links out, no pages from the web.
                .on_navigation(|url| {
                    matches!(url.scheme(), "tauri") || url.host_str() == Some("tauri.localhost")
                })
                .build()?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(core) = window.app_handle().try_state::<Core>() {
                    let taken = core.pipe.lock().unwrap_or_else(|p| p.into_inner()).take();
                    drop(taken); // closes the core's input; it exits cleanly
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("the app could not start");
}
