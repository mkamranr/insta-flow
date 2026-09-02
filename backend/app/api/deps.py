"""Shared FastAPI dependencies.

Routes depend on repositories, never on a collection handle, so the layering
holds at the HTTP layer too.
"""

from typing import Annotated, Any

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.db import mongodb
from app.db.repositories import (
    AccountSnapshotsRepository,
    AuditLogsRepository,
    CommentsRepository,
    ContactsRepository,
    ExecutionsRepository,
    InstagramAccountsRepository,
    LinkClicksRepository,
    MediaRepository,
    RulesRepository,
    UsersRepository,
    WebhookEventsRepository,
)
from app.errors import AuthenticationError
from app.services.security import sessions

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


def account_snapshots_repo(db: DbDep) -> AccountSnapshotsRepository:
    return AccountSnapshotsRepository(db)


def contacts_repo(db: DbDep) -> ContactsRepository:
    return ContactsRepository(db)


def link_clicks_repo(db: DbDep) -> LinkClicksRepository:
    return LinkClicksRepository(db)


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
SnapshotsRepo = Annotated[AccountSnapshotsRepository, Depends(account_snapshots_repo)]
RulesRepo = Annotated[RulesRepository, Depends(rules_repo)]
LinkClicksRepo = Annotated[LinkClicksRepository, Depends(link_clicks_repo)]
ContactsRepo = Annotated[ContactsRepository, Depends(contacts_repo)]
CommentsRepo = Annotated[CommentsRepository, Depends(comments_repo)]
ExecutionsRepo = Annotated[ExecutionsRepository, Depends(executions_repo)]
WebhookEventsRepo = Annotated[WebhookEventsRepository, Depends(webhook_events_repo)]
AuditRepo = Annotated[AuditLogsRepository, Depends(audit_repo)]


async def current_user(
    request: Request, settings: SettingsDep, users: UsersRepo
) -> dict[str, Any]:
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
