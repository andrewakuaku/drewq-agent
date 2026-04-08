"""
Settings dialog for Windows — tkinter input dialogs.
Ctrl+V paste works natively in tkinter Entry widgets.
"""

import tkinter as tk
from typing import Callable, Optional

import config as cfg


def _ask(title: str, prompt: str) -> Optional[str]:
    """Show a modal input dialog. Returns text or None if cancelled."""
    result = [None]

    root = tk.Tk()
    root.withdraw()

    dialog = tk.Toplevel(root)
    dialog.title(title)
    dialog.resizable(False, False)
    dialog.grab_set()

    w, h = 420, 140
    x = (dialog.winfo_screenwidth() - w) // 2
    y = (dialog.winfo_screenheight() - h) // 2
    dialog.geometry(f"{w}x{h}+{x}+{y}")

    tk.Label(dialog, text=prompt, wraplength=390, justify="left",
             padx=16, pady=12).pack(fill="x")

    entry = tk.Entry(dialog, width=50)
    entry.pack(padx=16, pady=(0, 8))
    entry.focus_set()

    def on_ok():
        result[0] = entry.get()
        dialog.destroy()

    def on_cancel():
        dialog.destroy()

    btn_frame = tk.Frame(dialog)
    btn_frame.pack(pady=(0, 12))
    tk.Button(btn_frame, text="Cancel", width=10, command=on_cancel).pack(side="left", padx=6)
    tk.Button(btn_frame, text="OK", width=10, command=on_ok, default="active").pack(side="left", padx=6)

    dialog.bind("<Return>", lambda _: on_ok())
    dialog.bind("<Escape>", lambda _: on_cancel())
    dialog.protocol("WM_DELETE_WINDOW", on_cancel)

    root.wait_window(dialog)
    root.destroy()
    return result[0]


def open_settings(on_save: Optional[Callable[[], None]] = None) -> None:
    c = cfg.load()

    key = _ask(
        "DREWQ Reader — Settings",
        "Paste your API key from the DREWQ dashboard\n(API Keys → Create new key):",
    )
    if key is None:
        return

    url = _ask(
        "DREWQ Reader — Server URL",
        "WebSocket server URL (leave blank to keep current):",
    )
    if url is None:
        url = c.get("server_url", cfg.DEFAULTS["server_url"])
    elif url == "":
        url = c.get("server_url", cfg.DEFAULTS["server_url"])

    cfg.save({"api_key": key, "server_url": url})

    if on_save:
        on_save()
