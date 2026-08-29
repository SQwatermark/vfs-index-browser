"""Console logging configuration for the local VFS service."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import TextIO


LOGGER_NAME = "vfs_browser"
LOGGER = logging.getLogger(LOGGER_NAME)

_STANDARD_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for name, value in record.__dict__.items():
            if name not in _STANDARD_RECORD_FIELDS and name != "message":
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def configure_service_logging(
    level: str,
    output_format: str,
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    logger = LOGGER
    for previous in logger.handlers[:]:
        logger.removeHandler(previous)
        previous.close()
    logger.setLevel(level.upper())
    logger.propagate = False

    handler = logging.StreamHandler(stream or sys.stderr)
    if output_format == "json":
        handler.setFormatter(JsonLogFormatter())
    elif output_format == "text":
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    else:
        raise ValueError(f"unsupported log format: {output_format}")
    logger.addHandler(handler)
    return logger
