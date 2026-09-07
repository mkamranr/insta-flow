"""Sending mail off the request path.

A separate module from `tasks.py` on purpose: that file is the Meta cold path
and is already 1700 lines. The import direction is one-way -- `email_tasks`
imports the retry ladder from `tasks`, never the reverse -- so nothing in the
comment pipeline gains a mail dependency. There is deliberately no second ladder;
`test_email_worker.py` asserts the two are the same object.
"""

import logging
from typing import Any

from arq.worker import Retry

from app.db.repositories import AppSettingsRepository, EmailLogRepository
from app.errors import MailError
from app.logging import redact
from app.services.email import MailMessage, render_template, resolve
from app.services.email.config import AUTH_ERROR_KEY, AUTH_FAILED_AT_KEY
from app.services.email.factory import build_mailer
from app.workers.tasks import MAX_ATTEMPTS, RETRY_DELAYS, retry_delay

logger = logging.getLogger(__name__)

__all__ = ["MAX_ATTEMPTS", "RETRY_DELAYS", "send_email"]


async def _note_auth_failure(store: AppSettingsRepository, error: MailError) -> None:
    """Record a rejected credential so the Settings card can explain itself.

    Deliberately does NOT block later sends, which is where this diverges from
    the Meta equivalent: a 401 from Meta renders the stored token unusable and
    pauses the account, but Gmail's own answer to "is this App Password still
    good" is the next AUTH. Blocking would strand an operator who has already
    fixed it at Google's end and cannot tell us so. Surfaced loudly, not
    enforced -- please do not "fix" this into a block.
    """
    from app.db.repositories.base import utcnow

    await store.save({AUTH_FAILED_AT_KEY: utcnow(), AUTH_ERROR_KEY: str(redact(error.message))})


async def send_email(
    ctx: dict[str, Any],
    *,
    message_id: str,
    purpose: str,
    to_address: str,
    context: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send one message, identified end to end by its RFC 5322 Message-ID.

    Keyword-only so a later field cannot silently break positional order in an
    already-queued job.

    **Idempotency.** The caller opens the `email_log` row and mints the
    Message-ID *before* enqueuing, so a redelivered job finds the row already
    terminal and returns without sending again. That differs from
    `ExecutionsRepository.reserve`, which claims inside the worker, and the
    reason is worth keeping: a comment event arrives with a natural unique key
    from Meta, so the webhook can double-enqueue freely. An email has none --
    two clicks of "Send test" are two legitimate sends -- so the key has to be
    minted where the intent is formed.

    **What this does not guarantee.** A crash between the mail server accepting
    the message and `mark_sent` returning leaves the row at `sending`, and a
    retry then sends a second copy. Mongo is not part of the SMTP transaction
    and cannot be. This is the same trade `executions` makes, and it is better
    than marking sent first and losing the record of every failure -- but it is
    at-least-once, not exactly-once.
    """
    settings = ctx["settings"]
    log = EmailLogRepository(ctx["db"])
    store = AppSettingsRepository(ctx["db"])
    attempt = int(ctx.get("job_try", 1))

    row = await log.claim(message_id, attempt=attempt)
    if row is None:
        # Already sent, already failed for good, or already skipped. A
        # redelivery, and the whole reason the key is minted up front.
        logger.info("email_already_settled", extra={"purpose": purpose})
        return {"status": "already_settled", "message_id": message_id}

    config = resolve(settings, await store.load())
    if not config.configured:
        await log.mark_terminal(message_id, "skipped", error="Mail is not configured.")
        logger.warning("email_skipped_not_configured", extra={"purpose": purpose})
        return {"status": "skipped", "message_id": message_id}

    try:
        subject, body = render_template(purpose, dict(context or {}))
        mailer = build_mailer(settings, config)
        result = await mailer.send(
            MailMessage(to=to_address, subject=subject, body=body, message_id=message_id),
            config,
        )
    except MailError as exc:
        message = str(redact(exc.message))
        if exc.auth_refused:
            await _note_auth_failure(store, exc)

        if exc.retryable and attempt < MAX_ATTEMPTS:
            await log.mark_failed(
                message_id, error=message, smtp_code=exc.smtp_code, retryable=True
            )
            logger.warning(
                "email_send_retrying",
                extra={"purpose": purpose, "attempt": attempt, "smtp_code": exc.smtp_code},
            )
            raise Retry(defer=retry_delay(attempt + 1)) from exc

        await log.mark_failed(
            message_id, error=message, smtp_code=exc.smtp_code, retryable=exc.retryable
        )
        logger.error(
            "email_send_failed",
            extra={"purpose": purpose, "attempt": attempt, "smtp_code": exc.smtp_code},
        )
        return {"status": "failed", "message_id": message_id}

    await log.mark_sent(message_id, response=result.response)
    # A send proves the credential, so any earlier rejection is stale.
    if config.auth_refused_at or config.auth_error:
        await store.save({AUTH_FAILED_AT_KEY: None, AUTH_ERROR_KEY: None})

    logger.info("email_sent", extra={"purpose": purpose, "attempt": attempt})
    return {"status": "sent", "message_id": message_id}
