"""Shared FastAPI dependencies.

Routes depend on repositories, never on a collection handle, so the layering
holds at the HTTP layer too.
"""

import logging
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, NamedTuple

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.db import mongodb
from app.db.repositories import (
    AccountSnapshotsRepository,
    AppSettingsRepository,
    AuditLogsRepository,
    ClickEventsRepository,
    CommentsRepository,
    ContactsRepository,
    ExecutionsRepository,
    InstagramAccountsRepository,
    LinkClicksRepository,
    MediaRepository,
    RolesRepository,
    RulesRepository,
    StoriesRepository,
    UpcomingPostsRepository,
    UsersRepository,
    WebhookEventsRepository,
)
from app.errors import AuthenticationError, AuthorizationError
from app.permissions import is_valid
from app.services.geoip import GeoIpReader
from app.services.security import sessions

logger = logging.getLogger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_db() -> Any:
    return mongodb.get_database()


DbDep = Annotated[Any, Depends(get_db)]


def users_repo(db: DbDep) -> UsersRepository:
    return UsersRepository(db)


def accounts_repo(db: DbDep) -> InstagramAccountsRepository:
    return InstagramAccountsRepository(db)


def media_repo(db: DbDep) -> MediaRepository:
    return MediaRepository(db)


def stories_repo(db: DbDep) -> StoriesRepository:
    return StoriesRepository(db)


def upcoming_repo(db: DbDep) -> UpcomingPostsRepository:
    return UpcomingPostsRepository(db)


def account_snapshots_repo(db: DbDep) -> AccountSnapshotsRepository:
    return AccountSnapshotsRepository(db)


def app_settings_repo(db: DbDep) -> AppSettingsRepository:
    return AppSettingsRepository(db)


def click_events_repo(db: DbDep) -> ClickEventsRepository:
    return ClickEventsRepository(db)


@lru_cache(maxsize=1)
def _reader_for(directory: str) -> GeoIpReader:
    """One reader per process. Opening the database per click would mean a file
    open on every redirect."""
    return GeoIpReader(Path(directory))


def geoip_reader(settings: SettingsDep) -> GeoIpReader:
    return _reader_for(settings.geoip_dir)


def contacts_repo(db: DbDep) -> ContactsRepository:
    return ContactsRepository(db)


def link_clicks_repo(db: DbDep) -> LinkClicksRepository:
    return LinkClicksRepository(db)


def roles_repo(db: DbDep) -> RolesRepository:
    return RolesRepository(db)


def rules_repo(db: DbDep) -> RulesRepository:
    return RulesRepository(db)


def comments_repo(db: DbDep) -> CommentsRepository:
    return CommentsRepository(db)


def executions_repo(db: DbDep) -> ExecutionsRepository:
    return ExecutionsRepository(db)


def webhook_events_repo(db: DbDep) -> WebhookEventsRepository:
    return WebhookEventsRepository(db)


def audit_repo(db: DbDep) -> AuditLogsRepository:
    return AuditLogsRepository(db)


UsersRepo = Annotated[UsersRepository, Depends(users_repo)]
AccountsRepo = Annotated[InstagramAccountsRepository, Depends(accounts_repo)]
MediaRepo = Annotated[MediaRepository, Depends(media_repo)]
StoriesRepo = Annotated[StoriesRepository, Depends(stories_repo)]
UpcomingRepo = Annotated[UpcomingPostsRepository, Depends(upcoming_repo)]
SnapshotsRepo = Annotated[AccountSnapshotsRepository, Depends(account_snapshots_repo)]
AppSettingsRepo = Annotated[AppSettingsRepository, Depends(app_settings_repo)]
ClickEventsRepo = Annotated[ClickEventsRepository, Depends(click_events_repo)]
GeoIpDep = Annotated[GeoIpReader, Depends(geoip_reader)]
RolesRepo = Annotated[RolesRepository, Depends(roles_repo)]
RulesRepo = Annotated[RulesRepository, Depends(rules_repo)]
LinkClicksRepo = Annotated[LinkClicksRepository, Depends(link_clicks_repo)]
ContactsRepo = Annotated[ContactsRepository, Depends(contacts_repo)]
CommentsRepo = Annotated[CommentsRepository, Depends(comments_repo)]
ExecutionsRepo = Annotated[ExecutionsRepository, Depends(executions_repo)]
WebhookEventsRepo = Annotated[WebhookEventsRepository, Depends(webhook_events_repo)]
AuditRepo = Annotated[AuditLogsRepository, Depends(audit_repo)]


async def current_user(request: Request, settings: SettingsDep, users: UsersRepo) -> dict[str, Any]:
    """Resolve the signed session cookie to an active user.

    Rejects a session whose epoch is behind the user's current one — that is how
    logout revokes cookies that were already issued.
    """
    cookie = request.cookies.get(settings.session_cookie_name, "")
    session = sessions.verify(
        settings.session_secret, cookie, settings.session_lifetime_hours * 3600
    )
    if session is None:
        raise AuthenticationError("Not signed in.")

    try:
        user = await users.get(session.user_id)
    except ValueError:
        raise AuthenticationError("Not signed in.") from None

    if user is None or not user.get("is_active", False):
        raise AuthenticationError("Not signed in.")
    if int(user.get("session_epoch", 0)) != session.epoch:
        raise AuthenticationError("Session has been signed out.")

    return user


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]


class Grant(NamedTuple):
    """One request's resolved authorization decision."""

    role: dict[str, Any] | None
    permissions: frozenset[str]


async def current_grant(user: CurrentUser, roles: RolesRepo) -> Grant:
    """The signed-in user's role and the permissions it carries.

    Resolved from the database on every request, deliberately. The session cookie
    carries only `{uid, epoch}` (`services/security/sessions.py`), so a role change
    takes effect on the caller's next request rather than at their next login. The
    cost is one indexed lookup; the alternative is a permission you revoked that
    keeps working until the cookie expires.

    FastAPI caches dependencies per request, so a route that declares both
    `Depends(require(...))` and `user: CurrentUser` still reads the user once.
    """
    role = await roles.get_for_user(user)
    if role is None:
        # No role, or a role that has since been deleted. Fail closed: an
        # unresolvable user gets nothing rather than falling back to a default.
        logger.warning("user_has_no_role", extra={"user_id": str(user["_id"])})
        return Grant(role=None, permissions=frozenset())
    return Grant(role, frozenset(str(p) for p in role.get("permissions", ())))


CurrentGrant = Annotated[Grant, Depends(current_grant)]


def _catalogued(permissions: tuple[str, ...]) -> tuple[str, ...]:
    unknown = sorted(p for p in permissions if not is_valid(p))
    if unknown:
        # At import time, not request time. A typo must break the build: a
        # permission nobody can hold silently locks a route, and there is no
        # symptom until someone complains they cannot do their job.
        raise ValueError(f"not in the permission catalogue: {', '.join(unknown)}")
    return permissions


def require(*permissions: str) -> Callable[[Grant], Awaitable[None]]:
    """Route guard: every listed permission is required.

    Written as a route dependency so the requirement sits on the same line as the
    method and the path:

        @router.patch("/{rule_id}", dependencies=[Depends(require("automations.edit"))])

    rather than a call inside the handler, which is opt-in and therefore
    forgettable -- which is exactly how three handlers in `rules.py` came to load a
    rule with no ownership check at all. Being in the decorator also makes it
    machine-checkable: `test_authorization.py` walks every route and fails if one
    declares no permission.
    """
    wanted = _catalogued(permissions)

    async def dependency(grant: CurrentGrant) -> None:
        missing = [p for p in wanted if p not in grant.permissions]
        if missing:
            # 403 and not 404: this is about a capability, which reveals nothing
            # about which rows exist. Ownership checks keep using 404. The reply
            # stays generic; the detail goes to the log.
            logger.info("permission_denied", extra={"missing": missing})
            raise AuthorizationError("You do not have access to do that.")

    dependency.__permissions__ = wanted  # type: ignore[attr-defined]
    return dependency


def require_any(*permissions: str) -> Callable[[Grant], Awaitable[None]]:
    """Route guard: any one of the listed permissions is enough.

    Only for an endpoint that genuinely serves two paths -- uploading an
    attachment belongs to whoever may create *or* edit an automation.
    """
    wanted = _catalogued(permissions)

    async def dependency(grant: CurrentGrant) -> None:
        if not grant.permissions.intersection(wanted):
            logger.info("permission_denied", extra={"needed_any": list(wanted)})
            raise AuthorizationError("You do not have access to do that.")

    dependency.__permissions__ = wanted  # type: ignore[attr-defined]
    return dependency
