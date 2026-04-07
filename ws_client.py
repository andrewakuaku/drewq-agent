"""
WebSocket client — connects to the Heroku backend and handles scan commands.

Runs in a background thread (its own asyncio event loop).
Communicates status back to the tray via callbacks.
"""

import asyncio
import json
import logging
import threading
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosedError, InvalidHandshake

from scanner import read_card
import config as cfg

logger = logging.getLogger(__name__)

# Reconnect delays: 2s, 4s, 8s, 16s, 30s (cap)
_BACKOFF = [2, 4, 8, 16, 30]


class ReaderAgent:
    def __init__(self):
        self._stop_event   = threading.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Callbacks set by the tray
        self.on_connected:    Callable[[], None] = lambda: None
        self.on_disconnected: Callable[[], None] = lambda: None
        self.on_scanning:     Callable[[], None] = lambda: None
        self.on_scan_done:    Callable[[], None] = lambda: None
        self.on_error:        Callable[[str], None] = lambda _: None

    # ── Public control ────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the WebSocket loop in a daemon thread."""
        t = threading.Thread(target=self._run_loop, daemon=True, name="ws-agent")
        t.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def restart(self) -> None:
        """Restart after config change."""
        self.stop()
        self._stop_event.clear()
        self.start()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_loop())
        finally:
            self._loop.close()

    async def _connect_loop(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            c = cfg.load()
            api_key    = c.get("api_key", "")
            server_url = c.get("server_url", "")

            if not api_key or not server_url:
                logger.warning("Not configured — waiting 10 s")
                self.on_error("Not configured. Open Settings to add your API key.")
                await asyncio.sleep(10)
                continue

            url = f"{server_url}?api_key={api_key}"
            try:
                logger.info("Connecting to %s", server_url)
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    attempt = 0
                    self.on_connected()
                    await self._message_loop(ws)
            except InvalidHandshake as exc:
                msg = str(exc)
                if "4001" in msg:
                    self.on_error("Authentication failed. Check your API key in Settings.")
                    await asyncio.sleep(30)
                    continue
                self.on_error(f"Connection refused: {exc}")
            except (ConnectionClosedError, OSError) as exc:
                logger.warning("Disconnected: %s", exc)
            except Exception as exc:
                logger.exception("Unexpected error: %s", exc)
                self.on_error(str(exc))

            if self._stop_event.is_set():
                break

            self.on_disconnected()
            delay = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            logger.info("Reconnecting in %d s (attempt %d)…", delay, attempt + 1)
            await asyncio.sleep(delay)
            attempt += 1

    async def _message_loop(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "scan":
                await self._handle_scan(ws, msg)

            elif msg_type == "pong":
                pass  # heartbeat acknowledged

    async def _handle_scan(self, ws, msg: dict) -> None:
        cmd_id       = msg.get("id", "")
        doc_number   = msg.get("doc_number", "")
        date_of_birth = msg.get("date_of_birth", "")
        expiry_date  = msg.get("expiry_date", "")

        self.on_scanning()
        try:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: read_card(
                    doc_number=doc_number,
                    date_of_birth=date_of_birth,
                    expiry_date=expiry_date,
                ),
            )
            data = result.to_dict()
        except Exception as exc:
            data = {"success": False, "error": str(exc)}
        finally:
            self.on_scan_done()

        await ws.send(json.dumps({
            "type": "scan_result",
            "id":   cmd_id,
            "data": data,
        }))
