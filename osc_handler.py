"""
osc_handler.py — OSC UDP server and message routing.

Uses python-osc's AsyncIOOSCUDPServer so everything runs inside
NiceGUI's existing asyncio event loop with zero extra threads.

Supported OSC address patterns
───────────────────────────────
Cue list control:
  /cue/go                                 fire next cue
  /cue/back  (alias: /cue/prev)           fire previous cue
  /cue/reset                              reset pointer to before first cue
  /cue/<number>                           jump to cue by display number and fire

Preset recall (main use):
  /switcher/<name>/recall <int>           recall preset by number
  /switcher/<name>/recall <string>        recall preset by name
  /switcher/all/recall  <int|string>      recall on every connected switcher

Hard cut:
  /switcher/<name>/cut

Layer opacity:
  /switcher/<name>/layer/<n>/opacity <0-100>

Transition time (live, overrides config value):
  /switcher/<name>/transition <int_ms>
  /switcher/all/transition  <int_ms>

Where <name> is the switcher's Name field (case-insensitive) or "all".
"""

import asyncio
import logging
from collections import deque
from datetime import datetime
from typing import Any, Callable

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import AsyncIOOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

from config import ConfigManager
from cue_engine import CueEngine
from switcher_manager import SwitcherManager

logger = logging.getLogger("cuebridge.osc")

# ── Module-level OSC traffic ring buffer ─────────────────────────────────────
_OSC_TRAFFIC: deque[dict] = deque(maxlen=300)
_osc_callbacks: list[Callable[[dict], None]] = []

# ── Preset recall event queue ────────────────────────────────────────────────
# Background tasks push {mode, preset, switcher, ok} here.
# UI timer drains it — avoids NiceGUI context issues with run_javascript.
_recall_callbacks: list[Callable[[dict], None]] = []
_recall_queue: deque[dict] = deque(maxlen=50)


def get_osc_traffic() -> list[dict]:
    return list(_OSC_TRAFFIC)


def clear_osc_traffic() -> None:
    _OSC_TRAFFIC.clear()


def register_osc_callback(cb: Callable[[dict], None]) -> None:
    if cb not in _osc_callbacks:
        _osc_callbacks.append(cb)


def unregister_osc_callback(cb: Callable[[dict], None]) -> None:
    if cb in _osc_callbacks:
        _osc_callbacks.remove(cb)


def get_recall_queue() -> deque[dict]:
    return _recall_queue


def register_recall_callback(cb: Callable[[dict], None]) -> None:
    if cb not in _recall_callbacks:
        _recall_callbacks.append(cb)


def unregister_recall_callback(cb: Callable[[dict], None]) -> None:
    if cb in _recall_callbacks:
        _recall_callbacks.remove(cb)


class OSCHandler:
    """
    Manages the lifecycle of the OSC UDP server.

    start() / stop() can be called multiple times (e.g. when the user
    changes the listening port in the UI).
    """

    def __init__(
        self,
        config: ConfigManager,
        manager: SwitcherManager,
        engine: CueEngine | None = None,
    ) -> None:
        self._config  = config
        self._manager = manager
        self._engine  = engine
        self._transport: asyncio.BaseTransport | None = None
        self._protocol: Any = None
        self._running  = False
        self._port: int = config.osc_port

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> bool:
        """Bind the OSC UDP socket. Returns True on success."""
        if self._running:
            await self.stop()

        self._port = self._config.osc_port
        bind_addr  = self._config.osc_bind_address

        dispatcher = Dispatcher()
        # needs_reply_address=True → handler receives (client_address, address, *args)
        dispatcher.set_default_handler(self._dispatch, needs_reply_address=True)

        try:
            server = AsyncIOOSCUDPServer(
                (bind_addr, self._port),
                dispatcher,
                asyncio.get_event_loop(),
            )
            self._transport, self._protocol = await server.create_serve_endpoint()
            self._running = True
            logger.info("OSC server listening on %s:%s", bind_addr, self._port)
            return True
        except Exception as exc:
            self._running = False
            logger.error("Failed to start OSC server on port %s: %s", self._port, exc)
            return False

    async def stop(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None
            self._protocol  = None
        self._running = False
        logger.info("OSC server stopped.")

    async def restart(self) -> bool:
        """Stop and re-start (used when port is changed in the UI)."""
        await self.stop()
        return await self.start()

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def port(self) -> int:
        return self._port

    # ------------------------------------------------------------------
    # Dispatch — called from the asyncio protocol's datagram_received
    # ------------------------------------------------------------------

    def _dispatch(self, client_address: tuple[str, int], address: str, *args: Any) -> None:
        """
        Entry point for every incoming OSC message.

        python-osc calls this synchronously from inside the asyncio event
        loop, so we can safely schedule async work with create_task().
        """
        logger.info("OSC ← %s  args=%s  from=%s", address, args, client_address)

        entry = {
            "ts":      datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "src":     f"{client_address[0]}:{client_address[1]}",
            "address": address,
            "args":    list(args),
        }
        _OSC_TRAFFIC.append(entry)
        for cb in list(_osc_callbacks):
            try:
                cb(entry)
            except Exception:
                pass

        asyncio.create_task(self._route(client_address, address, list(args)))

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def _route(
        self, client_address: tuple[str, int], address: str, args: list[Any]
    ) -> None:
        """
        Parse the OSC address and dispatch to the appropriate command.

        Address structure:
          /switcher / <target> / <action> [/ <sub>]
          parts[0]   parts[1]   parts[2]   parts[3]
        """
        parts = [p for p in address.split("/") if p]

        if not parts:
            return

        # ── /cue/... ──────────────────────────────────────────────────────
        if parts[0].lower() == "cue":
            await self._route_cue(client_address, address, parts, args)
            return

        # ── /atem/<name>/macro/<index> ────────────────────────────────────
        if parts[0].lower() == "atem":
            await self._route_atem(client_address, address, parts, args)
            return

        # Must start with "switcher"
        if parts[0].lower() != "switcher":
            logger.debug("Ignored non-switcher address: %s", address)
            return

        if len(parts) < 3:
            logger.warning("Malformed OSC address (too short): %s", address)
            self._send_reply(client_address, address, ok=False)
            return

        target = parts[1]   # switcher name or "all"
        action = parts[2].lower()
        ok = False

        # ── recall / program (aliases — load directly to PGM) ────────────
        if action in ("recall", "program"):
            ok = await self._handle_preset_cmd(address, target, args, "program")

        # ── preview (load to PVW bus) ─────────────────────────────────
        elif action == "preview":
            ok = await self._handle_preset_cmd(address, target, args, "preview")

        # ── take (transition PVW → PGM) ───────────────────────────────
        elif action == "take":
            ok = await self._handle_take(address, target, args)

        # ── cut (hard cut) ────────────────────────────────────────────
        elif action == "cut":
            ok = await self._handle_cut(address, target)

        # ── layer opacity ─────────────────────────────────────────────
        elif action == "layer":
            ok = await self._handle_layer_opacity(address, target, parts, args)

        # ── transition time ───────────────────────────────────────────
        elif action == "transition":
            ok = await self._handle_transition(address, target, args)

        else:
            logger.warning("Unknown OSC action %r in address %s", action, address)

        self._send_reply(client_address, address, ok=ok)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _parse_preset_arg(self, address: str, args: list[Any]) -> int | str | None:
        """Parse the first OSC arg as a preset ID (int) or name (str)."""
        if not args:
            logger.warning("Preset command has no argument: %s", address)
            return None
        raw = args[0]
        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, str):
            stripped = raw.strip()
            try:
                return int(stripped)
            except ValueError:
                return stripped
        logger.warning("Unrecognised preset argument type %s in %s", type(raw).__name__, address)
        return None

    async def _handle_preset_cmd(
        self, address: str, target: str, args: list[Any], mode: str
    ) -> bool:
        """Shared handler for /program and /preview commands."""
        preset = self._parse_preset_arg(address, args)
        if preset is None:
            return False
        if mode == "preview":
            results = await self._manager.osc_preview(target, preset)
        else:
            results = await self._manager.osc_recall(target, preset)
        self._log_results(mode, target, preset, results)
        ok = bool(results) and all(results.values())
        if results:
            for switcher_name, success in results.items():
                event = {
                    "mode":     "PVW" if mode == "preview" else "PGM",
                    "preset":   str(preset),
                    "switcher": switcher_name,
                    "ok":       success,
                }
                _recall_queue.append(event)
                for cb in list(_recall_callbacks):
                    try:
                        cb(event)
                    except Exception:
                        pass
        return ok

    async def _handle_take(self, address: str, target: str, args: list[Any]) -> bool:
        ms: int | None = None
        if args:
            try:
                ms = max(0, int(float(args[0])))
            except (ValueError, TypeError):
                logger.warning("Ignoring non-numeric take argument in %s: %r", address, args[0])
        results = await self._manager.osc_take(target, ms)
        label = f"take ({ms}ms)" if ms is not None else "take"
        self._log_results(label, target, "", results)
        return bool(results) and all(results.values())

    async def _handle_cut(self, address: str, target: str) -> bool:
        results = await self._manager.osc_cut(target)
        self._log_results("CUT", target, "", results)
        return bool(results) and all(results.values())

    async def _handle_layer_opacity(
        self, address: str, target: str, parts: list[str], args: list[Any]
    ) -> bool:
        """
        Expects: /switcher/<name>/layer/<n>/opacity <value>
        parts index:              [0]  [1]   [2]   [3]   [4]
        """
        if len(parts) < 5 or parts[4].lower() != "opacity":
            logger.warning("Malformed layer opacity address: %s", address)
            return False
        if not args:
            logger.warning("Layer opacity message has no value: %s", address)
            return False
        try:
            layer   = int(parts[3])
            opacity = max(0, min(100, int(float(args[0]))))
        except (ValueError, IndexError) as exc:
            logger.warning("Could not parse layer/opacity from %s: %s", address, exc)
            return False

        results = await self._manager.osc_layer_opacity(target, layer, opacity)
        self._log_results(f"layer/{layer}/opacity", target, opacity, results)
        return bool(results) and all(results.values())

    async def _handle_transition(self, address: str, target: str, args: list[Any]) -> bool:
        """
        /switcher/<name>/transition <ms>

        Sets the transition time (in milliseconds) on the target switcher(s).
        The new value is used for all subsequent recall and take commands.
        """
        if not args:
            logger.warning("Transition command has no argument: %s", address)
            return False
        try:
            ms = max(0, int(float(args[0])))
        except (ValueError, TypeError) as exc:
            logger.warning("Could not parse transition time from %s: %s", address, exc)
            return False
        results = await self._manager.osc_set_transition(target, ms)
        self._log_results("transition", target, f"{ms}ms", results)
        return bool(results) and all(results.values())

    # ------------------------------------------------------------------
    # Cue list routing
    # ------------------------------------------------------------------

    async def _route_cue(
        self,
        client_address: tuple[str, int],
        address: str,
        parts: list[str],
        args: list[Any],
    ) -> None:
        """
        Handle /cue/... commands.

          /cue/go           fire next cue
          /cue/back         fire previous cue  (alias: /cue/prev)
          /cue/reset        reset pointer to before first cue
          /cue/<number>     jump to cue by display number and fire it
        """
        if self._engine is None:
            logger.warning("/cue command received but no CueEngine is attached.")
            self._send_reply(client_address, address, ok=False)
            return

        if len(parts) < 2:
            logger.warning("Malformed /cue address (too short): %s", address)
            self._send_reply(client_address, address, ok=False)
            return

        action = parts[1].lower()
        ok = False

        if action == "go":
            cue, results = await self._engine.go()
            ok = cue is not None
            if cue:
                logger.info("/cue/go → fired cue %s %r", cue.get("number"), cue.get("label"))
            else:
                logger.info("/cue/go → end of cue list or list empty")

        elif action in ("back", "prev"):
            cue, _ = await self._engine.back()
            ok = True
            if cue:
                logger.info("/cue/back → fired cue %s %r", cue.get("number"), cue.get("label"))
            else:
                logger.info("/cue/back → reset to before first cue")

        elif action == "reset":
            self._engine.reset()
            ok = True
            logger.info("/cue/reset → pointer reset")

        else:
            # /cue/<number> — find by display number and fire
            target_num = parts[1]
            cues = self._engine.cues
            idx = next(
                (i for i, c in enumerate(cues)
                 if str(c.get("number", "")).strip() == target_num.strip()),
                None,
            )
            if idx is None:
                logger.warning("/cue/%s — cue number %r not found", target_num, target_num)
            else:
                self._engine.jump_before(idx)
                cue, results = await self._engine.go()
                ok = cue is not None
                if cue:
                    logger.info("/cue/%s → fired cue %r", target_num, cue.get("label"))

        self._send_reply(client_address, address, ok=ok)

    # ------------------------------------------------------------------
    # ATEM-specific routing
    # ------------------------------------------------------------------

    async def _route_atem(
        self,
        client_address: tuple[str, int],
        address: str,
        parts: list[str],
        args: list[Any],
    ) -> None:
        """
        Handle /atem/<name>/macro/<index>  — run ATEM macro by slot index.

        parts: ["atem", <name>, "macro", <index>]

        <name> can be the switcher's configured name (case-insensitive) or "all"
        to target every connected ATEM switcher.
        """
        ok = False
        if len(parts) < 4 or parts[2].lower() != "macro":
            logger.warning("Malformed /atem address (expected /atem/<name>/macro/<index>): %s", address)
            self._send_reply(client_address, address, ok=False)
            return

        target = parts[1]  # switcher name or "all"
        try:
            index = int(parts[3])
        except (ValueError, IndexError):
            logger.warning("/atem macro index must be an integer, got %r in %s", parts[3], address)
            self._send_reply(client_address, address, ok=False)
            return

        results = await self._manager.osc_recall(target, index)
        self._log_results("atem/macro", target, index, results)
        ok = bool(results) and all(results.values())

        if results:
            for switcher_name, success in results.items():
                event = {
                    "mode":     "PGM",
                    "preset":   f"Macro {index}",
                    "switcher": switcher_name,
                    "ok":       success,
                }
                _recall_queue.append(event)
                for cb in list(_recall_callbacks):
                    try:
                        cb(event)
                    except Exception:
                        pass

        self._send_reply(client_address, address, ok=ok)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _send_reply(client_address: tuple[str, int], original_address: str, ok: bool) -> None:
        """Send an OSC reply back to the sender confirming success or failure."""
        try:
            reply_address = original_address + ("/reply" if not original_address.endswith("/reply") else "")
            client = SimpleUDPClient(client_address[0], client_address[1])
            client.send_message(reply_address, 1 if ok else 0)
        except Exception as exc:
            logger.debug("OSC reply failed: %s", exc)

    @staticmethod
    def _log_results(action: str, target: str, value: Any, results: dict[str, bool]) -> None:
        if not results:
            logger.warning("Action %r → target %r — no switchers matched / connected", action, target)
            return
        for name, ok in results.items():
            status = "OK" if ok else "FAILED"
            logger.info("  → %s %r %s: %s", action, value, name, status)
