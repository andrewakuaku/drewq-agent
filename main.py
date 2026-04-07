"""
DREWQ Reader Agent — entry point.

Startup sequence:
  1. First-run check — if no API key, show setup dialog
  2. Start WebSocket agent in background thread
  3. Run system tray on main thread (required on macOS)
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from setup_dialog import open_first_run, open_settings
from ws_client import ReaderAgent
from tray import TrayApp


def main():
    # ── First-run setup ───────────────────────────────────────────────────────
    if not open_first_run():
        # User closed the setup dialog without saving — exit silently
        return

    # ── Start WebSocket agent ─────────────────────────────────────────────────
    agent = ReaderAgent()
    agent.start()

    # ── System tray (blocks main thread) ─────────────────────────────────────
    def on_settings():
        open_settings(on_save=agent.restart)

    tray = TrayApp(agent=agent, on_settings=on_settings)
    tray.run()


if __name__ == "__main__":
    main()
