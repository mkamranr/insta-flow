"""Outbound email.

**All SMTP lives in this package, and specifically in `transport.py`.**
Everything else depends on the `MailerProtocol`, which is why `MockMailer`
substitutes in tests. `tests/test_email_boundary.py` asserts the import set, so
the boundary is enforced rather than merely written down -- unlike the
`services/meta/` equivalent, whose "enforced by grep in the test run" claim in
the working notes turned out to describe a test that was never written.

The password is decrypted in exactly one place, `config.py`, mirroring
`services/meta/credentials.py`.
"""

from app.services.email.client import (
    AUTH_CODES,
    MailerProtocol,
    MailMessage,
    MailResult,
    error_for_code,
)
from app.services.email.config import (
    DEFAULT_PORT,
    DEFAULT_SECURITY,
    SECURITY_CHOICES,
    SETTINGS_KEYS,
    MailConfig,
    resolve,
    status,
)
from app.services.email.factory import build_mailer
from app.services.email.templates import (
    APP_NAME,
    EMAIL_MAX_LENGTH,
    EMAIL_VARIABLES,
    TEMPLATES,
    Template,
    render_template,
)

__all__ = [
    "APP_NAME",
    "AUTH_CODES",
    "DEFAULT_PORT",
    "DEFAULT_SECURITY",
    "EMAIL_MAX_LENGTH",
    "EMAIL_VARIABLES",
    "SECURITY_CHOICES",
    "SETTINGS_KEYS",
    "TEMPLATES",
    "MailConfig",
    "MailMessage",
    "MailResult",
    "MailerProtocol",
    "Template",
    "build_mailer",
    "error_for_code",
    "render_template",
    "resolve",
    "status",
]
