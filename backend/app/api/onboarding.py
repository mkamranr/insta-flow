"""The guided setup: content, progress, and whether it is finished.

**Ungated, like the dashboard index.** This is where a freshly invited person
lands the instant they choose a password, so it must never refuse them -- and
`progress` also feeds the "finish setup" banner in the layout, which has to be
correct for *every* role. That rules out gating it on `analytics.view` or
`instagram.view`, which is what `GET /api/analytics/summary` and
`GET /api/instagram/accounts` require: a narrow role gets 403 from those and
silently reads as "never connected", which for an invitee is exactly wrong.

Everything here is scoped to the caller: the progress read and write name
`user["_id"]`, and the content is the same static list for everybody. The steps
that need `instagram.manage` say so in place -- the frontend degrades that one
block rather than the whole page.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import (
    AccountsRepo,
    AppSettingsRepo,
    CurrentUser,
    CurrentWorkspace,
    MetaAppsRepo,
    SettingsDep,
    UsersRepo,
)
from app.errors import NotFoundError
from app.services.meta.credentials import origin_for, workspace_urls
from app.services.onboarding import REQUIRED_STEP_IDS, STEP_IDS, content

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


def _progress(user: dict[str, Any]) -> dict[str, Any]:
    saved = (user.get("onboarding") or {}).get("steps") or {}
    return {
        "done": sorted(step_id for step_id, state in saved.items() if (state or {}).get("done")),
        "dismissed": bool((user.get("onboarding") or {}).get("dismissed")),
    }


async def _state(
    *,
    user: dict[str, Any],
    workspace: Any,
    accounts: Any,
    apps: Any,
) -> dict[str, Any]:
    """Progress plus the two facts the ticks and the banner derive from."""
    account = await accounts.default_for_user(workspace.user_id)
    app = await apps.for_user(workspace.user_id)
    subscribed = bool(account and account.get("webhook_subscribed"))
    fields = list((account or {}).get("subscribed_fields") or [])

    return {
        **_progress(user),
        "total": len(REQUIRED_STEP_IDS),
        "connected": account is not None,
        "meta_app_configured": bool(app and app.get("app_id")),
        # The account-level subscription only. Meta exposes no way to read the
        # app-level field toggle, so the step's own copy says so rather than
        # showing a green tick for something unverifiable.
        "webhooks_subscribed": subscribed and "comments" in fields,
    }


@router.get("/steps")
async def onboarding_steps(
    user: CurrentUser,
    workspace: CurrentWorkspace,
    settings: SettingsDep,
    store: AppSettingsRepo,
) -> dict[str, Any]:
    """The step list, plus the URLs this workspace must paste into Meta.

    The values are resolved here rather than embedded in the content module, so
    the walkthrough cannot drift from `urls_for` -- which is how the deauthorize
    callback went missing from the Settings page in the first place.
    """
    origin = origin_for(settings, await store.load())
    return {
        **content(),
        "urls": workspace_urls(origin, workspace.slug),
    }


@router.get("/progress")
async def onboarding_progress(
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    apps: MetaAppsRepo,
) -> dict[str, Any]:
    return await _state(user=user, workspace=workspace, accounts=accounts, apps=apps)


@router.put("/progress/{step_id}")
async def set_onboarding_progress(
    step_id: str,
    payload: dict[str, Any],
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    users: UsersRepo,
    accounts: AccountsRepo,
    apps: MetaAppsRepo,
) -> dict[str, Any]:
    """Tick or untick one step.

    `step_id` is validated against the content module, so a stale bookmark or a
    renamed step cannot write a key nothing will ever read again.
    """
    if step_id not in STEP_IDS:
        raise NotFoundError("No such setup step.")

    updated = await users.set_onboarding_step(user["_id"], step_id, bool(payload.get("done")))
    return await _state(user=updated or user, workspace=workspace, accounts=accounts, apps=apps)


@router.post("/dismiss")
async def dismiss_onboarding(
    user: CurrentUser,
    workspace: CurrentWorkspace,
    users: UsersRepo,
    accounts: AccountsRepo,
    apps: MetaAppsRepo,
) -> dict[str, Any]:
    """Hide the banner without finishing.

    Ticking every step does **not** do this, on purpose: a banner that vanished
    while nothing actually worked would be the worst of both. Only connecting an
    account, or asking explicitly here, clears it.
    """
    updated = await users.dismiss_onboarding(user["_id"])
    return await _state(user=updated or user, workspace=workspace, accounts=accounts, apps=apps)
