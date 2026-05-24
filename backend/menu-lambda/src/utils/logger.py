"""
Structured JSON logger.
Emits one JSON line per log record — compatible with CloudWatch Logs Insights.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any


_LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


class _JsonFormatter(logging.Formatter):
    """Format log records as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge any extra kwargs passed via logger.info("msg", extra={...})
        for key, value in record.__dict__.items():
            if key not in (
                "args", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "message",
                "module", "msecs", "msg", "name", "pathname", "process",
                "processName", "relativeCreated", "stack_info", "thread",
                "threadName",
            ):
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger with JSON formatting."""
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    logger.setLevel(_LOG_LEVEL)
    return logger