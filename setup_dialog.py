"""
Settings dialog using osascript — native macOS dialogs with full Cmd+V support.
Works regardless of how Python was compiled.
"""

import os
import subprocess
import tempfile
from typing import Callable, Optional

import rumps
import config as cfg

_icns_path: str | None = None


def _build_dialog_icon() -> str | None:
    """
    Generate the app fingerprint icon as a temp .icns file for use in dialogs.
    Returns the path, or None if generation fails.
    """
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


def _ask(title: str, prompt: str, default: str = "") -> Optional[str]:
    """Show a native macOS input dialog. Returns text or None if cancelled."""
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
        return None  # user cancelled
    # Output format: "button returned:OK, text returned:the_value"
    for part in result.stdout.strip().split(", "):
        if part.startswith("text returned:"):
            return part[len("text returned:"):].strip()
    return ""


def open_settings(on_save: Optional[Callable[[], None]] = None) -> None:
    c = cfg.load()

    # ── API Key ───────────────────────────────────────────────────────────────
    key = _ask(
        "DREWQ Reader — Settings",
        "Paste your API key from the DREWQ dashboard\\n(API Keys → Create new key):",
        default=c.get("api_key", ""),
    )
    if key is None:
        return  # cancelled

    # ── Server URL ────────────────────────────────────────────────────────────
    url = _ask(
        "DREWQ Reader — Server URL",
        "WebSocket server URL\\n(e.g. wss://api.drewq.com/ws/reader or ws://localhost:8000/ws/reader):",
        default=c.get("server_url", ""),
    )
    if not url:
        return  # cancelled or blank — don't save incomplete config

    cfg.save({"api_key": key, "server_url": url})

    if on_save:
        on_save()


def open_first_run() -> bool:
    """Show welcome + open settings on first run. Returns True if configured."""
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
