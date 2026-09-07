"""The real SMTP transport. **The only module in the repo that imports aiosmtplib.**

`tests/test_email_boundary.py` enforces that sentence, because the working notes
already carry one boundary claim ("enforced by grep in the test run") for which
no test was ever written. A convention nothing checks is a convention that has
already drifted somewhere.

Translating the library's exceptions also lives here, rather than in
`client.py`: knowing what an `SMTPRecipientsRefused` is belongs to the module
that speaks the protocol. What `client.py` keeps is the part worth reading and
testing on its own -- `error_for_code`, which decides that SMTP 4xx is retryable
and 5xx is not.
"""

import logging
import ssl
from email.message import EmailMessage
from typing import Any

import aiosmtplib
from aiosmtplib import errors as smtp_errors

from app.errors import MailError
from app.services.email.client import MailMessage, MailResult, error_for_code
from app.services.email.config import MailConfig

logger = logging.getLogger(__name__)


def _build(message: MailMessage, config: MailConfig) -> EmailMessage:
    payload = EmailMessage()
    payload["From"] = config.sender
    payload["To"] = message.to
    payload["Subject"] = message.subject
    payload["Message-ID"] = message.message_id
    if message.reply_to:
        payload["Reply-To"] = message.reply_to
    payload["Auto-Submitted"] = "auto-generated"
    payload.set_content(message.body)
    return payload


def translate(exc: BaseException) -> MailError:
    """Turn anything the library or the socket can raise into one `MailError`.

    Ordered most specific first. `SMTPAuthenticationError` must precede
    `SMTPResponseException` -- it is a subclass, and its 535 would otherwise be
    classified as an ordinary permanent failure and lose the flag the Settings
    card needs.
    """
    if isinstance(exc, smtp_errors.SMTPAuthenticationError):
        return error_for_code(getattr(exc, "code", 535) or 535, str(exc))

    if isinstance(exc, smtp_errors.SMTPRecipientsRefused):
        refused = tuple(str(r) for r in getattr(exc, "recipients", ()) or ())
        return MailError(
            "The mail server refused the recipient address.",
            smtp_code=550,
            retryable=False,
            refused=refused,
        )

    if isinstance(
        exc, smtp_errors.SMTPRecipientRefused | smtp_errors.SMTPSenderRefused
    ):
        code = getattr(exc, "code", None)
        # A refused envelope is permanent even when the server dressed it as a
        # 4xx: the address is wrong, and waiting does not change that.
        return MailError(str(exc), smtp_code=code, retryable=False)

    if isinstance(exc, smtp_errors.SMTPResponseException):
        return error_for_code(exc.code, str(getattr(exc, "message", "") or exc))

    if isinstance(exc, smtp_errors.SMTPNotSupported):
        # STARTTLS was required and not advertised, or similar. A retry cannot
        # fix a port or a server's capability list.
        return MailError(str(exc), retryable=False)

    if isinstance(
        exc,
        smtp_errors.SMTPTimeoutError
        | smtp_errors.SMTPConnectError
        | smtp_errors.SMTPServerDisconnected,
    ):
        return MailError(str(exc) or "The mail server did not respond.", retryable=True)

    if isinstance(exc, ssl.SSLError | OSError):
        return MailError(str(exc) or "Could not reach the mail server.", retryable=True)

    if isinstance(exc, smtp_errors.SMTPException):
        # Unknown member of the library's taxonomy -- most likely a new one from
        # an upgrade. Retryable is the safer default for something transport
        # shaped, and `test_every_aiosmtplib_exception_is_classified` exists so
        # a new type shows up as a failing test rather than as this branch.
        logger.warning("smtp_error_unclassified", extra={"error_type": type(exc).__name__})
        return MailError(str(exc), retryable=True)

    raise exc


class SmtpMailer:
    """Sends one message per call. Holds no connection and no credentials."""

    async def send(self, message: MailMessage, config: Any) -> MailResult:
        if not config.configured:
            raise MailError("Mail is not configured.", retryable=False)

        try:
            responses, response_text = await aiosmtplib.send(
                _build(message, config),
                hostname=config.host,
                port=config.port,
                username=config.username or None,
                password=config.password or None,
                timeout=config.timeout_seconds,
                # Explicit rather than left to the library's default of None,
                # which means "upgrade if the server advertises STARTTLS". A
                # server that quietly stops advertising it would then get the
                # credentials in clear; this way the send fails loudly instead.
                start_tls=config.starttls,
                use_tls=config.use_tls,
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised by `translate`
            raise translate(exc) from exc

        # `send` returns (errors, text), where `errors` maps each REFUSED
        # recipient to its reply. It is empty whenever the send fully
        # succeeded, and since every message here has exactly one recipient a
        # total refusal arrives as SMTPRecipientsRefused instead -- so in
        # practice this is always empty. Carried through anyway rather than
        # discarded, because silently dropping a partial refusal is how "it
        # said sent" and "nobody got it" become the same log line.
        return MailResult(
            success=True,
            message_id=message.message_id,
            response=str(response_text or "")[:200],
            refused=tuple((responses or {}).keys()),
        )
