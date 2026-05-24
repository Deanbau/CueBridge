"""
switchers/pixelhue.py — PixelHue / PixelFlow switcher implementation.

Protocol: HTTP REST at http://{ip}:8088/unico/v1/...
API reference: PixelFlow OpenAPI V2.0.1

Authentication flow:
  1. POST /v1/system/auth/login  { username, password }  → token
  2. All subsequent requests:    Authorization: <token> header

Default credentials: admin / MTIzNDU2  (base64 of "123456")
Configure via switcher config keys "username" and "password".
"""

import asyncio
import logging
from typing import Any

import aiohttp

from .base import BaseSwitcher, Preset, SwitcherStatus

logger = logging.getLogger("cuebridge.switcher.pixelhue")

_TIMEOUT   = aiohttp.ClientTimeout(total=8.0)
_BASE_PATH = "/unico"


class PixelHueSwitcher(BaseSwitcher):
    """PixelHue / PixelFlow Q8 (and P10/P20/P80) via HTTP REST API."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        cfg.setdefault("port", 8088)
        super().__init__(cfg)
        self._username: str = cfg.get("username", "admin")
        self._password: str = cfg.get("password", "MTIzNDU2")
        self._token: str = ""
        self._session: aiohttp.ClientSession | None = None
        # _screens: active screen objects used for take/cut — each has screenId, screenGuid, screenName
        self._screens: list[dict] = []
        self._all_screen_ids: list[int] = []    # every discovered ID (for logging)
        raw_target = cfg.get("target_screen_ids", [])
        self._target_screen_ids: list[int] = [int(x) for x in raw_target] if raw_target else []

    @property
    def _screen_ids(self) -> list[int]:
        return [s["screenId"] for s in self._screens]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_url(self) -> str:
        return f"http://{self.ip}:{self.port}{_BASE_PATH}"

    def _auth_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = self._token
        return headers

    async def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self._base_url()}{path}"
        async with self._session.get(url, headers=self._auth_headers(),
                                     params=params, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            return data

    async def _put(self, path: str, body: Any) -> dict:
        url = f"{self._base_url()}{path}"
        async with self._session.put(url, headers=self._auth_headers(),
                                     json=body, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {}

    async def _post(self, path: str, body: Any) -> dict:
        url = f"{self._base_url()}{path}"
        async with self._session.post(url, headers=self._auth_headers(),
                                      json=body, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {}

    async def _get_screens(self) -> list[dict]:
        """GET /v1/screen/list-detail — returns screen objects with id/guid/name.

        API response field paths (from Companion module interfaces):
          screen.guid            → screenGuid  (NOT screen.screenGuid)
          screen.general.name   → screenName  (NOT screen.screenName)
          screen.screenIdObj.type: 2=SCREEN, 4=AUX, 8=MVR
        """
        try:
            data = await self._get("/v1/screen/list-detail")
            raw = data.get("data", {}).get("list", [])
            screens = []
            for sc in raw:
                if "screenId" not in sc:
                    continue
                sc_type = sc.get("screenIdObj", {}).get("type", 2)
                screens.append(
                    {
                        "screenId":   sc["screenId"],
                        "screenGuid": sc.get("guid", ""),
                        "screenName": sc.get("general", {}).get("name", ""),
                        "screenType": sc_type,  # 2=SCREEN, 4=AUX, 8=MVR
                    }
                )
            return screens
        except Exception as exc:
            self._log.warning("Could not fetch screen list: %s", exc)
            return []

    async def _login(self) -> bool:
        """Authenticate and store the token. Returns True on success."""
        url = f"{self._base_url()}/v1/system/auth/login"
        body = {"username": self._username, "password": self._password}
        try:
            async with self._session.post(
                url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=_TIMEOUT,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                token = data.get("data", {}).get("token", "")
                if not token:
                    self._log.error("Login returned no token. Response: %s", data)
                    return False
                self._token = token
                self._log.debug("Authenticated, token obtained.")
                return True
        except Exception as exc:
            self._log.error("Login failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # BaseSwitcher implementation
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        self.cancel_reconnect()
        self.status = SwitcherStatus.CONNECTING
        self.status_detail = "Connecting…"
        self._token = ""

        if self._session and not self._session.closed:
            await self._session.close()

        try:
            connector = aiohttp.TCPConnector(limit=4)
            self._session = aiohttp.ClientSession(connector=connector)

            # Verify device is reachable (no auth needed)
            info = await self._get("/v1/node/open-detail")
            sn = info.get("data", {}).get("sn", "unknown")
            self._log.debug("Device SN: %s", sn)

            # Authenticate
            ok = await self._login()
            if not ok:
                raise ConnectionError("Authentication failed — check username/password in config")

            all_screens = await self._get_screens()
            self._all_screen_ids = [s["screenId"] for s in all_screens]
            _TYPE_STR = {2: "SCREEN", 4: "AUX", 8: "MVR"}
            for s in all_screens:
                t = _TYPE_STR.get(s["screenType"], str(s["screenType"]))
                self._log.info("  Screen id=%s guid=%s name=%r type=%s",
                               s["screenId"], s["screenGuid"] or "(no guid)", s["screenName"] or "(no name)", t)
            if self._target_screen_ids:
                self._screens = [s for s in all_screens if s["screenId"] in self._target_screen_ids]
                self._log.info("Screens (filtered to target_screen_ids %s): %s",
                               self._target_screen_ids, [(s["screenId"], s["screenName"]) for s in self._screens])
            else:
                # Default: only SCREEN type (2). Exclude MVR (8), AUX (4), and unknowns
                # to prevent multiviewer/aux outputs going black on take.
                self._screens = [s for s in all_screens if s["screenType"] == 2]
                excluded = [s for s in all_screens if s["screenType"] != 2]
                self._log.info("Active screens (type=SCREEN only): %s",
                               [(s["screenId"], s["screenName"]) for s in self._screens])
                if excluded:
                    self._log.info("Excluded (non-SCREEN type): %s",
                                   [(s["screenId"], s["screenName"], s["screenType"]) for s in excluded])

            self.status = SwitcherStatus.CONNECTED
            self.status_detail = "Connected"
            self._mark_connected()
            self._log.info("Connected to PixelHue at %s (SN: %s, active screens: %s, all: %s)",
                           self.address, sn, self._screen_ids, self._all_screen_ids)

            self.presets = await self.get_presets()
            self._log.info("Fetched %d presets from %s", len(self.presets), self.name)
            return True

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.status = SwitcherStatus.ERROR
            self.status_detail = str(exc)
            self._log.error("Failed to connect to PixelHue %s: %s", self.address, exc)
            if self._session and not self._session.closed:
                await self._session.close()
            self._start_reconnect_loop()
            return False

    async def disconnect(self) -> None:
        self.cancel_reconnect()
        self._mark_disconnected()
        self._token = ""
        self._screens = []
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self.status = SwitcherStatus.DISCONNECTED
        self.status_detail = ""
        self._log.info("Disconnected from %s", self.name)

    async def get_presets(self) -> list[Preset]:
        """
        GET /v1/preset  — paginate with limit=100.

        Returns list of Preset with id=serial (preset slot number), name=name.
        """
        if not self._session or self._session.closed:
            return []
        presets: list[Preset] = []
        page = 1
        try:
            while True:
                data = await self._get("/v1/preset", params={"limit": 100, "page": page})
                items: list[dict] = data.get("data", {}).get("list", [])
                if not items:
                    break
                for item in items:
                    pid  = item.get("serial") if item.get("serial") is not None else item.get("guid")
                    name = item.get("name") or f"Preset {pid}"
                    if pid is not None:
                        presets.append(Preset(id=pid, name=str(name), extra=item))
                if len(items) < 100:
                    break
                page += 1
        except Exception as exc:
            self._log.error("get_presets failed: %s", exc)
        return presets

    def _ok(self, result: dict) -> bool:
        return result.get("code") in (0, 200)

    def _aux(self) -> dict:
        """Auxiliary block for preset apply — matches Companion module format."""
        return {
            "keyFrame":     {"enable": 1},
            "switchEffect": {"type": 1, "time": max(1, self.transition_time)},
            "swapEnable":   1,
            "effect":       {"enable": 1},
        }

    def _preset_guid(self, preset_id: int | str) -> str:
        """Look up the GUID for a preset serial from the cached preset list."""
        serial = int(preset_id)
        for p in self.presets:
            if int(p.id) == serial:
                return (p.extra or {}).get("guid", "")
        return ""

    async def recall_preset_by_id(self, preset_id: int | str) -> bool:
        """POST /v1/preset/apply  →  targetRegion 2 = PGM (live)."""
        if not self.is_connected:
            return False
        try:
            self._mark_activity()
            guid = self._preset_guid(preset_id)
            body = {
                "serial":       int(preset_id),
                "presetId":     guid,
                "targetRegion": 2,
                "auxiliary":    self._aux(),
            }
            result = await self._post("/v1/preset/apply", body)
            self._log.info("Recalled preset %s on %s → %s", preset_id, self.name, result.get("message"))
            return self._ok(result)
        except Exception as exc:
            self._log.error("recall_preset_by_id(%s) failed: %s", preset_id, exc)
            self._handle_comms_error()
            return False

    async def preview_preset_by_id(self, preset_id: int | str) -> bool:
        """POST /v1/preset/apply  →  targetRegion 4 = PVW."""
        if not self.is_connected:
            return False
        try:
            self._mark_activity()
            guid = self._preset_guid(preset_id)
            body = {
                "serial":       int(preset_id),
                "presetId":     guid,
                "targetRegion": 4,
                "auxiliary":    self._aux(),
            }
            result = await self._post("/v1/preset/apply", body)
            self._log.info("Preview preset %s on %s → %s", preset_id, self.name, result.get("message"))
            return self._ok(result)
        except Exception as exc:
            self._log.error("preview_preset_by_id(%s) failed: %s", preset_id, exc)
            self._handle_comms_error()
            return False

    async def take(self) -> bool:
        """PUT /v1/screen/take — transition PVW→PGM with switchEffect."""
        if not self.is_connected:
            return False
        if not self._screens:
            self._log.warning("take: no screens cached — did connect() run?")
            return False
        try:
            self._mark_activity()
            fade_ms = max(1, self.transition_time)
            body = [
                {
                    "screenId":    s["screenId"],
                    "screenGuid":  s["screenGuid"],
                    "screenName":  s["screenName"],
                    "direction":   0,
                    "effectSelect": 0,
                    "swapEnable":  1,
                    "switchEffect": {"type": 1, "time": fade_ms},
                }
                for s in self._screens
            ]
            result = await self._put("/v1/screen/take", body)
            self._log.info("TAKE → %s (code %s)", self.name, result.get("code"))
            return self._ok(result)
        except Exception as exc:
            self._log.error("take failed: %s", exc)
            self._handle_comms_error()
            return False

    async def cut(self) -> bool:
        """PUT /v1/screen/cut — instant PVW→PGM with no transition."""
        if not self.is_connected:
            return False
        if not self._screens:
            self._log.warning("cut: no screens cached")
            return False
        try:
            self._mark_activity()
            body = [
                {
                    "screenId":   s["screenId"],
                    "screenGuid": s["screenGuid"],
                    "screenName": s["screenName"],
                    "direction":  0,
                    "swapEnable": 0,
                }
                for s in self._screens
            ]
            result = await self._put("/v1/screen/cut", body)
            self._log.info("CUT → %s (code %s)", self.name, result.get("code"))
            return self._ok(result)
        except Exception as exc:
            self._log.error("cut failed: %s", exc)
            self._handle_comms_error()
            return False

    async def ping(self) -> bool:
        """GET /v1/node/open-detail — no auth required, lightweight liveness check."""
        if not self._session or self._session.closed:
            return False
        try:
            resp = await self._get("/v1/node/open-detail")
            return resp.get("code") in (0, 200)
        except Exception:
            return False
