"""
switchers/base.py — Abstract base class for all switcher implementations.

Every concrete switcher must subclass BaseSwitcher and implement the abstract
methods.  Optional capabilities (layer opacity, cut) can be left raising
NotImplementedError — the manager will handle that gracefully.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("cuebridge.switcher.base")


class SwitcherStatus(Enum):
    DISCONNECTED  = "disconnected"
    CONNECTING    = "connecting"
    CONNECTED     = "connected"
    ERROR         = "error"
    RECONNECTING  = "reconnecting"


@dataclass
class Preset:
    """A single preset / scene entry fetched from the switcher."""
    id: int | str          # numeric id OR string key, switcher-dependent
    name: str
    extra: dict = field(default_factory=dict)  # any extra metadata

    def __str__(self) -> str:
        return f"[{self.id}] {self.name}"


class BaseSwitcher(ABC):
    """
    Abstract async switcher.

    Subclasses must implement:
      connect / disconnect / get_presets / recall_preset
    Subclasses may optionally override:
      cut / set_layer_opacity
    """

    # How long to wait between reconnect attempts (seconds)
    RECONNECT_DELAY: float = 5.0

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.id: str   = cfg["id"]
        self.name: str = cfg.get("name", "Switcher")
        self.ip: str   = cfg.get("ip", "")
        self.port: int = int(cfg.get("port", 9999))
        self.auto_connect: bool = cfg.get("auto_connect", True)
        # Transition duration in milliseconds (used for scene recall and take)
        self.transition_time: int = max(0, int(cfg.get("transition_time", 1000)))

        self.status: SwitcherStatus = SwitcherStatus.DISCONNECTED
        self.status_detail: str = ""          # human-readable extra info
        self.presets: list[Preset] = []
        self._reconnect_task: asyncio.Task | None = None
        self._log = logging.getLogger(f"cuebridge.switcher.{self.name}")

        # Health / stats
        self.connected_at: float | None = None   # wall-clock time.time()
        self.command_count: int = 0
        self.error_count: int = 0
        self._last_activity: float = 0.0         # time.monotonic()

        # Watchdog — seconds of idle before a ping is sent (0 = disabled)
        self.watchdog_interval: float = max(0.0, float(cfg.get("watchdog_interval", 30.0)))
        self._watchdog_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Abstract interface — must implement
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> bool:
        """
        Establish connection to the switcher.
        Must set self.status accordingly and fetch presets on success.
        Returns True on success.
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Cleanly close the connection and cancel any reconnect tasks."""

    @abstractmethod
    async def get_presets(self) -> list[Preset]:
        """Fetch the current preset list from the device and return it."""

    @abstractmethod
    async def recall_preset_by_id(self, preset_id: int | str) -> bool:
        """Recall a preset by its device-native ID. Returns True on success."""

    # ------------------------------------------------------------------
    # Optional — subclasses override as supported
    # ------------------------------------------------------------------

    async def recall_preset_by_name(self, name: str) -> bool:
        """
        Recall (load to program) by name.
        Default: resolve against cached self.presets.
        """
        name_lower = name.strip().lower()
        for preset in self.presets:
            if preset.name.strip().lower() == name_lower:
                return await self.recall_preset_by_id(preset.id)
        self._log.warning("Preset named %r not found (have %d presets)", name, len(self.presets))
        return False

    # ── Preview bus ───────────────────────────────────────────────────

    async def preview_preset_by_id(self, preset_id: int | str) -> bool:
        """Load a preset onto the preview/PVW bus (not yet live)."""
        self._log.warning("preview_preset not supported by %s", type(self).__name__)
        return False

    async def preview_preset_by_name(self, name: str) -> bool:
        """Load a preset onto preview by name. Default: resolve then call by ID."""
        name_lower = name.strip().lower()
        for preset in self.presets:
            if preset.name.strip().lower() == name_lower:
                return await self.preview_preset_by_id(preset.id)
        self._log.warning("Preview: preset named %r not found", name)
        return False

    # ── Take ──────────────────────────────────────────────────────────

    async def take(self) -> bool:
        """
        Take — transition the preview bus to program using the switcher's
        current transition type and speed.  Falls back to a hard cut if
        the switcher has no separate take command.
        """
        self._log.warning("take not supported by %s — falling back to cut", type(self).__name__)
        return await self.cut()

    # ── Other optional actions ────────────────────────────────────────

    async def cut(self) -> bool:
        self._log.warning("cut not supported by %s", type(self).__name__)
        return False

    async def set_layer_opacity(self, layer: int, opacity: int) -> bool:
        self._log.warning("set_layer_opacity not supported by %s", type(self).__name__)
        return False

    # ------------------------------------------------------------------
    # Health tracking helpers
    # ------------------------------------------------------------------

    def _mark_activity(self) -> None:
        self._last_activity = time.monotonic()
        self.command_count += 1

    def _mark_connected(self) -> None:
        self.connected_at = time.time()
        self._last_activity = time.monotonic()
        self._start_watchdog()

    def _mark_disconnected(self) -> None:
        self.connected_at = None
        self.cancel_watchdog()

    @property
    def uptime_seconds(self) -> float | None:
        if self.connected_at is None:
            return None
        return time.time() - self.connected_at

    def _handle_comms_error(self) -> None:
        self.error_count += 1
        if self.status == SwitcherStatus.CONNECTED:
            self.status = SwitcherStatus.ERROR
            self.status_detail = "Communication error"
            self._mark_disconnected()
            self._start_reconnect_loop()

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def _start_watchdog(self) -> None:
        if self.watchdog_interval <= 0:
            return
        self.cancel_watchdog()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    def cancel_watchdog(self) -> None:
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
        self._watchdog_task = None

    async def _watchdog_loop(self) -> None:
        check_interval = max(5.0, self.watchdog_interval / 3)
        while True:
            await asyncio.sleep(check_interval)
            if not self.is_connected:
                break
            idle = time.monotonic() - self._last_activity
            if idle >= self.watchdog_interval:
                try:
                    ok = await self.ping()
                except Exception:
                    ok = False
                if ok:
                    self._last_activity = time.monotonic()
                else:
                    self._log.warning("Watchdog: ping failed for %s — reconnecting", self.name)
                    self._handle_comms_error()
                    break

    async def ping(self) -> bool:
        """Lightweight liveness check. Override in subclasses for real probing."""
        return self.is_connected

    # ------------------------------------------------------------------
    # Reconnect logic (shared; subclasses call _start_reconnect_loop)
    # ------------------------------------------------------------------

    def _start_reconnect_loop(self) -> None:
        """Start a background task that retries connect() until successful."""
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        self.status = SwitcherStatus.RECONNECTING
        self._log.info("Reconnect loop started for %s", self.name)
        while True:
            await asyncio.sleep(self.RECONNECT_DELAY)
            if self.status == SwitcherStatus.CONNECTED:
                break
            self._log.info("Attempting reconnect to %s (%s:%s)…", self.name, self.ip, self.port)
            try:
                success = await self.connect()
                if success:
                    self._log.info("Reconnected to %s", self.name)
                    break
            except Exception as exc:
                self._log.debug("Reconnect attempt failed: %s", exc)

    def cancel_reconnect(self) -> None:
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            self._reconnect_task = None

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self.status == SwitcherStatus.CONNECTED

    @property
    def address(self) -> str:
        return f"{self.ip}:{self.port}"

    def preset_by_id(self, pid: int | str) -> Preset | None:
        for p in self.presets:
            if str(p.id) == str(pid):
                return p
        return None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} addr={self.address} status={self.status.value}>"
