"""
logger_setup.py — Logging configuration.

Sets up the root logger for the application. Also maintains an in-memory
ring buffer of recent log entries so the UI can display them without
polling a file.
"""

import logging
import sys
from collections import deque
from datetime import datetime
from typing import Callable

# Maximum log lines kept in memory for the UI
_MAX_LINES = 500

# Global ring buffer — each entry is a dict for structured display
_log_buffer: deque[dict] = deque(maxlen=_MAX_LINES)

# Optional callbacks the UI can register to be notified of new lines
_callbacks: list[Callable[[dict], None]] = []


class _BufferHandler(logging.Handler):
    """Pushes log records into the in-memory ring buffer and calls any UI callbacks."""

    LEVEL_COLORS = {
        "DEBUG":    "text-gray-400",
        "INFO":     "text-blue-300",
        "WARNING":  "text-yellow-400",
        "ERROR":    "text-red-400",
        "CRITICAL": "text-red-600",
    }

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts":       datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "level":    record.levelname,
                "name":     record.name,
                "msg":      self.format(record),
                "color":    self.LEVEL_COLORS.get(record.levelname, "text-white"),
            }
            _log_buffer.append(entry)
            for cb in list(_callbacks):
                try:
                    cb(entry)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)


def setup_logging(level: int = logging.DEBUG, log_file: str | None = None) -> None:
    """
    Call once at startup to configure the root logger.

    Args:
        level:    Root logging level.
        log_file: Optional path to write a log file (rotated daily).
    """
    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)-30s  %(message)s",
        datefmt="%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on reload
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # In-memory buffer for the UI
    buf = _BufferHandler()
    buf.setFormatter(fmt)
    root.addHandler(buf)

    # Optional file handler
    if log_file:
        from logging.handlers import TimedRotatingFileHandler
        fh = TimedRotatingFileHandler(log_file, when="midnight", backupCount=7)
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # Silence noisy third-party loggers
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("nicegui").setLevel(logging.WARNING)
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("AtemProtocol").setLevel(logging.WARNING)
    logging.getLogger("UdpTransport").setLevel(logging.WARNING)


def register_ui_callback(cb: Callable[[dict], None]) -> None:
    """Register a function to be called each time a new log entry arrives."""
    if cb not in _callbacks:
        _callbacks.append(cb)


def unregister_ui_callback(cb: Callable[[dict], None]) -> None:
    if cb in _callbacks:
        _callbacks.remove(cb)


def get_recent_logs(n: int = _MAX_LINES) -> list[dict]:
    """Return the most recent `n` log entries (oldest first)."""
    entries = list(_log_buffer)
    return entries[-n:] if n < len(entries) else entries


def clear_log_buffer() -> None:
    _log_buffer.clear()
