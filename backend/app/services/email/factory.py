"""Chooses the mail implementation."""

import logging

from app.config import Settings
from app.services.email.client import MailerProtocol
from app.services.email.config import MailConfig
from app.services.email.mock import MockMailer
from app.services.email.transport import SmtpMailer

logger = logging.getLogger(__name__)


def build_mailer(settings: Settings, config: MailConfig | None = None) -> MailerProtocol:
    """Real SMTP when the mail settings are configured; mock otherwise.

    Shaped exactly like `services/meta/factory.build_client`, and independent of
    `DRY_RUN` for a related but not identical reason.

    For Meta, dry run is enforced by the policy engine rather than by swapping
    the client, because a Meta send has gates -- reply window, cooldown, rate
    limit -- that must still be evaluated and recorded when nothing is sent, so
    the execution row explains itself months later. Mail has no such ladder.

    For mail, dry run does not apply at all: `DRY_RUN` means "no Meta mutation",
    and an invitation is not one. Suppressing it would mean invitations silently
    never arrive on a default install, which sends the operator debugging the
    tunnel or the mail server instead of a flag. The Settings card says so on
    screen and `tests/test_email_client.py` pins it.
    """
    choice = settings.email_client
    if choice == "auto":
        choice = "real" if (config is not None and config.configured) else "mock"

    if choice == "mock":
        logger.warning("mailer_selected", extra={"implementation": "mock"})
        return MockMailer()

    logger.info("mailer_selected", extra={"implementation": "real"})
    return SmtpMailer()
