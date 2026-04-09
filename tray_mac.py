"""
System tray for macOS using rumps.
Supports: Connected / Scanning / Disconnected / Error states.
"""

import os
import tempfile
import threading
from typing import Callable

import rumps

import config as cfg


def _make_state_icons() -> dict[str, str]:
    """Generate coloured circle PNGs for each agent state. Returns state → file path."""
    try:
        from PIL import Image, ImageDraw
        colors = {
            "connected":    "#22c55e",
            "disconnected": "#ef4444",
            "scanning":     "#eab308",
            "error":        "#ef4444",
        }
        size = 44  # 44 px → renders at 22 pt (retina-ready)
        icons: dict[str, str] = {}
        for state, color in colors.items():
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse([2, 2, size - 2, size - 2], fill=color)
            fd, path = tempfile.mkstemp(suffix=".png", prefix=f"drewq_{state}_")
            os.close(fd)
            img.save(path, "PNG")
            icons[state] = path
        return icons
    except Exception:
        return {}


_STATE_ICONS = _make_state_icons()


class TrayApp(rumps.App):
    def __init__(self, agent, on_settings: Callable[[], None]):
        initial_icon = _STATE_ICONS.get("disconnected")
        super().__init__(
            name="DREWQ Reader",
            icon=initial_icon,
            template=False,
            quit_button=None,
        )
        self._agent       = agent
        self._on_settings = on_settings
        self._lock        = threading.Lock()
        self._state       = "disconnected"
        self._pending     = ("disconnected", "Connecting…")

        self.menu = [
            rumps.MenuItem("DREWQ Reader",   callback=None),
            None,
            rumps.MenuItem("Disconnected",   callback=None),
            None,
            rumps.MenuItem("Settings…",      callback=self._settings_clicked),
            rumps.MenuItem("Reset…",         callback=self._reset_clicked),
            None,
            rumps.MenuItem("Quit",           callback=self._quit_clicked),
        ]

        agent.on_connected    = self._on_connected
        agent.on_disconnected = self._on_disconnected
        agent.on_scanning     = self._on_scanning
        agent.on_scan_done    = self._on_scan_done
        agent.on_error        = self._on_error

        self._set_state("disconnected", "Connecting…")

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
        with self._lock:
            self._state = state
        rumps.Timer(self._do_update, 0).start()
        self._pending = (state, text)

    def _do_update(self, _timer):
        state, text = self._pending
        icon_path = _STATE_ICONS.get(state)
        if icon_path:
            self.icon = icon_path
        self.menu["Disconnected"].title = text

    # ── Menu actions ──────────────────────────────────────────────────────────

    def _settings_clicked(self, _):
        threading.Thread(target=self._on_settings, daemon=True).start()

    def _reset_clicked(self, _):
        threading.Thread(target=self._do_reset, daemon=True).start()

    def _do_reset(self):
        response = rumps.alert(
            title="Reset Server URL",
            message="This will reset the server URL to the production default. Your API key will be kept.",
            ok="Reset",
            cancel="Cancel",
        )
        if response:
            c = cfg.load()
            cfg.save({**c, "server_url": cfg.DEFAULTS["server_url"]})
            self._agent.restart()

    def _quit_clicked(self, _):
        self._agent.stop()
        rumps.quit_application()
