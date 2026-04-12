"""
Settings dialog for macOS.

Uses tkinter (auto-resize text field) when available, otherwise falls back to
native osascript dialogs. The osascript path works regardless of how Python was
compiled — it was the original implementation and remains the safe default.
"""

import os
import subprocess
import tempfile
from typing import Callable, Optional

import config as cfg

# ── Tkinter availability check ────────────────────────────────────────────────

try:
    import tkinter as tk
    _HAS_TKINTER = True
except ImportError:
    _HAS_TKINTER = False


# ── Tkinter dialog (auto-resize) ──────────────────────────────────────────────

def _ask_tk(title: str, prompt: str, default: str = "") -> Optional[str]:
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
        dialog.geometry(
            f"{w}x{dialog.winfo_reqheight()}"
            f"+{(sw - w) // 2}+{(sh - dialog.winfo_reqheight()) // 2}"
        )

    txt.bind("<KeyRelease>", _resize)
    _resize()

    def on_ok(*_):
        result[0] = txt.get("1.0", "end-1c").strip()
        dialog.destroy()

    def on_cancel(*_):
        cancelled[0] = True
        dialog.destroy()

    txt.bind("<Return>",   lambda e: (on_ok(), "break")[1])
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


# ── osascript dialog (fallback) ───────────────────────────────────────────────

_icns_path: str | None = None


def _build_dialog_icon() -> str | None:
    global _icns_path
    if _icns_path and os.path.exists(_icns_path):
        return _icns_path
    try:
        from PIL import Image, ImageDraw

        def _make(size: int) -> Image.Image:
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                [0, 0, size - 1, size - 1],
                radius=max(4, size // 8),
                fill="#000000",
            )
            cx, cy = size * 0.5, size * 0.54
            lw = max(1, round(size / 20))
            for frac in [0.10, 0.18, 0.26, 0.34, 0.42]:
                r = size * frac
                draw.arc([cx - r, cy - r, cx + r, cy + r],
                         start=200, end=340, fill=(255, 255, 255, 255), width=lw)
            return img

        iconset = os.path.join(tempfile.gettempdir(), "drewq_icon.iconset")
        os.makedirs(iconset, exist_ok=True)
        for base in [16, 32, 128, 256, 512]:
            _make(base).save(os.path.join(iconset, f"icon_{base}x{base}.png"))
            _make(base * 2).save(os.path.join(iconset, f"icon_{base}x{base}@2x.png"))

        icns = os.path.join(tempfile.gettempdir(), "drewq_icon.icns")
        subprocess.run(
            ["iconutil", "-c", "icns", iconset, "-o", icns],
            check=True, capture_output=True,
        )
        _icns_path = icns
        return icns
    except Exception:
        return None


def _ask_osascript(title: str, prompt: str, default: str = "") -> Optional[str]:
    icns = _build_dialog_icon()
    icon_clause = f'with icon (POSIX file "{icns}")' if icns else "with icon note"
    safe_default = default.replace('"', '\\"')
    script = (
        f'display dialog "{prompt}" '
        f'default answer "{safe_default}" '
        f'{icon_clause} '
        f'buttons {{"Cancel", "OK"}} '
        f'default button "OK" '
        f'with title "{title}"'
    )
    result = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    for part in result.stdout.strip().split(", "):
        if part.startswith("text returned:"):
            return part[len("text returned:"):].strip()
    return ""


# ── Public interface ──────────────────────────────────────────────────────────

def _ask(title: str, prompt: str, default: str = "") -> Optional[str]:
    if _HAS_TKINTER:
        return _ask_tk(title, prompt, default)
    return _ask_osascript(title, prompt, default)


def open_settings(on_save: Optional[Callable[[], None]] = None) -> None:
    c = cfg.load()

    key = _ask(
        "DREWQ Reader — Settings",
        "Paste your API key from the DREWQ dashboard\n(API Keys → Create new key):",
        default=c.get("api_key", ""),
    )
    if key is None:
        return  # cancelled

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
