"""Message bodies as data, rendered through the shared `{{var}}` substitution.

**text/plain only, deliberately.** `services/templating.render` does no HTML
escaping -- it was written for direct messages, where there is no markup to
escape -- so putting a variable into an HTML body would be an injection vector
the moment that variable carries operator- or recipient-supplied text. When an
HTML part is wanted (the invitation will want one), add escaping at that point
as a deliberate change, rather than inheriting the gap by accident here.

The allowlist is this module's own and is passed explicitly on every call, so a
mail variable can never be rendered into a DM. See `campaigns/renderer.py`.
"""

from dataclasses import dataclass
from typing import Final

from app.errors import TemplateError
from app.services import templating

EMAIL_VARIABLES: Final = frozenset(
    {"app_name", "email", "invite_url", "expires_at", "invited_by", "origin"}
)

# Two orders of magnitude above the DM limit: a DM is capped by what Meta will
# carry, an email body by nothing in particular.
EMAIL_MAX_LENGTH: Final = 20_000
MAX_SUBJECT_LENGTH: Final = 200

APP_NAME: Final = "Social Flow"


@dataclass(frozen=True)
class Template:
    key: str
    subject: str
    body: str


TEMPLATES: Final[dict[str, Template]] = {
    "test": Template(
        key="test",
        subject="{{app_name}} test email",
        body=(
            "This is a test message from {{app_name}}.\n\n"
            "If you are reading it, the mail settings on the Settings page are "
            "working and invitations will be able to go out.\n\n"
            "Nothing else happened -- this message was sent because somebody "
            "clicked Send test email.\n"
        ),
    ),
    "invite": Template(
        key="invite",
        subject="You have been invited to {{app_name}}",
        body=(
            "{{invited_by}} has invited you to {{app_name}}, a tool for replying "
            "automatically to comments on your own Instagram account.\n\n"
            "Open this link to choose a password and set up your account:\n\n"
            "{{invite_url}}\n\n"
            "The link works once and expires on {{expires_at}}. If it has already "
            "expired, ask whoever invited you to send another.\n\n"
            "You are receiving this because {{email}} was on the sender's contact "
            "list. If you were not expecting it, you can ignore this message -- "
            "nothing has been created in your name, and the link expires by "
            "itself.\n"
        ),
    ),
}


def render_template(key: str, context: dict[str, str | None]) -> tuple[str, str]:
    """Return `(subject, body)` for a template key.

    Both halves go through the same allowlist, so a typo in a subject is caught
    at the same moment as one in a body.
    """
    template = TEMPLATES.get(key)
    if template is None:
        raise TemplateError(f"Unknown email template: {key}")

    full = {"app_name": APP_NAME, **context}
    subject = templating.render(
        template.subject, full, allowed=EMAIL_VARIABLES, max_length=MAX_SUBJECT_LENGTH
    )
    body = templating.render(
        template.body, full, allowed=EMAIL_VARIABLES, max_length=EMAIL_MAX_LENGTH
    )
    return subject, body
