"""Structured JSON logging with mandatory secret redaction.

project-plan.md §52: access tokens, passwords, session secrets, encryption keys
and authorization codes must never reach a log sink. The filter below is the
enforcement point, so it runs on every record regardless of call site.
"""

import json
import logging
import re
import sys
from typing import Any

SERVICE = "api"

# Patterns are deliberately broad: a false-positive redaction is harmless,
# a leaked token is not.
_SECRET_PATTERNS = [
    re.compile(r"(access_token=)[^&\s\"']+", re.I),
    re.compile(r"(client_secret=)[^&\s\"']+", re.I),
    re.compile(r"(\bcode=)[^&\s\"']+", re.I),
    re.compile(
        r"(\"(?:access_token|password|token|secret|encryption_key|"
        r"session_secret|client_secret)\"\s*:\s*\")[^\"]*",
        re.I,
    ),
    re.compile(r"(Bearer\s+)\S+", re.I),
    # Meta long-lived tokens start with IG/EAA and are long.
    re.compile(r"\b(?:IG|EAA)[A-Za-z0-9_\-]{20,}"),
]

_SENSITIVE_KEYS = {
    "access_token",
    "access_token_encrypted",
    "password",
    "password_hash",
    "token",
    "secret",
    "client_secret",
    "encryption_key",
    "session_secret",
    "meta_app_secret",
    "code",
    "authorization",
}

_LOG_RECORD_BUILTINS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "service",
    "taskName",
}


def redact(value: Any) -> Any:
    """Recursively strip secrets from a value before it is serialised."""
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            replacement = "[REDACTED]" if pattern.groups == 0 else r"\1[REDACTED]"
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if k.lower() in _SENSITIVE_KEYS else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "service": getattr(record, "service", SERVICE),
            "event": record.getMessage(),
            "logger": record.name,
        }
        # Anything attached via logger.info("...", extra={...}) is merged in.
        for key, value in record.__dict__.items():
            if key not in _LOG_RECORD_BUILTINS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), default=str)


def configure_logging(level: str = "INFO", service: str = "api") -> None:
    global SERVICE
    SERVICE = service

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
