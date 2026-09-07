"""The mail contract, and the part of failure classification that is pure policy.

Nothing here imports `aiosmtplib` or performs I/O. `transport.py` is the only
module that names the library's exception types, because translating them is the
job of the module that speaks the protocol; what lives here is the decision that
is worth reading on its own -- **which SMTP replies are worth retrying**.

That decision inverts the one next door in `services/meta/client.py`, where
`error_for_status` sets `retryable = status >= 500` because HTTP 5xx means "the
server broke, come back". SMTP means the opposite (RFC 5321 §4.2.1): a 4xx reply
is a transient negative -- mailbox busy, greylisted, out of space right now --
and a 5xx reply is permanent. Retrying a 550 cannot succeed, and on Gmail it
also feeds a sender-reputation problem.

Anyone reading the two functions side by side will want to harmonise them. Do
not.
"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.errors import MailError

# Gmail's rejection for a wrong or revoked App Password is `535-5.7.8 Username
# and Password not accepted`. 534 and 538 are the neighbouring auth failures.
AUTH_CODES: frozenset[int] = frozenset({530, 534, 535, 538})


@dataclass(frozen=True)
class MailMessage:
    """One message, already rendered.

    `message_id` is an RFC 5322 Message-ID minted by the caller before the send
    and used as the idempotency key for the whole attempt, so the row in
    `email_log` is keyed on the same string the receiving mail server records.
    """

    to: str
    subject: str
    body: str
    message_id: str
    reply_to: str | None = None


@dataclass(frozen=True)
class MailResult:
    success: bool
    message_id: str
    response: str = ""
    refused: tuple[str, ...] = ()


@runtime_checkable
class MailerProtocol(Protocol):
    """What everything outside this package depends on.

    The config is passed **per call** rather than bound at construction, which is
    the one structural difference from `InstagramClient`. Two reasons: the
    credentials live in the database and can change while the worker process
    runs, whereas the ARQ context is built once at startup; and the decrypted
    password then exists for the duration of one send instead of the lifetime of
    the process.
    """

    async def send(self, message: MailMessage, config: object) -> MailResult: ...


def error_for_code(code: int, text: str = "") -> MailError:
    """Classify an SMTP reply code. See the module docstring for the inversion."""
    detail = text.strip() or f"SMTP {code}"

    if code in AUTH_CODES:
        return MailError(
            "The mail server refused the username and password.",
            smtp_code=code,
            retryable=False,
            auth_refused=True,
        )
    if 400 <= code < 500:
        return MailError(detail, smtp_code=code, retryable=True)
    return MailError(detail, smtp_code=code, retryable=False)
