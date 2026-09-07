"""Invitations: sending them, and redeeming them.

Two routers, mounted differently, following the `uploads.router` /
`uploads.public_router` precedent.

`router` is workspace-scoped and therefore mounted **twice**, bare and under
`/u/{slug}`. Not because an invitation belongs to a workspace -- it does not --
but because `api/client.ts` prefixes every request with the SPA's basename, so
an Owner browsing `/u/ameera/contacts` calls `/u/ameera/api/invites`. Mounting
once would 404 them, which is exactly why `users.router` is double-mounted too.

`public_router` is mounted **once, bare**. The activation page has no workspace
and no session, and a second URL that can mint an account is surface with no
caller -- the security review would have to reason about it twice.

**The token travels in the request body, never in the URL.** Caddy logs
`request>uri`, and its `format filter` can only redact query parameters -- a
path segment would need a second regexp filter to maintain. A body cannot be
logged by a reverse proxy at all, so this removes the exposure instead of
filtering it. (The browser-visible `/invite?token=...` page load is a different
request and *is* covered by the query filter.)
"""

import logging
from email.utils import make_msgid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app import queue
from app.api.deps import (
    AppSettingsRepo,
    AuditRepo,
    CurrentGrant,
    CurrentUser,
    EmailLogRepo,
    InvitesRepo,
    RolesRepo,
    SettingsDep,
    UsersRepo,
    require,
    role_you_may_grant,
)
from app.db.repositories.base import to_object_id, utcnow
from app.db.repositories.invites import hash_token, new_token
from app.errors import (
    ConflictError,
    GoneError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from app.models.invites import (
    AcceptInviteRequest,
    InviteLookup,
    InvitePublic,
    InviteRequest,
)
from app.models.users import UserPublic
from app.permissions import WORKSPACE_OWNER_SLUG
from app.services import invites as invite_service
from app.services import slugs
from app.services.email import APP_NAME, render_template
from app.services.meta.credentials import origin_for
from app.services.security import sessions
from app.services.security.passwords import hash_password
from app.services.security.throttle import Throttle

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/invites", tags=["invites"])
public_router = APIRouter(prefix="/api/invite", tags=["invites"])


class TokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class AcceptRequest(AcceptInviteRequest):
    token: str = Field(min_length=1, max_length=512)


async def _throttle(request: Request, settings: Any, key: str, limit: int) -> None:
    counter = Throttle(
        queue.get_pool(),
        limit=limit,
        window_seconds=settings.invite_attempt_window_seconds,
    )
    allowed, attempts = await counter.hit(key)
    if not allowed:
        logger.warning(
            "invite_throttled", extra={"attempts": attempts, "bucket": key.split(":")[0]}
        )
        raise RateLimitedError("Too many attempts. Try again in a few minutes.")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def _decorate(
    invite: dict[str, Any],
    *,
    roles: Any,
    users: Any,
    url: str | None = None,
) -> InvitePublic:
    role = await roles.get(invite["role_id"]) if invite.get("role_id") else None
    inviter = await users.get(invite["invited_by"]) if invite.get("invited_by") else None
    return InvitePublic.from_document(invite, role=role, invited_by=inviter, url=url)


async def _queue_invite_email(
    *,
    invite: dict[str, Any],
    token: str,
    origin: str,
    inviter: dict[str, Any],
    log: Any,
    invites: Any,
) -> None:
    """Render nothing here; hand the job a template key and a context.

    Enqueued rather than sent inline so an SMTP timeout cannot turn a request
    that has already written the invite row into a 502.
    """
    url = invite_service.activation_url(origin, token)
    context = {
        "email": str(invite["email"]),
        "invite_url": url,
        "expires_at": invite["expires_at"].strftime("%d %B %Y at %H:%M UTC"),
        "invited_by": str(inviter.get("email") or APP_NAME),
        "origin": origin,
    }
    subject, _ = render_template("invite", context)
    message_id = make_msgid(domain="social-flow.local")

    row = await log.open(
        message_id=message_id,
        purpose="invite",
        to_address=str(invite["email"]),
        subject=subject,
        requested_by=inviter.get("_id"),
        invite_id=invite["_id"],
    )
    if row is None:  # pragma: no cover - a fresh Message-ID cannot collide
        return

    try:
        await queue.enqueue(
            "send_email",
            message_id=message_id,
            purpose="invite",
            to_address=str(invite["email"]),
            context=context,
        )
    except Exception:
        # The invite exists and is valid; only the mail failed. Recorded on both
        # rows so the admin list can show it, and left for a resend rather than
        # rolled back -- the operator can hand over the link directly.
        await log.mark_failed(message_id, error="Could not queue the message.", retryable=False)
        await invites.record_send(
            invite["_id"], status="failed", error="Could not queue the message."
        )
        logger.error("invite_email_enqueue_failed", exc_info=True)
        return

    await invites.record_send(invite["_id"], status="queued")


# --- sending ----------------------------------------------------------------


@router.post("", dependencies=[Depends(require("users.manage"))])
async def create_invites(
    payload: InviteRequest,
    request: Request,
    user: CurrentUser,
    grant: CurrentGrant,
    invites: InvitesRepo,
    users: UsersRepo,
    roles: RolesRepo,
    store: AppSettingsRepo,
    log: EmailLogRepo,
    settings: SettingsDep,
    audit: AuditRepo,
) -> dict[str, Any]:
    """Invite one or more addresses.

    Gated on `users.manage` rather than a new `contacts.invite`, and that is a
    deliberate saving. `ACTIONS` is a closed five-verb vocabulary, so "invite"
    is not expressible and `contacts.create` would be a lie -- but more
    usefully, adding no `Module` keeps `ALL` the same size, which means the
    Owner re-sync stays a no-op on upgrade and no operator-edited Manager or
    Viewer role shifts underneath them. An invitation *is* "add a person", which
    is what `users.manage` already means.

    Returns a verdict per address rather than failing the batch: a bulk action
    that 409s on row 7 leaves rows 1-6 sent and the operator guessing which.
    """
    role = (
        await role_you_may_grant(roles, payload.role_id, grant)
        if payload.role_id
        else await roles.get_by_slug(WORKSPACE_OWNER_SLUG)
    )
    if role is None:
        raise ValidationError("The workspace-owner role is missing. Restart the API to reseed it.")

    saved = await store.load()
    origin = origin_for(settings, saved)
    sent: list[InvitePublic] = []
    skipped: list[dict[str, str]] = []

    for email in payload.emails:
        await _throttle(
            request,
            settings,
            f"invite:send:{user['_id']}",
            settings.invite_send_limit,
        )

        if await users.get_by_email(email):
            skipped.append({"email": email, "reason": "already_registered"})
            continue

        token = new_token()
        invite = await invites.create(
            email=email,
            token_hash=hash_token(token),
            role_id=role["_id"],
            invited_by=user["_id"],
            expires_at=invite_service.expires_at(saved),
        )
        if invite is None:
            # `uq_invite_pending_email` refused it. Caught, never pre-checked.
            skipped.append({"email": email, "reason": "already_invited"})
            continue

        await _queue_invite_email(
            invite=invite,
            token=token,
            origin=origin,
            inviter=user,
            log=log,
            invites=invites,
        )
        await audit.record(
            user_id=user["_id"],
            action="invite.created",
            entity_type="invite",
            entity_id=invite["_id"],
            # Never the token or its hash.
            changes={"email": email, "role": str(role["name"])},
            ip_address=_client_ip(request),
        )
        # The raw link is returned once, here, so an install with no mail
        # configured is still usable and the flow is testable. This grants
        # nothing new: a `users.manage` holder can already set anyone's password.
        sent.append(
            InvitePublic.from_document(
                invite,
                role=role,
                invited_by=user,
                url=invite_service.activation_url(origin, token),
            )
        )

    return {"sent": sent, "skipped": skipped}


@router.get("", dependencies=[Depends(require("users.view"))])
async def list_invites(
    user: CurrentUser,
    invites: InvitesRepo,
    users: UsersRepo,
    roles: RolesRepo,
    invite_status: str | None = None,
) -> dict[str, Any]:
    """Who has been asked. Same disclosure class as `GET /api/users`."""
    rows = await invites.list_for_admin(status=invite_status, now=utcnow())
    return {"invites": [await _decorate(row, roles=roles, users=users) for row in rows]}


@router.post("/{invite_id}/resend", dependencies=[Depends(require("users.manage"))])
async def resend_invite(
    invite_id: str,
    request: Request,
    user: CurrentUser,
    invites: InvitesRepo,
    users: UsersRepo,
    roles: RolesRepo,
    store: AppSettingsRepo,
    log: EmailLogRepo,
    settings: SettingsDep,
    audit: AuditRepo,
) -> InvitePublic:
    """Re-issue a pending invite with a **new** token and a new deadline.

    Rotating rather than resending the same link means there is exactly one
    live link per invite at any moment, so a first email that was forwarded
    stops working the instant the operator resends.
    """
    await _throttle(request, settings, f"invite:send:{user['_id']}", settings.invite_send_limit)
    try:
        wanted = to_object_id(invite_id)
    except ValueError as exc:
        raise ValidationError("invite_id is not a valid id.") from exc

    saved = await store.load()
    token = new_token()
    invite = await invites.rotate_token(
        wanted, token_hash=hash_token(token), expires_at=invite_service.expires_at(saved)
    )
    if invite is None:
        existing = await invites.get(wanted)
        if existing is None:
            raise NotFoundError("Invite not found.")
        raise ConflictError(f"That invite is already {existing['status']}.")

    origin = origin_for(settings, saved)
    await _queue_invite_email(
        invite=invite, token=token, origin=origin, inviter=user, log=log, invites=invites
    )
    await audit.record(
        user_id=user["_id"],
        action="invite.resent",
        entity_type="invite",
        entity_id=invite["_id"],
        changes={"email": str(invite["email"])},
        ip_address=_client_ip(request),
    )
    return await _decorate(
        invite, roles=roles, users=users, url=invite_service.activation_url(origin, token)
    )


@router.post("/{invite_id}/revoke", dependencies=[Depends(require("users.manage"))])
async def revoke_invite(
    invite_id: str,
    request: Request,
    user: CurrentUser,
    invites: InvitesRepo,
    users: UsersRepo,
    roles: RolesRepo,
    audit: AuditRepo,
) -> InvitePublic:
    """Withdraw a pending invite.

    POST rather than DELETE because the row is kept for the audit trail, and
    DELETE would imply otherwise.

    **Revoking after acceptance does nothing to the account that was created.**
    That is the thing an operator will assume this does, so it is worth stating:
    to shut down a user who already signed up, deactivate them with
    `PATCH /api/users/{id}` `is_active: false`.
    """
    try:
        wanted = to_object_id(invite_id)
    except ValueError as exc:
        raise ValidationError("invite_id is not a valid id.") from exc

    invite = await invites.revoke(wanted, revoked_by=user["_id"])
    if invite is None:
        existing = await invites.get(wanted)
        if existing is None:
            raise NotFoundError("Invite not found.")
        raise ConflictError(f"That invite is already {existing['status']} and cannot be revoked.")

    await audit.record(
        user_id=user["_id"],
        action="invite.revoked",
        entity_type="invite",
        entity_id=invite["_id"],
        changes={"email": str(invite["email"])},
        ip_address=_client_ip(request),
    )
    return await _decorate(invite, roles=roles, users=users)


# --- redeeming (unauthenticated) --------------------------------------------


@public_router.post("/lookup", response_model=InviteLookup)
async def lookup_invite(
    payload: TokenRequest,
    request: Request,
    invites: InvitesRepo,
    settings: SettingsDep,
) -> InviteLookup:
    """What address this token is for, so the signup form can show it.

    Unauthenticated by necessity: the invitee has no account, and creating one
    is the point. The token is a bearer credential and is treated as one --
    256 bits, single use, and throttled per IP *before* the database is touched.

    Returns the address and the deadline and nothing else: not the inviter, not
    the role, not the invite id, not whether other invites exist for the
    address. What a holder learns is the address their own token was issued for.

    **A deliberate departure from the house 404 rule**, which is worth naming.
    `current_workspace` returns 404 rather than 403 so a slug cannot be
    enumerated. That argument does not transfer: only a holder of a 256-bit
    token reaches this path, and no amount of guessing produces a once-valid
    one. Meanwhile collapsing spent into unknown would make the overwhelmingly
    common real case -- clicking a link three days late -- indistinguishable
    from a corrupted link, which is the support question that actually happens.
    So: unknown is 404, and spent or expired is 410 with copy the recipient can
    act on.
    """
    await _throttle(
        request,
        settings,
        f"invite:lookup:{_client_ip(request)}",
        settings.invite_lookup_limit,
    )

    # Hashed unconditionally, whatever arrived, and the lookup always runs.
    invite = await invites.find_by_token(hash_token(payload.token))
    if invite is None:
        logger.info("invite_lookup_unknown")
        raise NotFoundError("That invitation link is not valid.")

    if invite["status"] == "accepted":
        raise GoneError("That invitation has already been used. Try signing in instead.")
    if invite["status"] == "revoked":
        raise GoneError("That invitation was withdrawn. Ask for a new one.")
    if invite_service.is_expired(invite):
        raise GoneError("That invitation has expired. Ask for a new one.")

    return InviteLookup(
        email=str(invite["email"]),
        expires_at=invite["expires_at"],
        # The UNSUFFIXED base, deliberately. `claim_slug` probes for a free name
        # in a loop, so returning `ameera-3` would tell an unauthenticated
        # caller that `ameera` and `ameera-2` are taken -- a free
        # workspace-existence oracle, which is precisely what
        # `current_workspace`'s 404-not-403 exists to prevent. The unique index
        # resolves a collision at accept time and the 409 carries a suggestion,
        # by which point the caller has demonstrably presented a valid token.
        suggested_slug=slugs.from_email(str(invite["email"])),
    )


@public_router.post("/accept", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def accept_invite(
    payload: AcceptRequest,
    request: Request,
    response: Response,
    invites: InvitesRepo,
    users: UsersRepo,
    roles: RolesRepo,
    settings: SettingsDep,
    audit: AuditRepo,
) -> UserPublic:
    """Create the invited account and sign it in.

    Order matters, and each step is here for a reason:

    1. Pydantic has already validated the body, **including the slug**, so a
       typo returns 422 with the invite untouched rather than burning a
       single-use token on a rejected request.
    2. Throttle per IP.
    3. Redeem, which is an atomic status flip and therefore the reservation --
       never a read-then-write (§25, §63).
    4. Only then hash the password. Argon2 is deliberately expensive, so doing
       it after the reservation means a token guesser cannot make the server
       burn CPU. There is no account-existence oracle to equalise here, unlike
       login: the token *is* the secret.
    5. Create the user with the slug they chose. `uq_user_slug` decides, not a
       pre-check.
    6. On a duplicate, release the reservation so the token stays live and they
       can pick another name.

    The request body carries **no email**. The address comes off the invite row,
    so there is no way to sign up as anybody else -- the form's `readOnly`
    attribute is presentation, and this is the guarantee.
    """
    await _throttle(
        request,
        settings,
        f"invite:accept:{_client_ip(request)}",
        settings.invite_accept_limit,
    )

    invite = await invites.redeem(hash_token(payload.token), now=utcnow())
    if invite is None:
        # Unknown, spent, revoked or expired -- one query covers all four, and
        # the follow-up read is only to choose the right message.
        existing = await invites.find_by_token(hash_token(payload.token))
        if existing is None:
            raise NotFoundError("That invitation link is not valid.")
        raise GoneError("That invitation is no longer usable. Ask for a new one.")

    role = await roles.get(invite["role_id"])
    if role is None:  # pragma: no cover - the role is seeded on every boot
        await invites.release(invite["_id"])
        raise ValidationError("The invited role no longer exists.")

    try:
        created = await users.create(
            str(invite["email"]),
            hash_password(payload.password),
            role_id=role["_id"],
            slug=payload.slug,
        )
    except DuplicateKeyError as exc:
        released = await invites.release(invite["_id"])
        if "uq_user_slug" in str(exc):
            logger.info("invite_slug_taken")
            raise ConflictError(
                f"The address /u/{payload.slug}/ was just taken. Choose another."
            ) from exc
        logger.info("invite_email_already_registered", extra={"released": released})
        raise ConflictError(
            "Someone already has an account with that address. Try signing in."
        ) from exc

    await invites.attach_user(invite["_id"], created["_id"])
    sessions.set_cookie(
        response,
        settings,
        sessions.issue(settings.session_secret, str(created["_id"]), 0),
    )

    # Two records, because `audit.record` takes one actor and these are two
    # different facts with two different ones -- the same point `Workspace`
    # makes about recording the caller rather than the workspace owner.
    await audit.record(
        user_id=created["_id"],
        action="invite.accepted",
        entity_type="user",
        entity_id=created["_id"],
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await audit.record(
        user_id=invite["invited_by"],
        action="invite.consumed",
        entity_type="invite",
        entity_id=invite["_id"],
        changes={"email": str(invite["email"])},
        ip_address=_client_ip(request),
    )
    logger.info("invite_accepted", extra={"user_id": str(created["_id"])})
    return UserPublic.from_document(created, role)
