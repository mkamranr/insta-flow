"""Wire models for invitations.

The important one is `AcceptInviteRequest`, and the important thing about it is
what it does **not** have: an email field. The address comes off the invite row,
so there is no mismatch to check and no way to sign up as anybody else. The
`readOnly` attribute on the form is presentation; this is the guarantee.

Slug validation lives on the model rather than in the handler so it runs
*before* the handler does -- which means a typo returns 422 with the invite
still pending, instead of burning a single-use token on a rejected request.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.models.users import RoleSummary
from app.services import slugs
from app.services.invites import (
    DEFAULT_EXPIRY_HOURS,
    MAX_EXPIRY_HOURS,
    MIN_EXPIRY_HOURS,
    display_status,
)
from app.services.security.passwords import MIN_PASSWORD_LENGTH

# One mail job per recipient, so an unbounded list is an SMTP amplifier -- and
# Gmail's daily cap turns that into a self-inflicted outage.
MAX_BULK_RECIPIENTS = 100


class InviteRequest(BaseModel):
    """Invite one or more addresses. Bulk and single share one path."""

    emails: list[EmailStr] = Field(min_length=1, max_length=MAX_BULK_RECIPIENTS)
    # Absent means the seeded `workspace-owner` role. Present is validated
    # against the caller's own permissions by `deps.role_you_may_grant`, so an
    # inviter can never grant more access than they hold themselves.
    role_id: str | None = None

    @field_validator("emails")
    @classmethod
    def normalise(cls, value: list[str]) -> list[str]:
        # Deduplicated here rather than in the route: the same address can
        # legitimately arrive twice from a contact list, where one person may
        # have been captured by two automations on two posts.
        seen: dict[str, None] = {}
        for email in value:
            seen.setdefault(email.strip().lower(), None)
        return list(seen)


class InviteSettings(BaseModel):
    expiry_hours: int = Field(
        default=DEFAULT_EXPIRY_HOURS, ge=MIN_EXPIRY_HOURS, le=MAX_EXPIRY_HOURS
    )


class AcceptInviteRequest(BaseModel):
    """Everything the invitee supplies. Note there is no `email`.

    Pydantic ignores unknown keys by default, so a client that sends one anyway
    has it silently dropped -- which is the behaviour we want, and is pinned by
    a test rather than left to be assumed.
    """

    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=512)
    password_confirm: str = Field(min_length=1, max_length=512)
    slug: str = Field(min_length=1, max_length=slugs.MAX_LENGTH)

    @field_validator("slug")
    @classmethod
    def usable_slug(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not slugs.is_acceptable_input(cleaned):
            raise ValueError(
                "Use 2 to 32 characters: lowercase letters, numbers and single "
                "hyphens, starting and ending with a letter or number."
            )
        return cleaned

    @model_validator(mode="after")
    def passwords_match(self) -> "AcceptInviteRequest":
        # Server-side as well as in the form, for the reason `SetupAdminRequest`
        # gives: a mistyped password that only the browser checked would lock
        # someone out of an account they cannot recreate.
        if self.password != self.password_confirm:
            raise ValueError("Passwords do not match.")
        return self


class InviteLookup(BaseModel):
    """What an unauthenticated holder of a token is told, and nothing more.

    Not the inviter, not the role, not the invite id, not whether other invites
    exist for the address. The suggested slug is the **unsuffixed** base -- see
    `api/invites.py` for why a collision-suffixed one would be an oracle.
    """

    email: str
    expires_at: datetime
    suggested_slug: str


class InvitePublic(BaseModel):
    """An invite as the admin screen sees it.

    Built with `from_document`, never `serialise()`. `serialise()` copies every
    key and would put `token_hash` on the wire -- the same trap `UserPublic`
    documents about `password_hash`, and the likeliest accidental leak in this
    feature. A test string-searches every invite response for "token".
    """

    id: str
    email: str
    status: str
    expires_at: datetime
    created_at: datetime
    last_sent_at: datetime | None = None
    sent_count: int = 0
    accepted_at: datetime | None = None
    role: RoleSummary | None = None
    invited_by_email: str | None = None
    # Surfaced so an Owner can spot invites left behind by an admin who has
    # since been deactivated. Not cascaded automatically -- see the route.
    invited_by_active: bool = True
    last_send_status: str | None = None
    last_send_error: str | None = None
    # Only ever populated in the response that *mints* the link, so an operator
    # can hand it over on an install with no mail configured. Never read back
    # from the database, because only the hash is stored.
    url: str | None = None

    @classmethod
    def from_document(
        cls,
        document: dict[str, Any],
        *,
        role: dict[str, Any] | None = None,
        invited_by: dict[str, Any] | None = None,
        url: str | None = None,
        now: datetime | None = None,
    ) -> "InvitePublic":
        return cls(
            id=str(document["_id"]),
            email=str(document["email"]),
            status=display_status(document, now=now),
            expires_at=document["expires_at"],
            created_at=document["created_at"],
            last_sent_at=document.get("last_sent_at"),
            sent_count=int(document.get("sent_count") or 0),
            accepted_at=document.get("accepted_at"),
            role=RoleSummary.from_document(role) if role else None,
            invited_by_email=str(invited_by["email"]) if invited_by else None,
            invited_by_active=bool(invited_by.get("is_active", True)) if invited_by else True,
            last_send_status=document.get("last_send_status"),
            last_send_error=document.get("last_send_error"),
            url=url,
        )
