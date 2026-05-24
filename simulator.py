#!/usr/bin/env python3
"""
simulator.py — Simulated Barco Event Master + PixelHue Switcher.

Starts two virtual devices on localhost so you can run CueBridge and test
every OSC command without any real hardware connected.

  Barco Event Master   HTTP REST    0.0.0.0:{barco_port}    (default 9999)
  PixelHue Switcher    WebSocket    0.0.0.0:{pixelhue_port}  (default 9000)

Simulator dashboard:
  http://localhost:{ui_port}   (default 8090)

Usage:
    python simulator.py
    python simulator.py --barco-port 9999 --pixelhue-port 9000 --ui-port 8090 --presets 12
"""

import argparse
import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aiohttp import web
import aiohttp
from nicegui import app as ng_app, ui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("simulator")

# ─────────────────────────────────────────────────────────────────────────────
# Shared state model
# ─────────────────────────────────────────────────────────────────────────────

LOG_MAX = 300


@dataclass
class SwitcherState:
    device_name: str
    presets: list[dict]          # [{"id": 1, "name": "Scene 1"}, …]
    program: int | None = None   # current PGM preset id (None = nothing taken yet)
    preview: int | None = None   # current PVW preset id
    layer_opacities: dict = field(default_factory=dict)
    log: deque = field(default_factory=lambda: deque(maxlen=LOG_MAX))
    version: int = 0             # incremented on each change so the UI can poll

    def touch(self) -> None:
        self.version += 1

    def preset_name(self, pid: Any) -> str:
        if pid is None:
            return "—"
        for p in self.presets:
            if str(p["id"]) == str(pid):
                return p["name"]
        return f"#{pid}"

    def add_log(self, msg: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.append({"ts": ts, "msg": msg, "level": level})
        self.touch()
        logger.info("[%s] %s", self.device_name, msg)


def _make_presets(n: int) -> list[dict]:
    return [{"id": i, "name": f"Scene {i}"} for i in range(1, n + 1)]


# ─────────────────────────────────────────────────────────────────────────────
# Barco Event Master — HTTP REST server (aiohttp)
# ─────────────────────────────────────────────────────────────────────────────

def _build_barco_app(state: SwitcherState) -> web.Application:
    """Return an aiohttp Application that simulates the Barco EM REST API."""

    routes = web.RouteTableDef()

    # ── System info ───────────────────────────────────────────────────────────
    @routes.get("/api/system/listAll")
    @routes.get("/api/system/getSystemSettings")
    async def system_info(request: web.Request) -> web.Response:
        state.add_log(f"GET {request.path}")
        return web.json_response({
            "systemName": state.device_name,
            "firmwareVersion": "7.5.0-sim",
            "serialNumber": "SIM-0001",
            "status": "ok",
        })

    # ── Preset list ───────────────────────────────────────────────────────────
    @routes.get("/api/preset/listPreset")
    async def list_presets(request: web.Request) -> web.Response:
        state.add_log(f"GET /api/preset/listPreset → {len(state.presets)} presets")
        preset_list = [
            {"presetId": p["id"], "presetName": p["name"]}
            for p in state.presets
        ]
        return web.json_response({"presetList": preset_list})

    # ── Activate (program) ────────────────────────────────────────────────────
    @routes.post("/api/preset/activatePreset")
    async def activate_preset(request: web.Request) -> web.Response:
        body = await request.json()
        objs = body.get("inputObj", [{}])
        pid = objs[0].get("presetId") if objs else None
        state.program = pid
        state.add_log(f"PROGRAM → preset {pid}  ({state.preset_name(pid)})")
        return web.json_response({"status": "ok", "presetId": pid})

    # ── Preview ───────────────────────────────────────────────────────────────
    @routes.post("/api/preset/previewPreset")
    async def preview_preset(request: web.Request) -> web.Response:
        body = await request.json()
        objs = body.get("inputObj", [{}])
        pid = objs[0].get("presetId") if objs else None
        state.preview = pid
        state.add_log(f"PREVIEW → preset {pid}  ({state.preset_name(pid)})")
        return web.json_response({"status": "ok", "presetId": pid})

    # ── Take ──────────────────────────────────────────────────────────────────
    @routes.post("/api/output/Take")
    async def take(request: web.Request) -> web.Response:
        prev_pgm = state.program
        state.program = state.preview
        state.add_log(
            f"TAKE  PVW {state.preset_name(state.preview)} → "
            f"PGM  (was {state.preset_name(prev_pgm)})"
        )
        return web.json_response({"status": "ok"})

    # ── Cut ───────────────────────────────────────────────────────────────────
    @routes.post("/api/output/Cut")
    async def cut(request: web.Request) -> web.Response:
        prev_pgm = state.program
        state.program = state.preview
        state.add_log(
            f"CUT   PVW {state.preset_name(state.preview)} → "
            f"PGM  (was {state.preset_name(prev_pgm)})"
        )
        return web.json_response({"status": "ok"})

    # ── Layer opacity ─────────────────────────────────────────────────────────
    @routes.post("/api/layer/setOpacity")
    async def set_opacity(request: web.Request) -> web.Response:
        body = await request.json()
        objs = body.get("inputObj", [{}])
        item = objs[0] if objs else {}
        layer = item.get("layerId", "?")
        opacity = item.get("opacity", "?")
        # Convert 0-255 back to 0-100 for display
        pct = round(int(opacity) * 100 / 255) if isinstance(opacity, int) else opacity
        state.layer_opacities[str(layer)] = pct
        state.add_log(f"LAYER {layer} opacity → {pct}%")
        state.touch()
        return web.json_response({"status": "ok"})

    # ── Catch-all (useful for debugging unexpected calls) ─────────────────────
    @routes.route("*", "/{path_info:.*}")
    async def catch_all(request: web.Request) -> web.Response:
        state.add_log(
            f"⚠ Unhandled {request.method} {request.path}", level="warning"
        )
        return web.json_response({"status": "not_implemented"}, status=404)

    barco_app = web.Application()
    barco_app.add_routes(routes)
    return barco_app


async def start_barco_server(state: SwitcherState, port: int) -> web.AppRunner:
    aio_app = _build_barco_app(state)
    runner = web.AppRunner(aio_app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Barco EM simulator   → http://0.0.0.0:%d", port)
    return runner


# ─────────────────────────────────────────────────────────────────────────────
# PixelHue Switcher — WebSocket server (aiohttp)
# ─────────────────────────────────────────────────────────────────────────────

def _build_pixelhue_app(state: SwitcherState) -> web.Application:
    """Return an aiohttp Application that simulates the PixelHue WebSocket API."""

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state.add_log("WebSocket client connected")

        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                break
            try:
                frame = json.loads(msg.data)
            except json.JSONDecodeError:
                state.add_log("⚠ Non-JSON frame received", level="warning")
                continue

            cmd  = frame.get("type", "")
            mid  = frame.get("msgId")

            response: dict = {"status": "ok"}
            if mid:
                response["msgId"] = mid

            if cmd == "getSwitcherInfo":
                state.add_log("GET switcherInfo")
                response.update({
                    "deviceName": state.device_name,
                    "firmwareVersion": "2.1.0-sim",
                    "sceneList": [
                        {"index": p["id"], "name": p["name"]}
                        for p in state.presets
                    ],
                })

            elif cmd == "sceneRecall":
                idx = frame.get("index")
                state.program = idx
                state.add_log(f"PROGRAM → scene {idx}  ({state.preset_name(idx)})")

            elif cmd == "scenePreview":
                idx = frame.get("index")
                state.preview = idx
                state.add_log(f"PREVIEW → scene {idx}  ({state.preset_name(idx)})")

            elif cmd == "take":
                prev_pgm = state.program
                state.program = state.preview
                state.add_log(
                    f"TAKE  PVW {state.preset_name(state.preview)} → "
                    f"PGM  (was {state.preset_name(prev_pgm)})"
                )

            elif cmd == "cut":
                prev_pgm = state.program
                state.program = state.preview
                state.add_log(
                    f"CUT   PVW {state.preset_name(state.preview)} → "
                    f"PGM  (was {state.preset_name(prev_pgm)})"
                )

            elif cmd == "setLayerOpacity":
                layer   = frame.get("layer", "?")
                opacity = frame.get("opacity", "?")
                state.layer_opacities[str(layer)] = opacity
                state.add_log(f"LAYER {layer} opacity → {opacity}%")
                state.touch()

            else:
                state.add_log(f"⚠ Unknown command: {cmd}", level="warning")
                response["status"] = "unknown_command"

            await ws.send_str(json.dumps(response))

        state.add_log("WebSocket client disconnected")
        return ws

    ph_app = web.Application()
    ph_app.router.add_get("/websocket", ws_handler)
    return ph_app


async def start_pixelhue_server(state: SwitcherState, port: int) -> web.AppRunner:
    aio_app = _build_pixelhue_app(state)
    runner = web.AppRunner(aio_app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("PixelHue simulator   → ws://0.0.0.0:%d/websocket", port)
    return runner


# ─────────────────────────────────────────────────────────────────────────────
# NiceGUI dashboard
# ─────────────────────────────────────────────────────────────────────────────

_LEVEL_COLOR = {
    "info":    "text-gray-300",
    "warning": "text-yellow-400",
    "error":   "text-red-400",
}


def _build_device_card(state: SwitcherState, label: str, accent: str) -> None:
    """
    Render a live device state card for one simulator.
    Uses a timer to poll state.version so it only redraws on changes.
    """
    with ui.card().classes(f"bg-gray-800 border border-{accent}-700 flex-1 p-4 gap-3"):
        # ── Header ────────────────────────────────────────────────────────────
        with ui.row().classes("items-center gap-2"):
            ui.icon("tv").classes(f"text-{accent}-400 text-xl")
            ui.label(label).classes(f"text-{accent}-300 font-bold text-base uppercase tracking-wide")

        # ── PGM / PVW display ─────────────────────────────────────────────────
        with ui.card().classes("bg-gray-900 w-full p-3 gap-1"):
            with ui.row().classes("items-center gap-2"):
                ui.badge("PGM").props("color=red").classes("font-mono text-xs")
                pgm_lbl = ui.label("—").classes("text-white text-lg font-bold")

            with ui.row().classes("items-center gap-2"):
                ui.badge("PVW").props("color=blue").classes("font-mono text-xs")
                pvw_lbl = ui.label("—").classes("text-blue-300 text-base")

        # ── Layer opacities ───────────────────────────────────────────────────
        opacity_lbl = ui.label("").classes("text-gray-500 text-xs font-mono")

        # ── Manual controls ───────────────────────────────────────────────────
        with ui.expansion("Manual overrides", icon="tune").classes("w-full"):
            with ui.column().classes("gap-2 pt-1"):
                ui.label("Set PGM preset ID:").classes("text-gray-400 text-xs")
                with ui.row().classes("gap-2"):
                    pgm_in = ui.number(min=1, max=999, value=1).classes("w-24")
                    ui.button("Set PGM",
                              on_click=lambda: _manual_set(state, "program", int(pgm_in.value))
                              ).classes("bg-red-700 text-white text-xs")

                ui.label("Set PVW preset ID:").classes("text-gray-400 text-xs")
                with ui.row().classes("gap-2"):
                    pvw_in = ui.number(min=1, max=999, value=1).classes("w-24")
                    ui.button("Set PVW",
                              on_click=lambda: _manual_set(state, "preview", int(pvw_in.value))
                              ).classes("bg-blue-700 text-white text-xs")

                ui.button("Reset state",
                          on_click=lambda: _reset_state(state)
                          ).classes("bg-gray-700 text-white text-xs")

        # ── Auto-update ───────────────────────────────────────────────────────
        _last: dict = {"v": -1}

        def _refresh():
            if state.version == _last["v"]:
                return
            _last["v"] = state.version

            pgm_lbl.set_text(state.preset_name(state.program))
            pvw_lbl.set_text(state.preset_name(state.preview))

            if state.layer_opacities:
                parts = [f"L{k}:{v}%" for k, v in sorted(state.layer_opacities.items())]
                opacity_lbl.set_text("  ".join(parts))
            else:
                opacity_lbl.set_text("")

        ui.timer(0.25, _refresh)


def _manual_set(state: SwitcherState, attr: str, value: int) -> None:
    setattr(state, attr, value)
    state.add_log(f"[manual] set {attr} → {value}  ({state.preset_name(value)})", level="warning")


def _reset_state(state: SwitcherState) -> None:
    state.program = None
    state.preview = None
    state.layer_opacities.clear()
    state.add_log("[manual] state reset", level="warning")


def _build_log_panel(barco: SwitcherState, pixelhue: SwitcherState) -> None:
    """Scrolling combined command log for both simulators."""
    with ui.card().classes("bg-gray-900 border border-gray-700 w-full p-3 gap-2"):
        with ui.row().classes("items-center gap-2"):
            ui.label("Command Log").classes("text-gray-400 text-sm font-semibold uppercase tracking-wide")
            ui.space()
            ui.button("Clear", icon="delete_sweep",
                      on_click=lambda: (barco.log.clear(), pixelhue.log.clear(),
                                        barco.touch(), pixelhue.touch())
                      ).classes("bg-gray-700 text-white text-xs")

        log_col = ui.column().classes(
            "w-full bg-gray-950 rounded p-2 gap-0 overflow-y-auto font-mono text-xs"
        ).style("height: 280px;")

        _last: dict = {"bv": -1, "pv": -1}

        def _refresh_log():
            bv = barco.version
            pv = pixelhue.version
            if bv == _last["bv"] and pv == _last["pv"]:
                return
            _last["bv"] = bv
            _last["pv"] = pv

            log_col.clear()
            # Merge both logs by timestamp
            merged = []
            for entry in barco.log:
                merged.append(("Barco", entry))
            for entry in pixelhue.log:
                merged.append(("PixelHue", entry))
            merged.sort(key=lambda x: x[1]["ts"])

            with log_col:
                for source, entry in merged[-150:]:
                    color = _LEVEL_COLOR.get(entry["level"], "text-gray-300")
                    src_color = "text-orange-400" if source == "Barco" else "text-teal-400"
                    with ui.row().classes("gap-2 items-start py-0"):
                        ui.label(entry["ts"]).classes("text-gray-600 flex-shrink-0 w-24")
                        ui.label(source).classes(f"{src_color} flex-shrink-0 w-20")
                        ui.label(entry["msg"]).classes(f"{color} break-all")

            ui.run_javascript(
                "const el = document.querySelector('.sim-log'); if(el) el.scrollTop = el.scrollHeight;"
            )

        log_col.classes(add="sim-log")
        ui.timer(0.5, _refresh_log)


def setup_dashboard(barco: SwitcherState, pixelhue: SwitcherState,
                    barco_port: int, pixelhue_port: int) -> None:

    @ui.page("/")
    async def index():
        ui.add_head_html("<style>body{background:#111827}</style>")

        # ── Header ────────────────────────────────────────────────────────────
        with ui.header().classes("bg-gray-900 shadow px-6 py-3 flex items-center gap-4"):
            ui.icon("science").classes("text-yellow-400 text-2xl")
            ui.label("CueBridge Simulator").classes("text-white text-xl font-bold tracking-wide")
            ui.space()
            ui.badge(f"Barco EM  :{barco_port}").props("color=orange").classes("text-xs px-2")
            ui.badge(f"PixelHue  :{pixelhue_port}").props("color=teal").classes("text-xs px-2")
            ui.label("Point CueBridge here: 127.0.0.1").classes("text-gray-400 text-xs")

        with ui.column().classes("w-full p-4 gap-4"):
            # ── Device cards ──────────────────────────────────────────────────
            with ui.row().classes("w-full gap-4"):
                _build_device_card(barco,    "Barco Event Master",  "orange")
                _build_device_card(pixelhue, "PixelHue Switcher",   "teal")

            # ── Preset reference ──────────────────────────────────────────────
            with ui.expansion("Preset list", icon="format_list_numbered").classes("w-full"):
                with ui.row().classes("gap-8 flex-wrap"):
                    with ui.column().classes("gap-1"):
                        ui.label("Barco EM").classes("text-orange-400 text-xs font-semibold mb-1")
                        for p in barco.presets:
                            ui.label(f"  {p['id']:>3}  {p['name']}").classes("text-gray-400 text-xs font-mono")
                    with ui.column().classes("gap-1"):
                        ui.label("PixelHue").classes("text-teal-400 text-xs font-semibold mb-1")
                        for p in pixelhue.presets:
                            ui.label(f"  {p['id']:>3}  {p['name']}").classes("text-gray-400 text-xs font-mono")

            # ── Log ───────────────────────────────────────────────────────────
            _build_log_panel(barco, pixelhue)

            # ── Quick OSC test reference ──────────────────────────────────────
            with ui.expansion("OSC test commands", icon="code").classes("w-full"):
                examples = [
                    ("Load preset 1 to preview (Barco)",  "/switcher/Barco Main/preview 1"),
                    ("Load preset 1 to program (Barco)",  "/switcher/Barco Main/program 1"),
                    ("Take on Barco",                      "/switcher/Barco Main/take"),
                    ("Cut on Barco",                       "/switcher/Barco Main/cut"),
                    ("Load scene 3 to PVW (PixelHue)",    "/switcher/PixelHue Backup/preview 3"),
                    ("Take all switchers",                 "/switcher/all/take"),
                    ("Program all — scene 2",              "/switcher/all/program 2"),
                ]
                with ui.column().classes("gap-1 font-mono text-xs pt-1"):
                    for desc, cmd in examples:
                        with ui.row().classes("gap-4 items-center"):
                            ui.label(cmd).classes("text-green-400 w-64")
                            ui.label(desc).classes("text-gray-500")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="CueBridge Hardware Simulator")
    p.add_argument("--barco-port",    type=int, default=9999, help="Barco EM HTTP port (default 9999)")
    p.add_argument("--pixelhue-port", type=int, default=9000, help="PixelHue WS port (default 9000)")
    p.add_argument("--ui-port",       type=int, default=8090, help="Simulator dashboard port (default 8090)")
    p.add_argument("--presets",       type=int, default=10,   help="Number of presets per device (default 10)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    barco_state    = SwitcherState("Barco EM Simulator",    _make_presets(args.presets))
    pixelhue_state = SwitcherState("PixelHue Simulator",    _make_presets(args.presets))

    barco_state.add_log("Simulator ready", level="info")
    pixelhue_state.add_log("Simulator ready", level="info")

    @ng_app.on_startup
    async def _start() -> None:
        await start_barco_server(barco_state, args.barco_port)
        await start_pixelhue_server(pixelhue_state, args.pixelhue_port)
        logger.info("Simulator dashboard  → http://localhost:%d", args.ui_port)

    setup_dashboard(barco_state, pixelhue_state, args.barco_port, args.pixelhue_port)

    ui.run(
        title="CueBridge Simulator",
        favicon="🔬",
        dark=True,
        port=args.ui_port,
        host="0.0.0.0",
        reload=False,
        show=True,
        uvicorn_logging_level="warning",
    )


if __name__ == "__main__":
    main()
