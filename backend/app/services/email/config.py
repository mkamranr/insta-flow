"""Where the mail settings come from, and the only place the password is decrypted.

Modelled on `services/meta/credentials.py`, which carries the same warning: this
is the single decryption point, so a leak anywhere else is a leak this module
did not cause.

Everything is stored in the `app_settings` singleton rather than the
environment, because the operator edits it from the dashboard. Nothing about
mail belongs in `Settings.missing_required()` -- that list drives `/ready`, and a
deployment that never wanted email must not report itself unhealthy.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from app.services.security.encryption import EncryptionError, decrypt

logger = logging.getLogger(__name__)

HOST_KEY: Final = "smtp_host"
PORT_KEY: Final = "smtp_port"
SECURITY_KEY: Final = "smtp_security"
USERNAME_KEY: Final = "smtp_username"
# The name of a settings key, not a credential -- the value it points at is
# ciphertext and is decrypted in exactly one function below.
PASSWORD_KEY: Final = "smtp_password_encrypted"  # noqa: S105
FROM_ADDRESS_KEY: Final = "smtp_from_address"
FROM_NAME_KEY: Final = "smtp_from_name"
AUTH_FAILED_AT_KEY: Final = "smtp_auth_failed_at"
AUTH_ERROR_KEY: Final = "smtp_auth_error"

SETTINGS_KEYS: Final[tuple[str, ...]] = (
    HOST_KEY,
    PORT_KEY,
    SECURITY_KEY,
    USERNAME_KEY,
    PASSWORD_KEY,
    FROM_ADDRESS_KEY,
    FROM_NAME_KEY,
    AUTH_FAILED_AT_KEY,
    AUTH_ERROR_KEY,
)

DEFAULT_PORT: Final = 587
DEFAULT_SECURITY: Final = "starttls"
SECURITY_CHOICES: Final[tuple[str, ...]] = ("starttls", "ssl", "none")

# Well below `WorkerSettings.job_timeout`, which is 60 and class-wide. If the
# SMTP conversation could outlive the job, ARQ would cancel it with no
# classification and no status transition, leaving the `email_log` row stuck at
# `sending` with no error -- the one failure mode that looks like nothing at all.
TIMEOUT_SECONDS: Final = 20.0


@dataclass(frozen=True)
class MailConfig:
    host: str = ""
    port: int = DEFAULT_PORT
    security: str = DEFAULT_SECURITY
    username: str = ""
    password: str = ""
    from_address: str = ""
    from_name: str = ""
    timeout_seconds: float = TIMEOUT_SECONDS
    password_undecryptable: bool = False
    auth_refused_at: datetime | None = None
    auth_error: str = ""

    @property
    def configured(self) -> bool:
        """Enough to attempt a send. A missing password is not enough."""
        return bool(self.host and self.from_address and self.password)

    @property
    def starttls(self) -> bool:
        return self.security == "starttls"

    @property
    def use_tls(self) -> bool:
        return self.security == "ssl"

    @property
    def sender(self) -> str:
        """The `From` header. Gmail rewrites or rejects an unrelated address."""
        if self.from_name:
            return f"{self.from_name} <{self.from_address}>"
        return self.from_address


def _password(saved: dict[str, Any], secret: str) -> tuple[str, bool]:
    """Decrypt the stored password, or report that it cannot be read.

    Returns `("", True)` rather than raising, exactly as `credentials._secret`
    does. An unreadable password is an operator-visible state -- "re-enter it,
    this happens when ENCRYPTION_KEY changes" -- not a 500 on a retry path that
    nobody is watching.
    """
    blob = saved.get(PASSWORD_KEY)
    if not blob:
        return "", False
    try:
        return decrypt(str(blob), secret), False
    except EncryptionError:
        logger.error("smtp_password_undecryptable")
        return "", True


def resolve(settings: Any, saved: dict[str, Any] | None) -> MailConfig:
    saved = saved or {}
    password, undecryptable = _password(saved, settings.encryption_key)
    security = str(saved.get(SECURITY_KEY) or DEFAULT_SECURITY)
    if security not in SECURITY_CHOICES:
        security = DEFAULT_SECURITY
    return MailConfig(
        host=str(saved.get(HOST_KEY) or ""),
        port=int(saved.get(PORT_KEY) or DEFAULT_PORT),
        security=security,
        username=str(saved.get(USERNAME_KEY) or ""),
        password=password,
        from_address=str(saved.get(FROM_ADDRESS_KEY) or ""),
        from_name=str(saved.get(FROM_NAME_KEY) or ""),
        password_undecryptable=undecryptable,
        auth_refused_at=saved.get(AUTH_FAILED_AT_KEY),
        auth_error=str(saved.get(AUTH_ERROR_KEY) or ""),
    )


def status(saved: dict[str, Any] | None, *, dry_run: bool) -> dict[str, Any]:
    """What the Settings card may know.

    Built here rather than in the route so that no route can reach the whole
    `app_settings` document and spread it into a response. Host, port, username
    and sender come back because they are identifiers, not secrets, and the form
    is unusable without them -- the same line `geoip_status` draws when it
    returns the MaxMind account ID but never the license key. The password never
    comes back, and neither does its ciphertext.
    """
    saved = saved or {}
    return {
        "configured": bool(saved.get(HOST_KEY) and saved.get(PASSWORD_KEY)),
        "host": saved.get(HOST_KEY) or "",
        "port": int(saved.get(PORT_KEY) or DEFAULT_PORT),
        "security": saved.get(SECURITY_KEY) or DEFAULT_SECURITY,
        "username": saved.get(USERNAME_KEY) or "",
        "from_address": saved.get(FROM_ADDRESS_KEY) or "",
        "from_name": saved.get(FROM_NAME_KEY) or "",
        "password_set": bool(saved.get(PASSWORD_KEY)),
        "auth_refused_at": saved.get(AUTH_FAILED_AT_KEY),
        "auth_error": saved.get(AUTH_ERROR_KEY) or "",
        # Surfaced so the card can say plainly that mail is not gated by dry
        # run. The alternative -- letting the operator assume it is -- is how a
        # missing invitation becomes an hour spent in the wrong subsystem.
        "dry_run": dry_run,
    }
