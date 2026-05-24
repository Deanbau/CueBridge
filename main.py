"""
main.py — OSC Switcher Bridge entry point.

Usage:
    python main.py [--port 8080] [--config path/to/config.json]

The NiceGUI web server starts on the given port (default 8080).
Open a browser to http://localhost:8080 (or the machine's IP on the LAN).

Architecture overview:
    main.py
      ├─ ConfigManager     — JSON persistence
      ├─ SwitcherManager   — Live switcher instances (Barco / PixelHue)
      ├─ OSCHandler        — AsyncIO UDP OSC server on configurable port
      └─ NiceGUI (ui.run)  — Web UI served on HTTP port 8080
         └─ ui/app.py      — Page builder, tabs, live log

All components share the same asyncio event loop provided by NiceGUI/uvicorn.
Background tasks (reconnect loops, OSC listener) are scheduled via
asyncio.create_task() from app.on_startup callbacks.
"""

import argparse
import asyncio
import logging
import sys

from nicegui import app, ui

from config import ConfigManager
from cue_engine import CueEngine
from logger_setup import setup_logging
from osc_handler import OSCHandler
from switcher_manager import SwitcherManager
from ui.app import setup_ui


def _port_free(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OSC Switcher Bridge")
    parser.add_argument(
        "--port", type=int, default=None,
        help="Web UI HTTP port (default: from config, fallback 8080)"
    )
    parser.add_argument(
        "--server-mode", action="store_true",
        help="Run as server only (no launcher window) — used internally by the launcher"
    )
    parser.add_argument(
        "--config", type=str, default="cuebridge_config.json",
        help="Path to the JSON config file (default: cuebridge_config.json)"
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Console log level (default: DEBUG)"
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run without opening a browser window (useful on Raspberry Pi)"
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # ── Launcher (frozen bundle, not explicitly in server-mode) ───────────────
    if getattr(sys, "frozen", False) and not args.server_mode and not args.headless:
        from launcher import run_launcher
        run_launcher()
        return

    # ── Logging ───────────────────────────────────────────────────────────────
    # File logging is configured from config after load; console starts now.
    setup_logging(level=getattr(logging, args.log_level))
    logger = logging.getLogger("cuebridge.main")
    logger.info("OSC Switcher Bridge starting…")

    # ── Core singletons ───────────────────────────────────────────────────────
    config = ConfigManager(path=args.config)

    # Port resolution: CLI flag > config > default 8080
    if args.port is None:
        args.port = config.web_ui_port

    # Port conflict check
    if not _port_free(args.port):
        logger.error(
            "Port %d is already in use. Change the Web UI port in the OSC settings tab "
            "or pass --port <n> to use a different port.", args.port
        )
        sys.exit(1)
    manager = SwitcherManager(config)
    engine  = CueEngine(config, manager)
    osc     = OSCHandler(config, manager, engine)

    # If file logging is enabled in config, re-init logging with the file path
    if config.log_to_file:
        setup_logging(
            level=getattr(logging, args.log_level),
            log_file=config.log_file_path,
        )
        logger.info("File logging enabled → %s", config.log_file_path)

    # Holds zeroconf handle so we can close it on shutdown
    _state: dict = {"zeroconf": None}

    # ── NiceGUI startup/shutdown hooks ────────────────────────────────────────
    @app.on_startup
    async def _startup() -> None:
        logger.info("App startup: connecting switchers and starting OSC server…")
        await manager.auto_connect()
        await osc.start()

        # mDNS / Bonjour advertisement
        try:
            import socket
            from zeroconf import ServiceInfo, Zeroconf

            def _local_ip() -> str:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    try:
                        s.connect(("8.8.8.8", 80))
                        return s.getsockname()[0]
                    except Exception:
                        return "127.0.0.1"

            local_ip = _local_ip()
            zc = Zeroconf()
            info = ServiceInfo(
                "_cuebridge._tcp.local.",
                "CueBridge._cuebridge._tcp.local.",
                addresses=[socket.inet_aton(local_ip)],
                port=args.port,
                properties={"version": "1.0", "osc_port": str(config.osc_port)},
            )
            zc.register_service(info)
            _state["zeroconf"] = zc
            logger.info("mDNS: advertising CueBridge on %s:%d", local_ip, args.port)
        except Exception as exc:
            logger.warning("mDNS advertisement failed (install zeroconf to enable): %s", exc)

    @app.on_shutdown
    async def _shutdown() -> None:
        logger.info("App shutdown: disconnecting switchers and stopping OSC…")
        await osc.stop()
        await manager.disconnect_all()
        if _state["zeroconf"]:
            _state["zeroconf"].close()

    # ── Register UI pages ─────────────────────────────────────────────────────
    setup_ui(config, manager, osc, engine)

    # ── Launch ────────────────────────────────────────────────────────────────
    logger.info("Web UI available on http://0.0.0.0:%d", args.port)
    ui.run(
        title="OSC Switcher Bridge",
        favicon="🎬",
        dark=True,
        port=args.port,
        host="0.0.0.0",      # bind to all interfaces (LAN accessible)
        reload=False,         # never use reload in production
        show=not args.headless,
        uvicorn_logging_level="warning",
    )


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
