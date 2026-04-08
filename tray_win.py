"""
System tray for Windows using pystray + Pillow.
Supports: Connected / Scanning / Disconnected / Error states.
"""

import threading
from typing import Callable

import pystray
from PIL import Image, ImageDraw


def _make_icon(color: str) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, size - 4, size - 4], fill=color)
    return img


_ICONS = {
    "connected":    _make_icon("#22c55e"),
    "disconnected": _make_icon("#ef4444"),
    "scanning":     _make_icon("#eab308"),
    "error":        _make_icon("#ef4444"),
}


class TrayApp:
    def __init__(self, agent, on_settings: Callable[[], None]):
        self._agent       = agent
        self._on_settings = on_settings
        self._status_text = "Connecting…"

        self._icon = pystray.Icon(
            name="DREWQ Reader",
            icon=_ICONS["disconnected"],
            title="DREWQ Reader",
            menu=pystray.Menu(
                pystray.MenuItem("DREWQ Reader", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _: self._status_text, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Settings…", self._settings_clicked),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit_clicked),
            ),
        )

        agent.on_connected    = self._on_connected
        agent.on_disconnected = self._on_disconnected
        agent.on_scanning     = self._on_scanning
        agent.on_scan_done    = self._on_scan_done
        agent.on_error        = self._on_error

    # ── Agent callbacks ───────────────────────────────────────────────────────

    def _on_connected(self):
        self._set_state("connected", "Connected")

    def _on_disconnected(self):
        self._set_state("disconnected", "Reconnecting…")

    def _on_scanning(self):
        self._set_state("scanning", "Reading card…")

    def _on_scan_done(self):
        self._set_state("connected", "Connected")

    def _on_error(self, msg: str):
        short = (msg[:55] + "…") if len(msg) > 55 else msg
        self._set_state("error", short)

    # ── State ─────────────────────────────────────────────────────────────────

    def _set_state(self, state: str, text: str):
        self._status_text = text
        self._icon.icon  = _ICONS.get(state, _ICONS["disconnected"])
        self._icon.title = f"DREWQ Reader — {text}"

    # ── Menu actions ──────────────────────────────────────────────────────────

    def _settings_clicked(self, icon, item):
        threading.Thread(target=self._on_settings, daemon=True).start()

    def _quit_clicked(self, icon, item):
        self._agent.stop()
        self._icon.stop()

    def run(self):
        self._icon.run()
