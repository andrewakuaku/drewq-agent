"""
Settings dialog for macOS using tkinter — auto-resize text input with Cmd+V support.
"""

import tkinter as tk
from typing import Callable, Optional

import config as cfg


def _ask(title: str, prompt: str, default: str = "") -> Optional[str]:
    """Show a modal input dialog with an auto-resizing text field."""
    result = [None]
    cancelled = [False]

    root = tk.Tk()
    root.withdraw()

    dialog = tk.Toplevel(root)
    dialog.title(title)
    dialog.resizable(False, False)
    dialog.grab_set()

    w = 480
    sw, sh = dialog.winfo_screenwidth(), dialog.winfo_screenheight()

    tk.Label(
        dialog, text=prompt, wraplength=448, justify="left",
        padx=16, pady=12,
    ).pack(fill="x")

    # Auto-resize Text widget
    frame = tk.Frame(dialog, padx=16, pady=0)
    frame.pack(fill="x")

    txt = tk.Text(
        frame, width=56, height=2, wrap="char",
        font=("Menlo", 12), relief="solid", borderwidth=1,
        padx=6, pady=6,
    )
    txt.pack(fill="x")

    if default:
        txt.insert("1.0", default)
    txt.focus_set()
    txt.mark_set("insert", "end")

    def _resize(*_):
        try:
            lines = int(txt.tk.call(txt._w, "count", "-displaylines", "1.0", "end"))
        except Exception:
            lines = int(txt.index("end-1c").split(".")[0])
        txt.configure(height=max(2, min(lines, 6)))
        dialog.update_idletasks()
        dialog.geometry(f"{w}x{dialog.winfo_reqheight()}+{(sw - w) // 2}+{(sh - dialog.winfo_reqheight()) // 2}")

    txt.bind("<KeyRelease>", _resize)
    _resize()

    def on_ok(*_):
        result[0] = txt.get("1.0", "end-1c").strip()
        dialog.destroy()

    def on_cancel(*_):
        cancelled[0] = True
        dialog.destroy()

    # Return submits; block the newline insertion
    txt.bind("<Return>", lambda e: (on_ok(), "break")[1])
    txt.bind("<KP_Enter>", lambda e: (on_ok(), "break")[1])

    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=12)
    tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)
    tk.Button(btn_frame, text="OK",     width=10, command=on_ok, default="active").pack(side="left", padx=6)

    dialog.bind("<Escape>", on_cancel)
    dialog.protocol("WM_DELETE_WINDOW", on_cancel)

    root.wait_window(dialog)
    root.destroy()

    if cancelled[0]:
        return None
    return result[0]


def open_settings(on_save: Optional[Callable[[], None]] = None) -> None:
    c = cfg.load()

    # ── API Key ───────────────────────────────────────────────────────────────
    key = _ask(
        "DREWQ Reader — Settings",
        "Paste your API key from the DREWQ dashboard\n(API Keys → Create new key):",
        default=c.get("api_key", ""),
    )
    if key is None:
        return  # cancelled

    # ── Server URL ────────────────────────────────────────────────────────────
    url = _ask(
        "DREWQ Reader — Server URL",
        "WebSocket server URL\n(e.g. wss://api.drewq.com/ws/reader or ws://localhost:8000/ws/reader):",
        default=c.get("server_url", ""),
    )
    if not url:
        return  # cancelled or blank — don't save incomplete config

    cfg.save({"api_key": key, "server_url": url})

    if on_save:
        on_save()


def open_first_run() -> bool:
    """Show welcome notice then open settings on first run. Returns True if configured."""
    import rumps
    if cfg.is_configured():
        return True

    rumps.alert(
        title="Welcome to DREWQ Reader",
        message=(
            "To get started:\n\n"
            "1. Log in to your DREWQ dashboard\n"
            "2. Go to API Keys → Create a new key\n"
            "3. Paste your API key in the next screen\n"
            "4. Enter your server URL (local or production)"
        ),
    )
    open_settings()
    return cfg.is_configured()
