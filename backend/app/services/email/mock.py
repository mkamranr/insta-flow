"""A scriptable stand-in for the SMTP transport.

Chosen over monkeypatching `aiosmtplib.send` for two concrete reasons. First,
patching a module attribute the code under test also imports hits the
re-entrancy trap `tests/test_geoip.py` already documents, where the replacement
re-enters the name it just replaced. Second, and decisively, a patch cannot
express "fail twice with 421, then succeed" -- which is the only interesting
thing to say about a retry ladder.

Like `MockInstagramClient`, it enforces the real constraints rather than
accepting everything. A mock that always succeeds lets a malformed envelope or
an unconfigured deployment pass every test in the suite.
"""

from dataclasses import dataclass, field
from typing import Any

from app.errors import MailError
from app.services.email.client import MailMessage, MailResult, error_for_code
from app.services.email.templates import EMAIL_MAX_LENGTH

# The failures worth simulating: transient, permanent, and a rejected password.
SIMULATED_CODES = (421, 450, 451, 452, 535, 550, 552, 554)


class SimulatedMailTimeout(MailError):
    """The server did not answer in time. Retryable."""

    def __init__(self, message: str = "The mail server timed out.") -> None:
        super().__init__(message, retryable=True)


class SimulatedMailNetworkError(MailError):
    """The connection never reached the server. Retryable."""

    def __init__(self, message: str = "Could not reach the mail server.") -> None:
        super().__init__(message, retryable=True)


@dataclass
class SentMail:
    to: str
    subject: str
    body: str
    message_id: str
    sender: str


@dataclass
class MockMailer:
    sent: list[SentMail] = field(default_factory=list)
    _failures: list[MailError] = field(default_factory=list)

    def fail(self, error: MailError | int, times: int = 1) -> None:
        """Queue failures, consumed one per send.

        An int is shorthand for an SMTP reply code, so a test reads
        `mailer.fail(421)` rather than constructing the error by hand -- the
        same shorthand `MockInstagramClient.fail` takes for HTTP statuses.
        """
        resolved = error_for_code(error) if isinstance(error, int) else error
        self._failures.extend([resolved] * times)

    async def send(self, message: MailMessage, config: Any) -> MailResult:
        if self._failures:
            raise self._failures.pop(0)

        # Enforced rather than assumed: each of these is a real bug that a
        # permissive mock would let through to production.
        if not config.configured:
            raise MailError("Mail is not configured.", retryable=False)
        if not message.to or "@" not in message.to:
            raise MailError(f"Not a usable recipient: {message.to!r}", retryable=False)
        if not message.subject.strip():
            raise MailError("Refusing to send a message with no subject.", retryable=False)
        if len(message.body) > EMAIL_MAX_LENGTH:
            raise MailError(
                f"Body is {len(message.body)} characters; the limit is {EMAIL_MAX_LENGTH}.",
                retryable=False,
            )

        self.sent.append(
            SentMail(
                to=message.to,
                subject=message.subject,
                body=message.body,
                message_id=message.message_id,
                sender=config.sender,
            )
        )
        return MailResult(success=True, message_id=message.message_id, response="250 OK (mock)")
