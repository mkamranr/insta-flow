"""The mail job: idempotency, retry behaviour, and the auth-failure flag."""

import pytest

from app.config import Settings
from app.db.repositories import AppSettingsRepository, EmailLogRepository
from app.services.email.config import AUTH_ERROR_KEY, AUTH_FAILED_AT_KEY, PASSWORD_KEY
from app.services.email.mock import MockMailer
from app.services.security.encryption import encrypt
from app.workers import email_tasks, tasks

KEY = "k" * 64
PASSWORD = "sixteencharacter"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"encryption_key": KEY, "session_secret": "s"}
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


async def _configure(database) -> None:
    await AppSettingsRepository(database).save(
        {
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_username": "sender@example.com",
            PASSWORD_KEY: encrypt(PASSWORD, KEY),
            "smtp_from_address": "sender@example.com",
        }
    )


@pytest.fixture
async def ctx(database, monkeypatch):
    """A worker context whose mailer is a mock we can inspect and script."""
    mailer = MockMailer()
    monkeypatch.setattr(email_tasks, "build_mailer", lambda settings, config=None: mailer)
    return {"settings": _settings(email_client="mock"), "db": database, "mailer": mailer}


async def _open(database, message_id: str = "<one@test>") -> None:
    row = await EmailLogRepository(database).open(
        message_id=message_id, purpose="test", to_address="someone@example.com", subject="s"
    )
    assert row is not None


def test_the_retry_ladder_is_the_shared_one() -> None:
    """Not a copy. A second ladder would drift from the Meta one silently."""
    assert email_tasks.RETRY_DELAYS is tasks.RETRY_DELAYS
    assert email_tasks.MAX_ATTEMPTS == tasks.MAX_ATTEMPTS


async def test_a_successful_send_records_sent(database, ctx) -> None:
    await _configure(database)
    await _open(database)

    result = await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )

    assert result["status"] == "sent"
    row = await EmailLogRepository(database).by_message_id("<one@test>")
    assert row["status"] == "sent"
    assert row["attempts"] == 1
    assert [s.to for s in ctx["mailer"].sent] == ["someone@example.com"]


async def test_the_message_id_reaches_the_mail_server(database, ctx) -> None:
    """The row's key and the header the receiving server logs are one string."""
    await _configure(database)
    await _open(database)
    await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )
    assert ctx["mailer"].sent[0].message_id == "<one@test>"


async def test_a_transient_failure_defers_a_retry(database, ctx) -> None:
    from arq.worker import Retry

    await _configure(database)
    await _open(database)
    ctx["mailer"].fail(421)

    with pytest.raises(Retry):
        await email_tasks.send_email(
            ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
        )

    row = await EmailLogRepository(database).by_message_id("<one@test>")
    assert row["status"] == "failed"
    assert row["retryable"] is True
    assert row["smtp_code"] == 421


async def test_a_permanent_failure_does_not_retry(database, ctx) -> None:
    await _configure(database)
    await _open(database)
    ctx["mailer"].fail(550)

    result = await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )

    assert result["status"] == "failed"
    row = await EmailLogRepository(database).by_message_id("<one@test>")
    assert row["retryable"] is False
    assert len(ctx["mailer"].sent) == 0


async def test_a_redelivered_job_does_not_send_twice(database, ctx) -> None:
    """The point of minting the Message-ID before enqueuing.

    ARQ can redeliver; without the terminal-status check the recipient gets the
    message twice.
    """
    await _configure(database)
    await _open(database)

    first = await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )
    second = await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )

    assert first["status"] == "sent"
    assert second["status"] == "already_settled"
    assert len(ctx["mailer"].sent) == 1


async def test_an_unconfigured_deployment_skips_rather_than_failing(database, ctx) -> None:
    """Nothing to retry, so `skipped` rather than a burnt retry budget."""
    await _open(database)

    result = await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )

    assert result["status"] == "skipped"
    row = await EmailLogRepository(database).by_message_id("<one@test>")
    assert row["status"] == "skipped"
    assert len(ctx["mailer"].sent) == 0


async def test_an_auth_failure_flags_the_stored_credential(database, ctx) -> None:
    await _configure(database)
    await _open(database)
    ctx["mailer"].fail(535)

    result = await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )

    assert result["status"] == "failed"
    saved = await AppSettingsRepository(database).load()
    assert saved[AUTH_FAILED_AT_KEY] is not None
    assert saved[AUTH_ERROR_KEY]


async def test_a_successful_send_clears_the_auth_flag(database, ctx) -> None:
    """Gmail's answer to "is this password still good" is the next AUTH, so a
    success is what retires the warning."""
    await _configure(database)
    store = AppSettingsRepository(database)
    await store.save({AUTH_FAILED_AT_KEY: "2026-01-01", AUTH_ERROR_KEY: "refused"})
    await _open(database)

    await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )

    saved = await store.load()
    assert saved[AUTH_FAILED_AT_KEY] is None
    assert saved[AUTH_ERROR_KEY] is None


async def test_an_auth_failure_does_not_block_the_next_send(database, ctx) -> None:
    """Deliberately unlike a Meta 401, which pauses the account.

    Blocking would strand an operator who has already fixed the App Password at
    Google's end and has no way to tell us so.
    """
    await _configure(database)
    await _open(database, "<one@test>")
    ctx["mailer"].fail(535)
    await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )

    await _open(database, "<two@test>")
    result = await email_tasks.send_email(
        ctx, message_id="<two@test>", purpose="test", to_address="someone@example.com"
    )
    assert result["status"] == "sent"


async def test_the_stored_error_is_redacted(database, ctx) -> None:
    """Server text can quote what was attempted; nothing reaches the row raw."""
    from app.errors import MailError

    await _configure(database)
    await _open(database)
    ctx["mailer"].fail(MailError(f"refused: password={PASSWORD}", retryable=False))

    await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )

    row = await EmailLogRepository(database).by_message_id("<one@test>")
    assert PASSWORD not in str(row)


async def test_the_body_is_never_stored(database, ctx) -> None:
    """An invite body carries a single-use token; a copy here would outlive it
    with none of the encryption the token itself gets."""
    await _configure(database)
    await _open(database)
    await email_tasks.send_email(
        ctx, message_id="<one@test>", purpose="test", to_address="someone@example.com"
    )

    row = await EmailLogRepository(database).by_message_id("<one@test>")
    body = ctx["mailer"].sent[0].body
    assert "body" not in row
    assert body[:40] not in str(row)


async def test_claim_refuses_a_row_that_is_already_terminal(database) -> None:
    log = EmailLogRepository(database)
    await _open(database)
    await log.mark_sent("<one@test>")
    assert await log.claim("<one@test>", attempt=2) is None


async def test_opening_the_same_message_id_twice_is_refused(database) -> None:
    """`uq_message_id` is the authority, not a prior existence check."""
    log = EmailLogRepository(database)
    await _open(database)
    duplicate = await log.open(
        message_id="<one@test>", purpose="test", to_address="a@b.com", subject="s"
    )
    assert duplicate is None
