"""
switcher_manager.py — Manages multiple switcher instances.

Responsibilities:
  • Instantiate the correct switcher class (Barco / PixelHue) from config.
  • Keep a live dict of BaseSwitcher objects keyed by their config ID.
  • Route OSC commands to a named switcher or to every connected switcher
    when the target is "all".
  • Expose a clean async API used by both the OSC handler and the UI.
"""

import asyncio
import logging
from typing import Any

from config import ConfigManager
from switchers.base import BaseSwitcher, Preset, SwitcherStatus
from switchers.atem import AtemSwitcher
from switchers.barco_eventmaster import BarcoEventMaster
from switchers.pixelhue import PixelHueSwitcher

logger = logging.getLogger("cuebridge.manager")

# Maps config "type" strings to concrete classes
_SWITCHER_CLASSES: dict[str, type[BaseSwitcher]] = {
    "atem":     AtemSwitcher,
    "barco":    BarcoEventMaster,
    "pixelhue": PixelHueSwitcher,
}


def _build_switcher(cfg: dict[str, Any]) -> BaseSwitcher | None:
    cls = _SWITCHER_CLASSES.get(cfg.get("type", "").lower())
    if cls is None:
        logger.error("Unknown switcher type %r for %r", cfg.get("type"), cfg.get("name"))
        return None
    return cls(cfg)


class SwitcherManager:
    """
    Central registry of live switcher objects.

    All methods are async-safe (called from the asyncio event loop).
    """

    def __init__(self, config: ConfigManager) -> None:
        self._config = config
        # id → BaseSwitcher (live instance)
        self._switchers: dict[str, BaseSwitcher] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def auto_connect(self) -> None:
        """
        Called at startup. Instantiate all configured switchers and
        connect those with auto_connect=True.
        """
        for sw_cfg in self._config.switchers:
            sw_id = sw_cfg.get("id")
            if not sw_id:
                continue
            sw = _build_switcher(sw_cfg)
            if sw is None:
                continue
            self._switchers[sw_id] = sw
            if sw.auto_connect:
                asyncio.create_task(sw.connect())
                logger.info("Auto-connecting %s…", sw.name)

    async def disconnect_all(self) -> None:
        """Gracefully disconnect every switcher (called on app shutdown)."""
        tasks = [sw.disconnect() for sw in self._switchers.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("All switchers disconnected.")

    # ------------------------------------------------------------------
    # CRUD — called from the UI
    # ------------------------------------------------------------------

    async def add(self, cfg: dict[str, Any]) -> BaseSwitcher | None:
        """Persist a new switcher config and create its live instance."""
        async with self._lock:
            saved = self._config.add_switcher(cfg)
            sw = _build_switcher(saved)
            if sw is None:
                return None
            self._switchers[saved["id"]] = sw
            logger.info("Added switcher %r (%s) id=%s", sw.name, saved.get("type"), saved["id"])
            return sw

    async def remove(self, switcher_id: str) -> bool:
        """Disconnect, remove from registry, and delete from config."""
        async with self._lock:
            sw = self._switchers.pop(switcher_id, None)
            if sw:
                await sw.disconnect()
            removed = self._config.remove_switcher(switcher_id)
            if removed:
                logger.info("Removed switcher id=%s", switcher_id)
            return removed

    async def update(self, switcher_id: str, updates: dict[str, Any]) -> bool:
        """
        Update config fields for an existing switcher.
        The live instance is replaced (disconnect old, create new).
        """
        async with self._lock:
            ok = self._config.update_switcher(switcher_id, updates)
            if not ok:
                return False
            # Replace the live instance with fresh config
            sw = self._switchers.pop(switcher_id, None)
            if sw:
                await sw.disconnect()
            new_cfg = self._config.get_switcher(switcher_id)
            if new_cfg:
                new_sw = _build_switcher(new_cfg)
                if new_sw:
                    self._switchers[switcher_id] = new_sw
            logger.info("Updated switcher id=%s", switcher_id)
            return True

    async def connect(self, switcher_id: str) -> bool:
        sw = self._switchers.get(switcher_id)
        if not sw:
            logger.warning("connect: switcher id=%s not found", switcher_id)
            return False
        return await sw.connect()

    async def disconnect(self, switcher_id: str) -> None:
        sw = self._switchers.get(switcher_id)
        if sw:
            await sw.disconnect()

    async def refresh_presets(self, switcher_id: str) -> list[Preset]:
        sw = self._switchers.get(switcher_id)
        if not sw or not sw.is_connected:
            return []
        sw.presets = await sw.get_presets()
        return sw.presets

    # ------------------------------------------------------------------
    # Read-only accessors (for the UI)
    # ------------------------------------------------------------------

    def all_switchers(self) -> list[BaseSwitcher]:
        return list(self._switchers.values())

    def get(self, switcher_id: str) -> BaseSwitcher | None:
        return self._switchers.get(switcher_id)

    def find_by_name(self, name: str) -> BaseSwitcher | None:
        """Case-insensitive name lookup."""
        name_lower = name.strip().lower()
        for sw in self._switchers.values():
            if sw.name.strip().lower() == name_lower:
                return sw
        return None

    def connected_switchers(self) -> list[BaseSwitcher]:
        return [sw for sw in self._switchers.values() if sw.is_connected]

    # ------------------------------------------------------------------
    # Command routing — called from OSC handler
    # ------------------------------------------------------------------

    async def _targets(self, target_name: str) -> list[BaseSwitcher]:
        """
        Resolve an OSC target string to a list of live switcher instances.
        "all"  → every connected switcher
        other  → single switcher by name (must be connected)
        """
        if target_name.lower() == "all":
            return self.connected_switchers()
        sw = self.find_by_name(target_name)
        if sw is None:
            logger.warning("OSC target %r not found in registry", target_name)
            return []
        if not sw.is_connected:
            logger.warning("OSC target %r is not connected (status=%s)", target_name, sw.status.value)
            return []
        return [sw]

    async def osc_recall(self, target_name: str, preset: int | str) -> dict[str, bool]:
        """
        Recall a preset on the named target(s).

        Args:
            target_name: switcher name or "all"
            preset:      integer ID or string name
        Returns:
            {switcher_name: success} mapping
        """
        targets = await self._targets(target_name)
        results: dict[str, bool] = {}
        if not targets:
            return results

        async def _recall_one(sw: BaseSwitcher) -> None:
            if isinstance(preset, str):
                ok = await sw.recall_preset_by_name(preset)
            else:
                ok = await sw.recall_preset_by_id(preset)
            results[sw.name] = ok

        await asyncio.gather(*[_recall_one(sw) for sw in targets])
        return results

    async def osc_preview(self, target_name: str, preset: int | str) -> dict[str, bool]:
        """Load a preset to the PVW bus (not live yet)."""
        targets = await self._targets(target_name)
        results: dict[str, bool] = {}
        if not targets:
            return results

        async def _preview_one(sw: BaseSwitcher) -> None:
            if isinstance(preset, str):
                ok = await sw.preview_preset_by_name(preset)
            else:
                ok = await sw.preview_preset_by_id(preset)
            results[sw.name] = ok

        await asyncio.gather(*[_preview_one(sw) for sw in targets])
        return results

    async def osc_take(self, target_name: str, transition_ms: int | None = None) -> dict[str, bool]:
        """Transition PVW to PGM. If transition_ms is given, override the switcher's transition_time for this take only."""
        targets = await self._targets(target_name)
        results: dict[str, bool] = {}
        for sw in targets:
            if transition_ms is not None:
                prev = sw.transition_time
                sw.transition_time = transition_ms
                results[sw.name] = await sw.take()
                sw.transition_time = prev
            else:
                results[sw.name] = await sw.take()
        return results

    async def osc_cut(self, target_name: str) -> dict[str, bool]:
        targets = await self._targets(target_name)
        results: dict[str, bool] = {}
        for sw in targets:
            results[sw.name] = await sw.cut()
        return results

    async def osc_layer_opacity(self, target_name: str, layer: int, opacity: int) -> dict[str, bool]:
        targets = await self._targets(target_name)
        results: dict[str, bool] = {}
        for sw in targets:
            results[sw.name] = await sw.set_layer_opacity(layer, opacity)
        return results

    async def osc_set_transition(self, target_name: str, ms: int) -> dict[str, bool]:
        """Set transition time (ms) on the named target(s) without sending a command."""
        targets = await self._targets(target_name)
        results: dict[str, bool] = {}
        for sw in targets:
            sw.transition_time = max(0, ms)
            results[sw.name] = True
            logger.info("Transition time → %dms on %s", sw.transition_time, sw.name)
        return results
