"""Which mail implementation is selected, and what dry run does to mail.

The DRY_RUN behaviour here was decided deliberately and against the instinct, so
it is pinned rather than left to be re-derived: `DRY_RUN` means "no Meta
mutation", and an invitation is not one. Suppressing mail would mean invitations
silently never arrive on a default install -- the default is `true` -- sending
whoever debugs it into the tunnel or the mail server rather than a flag.
"""

import pytest

from app.config import Settings
from app.errors import MailError
from app.services.email.client import MailerProtocol
from app.services.email.config import MailConfig
from app.services.email.factory import build_mailer
from app.services.email.mock import MockMailer
from app.services.email.transport import SmtpMailer

CONFIGURED = MailConfig(
    host="smtp.gmail.com",
    username="someone@example.com",
    password="app-password",
    from_address="someone@example.com",
)


def settings(**overrides: object) -> Settings:
    """Explicit blanks, so the result never depends on the ambient .env."""
    base: dict[str, object] = {"encryption_key": "k" * 64, "session_secret": "s"}
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_email_client_defaults_to_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_CLIENT", raising=False)
    assert settings().email_client == "auto"


def test_dry_run_does_not_suppress_mail() -> None:
    """The decision this whole feature rests on.

    With dry run on -- the default -- a configured deployment still selects the
    real transport. If this test ever flips, invitations stop arriving on every
    default install and nothing on screen says why.
    """
    chosen = build_mailer(settings(dry_run=True), CONFIGURED)
    assert isinstance(chosen, SmtpMailer)


def test_an_unconfigured_deployment_gets_the_mock() -> None:
    """Failing visibly in the dashboard beats auth errors from inside the worker."""
    assert isinstance(build_mailer(settings(), MailConfig()), MockMailer)


def test_no_config_at_all_gets_the_mock() -> None:
    assert isinstance(build_mailer(settings(), None), MockMailer)


def test_email_client_mock_overrides_a_configured_deployment() -> None:
    assert isinstance(build_mailer(settings(email_client="mock"), CONFIGURED), MockMailer)


def test_email_client_real_overrides_an_unconfigured_one() -> None:
    assert isinstance(build_mailer(settings(email_client="real"), MailConfig()), SmtpMailer)


def test_both_implementations_satisfy_the_protocol() -> None:
    assert isinstance(MockMailer(), MailerProtocol)
    assert isinstance(SmtpMailer(), MailerProtocol)


async def test_the_mock_refuses_an_unconfigured_send() -> None:
    """A mock that accepts anything lets a not-configured bug reach production."""
    from app.services.email.client import MailMessage

    message = MailMessage(to="a@b.com", subject="hi", body="hello", message_id="<1@x>")
    with pytest.raises(MailError):
        await MockMailer().send(message, MailConfig())


@pytest.mark.parametrize("recipient", ["", "not-an-address", "   "])
async def test_the_mock_refuses_a_malformed_recipient(recipient: str) -> None:
    from app.services.email.client import MailMessage

    message = MailMessage(to=recipient, subject="hi", body="hello", message_id="<1@x>")
    with pytest.raises(MailError):
        await MockMailer().send(message, CONFIGURED)


async def test_the_mock_records_what_was_sent() -> None:
    from app.services.email.client import MailMessage

    mailer = MockMailer()
    message = MailMessage(to="a@b.com", subject="hi", body="hello", message_id="<1@x>")
    result = await mailer.send(message, CONFIGURED)

    assert result.success is True
    assert [(s.to, s.subject) for s in mailer.sent] == [("a@b.com", "hi")]


async def test_the_mock_can_be_scripted_to_fail_then_succeed() -> None:
    """The behaviour monkeypatching cannot express, and the reason this is a
    mock rather than a patch."""
    from app.services.email.client import MailMessage

    mailer = MockMailer()
    mailer.fail(421, times=2)
    message = MailMessage(to="a@b.com", subject="hi", body="hello", message_id="<1@x>")

    for _ in range(2):
        with pytest.raises(MailError) as caught:
            await mailer.send(message, CONFIGURED)
        assert caught.value.retryable is True

    assert (await mailer.send(message, CONFIGURED)).success is True
    assert len(mailer.sent) == 1


def test_the_sender_uses_the_display_name_when_there_is_one() -> None:
    assert CONFIGURED.sender == "someone@example.com"
    named = MailConfig(from_address="a@b.com", from_name="Social Flow")
    assert named.sender == "Social Flow <a@b.com>"


def test_a_config_without_a_password_is_not_configured() -> None:
    """`configured` gates the send, so a half-filled form must not pass it."""
    assert MailConfig(host="smtp.gmail.com", from_address="a@b.com").configured is False
