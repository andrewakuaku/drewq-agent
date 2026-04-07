"""
Settings dialog — tkinter window for configuring API key and server URL.

Opens on first run and when the user clicks Settings in the tray menu.
"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

import config as cfg


def open_settings(on_save: Optional[Callable[[], None]] = None) -> None:
    """
    Open the settings window. Blocks until closed.
    `on_save` is called if the user saves changes.
    """
    c = cfg.load()

    root = tk.Tk()
    root.title("DREWQ Reader — Settings")
    root.resizable(False, False)
    root.geometry("440x260")

    try:
        root.iconbitmap(default="assets/icon.ico")
    except Exception:
        pass

    # ── Header ────────────────────────────────────────────────────────────────
    header = tk.Frame(root, bg="#1a1a1a", height=52)
    header.pack(fill="x")
    tk.Label(
        header, text="DREWQ Reader", fg="white", bg="#1a1a1a",
        font=("Helvetica", 14, "bold"),
    ).pack(side="left", padx=16, pady=12)

    # ── Form ──────────────────────────────────────────────────────────────────
    form = tk.Frame(root, padx=20, pady=16)
    form.pack(fill="both", expand=True)

    # API Key
    tk.Label(form, text="API Key", font=("Helvetica", 10, "bold"),
             anchor="w").grid(row=0, column=0, sticky="w", pady=(0, 2))
    tk.Label(form, text="Create a key in your DREWQ dashboard under API Keys.",
             font=("Helvetica", 9), fg="#6b7280", anchor="w",
             wraplength=380).grid(row=1, column=0, sticky="w", pady=(0, 6))

    api_key_var = tk.StringVar(value=c.get("api_key", ""))
    api_entry = ttk.Entry(form, textvariable=api_key_var, width=52, show="•")
    api_entry.grid(row=2, column=0, sticky="ew", pady=(0, 14))

    show_var = tk.BooleanVar(value=False)
    def toggle_show():
        api_entry.config(show="" if show_var.get() else "•")
    ttk.Checkbutton(form, text="Show key", variable=show_var,
                    command=toggle_show).grid(row=3, column=0, sticky="w", pady=(0, 14))

    # Server URL
    tk.Label(form, text="Server URL", font=("Helvetica", 10, "bold"),
             anchor="w").grid(row=4, column=0, sticky="w", pady=(0, 2))
    server_var = tk.StringVar(value=c.get("server_url", cfg.DEFAULTS["server_url"]))
    ttk.Entry(form, textvariable=server_var, width=52).grid(row=5, column=0, sticky="ew")

    form.columnconfigure(0, weight=1)

    # ── Buttons ───────────────────────────────────────────────────────────────
    btn_frame = tk.Frame(root, padx=20, pady=10)
    btn_frame.pack(fill="x", side="bottom")

    def save():
        key = api_key_var.get().strip()
        url = server_var.get().strip()
        if not key:
            messagebox.showerror("Missing API Key", "Please enter your API key.", parent=root)
            return
        if not url.startswith("ws"):
            messagebox.showerror("Invalid URL",
                                 "Server URL must start with ws:// or wss://", parent=root)
            return
        cfg.save({"api_key": key, "server_url": url})
        if on_save:
            on_save()
        messagebox.showinfo("Saved", "Settings saved. Reconnecting…", parent=root)
        root.destroy()

    ttk.Button(btn_frame, text="Save", command=save).pack(side="right")
    ttk.Button(btn_frame, text="Cancel", command=root.destroy).pack(side="right", padx=6)

    # Focus on API key field if empty
    if not c.get("api_key"):
        api_entry.focus_set()

    root.mainloop()


def open_first_run() -> bool:
    """
    Show a first-run welcome dialog.
    Returns True if the user saved a valid config.
    """
    c = cfg.load()
    if c.get("api_key"):
        return True

    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo(
        "DREWQ Reader Setup",
        "Welcome to DREWQ Reader!\n\n"
        "To get started:\n"
        "1. Log in to your DREWQ dashboard\n"
        "2. Go to API Keys → Create a new key\n"
        "3. Copy the key and paste it in the next screen",
    )
    root.destroy()

    open_settings()
    return cfg.is_configured()
