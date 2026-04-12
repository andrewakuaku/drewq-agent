"""
System tray for Windows using pystray + Pillow.
Supports: Connected / Scanning / Disconnected / Error states.
"""

import threading
import webbrowser
from typing import Callable

import pystray
from PIL import Image, ImageDraw

import config as cfg
from scanner import list_readers


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

_REG_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_REG_VALUE   = "DREWQReader"


def _login_item_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_RUN_KEY) as key:
            winreg.QueryValueEx(key, _REG_VALUE)
            return True
    except Exception:
        return False


class TrayApp:
    def __init__(self, agent, on_settings: Callable[[], None]):
        self._agent        = agent
        self._on_settings  = on_settings
        self._status_text  = "Connecting…"
        self._operator     = ""
        self._reader_name  = "No reader detected"

        self._icon = pystray.Icon(
            name="DREWQ Reader",
            icon=_ICONS["disconnected"],
            title="DREWQ Reader",
            menu=pystray.Menu(
                pystray.MenuItem("DREWQ Reader",  None, enabled=False),
                pystray.MenuItem(lambda _: self._operator or "—", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(lambda _: self._status_text,  None, enabled=False),
                pystray.MenuItem(lambda _: f"Reader: {self._reader_name}", None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Open Dashboard", self._open_dashboard_clicked),
                pystray.MenuItem("Reconnect",      self._reconnect_clicked),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Settings…",      self._settings_clicked),
                pystray.MenuItem("Reset…",         self._reset_clicked),
                pystray.MenuItem(
                    "Start at Login",
                    self._login_item_clicked,
                    checked=lambda _: _login_item_enabled(),
                ),
                pystray.MenuItem("Copy API Key",   self._copy_api_key_clicked),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit_clicked),
            ),
        )

        agent.on_connected    = self._on_connected
        agent.on_disconnected = self._on_disconnected
        agent.on_scanning     = self._on_scanning
        agent.on_scan_done    = self._on_scan_done
        agent.on_error        = self._on_error
        agent.on_hello        = self._on_hello

        threading.Thread(target=self._refresh_reader_name, daemon=True).start()

    # ── Agent callbacks ───────────────────────────────────────────────────────

    def _on_hello(self, name: str, org: str):
        self._operator = org if org else name
        self._icon.update_menu()

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
        self._status_text = text
        self._icon.icon  = _ICONS.get(state, _ICONS["disconnected"])
        self._icon.title = f"DREWQ Reader — {text}"

    def _refresh_reader_name(self):
        try:
            readers = list_readers()
            name = readers[0] if readers else "No reader detected"
        except Exception:
            name = "No reader detected"
        if len(name) > 35:
            name = name[:35] + "…"
        self._reader_name = name
        self._icon.update_menu()

    # ── Menu actions ──────────────────────────────────────────────────────────

    def _open_dashboard_clicked(self, icon, item):
        c = cfg.load()
        server_url = c.get("server_url", "")
        if server_url.startswith("wss://"):
            base = "https://" + server_url[len("wss://"):]
        elif server_url.startswith("ws://"):
            base = "http://" + server_url[len("ws://"):]
        else:
            base = server_url
        for suffix in ("/ws/reader", "/ws"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        webbrowser.open(base or "https://app.drewq.com")

    def _reconnect_clicked(self, icon, item):
        threading.Thread(target=self._agent.restart, daemon=True).start()

    def _settings_clicked(self, icon, item):
        threading.Thread(target=self._on_settings, daemon=True).start()

    def _reset_clicked(self, icon, item):
        threading.Thread(target=self._do_reset, daemon=True).start()

    def _do_reset(self):
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        confirmed = messagebox.askyesno(
            "Reset Server URL",
            "This will reset the server URL to the production default.\nYour API key will be kept.",
        )
        root.destroy()
        if confirmed:
            c = cfg.load()
            cfg.save({**c, "server_url": cfg.DEFAULTS["server_url"]})
            self._agent.restart()

    def _login_item_clicked(self, icon, item):
        threading.Thread(target=self._toggle_login_item, daemon=True).start()

    def _toggle_login_item(self):
        try:
            import sys
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _REG_RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                if _login_item_enabled():
                    winreg.DeleteValue(key, _REG_VALUE)
                else:
                    exe   = sys.executable
                    script = __import__("os").path.realpath(
                        __import__("os").path.join(__import__("os").path.dirname(__file__), "main.py")
                    )
                    winreg.SetValueEx(key, _REG_VALUE, 0, winreg.REG_SZ, f'"{exe}" "{script}"')
        except Exception:
            pass
        self._icon.update_menu()

    def _copy_api_key_clicked(self, icon, item):
        threading.Thread(target=self._do_copy_api_key, daemon=True).start()

    def _do_copy_api_key(self):
        c = cfg.load()
        api_key = c.get("api_key", "")
        if not api_key:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("DREWQ Reader", "No API key saved. Open Settings to add one.")
            root.destroy()
            return
        try:
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            root.clipboard_clear()
            root.clipboard_append(api_key)
            root.update()
            root.destroy()
        except Exception:
            pass

    def _quit_clicked(self, icon, item):
        self._agent.stop()
        self._icon.stop()

    def run(self):
        self._icon.run()
