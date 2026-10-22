"""Structured JSON logs to stdout, with a redaction safety net.

Caliper does not log request bodies, secret values, or model prompts. This
module makes the output machine-readable and adds a filter that replaces any
registered secret accidentally embedded in a log message or exception.
"""

from __future__ import annotations

import datetime
import json
import logging
import sys
import threading

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message",
}

_secrets: set[str] = set()
_lock = threading.Lock()
_configured = False


def register_secret(value: str | None) -> None:
    """Remember one value so it can never appear in a log line."""
    if not value or len(value) < 4:
        return
    with _lock:
        _secrets.add(value)


def redact(text: str) -> str:
    if not text:
        return text
    with _lock:
        values = sorted(_secrets, key=len, reverse=True)
    for value in values:
        text = text.replace(value, "[redacted]")
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = redact(record.getMessage())
        payload = {
            "ts": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED or key.startswith("_"):
                continue
            payload[key] = redact(str(value))
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            try:
                record.args = tuple(
                    redact(arg) if isinstance(arg, str) else arg for arg in record.args
                )
            except TypeError:
                pass
        if record.exc_info:
            record.exc_text = redact(self._format_exception(record.exc_info))
        return True

    @staticmethod
    def _format_exception(exc_info) -> str:
        import traceback

        return "".join(traceback.format_exception(*exc_info))


def configure_logging(level: str = "INFO", stream=None) -> None:
    """Install one JSON handler on the root logger. Idempotent."""
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, (level or "INFO").upper(), logging.INFO))
    # Uvicorn's own handlers are disabled by passing log_config=None; keep its
    # loggers at INFO so request lines also arrive as JSON.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.INFO)
    _configured = True
