"""
cue_engine.py — Cue list state and execution engine.

A cue is a dict:
  {
    "id":      str (UUID),
    "number":  str  (display label like "1", "2A", "10.5"),
    "label":   str,
    "notes":   str,
    "actions": {
      "<switcher_id>": {
        "type":      "none" | "program" | "preview" | "take" | "cut" | "macro",
        "preset_id": int | str | None,
      }
    }
  }
"""

import asyncio
import logging
import uuid
from typing import Any, Callable

from config import ConfigManager
from switcher_manager import SwitcherManager

logger = logging.getLogger("cuebridge.cues")


class CueEngine:
    """Manages the cue list and executes cues against live switchers."""

    def __init__(self, config: ConfigManager, manager: SwitcherManager) -> None:
        self._config  = config
        self._manager = manager
        self._current_index: int = -1  # index of last-fired cue; -1 = not started
        self._callbacks: list[Callable[[], None]] = []

    # ── Accessors ─────────────────────────────────────────────────────

    @property
    def cues(self) -> list[dict]:
        return self._config.cues

    @property
    def current_index(self) -> int:
        return self._current_index

    def current_cue(self) -> dict | None:
        if 0 <= self._current_index < len(self.cues):
            return self.cues[self._current_index]
        return None

    def next_cue(self) -> dict | None:
        ni = self._current_index + 1
        if 0 <= ni < len(self.cues):
            return self.cues[ni]
        return None

    # ── Transport ─────────────────────────────────────────────────────

    async def go(self) -> tuple[dict | None, dict[str, bool]]:
        """Fire next cue, advance pointer. Returns (cue, {sw_name: ok})."""
        cues = self.cues
        if not cues:
            return None, {}
        next_idx = self._current_index + 1
        if next_idx >= len(cues):
            logger.info("Cue list end — no more cues.")
            return None, {}
        cue = cues[next_idx]
        results = await self._execute(cue)
        self._current_index = next_idx
        logger.info(
            "GO → cue %s %r  results=%s",
            cue.get("number", "?"), cue.get("label", ""), results,
        )
        self._notify()
        return cue, results

    async def back(self) -> tuple[dict | None, dict[str, bool]]:
        """Re-fire the cue before the current one."""
        if self._current_index <= 0:
            self._current_index = -1
            self._notify()
            return None, {}
        self._current_index -= 1
        cue = self.cues[self._current_index]
        results = await self._execute(cue)
        logger.info("BACK → cue %s %r", cue.get("number", "?"), cue.get("label", ""))
        self._notify()
        return cue, results

    def reset(self) -> None:
        """Reset pointer to before the first cue."""
        self._current_index = -1
        self._notify()

    def jump_before(self, index: int) -> None:
        """Set pointer so next GO fires cue at index."""
        clamped = max(-1, min(index - 1, len(self.cues) - 1))
        self._current_index = clamped
        self._notify()

    # ── CRUD ──────────────────────────────────────────────────────────

    def add_cue(self, after_index: int = -1) -> dict:
        """Insert a blank cue after after_index (-1 = append)."""
        cues = self._config.cues
        new_cue: dict[str, Any] = {
            "id":      str(uuid.uuid4()),
            "number":  str(len(cues) + 1),
            "label":   "",
            "notes":   "",
            "actions": {},
        }
        pos = after_index + 1
        if pos <= 0 or pos >= len(cues):
            cues.append(new_cue)
        else:
            cues.insert(pos, new_cue)
            if self._current_index >= pos:
                self._current_index += 1
        self._config.save()
        self._notify()
        return new_cue

    def delete_cue(self, cue_id: str) -> None:
        cues = self._config.cues
        idx = next((i for i, c in enumerate(cues) if c["id"] == cue_id), None)
        if idx is None:
            return
        cues.pop(idx)
        if self._current_index >= idx:
            self._current_index = max(-1, self._current_index - 1)
        self._config.save()
        self._notify()

    def update_cue(self, cue_id: str, updates: dict) -> None:
        for c in self._config.cues:
            if c["id"] == cue_id:
                c.update(updates)
                break
        self._config.save()

    def set_action(
        self,
        cue_id: str,
        switcher_id: str,
        action_type: str,
        preset_id: int | str | None,
    ) -> None:
        for c in self._config.cues:
            if c["id"] == cue_id:
                c.setdefault("actions", {})[switcher_id] = {
                    "type":      action_type,
                    "preset_id": preset_id,
                }
                break
        self._config.save()

    # ── Change notifications ──────────────────────────────────────────

    def register_callback(self, cb: Callable[[], None]) -> None:
        if cb not in self._callbacks:
            self._callbacks.append(cb)

    def unregister_callback(self, cb: Callable[[], None]) -> None:
        if cb in self._callbacks:
            self._callbacks.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception:
                pass

    # ── Execution ─────────────────────────────────────────────────────

    async def _execute(self, cue: dict) -> dict[str, bool]:
        tasks:   list = []
        sw_names: list[str] = []

        for sw_id, action in cue.get("actions", {}).items():
            sw    = self._manager.get(sw_id)
            atype = action.get("type", "none")
            pid   = action.get("preset_id")

            if sw is None or not sw.is_connected or atype == "none":
                continue

            if atype in ("program", "macro"):
                if pid is not None:
                    tasks.append(sw.recall_preset_by_id(pid))
                    sw_names.append(sw.name)
            elif atype == "preview":
                if pid is not None:
                    tasks.append(sw.preview_preset_by_id(pid))
                    sw_names.append(sw.name)
            elif atype == "take":
                tasks.append(sw.take())
                sw_names.append(sw.name)
            elif atype == "cut":
                tasks.append(sw.cut())
                sw_names.append(sw.name)

        if not tasks:
            return {}

        raw = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            name: (isinstance(r, bool) and r)
            for name, r in zip(sw_names, raw)
        }
