# DREWQ Reader Agent

The local agent that connects your USB smart card reader to your [DREWQ](https://drewq-app-9af671f9e6d5.herokuapp.com) dashboard.

It runs on the machine where the reader is physically plugged in, reads ECOWAS biometric identity cards via the PC/SC interface, and relays scan results to the DREWQ backend over a secure WebSocket connection.

---

## How it works

```
USB Reader → Agent (local machine) → WebSocket → DREWQ Backend → Dashboard
```

1. You plug in a supported USB smart card reader
2. The agent detects the reader via PC/SC (no drivers needed on modern OS)
3. When you press **Scan Card** in the dashboard, the backend sends a scan command over WebSocket to the agent
4. The agent reads the card chip using BAC (Basic Access Control) and returns the data
5. The backend stores the record and your dashboard updates in real time

---

## Requirements

- **Python 3.11+**
- **macOS** (Ventura or later) or **Windows 10/11 (64-bit)**
- A [supported USB smart card reader](https://drewq-app-9af671f9e6d5.herokuapp.com/readers)
- A DREWQ API key (get one from your [dashboard](https://drewq-app-9af671f9e6d5.herokuapp.com))

### PC/SC daemon

| Platform | Required |
|----------|----------|
| macOS    | Built-in — nothing to install |
| Windows  | Built-in Smart Card service — ensure it is running |

---

## Installation

```bash
# 1. Clone
git clone https://github.com/andrewakuaku/drewq-agent.git
cd drewq-agent

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run
python main.py
```

A settings dialog will open on first launch. Enter your DREWQ API key and the agent will connect automatically.

---

## Configuration

Settings are stored at `~/.drewq/config.json`:

```json
{
  "api_key": "drewq_xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "server_url": "wss://drewq-api-5df4dc0a4153.herokuapp.com/ws/reader"
}
```

| Field | Description |
|-------|-------------|
| `api_key` | Your DREWQ API key — found in the dashboard under **API Keys** |
| `server_url` | WebSocket URL of the DREWQ backend — defaults to the production server |

You can also open the settings dialog at any time from the system tray icon.

---

## System tray

Once running, the agent lives in the system tray (menu bar on macOS, taskbar on Windows):

| State | Icon |
|-------|------|
| Connected | Green indicator |
| Scanning | Pulsing indicator |
| Disconnected / error | Gray indicator |

Right-click the tray icon for **Settings** and **Quit**.

---

## Supported readers

Any USB smart card reader that supports the CCID protocol and ISO 7816 is compatible. The agent detects readers automatically via PC/SC — no configuration needed.

See the full list at [drewq.app/readers](https://drewq-app-9af671f9e6d5.herokuapp.com/readers).

---

## Project structure

```
agent/
├── main.py              # Entry point — startup sequence and tray launch
├── ws_client.py         # WebSocket client, reconnect loop, scan relay
├── scanner.py           # PC/SC card reading, BAC, MRZ/chip data parsing
├── config.py            # Config persistence (~/.drewq/config.json)
├── setup_dialog.py      # macOS settings dialog (rumps)
├── setup_dialog_win.py  # Windows settings dialog (tkinter)
├── tray_mac.py          # macOS system tray (rumps)
├── tray_win.py          # Windows system tray (pystray)
└── requirements.txt
```

---

## Updating

When a new version of the agent is released, pull the latest changes and restart:

```bash
cd drewq-agent
git pull origin main
pip install -r requirements.txt
python main.py
```

> **Tip:** Run `git pull` before launching the agent each time to stay on the latest version.

---

## Troubleshooting

**"No smart card readers found"**
- Make sure the reader is plugged in before launching the agent
- On Windows, check that the **Smart Card** service is running (`services.msc`)

**"Authentication failed. Check your API key in Settings"**
- Verify the API key in Settings matches the one in your DREWQ dashboard
- Make sure the key has not been revoked

**"Card read timed out"**
- Ensure the card is placed flat on the reader with the chip making contact
- Try removing and reinserting the card

**Agent shows connected but scans fail**
- Check the backend logs in your Heroku dashboard
- Ensure your API key has not been revoked

---

## License

MIT
