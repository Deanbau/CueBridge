"""
config.py — Persistent JSON configuration manager.

Loads and saves all application settings: switcher instances and OSC port.
Config is written to disk on every change so state survives restarts.
"""

import json
import logging
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

logger = logging.getLogger("cuebridge.config")

DEFAULT_CONFIG: dict[str, Any] = {
    "web_ui_port": 8080,
    "osc_port": 9000,
    "osc_bind_address": "0.0.0.0",
    "log_to_file": False,
    "log_file_path": "cuebridge.log",
    "switchers": [],
    "cues": [],
    "show_name": "Untitled Show",
}

# Per-switcher schema defaults
SWITCHER_DEFAULTS: dict[str, Any] = {
    "id": "",          # UUID, assigned on creation
    "name": "Switcher",
    "type": "barco",   # "barco" | "pixelhue" | "atem"
    "ip": "",
    "port": 9999,      # Barco: 9999 / PixelHue: 8088 / ATEM: 9910
    "auto_connect": True,
    # PixelHue-specific (ignored by others)
    "username": "admin",
    "password": "MTIzNDU2",  # base64("123456") — PixelFlow default
    "target_screen_ids": [],  # empty = all discovered; list of ints to restrict
    # ATEM-specific (ignored by others)
    "me": 0,           # M/E bus index for cut/take (0 = "M/E 1")
}


class ConfigManager:
    """Thread-safe (asyncio-safe) JSON config manager."""

    def __init__(self, path: str = "cuebridge_config.json") -> None:
        self._path = Path(path)
        self._data: dict[str, Any] = {}
        self.load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load config from disk. Missing keys are filled from defaults."""
        if self._path.exists():
            try:
                with self._path.open("r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                self._data = {**deepcopy(DEFAULT_CONFIG), **loaded}
                logger.info("Config loaded from %s", self._path)
            except Exception as exc:
                logger.error("Failed to load config (%s) — using defaults.", exc)
                self._data = deepcopy(DEFAULT_CONFIG)
        else:
            logger.info("No config file found — starting with defaults.")
            self._data = deepcopy(DEFAULT_CONFIG)

    def save(self) -> None:
        """Persist current config to disk atomically, keeping 5 rolling backups."""
        try:
            # Rotate existing backups: .bak.4 → .bak.5, .bak.3 → .bak.4, …
            for i in range(4, 0, -1):
                src = self._path.with_suffix(f".bak.{i}")
                dst = self._path.with_suffix(f".bak.{i + 1}")
                if src.exists():
                    src.replace(dst)
            if self._path.exists():
                self._path.replace(self._path.with_suffix(".bak.1"))
            tmp = self._path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2)
            tmp.replace(self._path)
        except Exception as exc:
            logger.error("Failed to save config: %s", exc)

    def export_json(self) -> str:
        """Return the full config as a pretty-printed JSON string."""
        return json.dumps(self._data, indent=2)

    def import_json(self, text: str) -> bool:
        """Replace config from a JSON string. Returns True on success."""
        try:
            loaded = json.loads(text)
            self._data = {**deepcopy(DEFAULT_CONFIG), **loaded}
            self.save()
            return True
        except Exception as exc:
            logger.error("Config import failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # OSC settings
    # ------------------------------------------------------------------

    @property
    def web_ui_port(self) -> int:
        return int(self._data.get("web_ui_port", 8080))

    @web_ui_port.setter
    def web_ui_port(self, value: int) -> None:
        self._data["web_ui_port"] = int(value)
        self.save()

    @property
    def osc_port(self) -> int:
        return int(self._data.get("osc_port", 9000))

    @osc_port.setter
    def osc_port(self, value: int) -> None:
        self._data["osc_port"] = int(value)
        self.save()

    @property
    def osc_bind_address(self) -> str:
        return self._data.get("osc_bind_address", "0.0.0.0")

    @osc_bind_address.setter
    def osc_bind_address(self, value: str) -> None:
        self._data["osc_bind_address"] = value
        self.save()

    @property
    def log_to_file(self) -> bool:
        return bool(self._data.get("log_to_file", False))

    @log_to_file.setter
    def log_to_file(self, value: bool) -> None:
        self._data["log_to_file"] = bool(value)
        self.save()

    @property
    def log_file_path(self) -> str:
        return self._data.get("log_file_path", "cuebridge.log")

    @log_file_path.setter
    def log_file_path(self, value: str) -> None:
        self._data["log_file_path"] = value
        self.save()

    # ------------------------------------------------------------------
    # Switcher management
    # ------------------------------------------------------------------

    @property
    def switchers(self) -> list[dict[str, Any]]:
        return self._data.get("switchers", [])

    def get_switcher(self, switcher_id: str) -> dict[str, Any] | None:
        for sw in self.switchers:
            if sw.get("id") == switcher_id:
                return sw
        return None

    def add_switcher(self, cfg: dict[str, Any]) -> dict[str, Any]:
        """Add a new switcher. Assigns a UUID if missing. Returns the final dict."""
        entry = {**deepcopy(SWITCHER_DEFAULTS), **cfg}
        if not entry.get("id"):
            entry["id"] = str(uuid.uuid4())
        self._data.setdefault("switchers", []).append(entry)
        self.save()
        return entry

    def update_switcher(self, switcher_id: str, updates: dict[str, Any]) -> bool:
        """Update fields on an existing switcher. Returns True if found."""
        for sw in self._data.get("switchers", []):
            if sw.get("id") == switcher_id:
                sw.update(updates)
                self.save()
                return True
        return False

    # ------------------------------------------------------------------
    # Cue list management
    # ------------------------------------------------------------------

    @property
    def cues(self) -> list[dict[str, Any]]:
        return self._data.setdefault("cues", [])

    @property
    def show_name(self) -> str:
        return self._data.get("show_name", "Untitled Show")

    @show_name.setter
    def show_name(self, value: str) -> None:
        self._data["show_name"] = value
        self.save()

    def export_cues(self) -> str:
        """Return cues + show name as pretty JSON."""
        import json as _json
        from datetime import datetime
        return _json.dumps({
            "show_name":   self.show_name,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "cues":        self.cues,
        }, indent=2)

    def import_cues(self, text: str) -> bool:
        """Replace cue list from JSON. Accepts {cues:[...]} or bare list. Returns True on success."""
        import json as _json
        try:
            loaded = _json.loads(text)
            if isinstance(loaded, list):
                cues = loaded
                name = None
            elif isinstance(loaded, dict):
                cues = loaded.get("cues", [])
                name = loaded.get("show_name")
            else:
                return False
            self._data["cues"] = cues
            if name:
                self._data["show_name"] = name
            self.save()
            return True
        except Exception as exc:
            logger.error("import_cues failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Switcher management (continued)
    # ------------------------------------------------------------------

    def remove_switcher(self, switcher_id: str) -> bool:
        """Remove a switcher by ID. Returns True if removed."""
        before = len(self.switchers)
        self._data["switchers"] = [
            s for s in self.switchers if s.get("id") != switcher_id
        ]
        if len(self.switchers) < before:
            self.save()
            return True
        return False
