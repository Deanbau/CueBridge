"""
switchers/barco_eventmaster.py — Barco Event Master switcher implementation.

Protocol: HTTP REST API, default port 9999.
API reference derived from the Bitfocus Companion module:
  https://github.com/bitfocus/companion-module-barco-eventmaster

Key endpoints used:
  GET  /api/system/listAll              → verify connectivity, get system info
  GET  /api/preset/listPreset           → list all presets (presetId, presetName)
  POST /api/preset/activatePreset       → recall a preset by ID
  POST /api/output/Cut                  → hard cut / take

All requests have a 5-second timeout to avoid blocking the async loop
during a performance.
"""

import asyncio
import logging
from typing import Any

import aiohttp

from .base import BaseSwitcher, Preset, SwitcherStatus

logger = logging.getLogger("cuebridge.switcher.barco")

# Default request timeout (seconds)
_TIMEOUT = aiohttp.ClientTimeout(total=5.0)


class BarcoEventMaster(BaseSwitcher):
    """
    Barco Event Master communicates over its HTTP REST API.

    The companion module uses the same endpoint structure. Adjust
    _BASE_PATH if your firmware version uses a different prefix.
    """

    _BASE_PATH = "/api"

    def __init__(self, cfg: dict[str, Any]) -> None:
        cfg.setdefault("port", 9999)
        super().__init__(cfg)
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_url(self) -> str:
        return f"http://{self.ip}:{self.port}{self._BASE_PATH}"

    async def _get(self, path: str) -> dict:
        """HTTP GET → parsed JSON dict. Raises on error."""
        url = f"{self._base_url()}{path}"
        async with self._session.get(url, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _post(self, path: str, body: dict) -> dict:
        """HTTP POST with JSON body → parsed JSON dict. Raises on error."""
        url = f"{self._base_url()}{path}"
        async with self._session.post(url, json=body, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {}

    # ------------------------------------------------------------------
    # BaseSwitcher implementation
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Open an aiohttp session and verify connectivity via /system/listAll."""
        self.cancel_reconnect()
        self.status = SwitcherStatus.CONNECTING
        self.status_detail = "Connecting…"

        try:
            if self._session and not self._session.closed:
                await self._session.close()

            connector = aiohttp.TCPConnector(limit=4)
            self._session = aiohttp.ClientSession(connector=connector)

            # Verify the device is reachable
            info = await self._get("/system/listAll")
            self._log.debug("System info: %s", info)

            self.status = SwitcherStatus.CONNECTED
            self.status_detail = "Connected"
            self._mark_connected()
            self._log.info("Connected to Barco Event Master at %s", self.address)

            # Fetch presets after connecting
            self.presets = await self.get_presets()
            self._log.info("Fetched %d presets from %s", len(self.presets), self.name)
            return True

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = SwitcherStatus.ERROR
            self.status_detail = str(exc)
            self._log.error("Failed to connect to %s: %s", self.address, exc)
            self._start_reconnect_loop()
            return False

    async def disconnect(self) -> None:
        self.cancel_reconnect()
        self._mark_disconnected()
        self.status = SwitcherStatus.DISCONNECTED
        self.status_detail = ""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._log.info("Disconnected from %s", self.name)

    async def get_presets(self) -> list[Preset]:
        """
        GET /api/preset/listPreset

        Expected response shape (Barco EM firmware 7.x):
          {
            "presetList": [
              { "presetId": 1, "presetName": "Scene 1", ... },
              ...
            ]
          }
        Also handles the alternate key "Presets" seen on some firmware.
        """
        if not self._session or self._session.closed:
            return []
        try:
            data = await self._get("/preset/listPreset")

            raw: list[dict] = (
                data.get("presetList")
                or data.get("Presets")
                or data.get("presets")
                or []
            )

            presets: list[Preset] = []
            for item in raw:
                pid  = item.get("presetId") or item.get("id") or item.get("Id")
                name = item.get("presetName") or item.get("name") or item.get("Name") or f"Preset {pid}"
                if pid is not None:
                    presets.append(Preset(id=int(pid), name=str(name), extra=item))

            return presets
        except Exception as exc:
            self._log.error("get_presets failed: %s", exc)
            return []

    async def recall_preset_by_id(self, preset_id: int | str) -> bool:
        """
        POST /api/preset/activatePreset
        Body: {"inputObj": [{"presetId": <id>}]}

        The companion module passes withAnimation: true by default.
        """
        if not self.is_connected:
            self._log.warning("recall_preset_by_id called while not connected")
            return False
        try:
            self._mark_activity()
            body = {"inputObj": [{"presetId": int(preset_id), "withAnimation": True,
                                   "transitionDuration": self.transition_time}]}
            result = await self._post("/preset/activatePreset", body)
            self._log.info("Recalled preset %s on %s → %s", preset_id, self.name, result)
            return True
        except Exception as exc:
            self._log.error("recall_preset_by_id(%s) failed: %s", preset_id, exc)
            self._handle_comms_error()
            return False

    async def preview_preset_by_id(self, preset_id: int | str) -> bool:
        """
        POST /api/preset/previewPreset
        Body: {"inputObj": [{"presetId": <id>}]}

        Loads the preset onto the PVW bus without taking it live.
        """
        if not self.is_connected:
            return False
        try:
            self._mark_activity()
            body = {"inputObj": [{"presetId": int(preset_id)}]}
            result = await self._post("/preset/previewPreset", body)
            self._log.info("Preview preset %s on %s → %s", preset_id, self.name, result)
            return True
        except Exception as exc:
            self._log.error("preview_preset_by_id(%s) failed: %s", preset_id, exc)
            self._handle_comms_error()
            return False

    async def take(self) -> bool:
        """
        POST /api/output/Take
        Transitions the current PVW preset to PGM using the switcher's
        configured transition. Falls back to Cut if Take is unsupported.
        """
        if not self.is_connected:
            return False
        try:
            self._mark_activity()
            body = {"inputObj": [{"outputId": 1}]}
            await self._post("/output/Take", body)
            self._log.info("TAKE → %s", self.name)
            return True
        except Exception as exc:
            self._log.debug("take via /output/Take failed (%s) — trying Cut", exc)
            return await self.cut()

    async def cut(self) -> bool:
        """
        POST /api/output/Cut  (hard cut / take)
        Body: {"inputObj": [{"outputId": 1}]}
        """
        if not self.is_connected:
            return False
        try:
            self._mark_activity()
            body = {"inputObj": [{"outputId": 1}]}
            await self._post("/output/Cut", body)
            self._log.info("CUT → %s", self.name)
            return True
        except Exception as exc:
            self._log.error("cut failed: %s", exc)
            self._handle_comms_error()
            return False

    async def set_layer_opacity(self, layer: int, opacity: int) -> bool:
        """
        POST /api/layer/setOpacity
        Body: {"inputObj": [{"layerId": <layer>, "opacity": <0-255>}]}

        The Event Master API uses 0-255 for opacity. We accept 0-100 and
        scale accordingly.
        """
        if not self.is_connected:
            return False
        try:
            self._mark_activity()
            scaled = max(0, min(255, round(opacity * 255 / 100)))
            body = {"inputObj": [{"layerId": int(layer), "opacity": scaled}]}
            await self._post("/layer/setOpacity", body)
            self._log.info("Layer %d opacity %d%% → %s", layer, opacity, self.name)
            return True
        except Exception as exc:
            self._log.error("set_layer_opacity failed: %s", exc)
            self._handle_comms_error()
            return False

    async def ping(self) -> bool:
        """GET /api/system/listAll — lightweight liveness check."""
        if not self._session or self._session.closed:
            return False
        try:
            await self._get("/system/listAll")
            return True
        except Exception:
            return False
