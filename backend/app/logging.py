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
    # A bare `password=value` in free text, which the quoted-JSON pattern below
    # does not reach. Mail servers can echo the AUTH attempt back in a rejection
    # message, and that message is stored on the `email_log` row -- so without
    # this the credential would be redacted in the settings response and left in
    # plain sight in the failure it caused. No `\b` before "password", so
    # `smtp_password=` is covered too.
    re.compile(r"(password=)[^&\s\"']+", re.I),
    re.compile(r"(\bcode=)[^&\s\"']+", re.I),
    # The webhook verification handshake carries the verify token in the query
    # string, and Meta sends it under BOTH spellings in the same request --
    # `hub.verify_token` and `hub_verify_token`. Redacting only the documented
    # dotted form leaves the value in the log twice over.
    re.compile(r"(hub[._]verify_token=)[^&\s\"']+", re.I),
    # Any `token=` in a URL, which is broader than the two hub spellings above
    # and covers the invite activation link. Needed because ARQ logs a job's
    # arguments verbatim -- verified live: the worker prints
    # `send_email(context={...}, ...)` at INFO -- so an invitation's URL would
    # otherwise reach the container log even though the token is only ever
    # stored as a hash.
    re.compile(r"(\btoken=)[^&\s\"']+", re.I),
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
    # Per-workspace Meta app credentials (`db/repositories/meta_apps.py`). Both
    # the stored ciphertext and the decrypted forms, because the resolved
    # `MetaCredentials` carries plaintext through the request.
    "app_secret",
    "app_secret_encrypted",
    "verify_token",
    "verify_token_encrypted",
    # Outbound mail (`services/email/config.py`). Matching here is EXACT, not by
    # substring -- `k.lower() in _SENSITIVE_KEYS` below -- so the plain
    # "password" entry above does not cover these two spellings.
    "smtp_password",
    "smtp_password_encrypted",
    # Invite activation tokens. `token` already covers the bare key; these are
    # the two names the invite code actually uses, spelled out because of the
    # same exact-match behaviour.
    "token_hash",
    "invite_token",
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
