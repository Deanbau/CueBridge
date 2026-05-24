"""
switchers/atem.py — Blackmagic Design ATEM switcher implementation.

Protocol: ATEM UDP protocol on port 9910.
Library:  pyatem  (pip install pyatem)
Repo:     https://git.sr.ht/~martijnbraam/pyatem

Presets = ATEM Macros (listed by name, recalled by index).

OSC commands supported:
  /switcher/<name>/recall  <index>        run macro by index
  /switcher/<name>/recall  <name>         run macro by name
  /switcher/<name>/take                   auto-take on M/E <me> (default 0)
  /switcher/<name>/cut                    cut on M/E <me>

Config keys (beyond base):
  me  — M/E number to control for cut/take, 0-indexed (default 0 = "M/E 1")
"""

import asyncio
import logging
import struct
import threading
from typing import Any

from .base import BaseSwitcher, Preset, SwitcherStatus

logger = logging.getLogger("cuebridge.switcher.atem")


class _MacroRunCommand:
    """Raw `MAct` command — runs a macro by slot index. Not in pyatem stdlib."""

    ACTION_RUN = 0

    def __init__(self, index: int, action: int = 0):
        self.index = index
        self.action = action

    def get_command(self) -> bytes:
        data = struct.pack('>HBx', self.index, self.action)
        length = len(data) + 8
        header = struct.pack('>H 2x 4s', length, b'MAct')
        return header + data


class AtemSwitcher(BaseSwitcher):
    """
    Blackmagic ATEM via pyatem.

    pyatem is a synchronous blocking library — data is received by calling
    protocol.loop() in a tight loop.  We run that loop in a daemon thread and
    bridge events back to the asyncio event loop via call_soon_threadsafe().
    """

    def __init__(self, cfg: dict[str, Any]) -> None:
        cfg.setdefault("port", 9910)
        super().__init__(cfg)
        self._me: int = max(0, int(cfg.get("me", 0)))
        self._protocol = None
        self._loop_thread: threading.Thread | None = None
        self._loop_stop: threading.Event = threading.Event()
        self._state_ready: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _import_pyatem(self):
        try:
            from pyatem.protocol import AtemProtocol
            return AtemProtocol
        except ImportError:
            raise RuntimeError("pyatem is not installed. Run: pip install pyatem")

    def _send(self, *commands) -> bool:
        if self._protocol is None:
            return False
        try:
            self._protocol.send_commands(list(commands))
            return True
        except Exception as exc:
            self._log.error("ATEM send error: %s", exc)
            return False

    def _stop_loop_thread(self) -> None:
        """Signal and join the receive thread."""
        self._loop_stop.set()
        if self._loop_thread and self._loop_thread.is_alive():
            self._loop_thread.join(timeout=3.0)
        self._loop_thread = None

    # ------------------------------------------------------------------
    # BaseSwitcher implementation
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        self.cancel_reconnect()
        self.cancel_watchdog()
        self.status = SwitcherStatus.CONNECTING
        self.status_detail = "Connecting…"

        # Tear down existing connection + thread
        self._stop_loop_thread()
        if self._protocol is not None:
            try:
                self._protocol.connect()  # pyatem has no disconnect(); reinit closes socket
            except Exception:
                pass
            self._protocol = None

        try:
            AtemProtocol = self._import_pyatem()
        except RuntimeError as exc:
            self.status = SwitcherStatus.ERROR
            self.status_detail = str(exc)
            return False

        loop = asyncio.get_running_loop()
        self._state_ready = asyncio.Event()
        self._loop_stop.clear()

        protocol = AtemProtocol(self.ip, self.port)

        def _on_connected():
            self._log.debug("ATEM connected event — waiting for state data")
            # Signal ready immediately on connection; we sleep after to let state populate
            if self._state_ready and not self._state_ready.is_set():
                loop.call_soon_threadsafe(self._state_ready.set)

        def _on_disconnected():
            self._log.warning("ATEM disconnected event")
            loop.call_soon_threadsafe(self._on_atem_disconnect)

        protocol.on('connected',    _on_connected)
        protocol.on('disconnected', _on_disconnected)

        try:
            protocol.connect()
        except Exception as exc:
            self.status = SwitcherStatus.ERROR
            self.status_detail = str(exc)
            self._start_reconnect_loop()
            return False

        # Run the blocking receive loop in a daemon thread
        stop_evt = self._loop_stop

        def _receive_loop():
            self._log.debug("ATEM receive thread started")
            while not stop_evt.is_set():
                try:
                    protocol.loop()
                except Exception as exc:
                    self._log.error("ATEM loop error: %s", exc)
                    break
                if not protocol.connected:
                    break
            self._log.debug("ATEM receive thread exited")

        self._loop_thread = threading.Thread(target=_receive_loop, daemon=True, name=f"atem-{self.name}")
        self._loop_thread.start()

        # Wait for 'connected' event (up to 10 s)
        try:
            await asyncio.wait_for(self._state_ready.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            self.status = SwitcherStatus.ERROR
            self.status_detail = "Connection timeout"
            self._log.error("Timed out waiting for ATEM connection from %s", self.address)
            self._stop_loop_thread()
            self._start_reconnect_loop()
            return False

        # Give the ATEM time to send its full state dump (macros, etc.)
        self.status_detail = "Loading state…"
        await asyncio.sleep(3.0)

        self._protocol = protocol
        self.status = SwitcherStatus.CONNECTED
        self.status_detail = "Connected"
        self._mark_connected()
        self._log.info("Connected to ATEM at %s", self.address)

        self.presets = await self.get_presets()
        self._log.info("Found %d macros on %s", len(self.presets), self.name)
        return True

    def _on_atem_disconnect(self) -> None:
        """Called (thread-safe) when pyatem fires a disconnect event."""
        if self.status == SwitcherStatus.CONNECTED:
            self._handle_comms_error()

    async def disconnect(self) -> None:
        self.cancel_reconnect()
        self._mark_disconnected()
        self._stop_loop_thread()
        self._protocol = None
        self.status = SwitcherStatus.DISCONNECTED
        self.status_detail = ""
        self._log.info("Disconnected from %s", self.name)

    async def get_presets(self) -> list[Preset]:
        """Return all valid (non-empty) macros as Preset objects."""
        if self._protocol is None:
            return []
        try:
            # mixerstate['macro-properties'] is a dict keyed by slot index tuple (idx,)
            macro_state: dict = getattr(self._protocol, 'mixerstate', {}).get('macro-properties', {})
            presets: list[Preset] = []
            for key, mp in macro_state.items():
                is_used = getattr(mp, 'is_used', False)
                name = getattr(mp, 'name', b'')
                if isinstance(name, bytes):
                    name = name.decode('utf-8', errors='replace').strip('\x00')
                slot = getattr(mp, 'index', None)
                if slot is None:
                    # key is a tuple like (0,) from FIELDNAME_UNIQUE unpacking
                    slot = key[0] if isinstance(key, tuple) else int(key)
                if is_used and name:
                    presets.append(Preset(id=int(slot), name=name))
            return sorted(presets, key=lambda p: p.id)
        except Exception as exc:
            self._log.error("get_presets failed: %s", exc)
            return []

    async def recall_preset_by_id(self, preset_id: int | str) -> bool:
        """Run a macro by its slot index."""
        if not self.is_connected:
            return False
        try:
            self._mark_activity()
            ok = self._send(_MacroRunCommand(index=int(preset_id)))
            if ok:
                self._log.info("Macro %s run on %s", preset_id, self.name)
            return ok
        except Exception as exc:
            self._log.error("recall_preset_by_id(%s) failed: %s", preset_id, exc)
            self._handle_comms_error()
            return False

    async def take(self) -> bool:
        """Auto-take on M/E."""
        if not self.is_connected:
            return False
        try:
            from pyatem.command import AutoCommand
            self._mark_activity()
            ok = self._send(AutoCommand(index=self._me))
            if ok:
                self._log.info("AUTO → %s M/E %d", self.name, self._me)
            return ok
        except Exception as exc:
            self._log.error("take failed: %s", exc)
            self._handle_comms_error()
            return False

    async def cut(self) -> bool:
        """Hard cut on M/E."""
        if not self.is_connected:
            return False
        try:
            from pyatem.command import CutCommand
            self._mark_activity()
            ok = self._send(CutCommand(index=self._me))
            if ok:
                self._log.info("CUT → %s M/E %d", self.name, self._me)
            return ok
        except Exception as exc:
            self._log.error("cut failed: %s", exc)
            self._handle_comms_error()
            return False

    async def ping(self) -> bool:
        """Liveness check — true if protocol object exists and connected."""
        if self._protocol is None:
            return False
        try:
            connected = getattr(self._protocol, 'connected', None)
            if connected is not None:
                return bool(connected)
            return self._protocol is not None
        except Exception:
            return False
