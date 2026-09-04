"""Creating the very first admin through the browser.

`make bootstrap-admin` needs a shell on the host. A container running on
someone else's cloud may not offer one, so the first admin has to be creatable
from the login page instead.

That is an account-creation endpoint reachable by anyone, so it is fenced in
four independent ways, each of which alone would be enough to make it useless
to an attacker:

1. It refuses once **any** user exists. Not "any admin" -- any user at all.
2. It refuses unless `ADMIN_SETUP_SECRET` is configured, and refuses a secret
   shorter than `MIN_SETUP_SECRET_LENGTH`. An unset secret disables the route;
   it never means "no secret required".
3. The comparison is constant time, and attempts are throttled per client.
4. A partial unique index on `is_bootstrap` means at most one user can ever be
   created this way, even if two requests race past check 1 together.
"""

import logging
import secrets

from fastapi import APIRouter, Request, Response, status
from pymongo.errors import DuplicateKeyError

from app import queue
from app.api.deps import AuditRepo, SettingsDep, UsersRepo
from app.errors import AuthenticationError, ConflictError, NotFoundError, RateLimitedError
from app.models.setup import MIN_SETUP_SECRET_LENGTH, SetupAdminRequest, SetupSecretRequest
from app.models.users import UserPublic
from app.services.security import sessions
from app.services.security.passwords import hash_password
from app.services.security.throttle import Throttle

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth/setup", tags=["setup"])


def _secret_is_usable(settings) -> bool:
    """A secret must exist and be long enough to be worth having.

    A three-character secret would pass a comparison and fail its purpose, so a
    short one disables setup exactly as an absent one does -- loudly, at the
    point of use, rather than by silently accepting it.
    """
    return len(settings.admin_setup_secret or "") >= MIN_SETUP_SECRET_LENGTH


async def _guard(request: Request, settings, users, secret: str) -> None:
    """Every check that must pass before an admin may be created."""
    if not _secret_is_usable(settings):
        logger.warning("setup_attempt_without_secret_configured")
        # 404, not 403: an unconfigured route should not advertise itself.
        raise NotFoundError("Setup is not available.")

    if await users.count({}) > 0:
        logger.warning("setup_attempt_after_users_exist")
        raise ConflictError("This app is already set up. Sign in instead.")

    client_ip = request.client.host if request.client else "unknown"
    throttle = Throttle(
        queue.get_pool(),
        limit=settings.login_attempt_limit,
        window_seconds=settings.login_attempt_window_seconds,
    )
    allowed, attempts = await throttle.hit(f"setup:{client_ip}")
    if not allowed:
        logger.warning("setup_throttled", extra={"attempts": attempts})
        raise RateLimitedError("Too many attempts. Try again in a few minutes.")

    # Constant time: a plain `==` leaks the secret one character at a time to
    # anyone who can measure the response.
    if not secrets.compare_digest(secret, settings.admin_setup_secret):
        logger.warning("setup_secret_rejected")
        raise AuthenticationError("That setup code is not correct.")

    await throttle.reset(f"setup:{client_ip}")


@router.get("")
async def setup_status(settings: SettingsDep, users: UsersRepo) -> dict[str, bool]:
    """Whether the login page should offer first-run setup.

    Says only whether setup is possible, never whether a secret is configured
    versus merely wrong -- and it is unauthenticated by necessity, since there
    is nobody to authenticate as yet.
    """
    return {"required": await users.count({}) == 0 and _secret_is_usable(settings)}


@router.post("/verify")
async def verify_secret(
    payload: SetupSecretRequest, request: Request, settings: SettingsDep, users: UsersRepo
) -> dict[str, bool]:
    """Check the code before showing the form.

    Purely so the operator finds out they have the wrong code before typing a
    password. It grants nothing: `create_admin` re-runs every check.
    """
    await _guard(request, settings, users, payload.secret)
    return {"ok": True}


@router.post("", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def create_admin(
    payload: SetupAdminRequest,
    request: Request,
    response: Response,
    settings: SettingsDep,
    users: UsersRepo,
    audit: AuditRepo,
) -> UserPublic:
    """Create the first admin and sign them in."""
    await _guard(request, settings, users, payload.secret)

    try:
        user = await users.create(
            payload.email, hash_password(payload.password), is_bootstrap=True
        )
    except DuplicateKeyError as exc:
        # Another request won the race between the count check and this insert.
        # The unique index is the authority, not the check.
        logger.warning("setup_lost_the_race")
        raise ConflictError("This app is already set up. Sign in instead.") from exc

    token = sessions.issue(settings.session_secret, str(user["_id"]), 0)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_lifetime_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )

    await audit.record(
        user_id=user["_id"],
        action="auth.setup_admin",
        entity_type="user",
        entity_id=user["_id"],
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    logger.info("setup_admin_created", extra={"user_id": str(user["_id"])})
    return UserPublic.from_document(user)
