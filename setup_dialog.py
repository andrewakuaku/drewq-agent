"""
Settings dialog using osascript — native macOS dialogs with full Cmd+V support.
Works regardless of how Python was compiled.
"""

import subprocess
from typing import Callable, Optional

import rumps
import config as cfg


def _ask(title: str, prompt: str) -> Optional[str]:
    """Show a native macOS input dialog. Returns text or None if cancelled."""
    script = (
        f'display dialog "{prompt}" '
        f'default answer "" '
        f'with icon note '
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
    )
    if key is None:
        return  # cancelled

    # ── Server URL ────────────────────────────────────────────────────────────
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
            "3. Copy the key and paste it in the next screen"
        ),
    )
    open_settings()
    return cfg.is_configured()
