"""
Persists agent configuration to ~/.drewq/config.json
"""

import json
from pathlib import Path

_CONFIG_DIR  = Path.home() / ".drewq"
_CONFIG_FILE = _CONFIG_DIR / "config.json"

DEFAULTS = {
    "api_key":    "",
    "server_url": "",
}


def load() -> dict:
    if _CONFIG_FILE.exists():
        try:
            data = json.loads(_CONFIG_FILE.read_text())
            return {**DEFAULTS, **data}
        except Exception:
            pass
    return dict(DEFAULTS)


def save(cfg: dict) -> None:
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


def is_configured() -> bool:
    cfg = load()
    return bool(cfg.get("api_key")) and bool(cfg.get("server_url"))
