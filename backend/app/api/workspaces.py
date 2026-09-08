"""Which workspaces this person may open.

A workspace is one user and the Instagram account they connected, addressed at
`/u/<slug>/`. Everyone has exactly one -- their own. Holding `workspaces.view`
widens this list to all of them, which is what puts a switcher under the logo for
an administrator and leaves everybody else's sidebar exactly as it was.

The connected account's username and avatar are included because the switcher
shows them; without them the dropdown would list email addresses, which is not
how anyone thinks about "which Instagram account am I looking at".
"""

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import (
    WORKSPACE_PERMISSION,
    AccountsRepo,
    CurrentGrant,
    CurrentUser,
    UsersRepo,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


class WorkspaceSummary(BaseModel):
    slug: str
    email: str
    is_own: bool
    username: str | None = None
    profile_picture_url: str | None = None
    connected: bool = False


@router.get("", response_model=list[WorkspaceSummary])
async def list_workspaces(
    user: CurrentUser, grant: CurrentGrant, users: UsersRepo, accounts: AccountsRepo
) -> list[WorkspaceSummary]:
    """Your own workspace, plus everyone else's if you may see them.

    Deliberately not gated by a route permission: like `GET /api/auth/me`, this
    is what the frontend gates *on*, and requiring a permission to learn which
    workspaces you may enter would be circular. The permission decides the
    *width* of the answer, not whether you get one -- and the narrow answer is
    only ever your own workspace, which you already know about.
    """
    own_slug = str(user.get("slug") or "")
    owners: list[dict[str, Any]] = [user]

    if WORKSPACE_PERMISSION in grant.permissions:
        # Sorted by email so the dropdown holds still between renders; a switcher
        # that reorders itself is a switcher people mis-click.
        everyone = await users.list(sort_field="email", descending=False, limit=200)
        owners = [u for u in everyone if str(u.get("slug") or "")]
        if not any(str(u["_id"]) == str(user["_id"]) for u in owners):
            owners.insert(0, user)

    summaries: list[WorkspaceSummary] = []
    for owner in owners:
        slug = str(owner.get("slug") or "")
        if not slug:
            # A user still awaiting `backfill_missing_slugs` has no URL to offer.
            continue
        found = await accounts.live_for_user(owner["_id"])
        account = found[0] if found else None
        summaries.append(
            WorkspaceSummary(
                slug=slug,
                email=str(owner.get("email", "")),
                is_own=slug == own_slug,
                username=account.get("username") if account else None,
                profile_picture_url=account.get("profile_picture_url") if account else None,
                connected=account is not None,
            )
        )
    return summaries
