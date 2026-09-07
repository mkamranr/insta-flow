"""Template rendering for automated Instagram messages (§29).

The substitution itself lives in `app/services/templating.py`, shared with
outbound email. This module is the **DM-specific** half: the vocabulary a rule
author may use and the length Meta will carry. Both stay here rather than
becoming defaults on the primitive, so no caller can widen the DM allowlist by
omitting an argument -- an email variable such as `{{invite_url}}` must never be
renderable into a direct message.

The public API is unchanged from when this file held everything, which is why no
call site moved.
"""

from typing import Final

from app.services import templating

ALLOWED_VARIABLES: Final = frozenset(
    {"username", "comment", "link", "campaign_name", "post_caption"}
)

MAX_MESSAGE_LENGTH: Final = 1000


def extract_variables(body: str) -> set[str]:
    return templating.extract_variables(body)


def validate(body: str) -> set[str]:
    """Check a DM template at save time. Returns the variables it uses."""
    return templating.validate(
        body, allowed=ALLOWED_VARIABLES, max_length=MAX_MESSAGE_LENGTH
    )


def render(body: str, context: dict[str, str | None]) -> str:
    """Substitute allowed variables. Unknown names raise; known-but-absent render empty."""
    return templating.render(
        body, context, allowed=ALLOWED_VARIABLES, max_length=MAX_MESSAGE_LENGTH
    )
