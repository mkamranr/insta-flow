"""Admin authentication: login, logout, current user."""

import logging
import secrets

from fastapi import APIRouter, Request, Response, status

from app import queue
from app.api.deps import AuditRepo, CurrentGrant, CurrentUser, SettingsDep, UsersRepo
from app.errors import AuthenticationError, RateLimitedError
from app.models.users import ChangeOwnPasswordRequest, LoginRequest, MePublic, UserPublic
from app.services.security import sessions
from app.services.security.passwords import hash_password, verify_password
from app.services.security.throttle import Throttle

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

# A real Argon2 hash of a random string. Verifying against this when the email is
# unknown costs the same as verifying a wrong password, so response time does not
# reveal whether an account exists. A fabricated hash string would fail to parse
# and return instantly, defeating the point.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def _set_session_cookie(response: Response, settings, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_lifetime_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )


@router.post("/login", response_model=UserPublic)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    users: UsersRepo,
    audit: AuditRepo,
) -> UserPublic:
    email = payload.email.strip().lower()

    # Throttle before doing any Argon2 work: verification is deliberately
    # expensive, so an unthrottled login is a cheap denial of service as well as
    # a credential-stuffing risk. Keyed on email and client IP together, so one
    # attacker cannot lock out a real user by guessing at their address.
    client_ip = request.client.host if request.client else "unknown"
    throttle = Throttle(
        queue.get_pool(),
        limit=settings.login_attempt_limit,
        window_seconds=settings.login_attempt_window_seconds,
    )
    allowed, attempts = await throttle.hit(f"login:{email}:{client_ip}")
    if not allowed:
        logger.warning("login_throttled", extra={"email": email, "attempts": attempts})
        raise RateLimitedError("Too many attempts. Try again in a few minutes.")

    user = await users.get_by_email(email)

    # Verify even when the user does not exist, so response time does not reveal
    # whether an email is registered.
    stored_hash = user.get("password_hash", "") if user else _DUMMY_HASH
    password_ok = verify_password(payload.password, stored_hash)

    if user is None or not password_ok or not user.get("is_active", False):
        logger.warning("login_failed", extra={"email": email})
        # One message for every failure mode: no account enumeration.
        raise AuthenticationError("Invalid email or password.")

    await throttle.reset(f"login:{email}:{client_ip}")
    await users.touch_login(user["_id"])
    token = sessions.issue(
        settings.session_secret, str(user["_id"]), int(user.get("session_epoch", 0))
    )
    _set_session_cookie(response, settings, token)

    await audit.record(
        user_id=user["_id"],
        action="auth.login",
        entity_type="user",
        entity_id=user["_id"],
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    logger.info("login_success", extra={"user_id": str(user["_id"])})
    return UserPublic.from_document(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    settings: SettingsDep,
    user: CurrentUser,
    users: UsersRepo,
    audit: AuditRepo,
) -> Response:
    # Bumping the epoch revokes every cookie already issued, not just this one.
    await users.bump_session_epoch(user["_id"])
    response.delete_cookie(settings.session_cookie_name, path="/")

    await audit.record(
        user_id=user["_id"],
        action="auth.logout",
        entity_type="user",
        entity_id=user["_id"],
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    logger.info("logout", extra={"user_id": str(user["_id"])})
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=dict(response.headers))


@router.get("/me", response_model=MePublic)
async def me(user: CurrentUser, grant: CurrentGrant) -> MePublic:
    """Your own identity, role and effective permissions.

    `grant` is already resolved and cached for this request, so carrying the role
    here costs no extra lookup. This is what the frontend gates its navigation on
    -- and it is UX only: every route re-checks server-side.
    """
    return MePublic.for_self(user, grant.role, sorted(grant.permissions))


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_own_password(
    payload: ChangeOwnPasswordRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    user: CurrentUser,
    users: UsersRepo,
    audit: AuditRepo,
) -> Response:
    """Your own password. No permission required -- everyone may do this for
    themselves, whatever their role."""
    if not verify_password(payload.current_password, user.get("password_hash", "")):
        logger.warning("password_change_rejected", extra={"user_id": str(user["_id"])})
        raise AuthenticationError("That is not your current password.")

    await users.set_password(user["_id"], hash_password(payload.password))
    # Bumping the epoch signs out every other device, which is the point of
    # changing a password. This session is then re-issued at the new epoch, so the
    # person is not logged out of the tab they are standing in.
    epoch = await users.bump_session_epoch(user["_id"])
    _set_session_cookie(
        response, settings, sessions.issue(settings.session_secret, str(user["_id"]), epoch)
    )
    await audit.record(
        user_id=user["_id"],
        action="auth.password_changed",
        entity_type="user",
        entity_id=user["_id"],
        ip_address=request.client.host if request.client else None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers=dict(response.headers))
