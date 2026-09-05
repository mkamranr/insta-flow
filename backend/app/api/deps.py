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
    MetaAppsRepository,
    RolesRepository,
    RulesRepository,
    StoriesRepository,
    UpcomingPostsRepository,
    UsersRepository,
    WebhookEventsRepository,
)
from app.errors import AuthenticationError, AuthorizationError, NotFoundError
from app.permissions import is_valid
from app.services.geoip import GeoIpReader
from app.services.meta.credentials import (
    MetaCredentials,
    origin_for,
    resolve_for,
    unconfigured,
)
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


def meta_apps_repo(db: DbDep) -> MetaAppsRepository:
    return MetaAppsRepository(db)


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
MetaAppsRepo = Annotated[MetaAppsRepository, Depends(meta_apps_repo)]
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


# --- workspaces -------------------------------------------------------------

WORKSPACE_PERMISSION = "workspaces.view"


class Workspace(NamedTuple):
    """Whose data this request is about, which is not always who is asking.

    Every workspace-scoped route resolves this instead of reading `user["_id"]`
    directly. `user` here is the workspace's *owner*; the caller is still
    `CurrentUser`, and audit entries must keep recording the caller -- otherwise
    an Owner tidying up someone else's automations writes an audit trail naming
    the wrong person.
    """

    user: dict[str, Any]
    slug: str | None
    is_own: bool

    @property
    def user_id(self) -> Any:
        return self.user["_id"]


def workspace_slug(request: Request) -> str | None:
    """The `/u/<slug>/` segment, or None on the unprefixed routes.

    Read from `request.path_params` rather than a declared parameter, so that
    including a router twice -- once bare, once under `/u/{slug}` -- needs no
    change to a single handler signature. Verified: FastAPI routes an undeclared
    path parameter happily and still builds its OpenAPI schema.
    """
    slug = request.path_params.get("slug")
    return str(slug) if slug else None


async def current_workspace(
    request: Request, user: CurrentUser, grant: CurrentGrant, users: UsersRepo
) -> Workspace:
    """The workspace this request addresses: your own unless the path says otherwise.

    Reaching into someone else's costs `workspaces.view`, and failing that check
    raises **404, not 403** -- the same choice `instagram.py::_load` makes for
    accounts. A 403 would confirm the slug exists, turning this endpoint into a
    way to enumerate who has an account here.

    Costs no extra query in the common case: your own slug is already on the user
    document `current_user` loaded.
    """
    slug = workspace_slug(request)
    own = str(user.get("slug") or "") or None
    if slug is None or slug == own:
        return Workspace(user=user, slug=own, is_own=True)

    if WORKSPACE_PERMISSION not in grant.permissions:
        logger.info("workspace_denied", extra={"actor": str(user["_id"])})
        raise NotFoundError("Workspace not found.")

    owner = await users.get_by_slug(slug)
    if owner is None:
        raise NotFoundError("Workspace not found.")
    # `is_active` is not checked. A deactivated person cannot sign in, but their
    # Instagram account and its automations still exist and still fire, so an
    # administrator must be able to open the workspace to stop them.
    return Workspace(user=owner, slug=slug, is_own=False)


CurrentWorkspace = Annotated[Workspace, Depends(current_workspace)]


async def _resolved(
    settings: Settings,
    store: AppSettingsRepository,
    apps: MetaAppsRepository,
    *,
    user_id: Any | None,
    slug: str | None,
) -> MetaCredentials:
    app = await apps.for_user(user_id) if user_id is not None else None
    return resolve_for(settings, await store.load(), app, slug)


async def workspace_credentials(
    workspace: CurrentWorkspace,
    settings: SettingsDep,
    store: AppSettingsRepo,
    apps: MetaAppsRepo,
) -> MetaCredentials:
    """The Meta app this workspace acts as. Declared only where it is needed.

    Kept off `current_workspace` on purpose: it costs two extra reads, and only
    the OAuth and Settings routes care which app is in play. Everything else just
    needs to know whose rows to read.
    """
    return await _resolved(settings, store, apps, user_id=workspace.user_id, slug=workspace.slug)


WorkspaceCredentials = Annotated[MetaCredentials, Depends(workspace_credentials)]


async def public_credentials(
    request: Request,
    settings: SettingsDep,
    users: UsersRepo,
    store: AppSettingsRepo,
    apps: MetaAppsRepo,
) -> MetaCredentials:
    """Credentials for a request Meta makes, where there is no session to read.

    The webhook receiver, the OAuth callback and the two signed_request callbacks
    all land here. The slug in the path is the only thing that can say which app
    is calling, which is the whole reason workspaces are a URL prefix rather than
    a header or a cookie -- see `services/meta/credentials.py`.

    An unknown slug resolves to `unconfigured`, not to the instance credentials.
    """
    slug = workspace_slug(request)
    if slug is None:
        return await _resolved(settings, store, apps, user_id=None, slug=None)

    owner = await users.get_by_slug(slug)
    if owner is None:
        logger.warning("workspace_slug_unknown", extra={"slug": slug})
        return unconfigured(origin_for(settings, await store.load()), slug)
    return await _resolved(settings, store, apps, user_id=owner["_id"], slug=slug)


PublicCredentials = Annotated[MetaCredentials, Depends(public_credentials)]
