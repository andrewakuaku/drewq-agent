"""
System tray UI for the DREWQ Reader Agent.

States
------
  disconnected  — red icon,    "Not connected"
  connected     — green icon,  "Connected"
  scanning      — yellow icon, "Reading card…"
  error         — red icon,    error message

Menu
----
  DREWQ Reader                    (non-clickable title)
  ─────────────────
  ● Connected / ✗ Disconnected    (status, non-clickable)
  ─────────────────
  Settings…
  ─────────────────
  Exit
"""

import threading
from typing import Callable

from PIL import Image, ImageDraw
import pystray


# ── Icon generation ───────────────────────────────────────────────────────────

_COLORS = {
    "connected":    "#22c55e",   # green
    "disconnected": "#ef4444",   # red
    "scanning":     "#f59e0b",   # amber
    "error":        "#ef4444",   # red
}


def _make_icon(state: str) -> Image.Image:
    size  = 64
    color = _COLORS.get(state, "#6b7280")
    img   = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)
    # Outer circle (white border)
    draw.ellipse([2, 2, size - 2, size - 2], fill="white")
    # Inner circle (state colour)
    draw.ellipse([8, 8, size - 8, size - 8], fill=color)
    return img


# ── Tray ──────────────────────────────────────────────────────────────────────

class TrayApp:
    def __init__(self, agent, on_settings: Callable[[], None]):
        self._agent        = agent
        self._on_settings  = on_settings
        self._state        = "disconnected"
        self._status_text  = "Not connected"
        self._lock         = threading.Lock()

        self._icon = pystray.Icon(
            name  = "DREWQ Reader",
            icon  = _make_icon("disconnected"),
            title = "DREWQ Reader",
            menu  = self._build_menu(),
        )

        # Wire agent callbacks
        agent.on_connected    = self._on_connected
        agent.on_disconnected = self._on_disconnected
        agent.on_scanning     = self._on_scanning
        agent.on_scan_done    = self._on_scan_done
        agent.on_error        = self._on_error

    # ── Agent callbacks (called from WS thread) ───────────────────────────────

    def _on_connected(self):
        self._set_state("connected", "Connected")

    def _on_disconnected(self):
        self._set_state("disconnected", "Reconnecting…")

    def _on_scanning(self):
        self._set_state("scanning", "Reading card…")

    def _on_scan_done(self):
        self._set_state("connected", "Connected")

    def _on_error(self, msg: str):
        short = msg[:60] + "…" if len(msg) > 60 else msg
        self._set_state("error", short)

    # ── State management ──────────────────────────────────────────────────────

    def _set_state(self, state: str, text: str):
        with self._lock:
            self._state       = state
            self._status_text = text
        self._refresh()

    def _refresh(self):
        """Update icon image and tooltip."""
        with self._lock:
            state = self._state
            text  = self._status_text
        self._icon.icon  = _make_icon(state)
        self._icon.title = f"DREWQ Reader — {text}"
        self._icon.menu  = self._build_menu()

    # ── Menu ──────────────────────────────────────────────────────────────────

    def _build_menu(self) -> pystray.Menu:
        with self._lock:
            status_label = self._status_text

        return pystray.Menu(
            pystray.MenuItem("DREWQ Reader", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(status_label, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings…", self._settings_clicked),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._exit_clicked),
        )

    # ── Actions ───────────────────────────────────────────────────────────────

    def _settings_clicked(self, icon, item):
        # Run on a thread so it doesn't block the tray loop
        threading.Thread(target=self._on_settings, daemon=True).start()

    def _exit_clicked(self, icon, item):
        self._agent.stop()
        icon.stop()

    # ── Run (blocks main thread) ──────────────────────────────────────────────

    def run(self):
        self._icon.run()
