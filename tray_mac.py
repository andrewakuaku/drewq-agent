"""
System tray for macOS using rumps.
Supports: Connected / Scanning / Disconnected / Error states.
"""

import os
import subprocess
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Callable

import rumps

import config as cfg
from scanner import list_readers


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

_LAUNCH_AGENT_LABEL = "com.drewq.reader"
_LAUNCH_AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCH_AGENT_LABEL}.plist"


def _get_launch_agent_plist_content() -> str:
    exe = os.path.realpath(os.sys.executable)
    script = os.path.realpath(os.path.join(os.path.dirname(__file__), "main.py"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>{_LAUNCH_AGENT_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{exe}</string>
    <string>{script}</string>
  </array>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <false/>
  <key>StandardOutPath</key>   <string>/tmp/drewq_reader.log</string>
  <key>StandardErrorPath</key> <string>/tmp/drewq_reader.log</string>
</dict>
</plist>
"""


def _login_item_enabled() -> bool:
    return _LAUNCH_AGENT_PLIST.exists()


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
            rumps.MenuItem("DREWQ Reader",      callback=None),
            rumps.MenuItem("—",                 callback=None),  # operator name placeholder
            None,
            rumps.MenuItem("Disconnected",      callback=None),
            rumps.MenuItem("Reader: —",         callback=None),
            None,
            rumps.MenuItem("Open Dashboard",    callback=self._open_dashboard_clicked),
            rumps.MenuItem("Reconnect",         callback=self._reconnect_clicked),
            None,
            rumps.MenuItem("Settings…",         callback=self._settings_clicked),
            rumps.MenuItem("Reset…",            callback=self._reset_clicked),
            rumps.MenuItem("Start at Login",    callback=self._login_item_clicked),
            rumps.MenuItem("Copy API Key",      callback=self._copy_api_key_clicked),
            None,
            rumps.MenuItem("Quit",              callback=self._quit_clicked),
        ]

        # Reflect current login-item state in the checkmark
        self.menu["Start at Login"].state = _login_item_enabled()

        agent.on_connected    = self._on_connected
        agent.on_disconnected = self._on_disconnected
        agent.on_scanning     = self._on_scanning
        agent.on_scan_done    = self._on_scan_done
        agent.on_error        = self._on_error
        agent.on_hello        = self._on_hello

        self._set_state("disconnected", "Connecting…")
        self._refresh_reader_name()

    # ── Agent callbacks ───────────────────────────────────────────────────────

    def _on_hello(self, name: str, org: str):
        label = org if org else name
        rumps.Timer(lambda _: self._update_name(label), 0).start()

    def _update_name(self, label: str):
        self.menu["—"].title = label

    def _on_connected(self):
        self._set_state("connected", "Connected")
        threading.Thread(target=self._refresh_reader_name, daemon=True).start()

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

    def _refresh_reader_name(self):
        try:
            readers = list_readers()
            name = readers[0] if readers else "No reader detected"
        except Exception:
            name = "No reader detected"
        # Shorten long USB reader names
        if len(name) > 35:
            name = name[:35] + "…"
        rumps.Timer(lambda _: self._update_reader_label(name), 0).start()

    def _update_reader_label(self, name: str):
        self.menu["Reader: —"].title = f"Reader: {name}"

    # ── Menu actions ──────────────────────────────────────────────────────────

    def _open_dashboard_clicked(self, _):
        c = cfg.load()
        server_url = c.get("server_url", "")
        # Derive the HTTP dashboard URL from the WebSocket server URL
        if server_url.startswith("wss://"):
            base = "https://" + server_url[len("wss://"):]
        elif server_url.startswith("ws://"):
            base = "http://" + server_url[len("ws://"):]
        else:
            base = server_url
        # Strip the /ws/reader path if present
        for suffix in ("/ws/reader", "/ws"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        webbrowser.open(base or "https://app.drewq.com")

    def _reconnect_clicked(self, _):
        threading.Thread(target=self._agent.restart, daemon=True).start()

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

    def _login_item_clicked(self, sender):
        threading.Thread(target=self._toggle_login_item, args=(sender,), daemon=True).start()

    def _toggle_login_item(self, sender):
        if _login_item_enabled():
            try:
                subprocess.run(
                    ["launchctl", "unload", str(_LAUNCH_AGENT_PLIST)],
                    capture_output=True,
                )
                _LAUNCH_AGENT_PLIST.unlink(missing_ok=True)
            except Exception:
                pass
        else:
            try:
                _LAUNCH_AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
                _LAUNCH_AGENT_PLIST.write_text(_get_launch_agent_plist_content())
                subprocess.run(
                    ["launchctl", "load", str(_LAUNCH_AGENT_PLIST)],
                    capture_output=True,
                )
            except Exception:
                pass
        enabled = _login_item_enabled()
        rumps.Timer(lambda _: self._update_login_checkmark(enabled), 0).start()

    def _update_login_checkmark(self, enabled: bool):
        self.menu["Start at Login"].state = enabled

    def _copy_api_key_clicked(self, _):
        threading.Thread(target=self._do_copy_api_key, daemon=True).start()

    def _do_copy_api_key(self):
        c = cfg.load()
        api_key = c.get("api_key", "")
        if not api_key:
            rumps.notification(
                title="DREWQ Reader",
                subtitle="",
                message="No API key saved. Open Settings to add one.",
            )
            return
        try:
            subprocess.run(["pbcopy"], input=api_key.encode(), check=True)
            rumps.notification(
                title="DREWQ Reader",
                subtitle="",
                message="API key copied to clipboard.",
            )
        except Exception:
            pass

    def _quit_clicked(self, _):
        self._agent.stop()
        rumps.quit_application()
