"""How a failed send is classified. Pure policy: no network, no database.

The single most valuable file in the email work, because the rule it pins is
counter-intuitive and sits three lines away from a function that does the
opposite. `services/meta/client.error_for_status` sets `retryable = status >=
500` for HTTP. SMTP inverts that (RFC 5321 §4.2.1): 4xx is transient, 5xx is
permanent. Somebody will try to harmonise the two; these tests are what stops
that landing quietly.
"""

import ssl

import pytest
from aiosmtplib import errors as smtp_errors

from app.errors import MailError
from app.services.email.client import error_for_code
from app.services.email.transport import translate


@pytest.mark.parametrize("code", [421, 450, 451, 452, 454])
def test_a_4xx_smtp_reply_is_retryable(code: int) -> None:
    """Transient by definition -- greylisted, mailbox busy, out of space now.

    The inverse of HTTP, which is the whole point of this test existing.
    """
    assert error_for_code(code, "try later").retryable is True


@pytest.mark.parametrize("code", [550, 552, 553, 554])
def test_a_5xx_smtp_reply_is_permanent(code: int) -> None:
    """Retrying a bad recipient cannot succeed, and on Gmail it also feeds a
    sender-reputation problem."""
    assert error_for_code(code, "no such user").retryable is False


@pytest.mark.parametrize("code", [530, 534, 535, 538])
def test_an_authentication_failure_is_never_retried_and_is_flagged(code: int) -> None:
    """Gmail counts repeated auth failures against the account, so a retry is
    worse than useless. The flag is what puts the reason on the Settings card."""
    error = error_for_code(code, "535-5.7.8 Username and Password not accepted")
    assert error.retryable is False
    assert error.auth_refused is True


def test_the_auth_message_does_not_echo_the_server_text() -> None:
    """The server's reply can quote what was attempted; the operator-facing
    message is written by us instead."""
    error = error_for_code(535, "535-5.7.8 Username and Password not accepted hunter2")
    assert "hunter2" not in error.message


@pytest.mark.parametrize(
    "exc",
    [
        smtp_errors.SMTPConnectError("refused"),
        smtp_errors.SMTPServerDisconnected("gone"),
        smtp_errors.SMTPTimeoutError("slow"),
        smtp_errors.SMTPConnectTimeoutError("slow connect"),
        smtp_errors.SMTPReadTimeoutError("slow read"),
        ssl.SSLError("handshake"),
        OSError("network down"),
    ],
)
def test_a_transport_failure_is_retryable(exc: BaseException) -> None:
    """Never reached the server, or it dropped mid-conversation."""
    assert translate(exc).retryable is True


def test_a_refused_recipient_is_never_retried() -> None:
    error = translate(smtp_errors.SMTPRecipientsRefused([]))
    assert error.retryable is False
    assert error.smtp_code == 550


def test_a_refused_sender_is_never_retried() -> None:
    """Gmail refuses a From it does not own, and waiting will not change that."""
    assert translate(smtp_errors.SMTPSenderRefused(553, "not owned", "a@b.com")).retryable is False


def test_starttls_unsupported_is_not_retried() -> None:
    """A retry cannot fix a port or a server's capability list."""
    assert translate(smtp_errors.SMTPNotSupported("no STARTTLS")).retryable is False


def test_an_auth_error_is_classified_before_its_parent() -> None:
    """SMTPAuthenticationError subclasses SMTPResponseException.

    Checked in the wrong order its 535 reads as an ordinary permanent failure
    and loses the flag the Settings card needs, which is a silent downgrade
    rather than a crash -- so it gets its own test.
    """
    error = translate(smtp_errors.SMTPAuthenticationError(535, "nope"))
    assert error.auth_refused is True


def test_a_refused_recipient_is_classified_before_its_parent() -> None:
    """SMTPRecipientRefused also subclasses SMTPResponseException. A server that
    dresses a refusal as 4xx must not win a retry: the address is still wrong."""
    assert translate(smtp_errors.SMTPRecipientRefused(450, "bad box", "a@b.com")).retryable is False


def _sample(cls: type) -> BaseException:
    """Construct any member of the taxonomy with the arguments it demands."""
    if issubclass(cls, smtp_errors.SMTPRecipientsRefused):
        return cls([])
    if issubclass(cls, smtp_errors.SMTPSenderRefused | smtp_errors.SMTPRecipientRefused):
        return cls(550, "refused", "someone@example.com")
    if issubclass(cls, smtp_errors.SMTPResponseException):
        return cls(451, "try later")
    return cls("boom")


def test_every_aiosmtplib_exception_is_classified() -> None:
    """A library upgrade that adds an exception type must fail HERE.

    Otherwise the new type surfaces as an unclassified 500 from inside the
    worker, on a path nobody is watching, months later.
    """
    taxonomy = [
        value
        for name, value in vars(smtp_errors).items()
        if isinstance(value, type)
        and issubclass(value, smtp_errors.SMTPException)
        and name.startswith("SMTP")
    ]
    assert taxonomy, "found no exception classes -- the scan itself is broken"

    for cls in taxonomy:
        result = translate(_sample(cls))
        assert isinstance(result, MailError), f"{cls.__name__} was not classified"


def test_an_unrelated_exception_is_re_raised_rather_than_swallowed() -> None:
    """Classifying a KeyError as a mail failure would hide a real bug behind a
    retry ladder."""
    with pytest.raises(KeyError):
        translate(KeyError("not a mail problem"))
