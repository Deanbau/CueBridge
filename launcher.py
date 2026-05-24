"""
launcher.py — CueBridge launcher window.
Subprocess model: launcher (tkinter main thread) spawns server as child process.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext

VERSION = "1.0.0"

BG      = "#13141f"
BG2     = "#1e2030"
BG3     = "#0d0e18"
BORDER  = "#2a2d45"
ORANGE  = "#f59e0b"
ORG_H   = "#d97706"
GREEN   = "#4ade80"
RED     = "#f87171"
FG      = "#e2e8f0"
FG2     = "#475569"
FG3     = "#94a3b8"
MONO    = ("Courier New", 9) if sys.platform == "win32" else ("Menlo", 10)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_saved_port() -> int:
    for p in [Path("cuebridge_config.json")]:
        if p.exists():
            try:
                return int(json.loads(p.read_text()).get("web_ui_port", 8080))
            except Exception:
                pass
    return 8080


def _asset(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


def _btn(parent, text, bg, fg, bg_h, cmd, font_size=12, bold=True,
         px=20, py=9, side=None, fill=None):
    """Frame+Label button — renders correctly on macOS (native tk.Button ignores bg)."""
    weight = "bold" if bold else "normal"
    f = tk.Frame(parent, bg=bg, cursor="hand2")
    lbl = tk.Label(f, text=text, bg=bg, fg=fg,
                   font=("Helvetica", font_size, weight), padx=px, pady=py)
    lbl.pack(fill="both")

    def _e(_):
        f.config(bg=bg_h); lbl.config(bg=bg_h)
    def _l(_):
        f.config(bg=bg); lbl.config(bg=bg)
    def _c(_):
        cmd()

    for w in (f, lbl):
        w.bind("<Enter>", _e)
        w.bind("<Leave>", _l)
        w.bind("<Button-1>", _c)

    if side or fill:
        f.pack(side=side or "left", fill=fill)
    return f, lbl


# ── App ───────────────────────────────────────────────────────────────────────

class LauncherApp:
    def __init__(self):
        self.process: subprocess.Popen | None = None
        self.log_q: queue.Queue = queue.Queue()
        self._running = False
        self._current_port = 8080

        self.root = tk.Tk()
        self.root.title("CueBridge")
        self.root.geometry("460x600")
        self.root.minsize(460, 600)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._poll_log()
        self._bring_front()

    def _bring_front(self):
        try:
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    # ── UI build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        r = self.root

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(r, bg=BG)
        hdr.pack(fill="x", padx=28, pady=(28, 0))

        # Icon
        try:
            ico = tk.PhotoImage(file=_asset("icon_launcher.png"))
            ico_lbl = tk.Label(hdr, image=ico, bg=BG)
            ico_lbl.image = ico
            ico_lbl.pack(side="left", padx=(0, 18))
        except Exception:
            pass

        # Title block
        tb = tk.Frame(hdr, bg=BG)
        tb.pack(side="left", anchor="center")

        tr = tk.Frame(tb, bg=BG)
        tr.pack(anchor="w")
        tk.Label(tr, text="Cue",    font=("Helvetica", 28, "bold"), fg=ORANGE, bg=BG).pack(side="left")
        tk.Label(tr, text="Bridge", font=("Helvetica", 28, "bold"), fg=FG,     bg=BG).pack(side="left")
        tk.Label(tb, text="OSC Switcher Bridge",
                 font=("Helvetica", 11), fg=FG2, bg=BG).pack(anchor="w", pady=(2, 0))

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(r, bg=BORDER, height=1).pack(fill="x", padx=28, pady=(22, 20))

        # ── Port row ──────────────────────────────────────────────────────────
        pr = tk.Frame(r, bg=BG)
        pr.pack(fill="x", padx=28)

        tk.Label(pr, text="Web UI Port", font=("Helvetica", 12),
                 fg=FG3, bg=BG).pack(side="left")

        self.port_var = tk.StringVar(value=str(_read_saved_port()))
        self.port_entry = tk.Entry(
            pr, textvariable=self.port_var, width=7,
            font=("Helvetica", 13),
            bg=BG2, fg="#ffffff", insertbackground="#ffffff",
            relief="flat",
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ORANGE,
        )
        self.port_entry.pack(side="right", ipady=6, ipadx=6)

        # ── Buttons ───────────────────────────────────────────────────────────
        self._btn_row = tk.Frame(r, bg=BG)
        self._btn_row.pack(fill="x", padx=28, pady=(16, 0))

        self._launch_btn, _ = _btn(
            self._btn_row, "Launch", ORANGE, "#fff", ORG_H, self._launch,
            font_size=13, px=28, py=10,
        )
        self._launch_btn.pack(side="left")

        self._browser_btn, _ = _btn(
            self._btn_row, "Open Browser", BG2, FG, BORDER, self._open_browser,
            font_size=12, px=18, py=10, bold=False,
        )
        self._stop_btn, self._stop_lbl = _btn(
            self._btn_row, "Stop", BG2, RED, BORDER, self._stop,
            font_size=12, px=18, py=10, bold=False,
        )

        # ── Status ────────────────────────────────────────────────────────────
        sf = tk.Frame(r, bg=BG)
        sf.pack(fill="x", padx=28, pady=(14, 0))
        self._dot = tk.Label(sf, text="●", font=("Helvetica", 11), fg=FG2, bg=BG)
        self._dot.pack(side="left")
        self._status = tk.Label(sf, text="  Not running",
                                font=("Helvetica", 11), fg=FG2, bg=BG)
        self._status.pack(side="left")

        # ── Log ───────────────────────────────────────────────────────────────
        tk.Label(r, text="LOG", font=("Helvetica", 8, "bold"),
                 fg=FG2, bg=BG, anchor="w").pack(fill="x", padx=28, pady=(18, 4))

        log_border = tk.Frame(r, bg=BORDER, padx=1, pady=1)
        log_border.pack(fill="both", expand=True, padx=28, pady=(0, 0))

        self._log = scrolledtext.ScrolledText(
            log_border, font=MONO, bg=BG3, fg=FG3,
            insertbackground="#fff", relief="flat",
            wrap="word", state="disabled", padx=10, pady=8,
        )
        self._log.pack(fill="both", expand=True)
        self._log.tag_config("err",  foreground=RED)
        self._log.tag_config("warn", foreground=ORANGE)
        self._log.tag_config("ok",   foreground=GREEN)
        self._log.tag_config("dim",  foreground=FG2)

        # ── Footer ────────────────────────────────────────────────────────────
        tk.Label(r, text=f"v{VERSION}", font=("Helvetica", 9),
                 fg=FG2, bg=BG).pack(side="bottom", pady=10)

        r.bind("<Return>", lambda _: self._launch() if not self._running else None)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _launch(self):
        if self._running:
            return
        try:
            port = int(self.port_var.get())
            assert 1024 <= port <= 65535
        except Exception:
            self._log_line("✗  Invalid port number.", "err")
            return

        self._current_port = port
        self._clear_log()
        self._log_line(f"Starting CueBridge on port {port}…", "dim")

        cmd = [sys.executable, "--server-mode", "--port", str(port)]
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except Exception as exc:
            self._log_line(f"✗  Failed to start: {exc}", "err")
            return

        self._running = True
        self._set_ui_state(running=True)
        threading.Thread(target=self._read_proc, daemon=True).start()

    def _stop(self):
        if self.process:
            self.process.terminate()
            self.process = None
        self._on_server_stopped()

    def _open_browser(self):
        import webbrowser
        webbrowser.open(f"http://localhost:{self._current_port}")

    def _on_close(self):
        self._stop()
        self.root.destroy()

    # ── Subprocess reader ─────────────────────────────────────────────────────

    def _read_proc(self):
        try:
            for line in self.process.stdout:
                self.log_q.put(("line", line.rstrip()))
        except Exception:
            pass
        self.log_q.put(("done", None))

    def _poll_log(self):
        try:
            while True:
                kind, data = self.log_q.get_nowait()
                if kind == "done":
                    self._on_server_stopped()
                    break
                lo = data.lower()
                tag = None
                if any(x in lo for x in ("error", "critical", "in use", "failed")):
                    tag = "err"
                elif any(x in lo for x in ("warning", "warn")):
                    tag = "warn"
                elif any(x in lo for x in ("web ui available", "running on", "nicegui ready", "started")):
                    tag = "ok"
                    self._set_status_running()
                self._log_line(data, tag)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_log)

    # ── UI state ──────────────────────────────────────────────────────────────

    def _log_line(self, text: str, tag: str | None = None):
        self._log.config(state="normal")
        self._log.insert("end", text + "\n", tag or "")
        self._log.see("end")
        self._log.config(state="disabled")

    def _clear_log(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")

    def _set_status_running(self):
        self._dot.config(fg=GREEN)
        self._status.config(text=f"  Running · http://localhost:{self._current_port}", fg=GREEN)

    def _on_server_stopped(self):
        self._running = False
        self.process = None
        self._dot.config(fg=RED)
        self._status.config(text="  Stopped", fg=RED)
        self._log_line("── Server stopped ──", "err")
        self._set_ui_state(running=False)

    def _set_ui_state(self, running: bool):
        if running:
            self._launch_btn.pack_forget()
            self.port_entry.config(state="disabled",
                                   bg="#0a0b14", highlightbackground=BORDER)
            self._browser_btn.pack(side="left")
            self._stop_btn.pack(side="right")
        else:
            self._browser_btn.pack_forget()
            self._stop_btn.pack_forget()
            self._launch_btn.pack(side="left")
            self.port_entry.config(state="normal", bg=BG2)

    def run(self):
        self.root.mainloop()


def run_launcher():
    LauncherApp().run()
