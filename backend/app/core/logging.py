"""Logging setup with credential redaction.

Every log record is passed through a redaction filter so that RTSP passwords,
bearer tokens and API keys can never end up in log files.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from app.core.config import settings

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(rtsps?|rtmp|http|https)://([^:/@\s]+):([^@/\s]+)@", re.I), r"\1://\2:***@"),
    (re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*([^\s&,;\"']+)"), r"\1=***"),
    (re.compile(r"(?i)(authorization:\s*bearer\s+)[A-Za-z0-9._\-]+"), r"\1***"),
]


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed record
            return True
        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)-28s %(message)s")
        )
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())

    # uvicorn access logs include the query string; keep them but redacted.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = [handler]
        lg.propagate = False

    logging.getLogger("ultralytics").setLevel(logging.WARNING)
