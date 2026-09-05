"""URL-safe names, in one place.

Two things in this app carry a slug: roles, since RBAC, and now users -- whose
slug is their workspace, the `/u/<slug>/` prefix that Meta posts webhooks to.

The rule was already written twice, identically, in `db/repositories/roles.py`
and `api/roles.py`. A third copy for workspaces is how three copies quietly
drift apart, and a workspace slug drifting is not cosmetic: it is the segment a
Meta app has been configured to call.
"""

import re
from typing import Final

# A workspace slug appears in a URL that someone pastes into Meta's dashboard,
# so it stays boring: lowercase, alphanumeric, hyphens inside only. The leading
# character must be alphanumeric so a slug can never be mistaken for a flag or a
# relative path segment.
MAX_LENGTH: Final[int] = 32
VALID: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")

# Workspaces live under a fixed literal segment rather than at the root. `/u/`
# costs one segment and buys the guarantee that no slug can ever collide with a
# route: `/settings` stays the settings page forever, even if somebody's email
# local part is "settings". Without it, every new top-level route would have to
# be added to a reserved-word list first, and forgetting would break whichever
# existing user held that name -- silently, and only for them.
URL_PREFIX: Final[str] = "/u"


def workspace_url(origin: str, slug: str | None) -> str:
    """The base URL a workspace's Meta app is configured against.

    `slug=None` yields the bare origin, which is the pre-workspace URL layout and
    still what the instance-wide credentials use. See
    `services/meta/credentials.py` for why those two travel together.
    """
    origin = origin.rstrip("/")
    return f"{origin}{URL_PREFIX}/{slug}" if slug else origin


def slugify(text: str, *, fallback: str) -> str:
    """Lowercase, alphanumeric, hyphen-separated. Never empty."""
    cleaned = "".join(character if character.isalnum() else "-" for character in text.lower())
    joined = "-".join(part for part in cleaned.split("-") if part)
    return joined[:MAX_LENGTH].rstrip("-") or fallback


def is_valid(slug: str) -> bool:
    return bool(VALID.match(slug))


def from_email(email: str) -> str:
    """A starting slug for a new user, taken from the local part of their email.

    Only a suggestion: `UsersRepository` still has to make it unique, and the
    unique index is what actually decides. Two people at different domains
    routinely share a local part.
    """
    local = email.split("@", 1)[0]
    candidate = slugify(local, fallback="user")
    # A single character passes `slugify` but fails `VALID`, which requires at
    # least two. Pad rather than reject: the email is already valid, and this is
    # a derived default the user never asked to supply.
    return candidate if is_valid(candidate) else f"{candidate}-user"[:MAX_LENGTH]
