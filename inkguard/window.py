"""
The Inkguard window: four screens, in order.

    1. Welcome        and the licence
    2. Set up         the folders; the local audit trail (optional); options
    3. Working        progress; an interrupted run is started over next time
    4. Done           how many files, and where they are

    inkguard-window          (or: python -m inkguard.window)

It runs exactly what `inkguard FOLDER -o RESULTS` runs, nothing more. No
terms to accept: the Apache License 2.0 carries the warranty and liability
terms. The screens say only what is needed; the guide, the watermark on text
and Word files and the folder names carry the rest. Built on tkinter, which
comes with Python (on Linux it may need
the `python3-tk` package). It opens no network connection.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

TITLE = "Inkguard"
PAD = 12
WRAP = 660
MUTED = "#5e6572"
ALERT = "#a91636"


def _open(path: Path) -> None:
    """Show a file or folder in the computer's own viewer."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])                      # noqa: S603,S607
    elif os.name == "nt":
        os.startfile(str(path))                                    # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])                  # noqa: S603,S607


class _Lines(io.TextIOBase):
    """Collects what the run prints, line by line, for the window."""

    def __init__(self, q: queue.Queue) -> None:
        self.q, self.buf = q, ""

    def write(self, s: str) -> int:
        self.buf += s
        while "\n" in self.buf or "\r" in self.buf:
            line, self.buf = re.split(r"[\r\n]", self.buf, maxsplit=1)
            line = line.strip()
            if not line:
                continue
            m = re.fullmatch(r"(\d+)/(\d+) …", line)
            self.q.put(("progress", (int(m.group(1)), int(m.group(2)))) if m else ("line", line))
        return len(s)


def summary(out: Path) -> str:
    """The run's outcome in plain words, from its record."""
    from inkguard.cli_report import REPORT_JSON, TECH_DIR
    try:
        r = json.loads((out / TECH_DIR / REPORT_JSON).read_text(encoding="utf-8"))
        pl = r["piles"]
        return (f"{pl['received']} files received: {pl['done']} redacted with no flags, "
                f"{pl['flagged']} redacted and flagged for review, {pl['not_processed']} not processed. "
                "Ready to share: 0. A person must read every file first.")
    except (OSError, KeyError, ValueError):
        return "The run did not finish. Run it again: what it wrote is deleted and it starts over."


def pile_counts(out: Path) -> dict | None:
    from inkguard.cli_report import REPORT_JSON, TECH_DIR
    try:
        return json.loads((out / TECH_DIR / REPORT_JSON).read_text(encoding="utf-8"))["piles"]
    except (OSError, KeyError, ValueError):
        return None


def run_cli(folder: Path, out: Path, lds: bool, q: queue.Queue, who: list[str] | None = None) -> None:
    from inkguard import cli
    argv = [str(folder), "-o", str(out)] + (["--profile", "lds"] if lds else []) + list(who or [])
    sink = _Lines(q)
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            code = cli.main(argv)
    except SystemExit as exc:                     # a refused run says why on the way out
        code = exc.code if isinstance(exc.code, int) else 1
        if exc.code and not isinstance(exc.code, int):
            q.put(("line", str(exc.code)))
    except Exception as exc:                      # noqa: BLE001 — shown, never hidden
        q.put(("line", f"Stopped: {type(exc).__name__}. See SUPPORT_CODES.md in the source repository."))
        code = 1
    q.put(("done", code))


# A restrained palette: white surfaces, hairline borders, one blue for actions, and
# colour only where it carries meaning. The first folder is deliberately not
# green: no flags does not mean safe.
C = {"bg": "#ffffff", "subtle": "#f6f8fa", "border": "#d0d7de", "text": "#1f2328", "muted": "#59636e",
     "accent": "#0969da", "accent_hover": "#0757ba", "accent_soft": "#ddf4ff",
     "done": "#0969da", "flag": "#9a6700", "flag_soft": "#fff8c5", "not": "#cf222e", "not_soft": "#ffebe9"}
STEP_NAMES = ("Welcome", "Set up", "Working", "Done")
# Shown while it works, so the person is ready for what comes next.
WORKING_NOTICE = ("Inkguard is an automated tool and can make mistakes, including, but not limited to, "
                  "misreading context. Every file it redacts requires your thorough review.")


def _logo_image(tk, px: int):
    """The logo at exactly px pixels, resampled smoothly (Lanczos) from the
    large image, as a Tk image."""
    import base64
    from PIL import Image
    src = Image.open(Path(__file__).with_name("logo.png")).convert("RGBA")
    buf = io.BytesIO()
    src.resize((px, px), Image.LANCZOS).save(buf, format="PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()).decode("ascii"))


def _style(root, ttk, tkfont):
    base = tkfont.nametofont("TkDefaultFont")
    family = base.actual("family")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        tkfont.nametofont(name).configure(size=11)
    st = ttk.Style(root)
    st.theme_use("clam")
    root.configure(background=C["bg"])
    st.configure(".", background=C["bg"], foreground=C["text"], bordercolor=C["border"],
                 lightcolor=C["border"], darkcolor=C["border"], focuscolor=C["accent"], font=(family, 11))
    st.configure("TFrame", background=C["bg"])
    st.configure("Subtle.TFrame", background=C["subtle"])
    st.configure("TLabel", background=C["bg"], foreground=C["text"])
    st.configure("Subtle.TLabel", background=C["subtle"], foreground=C["muted"], font=(family, 10))
    st.configure("Brand.TLabel", background=C["subtle"], foreground=C["text"], font=(family, 12, "bold"))
    st.configure("Muted.TLabel", foreground=C["muted"])
    st.configure("Small.TLabel", foreground=C["muted"], font=(family, 9))
    st.configure("Title.TLabel", font=(family, 18, "bold"))
    st.configure("Num.TLabel", font=(family, 20, "bold"))
    st.configure("StepOn.TLabel", background=C["subtle"], foreground=C["accent"], font=(family, 10, "bold"))
    st.configure("StepOff.TLabel", background=C["subtle"], foreground=C["muted"], font=(family, 10))
    st.configure("Mono.TLabel", font=("TkFixedFont", 10))
    st.configure("TButton", background=C["subtle"], foreground=C["text"], bordercolor=C["border"],
                 padding=(14, 7), relief="flat", borderwidth=1)
    st.map("TButton", background=[("active", "#eef1f4"), ("disabled", C["subtle"])],
           foreground=[("disabled", "#8c959f")])
    st.configure("Primary.TButton", background=C["accent"], foreground="#ffffff", bordercolor=C["accent"],
                 padding=(18, 8), font=(family, 11, "bold"))
    st.map("Primary.TButton", background=[("active", C["accent_hover"]), ("disabled", "#94b9e6")],
           foreground=[("disabled", "#ffffff")])
    st.configure("TEntry", fieldbackground=C["bg"], bordercolor=C["border"], padding=6,
                 lightcolor=C["border"], darkcolor=C["border"])
    st.map("TEntry", bordercolor=[("focus", C["accent"])], lightcolor=[("focus", C["accent"])])
    st.configure("TCheckbutton", background=C["bg"], foreground=C["text"], indicatorbackground=C["bg"],
                 indicatormargin=(0, 0, 8, 0))
    st.map("TCheckbutton", indicatorbackground=[("selected", C["accent"])],
           background=[("active", C["bg"])])
    st.configure("Horizontal.TProgressbar", troughcolor="#eaeef2", background=C["accent"],
                 bordercolor="#eaeef2", lightcolor=C["accent"], darkcolor=C["accent"], thickness=8)
    st.configure("Vertical.TScrollbar", background=C["subtle"], troughcolor=C["bg"], bordercolor=C["bg"],
                 arrowcolor=C["muted"])
    return family


class App:
    def __init__(self, root, tk, ttk, filedialog):
        from tkinter import font as tkfont
        from inkguard import __version__, cli_attest
        self.root, self.tk, self.ttk, self.fd = root, tk, ttk, filedialog
        self.attest = cli_attest
        self.version = __version__
        self.family = _style(root, ttk, tkfont)
        # The logo, in the title bar and the top bar (inkguard/logo.png, drawn
        # from docs/brand/inkguard-logo.svg). Each size is resampled smoothly
        # from the large image, so it is never jagged.
        self.logo = None
        try:
            sizes = {px: _logo_image(tk, px) for px in (18, 16, 32, 48, 128)}
            root.iconphoto(True, *[sizes[px] for px in (128, 48, 32, 16)])
            self.logo, self._icons = sizes[18], sizes          # keep references alive
        except Exception:  # noqa: BLE001 — the window works without it
            pass
        root.title(f"{TITLE} {__version__}")
        root.minsize(760, 0)
        root.columnconfigure(0, weight=1)
        # Top bar: the name, the version, and where the person is.
        self.top = ttk.Frame(root, style="Subtle.TFrame", padding=(PAD * 2, PAD))
        self.top.grid(row=0, column=0, sticky="ew")
        tk.Frame(root, height=1, background=C["border"]).grid(row=1, column=0, sticky="ew")
        self.body = ttk.Frame(root, padding=(PAD * 2, PAD * 2, PAD * 2, PAD * 2))
        self.body.grid(row=2, column=0, sticky="nsew")
        self.src, self.dst = tk.StringVar(), tk.StringVar()
        self.fields = {k: tk.StringVar() for k, *_ in cli_attest.FIELDS}
        self.lds = tk.BooleanVar()
        self.scramble = tk.BooleanVar()
        self.q: queue.Queue = queue.Queue()
        self.welcome()

    # ---- building blocks
    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()
        for w in self.top.winfo_children():
            w.destroy()
        self.body.columnconfigure(1, weight=1)

    def header(self, step: int, title: str):
        ttk = self.ttk
        if self.logo is not None:
            ttk.Label(self.top, image=self.logo, style="Subtle.TLabel").pack(side="left", padx=(0, 8))
        ttk.Label(self.top, text="Inkguard", style="Brand.TLabel").pack(side="left")
        ttk.Label(self.top, text=f"  {self.version}", style="Subtle.TLabel").pack(side="left")
        for i in range(len(STEP_NAMES), 0, -1):
            ttk.Label(self.top, text=f"{i}  {STEP_NAMES[i - 1]}",
                      style="StepOn.TLabel" if i == step else "StepOff.TLabel").pack(side="right", padx=(14, 0))
        ttk.Label(self.body, text=title, style="Title.TLabel").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, PAD))

    def card(self, row: int, colour: str | None = None, soft: str | None = None, pady=(0, 8)):
        """A box with a hairline border and, optionally, a coloured left edge."""
        outer = self.tk.Frame(self.body, background=C["border"])
        outer.grid(row=row, column=0, columnspan=3, sticky="ew", pady=pady)
        if colour:
            self.tk.Frame(outer, background=colour, width=4).pack(side="left", fill="y")
        inner = self.tk.Frame(outer, background=soft or C["bg"], padx=PAD, pady=10)
        inner.pack(side="left", fill="both", expand=True, padx=(0 if colour else 1, 1), pady=1)
        return inner

    def text_in(self, parent, text, bg=None, fg=None, bold=False, size=11, wrap=WRAP - 40, mono=False):
        lab = self.tk.Label(parent, text=text, background=bg or parent.cget("background"),
                            foreground=fg or C["text"], justify="left", anchor="w", wraplength=wrap,
                            font=("TkFixedFont", size - 1) if mono else (self.family, size, "bold" if bold else "normal"))
        lab.pack(anchor="w", fill="x")
        return lab

    def buttons(self, row: int, back=None, next_=None, next_text="Next", left=None):
        f = self.ttk.Frame(self.body)
        f.grid(row=row, column=0, columnspan=3, sticky="ew", pady=(PAD + 4, 0))
        if left:
            self.ttk.Button(f, text=left[0], command=left[1]).pack(side="left")
        b = None
        if next_:
            b = self.ttk.Button(f, text=next_text, style="Primary.TButton", command=next_)
            b.pack(side="right")
        if back:
            self.ttk.Button(f, text="Back", command=back).pack(side="right", padx=(0, 8))
        return b

    # ---- 1. welcome
    def welcome(self):
        ttk = self.ttk
        self.clear()
        self.header(1, "Welcome to Inkguard")
        ttk.Label(self.body, text="Open-source software · Apache License 2.0", style="Muted.TLabel").grid(
            row=2, column=0, columnspan=3, sticky="w")
        self.buttons(3, next_=self.setup, next_text="Continue")

    # ---- 2. set up
    def setup(self):
        ttk = self.ttk
        self.clear()
        self.header(2, "Set up")

        def chooser(var, title):
            def pick():
                p = self.fd.askdirectory(title=title, mustexist=False)
                if p:
                    var.set(p)
                    if var is self.src and not self.dst.get():
                        self.dst.set(str(Path(p).parent / (Path(p).name + "-results")))
            return pick
        box = self.card(2)
        self.text_in(box, "Folders", bold=True)
        for label, var, title, hint in (
                ("Folder with your files", self.src, "Folder with your files", "Only read, never changed."),
                ("Folder for the results", self.dst, "Folder for the results", "A new folder, outside the first.")):
            r = self.tk.Frame(box, background=C["bg"])
            r.pack(fill="x", pady=(8, 0))
            self.tk.Label(r, text=label, width=24, anchor="w", background=C["bg"], foreground=C["text"],
                          font=(self.family, 11)).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x", expand=True)
            ttk.Button(r, text="Choose…", command=chooser(var, title)).pack(side="left", padx=(8, 0))
            self.tk.Label(box, text=hint, background=C["bg"], foreground=C["muted"], anchor="w",
                          font=(self.family, 9)).pack(fill="x", padx=(196, 0))
        who = self.card(3, pady=(PAD // 2, 0))
        self.text_in(who, "Local audit trail (optional)", bold=True)
        for key, _flag, _env, question, _req in self.attest.FIELDS:
            r = self.tk.Frame(who, background=C["bg"])
            r.pack(fill="x", pady=(6, 0))
            self.tk.Label(r, text=question, width=24, anchor="w", background=C["bg"], foreground=C["text"],
                          font=(self.family, 11)).pack(side="left")
            ttk.Entry(r, textvariable=self.fields[key]).pack(side="left", fill="x", expand=True)
        opts = self.card(4, pady=(PAD // 2, 0))
        self.text_in(opts, "Options", bold=True)
        ttk.Checkbutton(opts, text="Randomise file and folder names", variable=self.scramble).pack(
            anchor="w", pady=(8, 0))
        ttk.Checkbutton(opts, text="Keep dates and towns (Limited Data Set; still PHI)", variable=self.lds).pack(
            anchor="w", pady=(6, 0))
        self.msg = self.tk.StringVar()
        ttk.Label(self.body, textvariable=self.msg, foreground=C["not"], wraplength=WRAP).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(PAD // 2, 0))
        self.buttons(6, back=self.welcome, next_=self.start, next_text="Start")

    def start(self):
        if not self.src.get() or not self.dst.get():
            self.msg.set("Choose the folder with your files, and a folder for the results.")
            return
        who = []
        for k, flag, _e, _q, _r in self.attest.FIELDS:
            if self.fields[k].get().strip():
                who += [flag, self.fields[k].get().strip()]
        if self.scramble.get():
            who.append("--scramble-names")
        self.working()
        threading.Thread(target=run_cli, args=(Path(self.src.get()), Path(self.dst.get()), self.lds.get(),
                                               self.q, who), daemon=True).start()
        self.root.after(150, self.poll)

    # ---- 3. working
    def working(self):
        ttk, tk = self.ttk, self.tk
        self.clear()
        self.header(3, "Working")
        box = self.card(2)
        self.text_in(box, "Inkguard is redacting. Large folders take a while.")
        self.bar = ttk.Progressbar(box, mode="indeterminate", length=WRAP)
        self.bar.pack(fill="x", pady=(PAD, 6))
        self.bar.start(12)
        self.count, self.step_text = tk.StringVar(value="Loading…"), tk.StringVar(value="")
        tk.Label(box, textvariable=self.count, background=C["bg"], foreground=C["muted"], anchor="w",
                 font=(self.family, 10)).pack(fill="x")
        tk.Label(box, textvariable=self.step_text, background=C["bg"], foreground=C["not"], anchor="w",
                 justify="left", wraplength=WRAP - 40, font=(self.family, 10)).pack(fill="x")
        ttk.Label(self.body, text="If the computer sleeps, shuts down or loses power, run it again: what this "
                  "run wrote is deleted and it starts over from your originals.", style="Muted.TLabel",
                  wraplength=WRAP, justify="left").grid(row=3, column=0, columnspan=3, sticky="w", pady=(PAD, 0))
        warn = self.card(4, colour=C["not"], soft=C["not_soft"], pady=(PAD * 2, 0))
        self.text_in(warn, WORKING_NOTICE, fg="#82071e", bg=C["not_soft"], size=10)
        self.t0 = time.time()

    def poll(self):
        try:
            while True:
                kind, value = self.q.get_nowait()
                if kind == "progress":
                    done, total = value
                    if str(self.bar["mode"]) != "determinate":
                        self.bar.stop()
                        self.bar.configure(mode="determinate", maximum=max(total, 1))
                    self.bar["value"] = done
                    el = time.time() - self.t0
                    left = el / max(done, 1) * (total - done)
                    self.count.set(f"{done:,} of {total:,} readable files   ·   about {max(left, 0):,.0f} s left")
                elif kind == "line" and (value.startswith("[") or "did not finish" in value
                                         or "Before Inkguard" in value or "did not start" in value):
                    self.step_text.set(value[:300])         # only what the person must know
                elif kind == "done":
                    self.done(value)
                    return
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    # ---- 4. done
    def done(self, code: int):
        ttk = self.ttk
        from inkguard.cli_report import REPORT_EMAIL
        out = Path(self.dst.get())
        pl = pile_counts(out)
        self.clear()
        # "Automated": the software's part is complete; the redaction is not,
        # until a person has reviewed it.
        self.header(4, "Automated redaction finished" if pl else "The run did not finish")
        if not pl:
            box = self.card(2, colour=C["not"], soft=C["not_soft"])
            self.text_in(box, summary(out) + ("\n\n" + self.step_text.get() if self.step_text.get() else ""),
                         bg=C["not_soft"])
            self.buttons(3, back=self.setup)
            return
        ttk.Label(self.body, text=f"{pl['received']:,} files received and routed to:").grid(
            row=2, column=0, columnspan=3, sticky="w")
        ttk.Label(self.body, text=str(out), style="Mono.TLabel").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(2, PAD))
        box = self.card(4, colour=C["accent"], soft=C["accent_soft"])
        self.text_in(box, "All files are pending human review.", fg="#0a3069", bold=True, bg=C["accent_soft"])
        f = ttk.Frame(self.body)
        f.grid(row=5, column=0, columnspan=3, sticky="w", pady=(PAD, 0))
        ttk.Button(f, text="Open the results folder", style="Primary.TButton",
                   command=lambda: _open(out)).pack(side="left")
        ttk.Label(self.body, text=f"Please email the feedback report to {REPORT_EMAIL}.", style="Muted.TLabel").grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(PAD + 4, 0))
        ttk.Label(self.body, text="Open-source software · Apache License 2.0", style="Small.TLabel").grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))


def main() -> int:
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except ImportError:
        print("This window needs tkinter. On Linux: sudo apt install python3-tk. "
              "Or use the command line: inkguard --help")
        return 1
    root = tk.Tk()
    App(root, tk, ttk, filedialog)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
