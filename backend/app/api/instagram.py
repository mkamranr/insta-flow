"""Instagram account connection: OAuth, webhook subscription, lifecycle.

Access tokens are encrypted before storage and never appear in a response
(§50, §51). The only shape returned is `InstagramAccountPublic`, which has no
token field at all.
"""

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app import queue
from app.api.deps import (
    AccountsRepo,
    AuditRepo,
    CurrentUser,
    CurrentWorkspace,
    ExecutionsRepo,
    PublicCredentials,
    SettingsDep,
    Workspace,
    WorkspaceCredentials,
    require,
)
from app.constants import PRIVATE_REPLY_WINDOW_DAYS
from app.db.repositories import to_object_id, utcnow
from app.errors import MetaAPIError, NotFoundError, ValidationError
from app.models.instagram import (
    MAX_PAUSE_DAYS,
    AccountPauseStatus,
    InstagramAccountPublic,
    PauseBacklogAction,
    PauseRequest,
)
from app.services.campaigns.policy import pause_active
from app.services.meta import auth as meta_auth
from app.services.meta import signed_request
from app.services.meta.client import DEFAULT_WEBHOOK_FIELDS
from app.services.meta.factory import build_client
from app.services.meta.tokens import is_expired, needs_refresh, token_fields
from app.services.security import oauth_state
from app.services.security.encryption import decrypt

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/api/auth/instagram", tags=["instagram"])
router = APIRouter(prefix="/api/instagram", tags=["instagram"])

# Named rather than inlined, mirroring webhooks.py: the worker and the route
# have to agree on the string, and a typo here fails only at consumption.
DELIVER_BACKLOG_TASK = "deliver_paused_backlog"


def _object_id(value: str, field: str) -> Any:
    try:
        return to_object_id(value)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid id.") from exc


def _public(account: dict[str, Any]) -> InstagramAccountPublic:
    return InstagramAccountPublic.from_document(
        account, token_expired=is_expired(account), automation_paused=pause_active(account)
    )


async def _load(accounts: Any, account_id: str, workspace: Workspace) -> dict[str, Any]:
    """Load an account belonging to this workspace.

    A missing account and an account outside this workspace both raise
    NotFoundError, so the response cannot be used to discover which ids exist.

    Scoped to the workspace rather than the caller: an administrator holding
    `workspaces.view` is legitimately looking at someone else's account, and
    `current_workspace` has already decided whether they may.
    """
    account = await accounts.get(_object_id(account_id, "account_id"))
    if account is None or account.get("user_id") != workspace.user_id:
        raise NotFoundError("Instagram account not found.")
    return account


def _access_token(account: dict[str, Any], settings: Any) -> str:
    return decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)


# --- OAuth ------------------------------------------------------------------


@auth_router.get("/start", dependencies=[Depends(require("instagram.manage"))])
async def start(
    workspace: CurrentWorkspace, creds: WorkspaceCredentials, settings: SettingsDep
) -> dict[str, str]:
    """Return the Meta consent URL plus a single-use state.

    Returns JSON rather than redirecting so the SPA controls navigation and the
    session cookie is not involved in a cross-site redirect.

    The state carries the **workspace owner's** id, not the caller's: it is what
    the callback stores the account against, so connecting while viewing another
    workspace must file the account under that workspace, not under whoever
    happened to click.
    """
    if not creds.configured:
        raise ValidationError(
            "This workspace has no Meta app configured. Add an App ID and App Secret "
            "in Settings, or see docs/meta-api.md."
        )

    state = await oauth_state.issue(
        queue.get_pool(), settings.session_secret, str(workspace.user_id)
    )
    return {
        "authorize_url": meta_auth.build_authorize_url(creds, state),
        "redirect_uri": creds.redirect_uri,
    }


@auth_router.get("/callback")
async def callback(
    request: Request,
    settings: SettingsDep,
    creds: PublicCredentials,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> RedirectResponse:
    """Meta redirects the browser here after consent (§3.1-§3.3).

    This route is unauthenticated by necessity — Meta drives the redirect — so
    the signed single-use state is the only thing binding it to a real session.

    Which app's secret completes the exchange comes from the `/u/<slug>/` segment
    in the URL Meta was configured with, and every redirect back lands inside
    that same workspace.
    """
    params = request.query_params
    dashboard = f"{creds.base_url}/instagram"

    if error := params.get("error"):
        logger.warning("oauth_denied", extra={"error": error})
        return RedirectResponse(f"{dashboard}?error={params.get('error_reason', error)}", 303)

    user_id = await oauth_state.consume(
        queue.get_pool(), settings.session_secret, params.get("state", "")
    )
    if user_id is None:
        logger.warning("oauth_state_rejected")
        return RedirectResponse(f"{dashboard}?error=invalid_state", 303)

    code = params.get("code", "")
    if not code:
        return RedirectResponse(f"{dashboard}?error=missing_code", 303)

    try:
        short = await meta_auth.exchange_code(creds, code)
        long_lived = await meta_auth.exchange_for_long_lived(creds, short.access_token)
        profile = await build_client(settings, creds).get_account(long_lived.access_token)
    except MetaAPIError as exc:
        logger.error("oauth_exchange_failed", extra={"error": str(exc)})
        return RedirectResponse(f"{dashboard}?error=exchange_failed", 303)

    instagram_user_id = profile.instagram_user_id or short.instagram_user_id
    if not instagram_user_id:
        return RedirectResponse(f"{dashboard}?error=no_account_id", 303)

    # Upsert on the unique instagram_user_id so reconnecting updates in place.
    account = await accounts.upsert(
        instagram_user_id,
        {
            "user_id": to_object_id(user_id),
            "instagram_user_id": instagram_user_id,
            "username": profile.username,
            "display_name": profile.name,
            "account_type": profile.account_type,
            # Meta's CDN URLs expire, so this is refreshed by the daily job too.
            "profile_picture_url": profile.profile_picture_url,
            "followers_count": profile.followers_count,
            "follows_count": profile.follows_count,
            "media_count": profile.media_count,
            "permissions": list(short.permissions) or list(meta_auth.SCOPES),
            "status": "connected",
            "status_error": None,
            "updated_at": utcnow(),
            **token_fields(long_lived.access_token, long_lived.expires_at, settings.encryption_key),
        },
    )

    # A workspace's first account becomes its default, so an unqualified request
    # has an answer. A second one changes nothing -- connecting an account must
    # never silently move what the rest of the dashboard is pointing at.
    await accounts.ensure_default(to_object_id(user_id))

    await audit.record(
        user_id=to_object_id(user_id),
        action="instagram.connected",
        entity_type="instagram_account",
        entity_id=account["_id"] if account else None,
        changes={"username": profile.username},
    )
    logger.info("instagram_connected", extra={"username": profile.username})
    return RedirectResponse(f"{dashboard}?connected=1", 303)


async def _signed_request_user(request: Request, app_secret: str) -> str | None:
    """Verify Meta's signed_request and return the Instagram-scoped user id.

    Takes the secret rather than `Settings`: the signature is made with the
    calling app's secret, and with per-workspace apps only the URL says which one
    that is. An empty secret makes `parse` fail, which is the right answer for a
    slug nothing recognises.
    """
    try:
        form = await request.form()
    except Exception:
        return None
    payload = signed_request.parse(str(form.get("signed_request", "")), app_secret)
    return str(payload.get("user_id", "")) if payload else None


@auth_router.post("/deauthorize")
async def deauthorize(
    request: Request, creds: PublicCredentials, accounts: AccountsRepo
) -> dict[str, str]:
    """Meta calls this when someone removes the app from their Instagram account.

    The signed_request is verified before acting: this URL is public, so an
    unverified payload would let anyone disconnect an account by POSTing to it.
    """
    user_id = await _signed_request_user(request, creds.app_secret)
    if user_id is None:
        # Acknowledge rather than error. Meta retries failures, and a payload we
        # cannot verify will never verify on a retry either.
        logger.warning("deauthorize_unverified")
        return {"status": "ignored"}

    account = await accounts.get_by_instagram_user_id(user_id)
    if account is None:
        logger.info("deauthorize_for_unknown_account")
        return {"status": "acknowledged"}

    # Drop the token rather than merely flagging the account: consent has been
    # withdrawn, so we must stop being able to act as them.
    await accounts.update(
        account["_id"],
        {
            "status": "disconnected",
            "status_error": "Access was revoked from Instagram.",
            "access_token_encrypted": "",
            "webhook_subscribed": False,
        },
    )
    logger.info("account_deauthorized", extra={"account_id": str(account["_id"])})
    return {"status": "acknowledged"}


@auth_router.get("/deauthorize", response_class=HTMLResponse)
async def deauthorize_info() -> HTMLResponse:
    """A human landing here should see an explanation, not raw JSON."""
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Deauthorize callback</title>"
        '<body style="font:16px/1.6 system-ui;max-width:34rem;margin:3rem auto;padding:0 1rem">'
        "<h1 style=font-size:1.3rem>Deauthorize callback</h1>"
        "<p>This endpoint exists for Meta, not for people. Meta sends a signed "
        "POST here when someone removes this app from their Instagram account, "
        "and it disconnects that account.</p>"
        "<p>Looking for something? See <a href=/privacy>Privacy</a> or "
        "<a href=/data-deletion>Data Deletion</a>.</p></body>"
    )


@auth_router.post("/data-deletion")
async def data_deletion(
    request: Request, creds: PublicCredentials, accounts: AccountsRepo
) -> dict[str, Any]:
    """Meta's data deletion callback.

    Same verification as deauthorize. The response shape (a status URL and a
    confirmation code) is what Meta expects so a user can check on the request.
    """
    user_id = await _signed_request_user(request, creds.app_secret)
    status_url = creds.data_deletion_url

    if user_id is None:
        logger.warning("data_deletion_unverified")
        return {"url": status_url, "confirmation_code": "unverified"}

    account = await accounts.get_by_instagram_user_id(user_id)
    if account is not None:
        await accounts.delete(account["_id"])
        logger.info("account_data_deleted", extra={"instagram_user_id": user_id})

    return {"url": status_url, "confirmation_code": f"ig-{user_id}"}


@auth_router.get("/data-deletion", response_class=HTMLResponse)
async def data_deletion_info() -> HTMLResponse:
    """As with deauthorize: a person here should get an explanation, not a 405."""
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Data deletion callback</title>"
        '<body style="font:16px/1.6 system-ui;max-width:34rem;margin:3rem auto;padding:0 1rem">'
        "<h1 style=font-size:1.3rem>Data deletion callback</h1>"
        "<p>This endpoint exists for Meta, not for people. Meta sends a signed "
        "POST here to request deletion of an account's data.</p>"
        "<p>If you want your data removed, the instructions are at "
        "<a href=/data-deletion>Data Deletion</a>.</p></body>"
    )


# --- account management -----------------------------------------------------


@router.get(
    "/accounts",
    response_model=list[InstagramAccountPublic],
    dependencies=[Depends(require("instagram.view"))],
)
async def list_accounts(
    workspace: CurrentWorkspace, accounts: AccountsRepo
) -> list[InstagramAccountPublic]:
    return [_public(a) for a in await accounts.list({"user_id": workspace.user_id})]


@router.get(
    "/{account_id}",
    response_model=InstagramAccountPublic,
    dependencies=[Depends(require("instagram.view"))],
)
async def get_account(
    account_id: str, workspace: CurrentWorkspace, accounts: AccountsRepo
) -> InstagramAccountPublic:
    return _public(await _load(accounts, account_id, workspace))


@router.post(
    "/{account_id}/subscribe",
    response_model=InstagramAccountPublic,
    dependencies=[Depends(require("instagram.manage"))],
)
async def subscribe(
    account_id: str,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    creds: WorkspaceCredentials,
    settings: SettingsDep,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> InstagramAccountPublic:
    """Subscribe the account to the `comments` webhook field (§5.3)."""
    account = await _load(accounts, account_id, workspace)
    client = build_client(settings, creds)

    try:
        ok = await client.subscribe_webhooks(
            _access_token(account, settings),
            account["instagram_user_id"],
            DEFAULT_WEBHOOK_FIELDS,
        )
        fields = await client.get_subscribed_fields(
            _access_token(account, settings), account["instagram_user_id"]
        )
    except MetaAPIError as exc:
        await accounts.update(
            account["_id"], {"webhook_subscribed": False, "status_error": str(exc)}
        )
        logger.error("webhook_subscribe_failed", extra={"error": str(exc)})
        raise

    updated = await accounts.update(
        account["_id"],
        {
            "webhook_subscribed": bool(ok),
            "subscribed_fields": fields,
            "status_error": None,
            "webhook_subscribed_at": utcnow(),
        },
    )
    await audit.record(
        user_id=user["_id"],
        action="instagram.webhook_subscribed",
        entity_type="instagram_account",
        entity_id=account["_id"],
        changes={"fields": fields},
    )
    return _public(updated or account)


@router.get("/{account_id}/subscription", dependencies=[Depends(require("instagram.view"))])
async def subscription_status(
    account_id: str,
    workspace: CurrentWorkspace,
    creds: WorkspaceCredentials,
    settings: SettingsDep,
    accounts: AccountsRepo,
) -> dict[str, Any]:
    """Ask Meta what this account is actually subscribed to.

    Read from Meta rather than trusting our stored flag: a subscription can be
    removed in the app dashboard without Social Flow ever hearing about it.
    """
    account = await _load(accounts, account_id, workspace)
    try:
        fields = await build_client(settings, creds).get_subscribed_fields(
            _access_token(account, settings), account["instagram_user_id"]
        )
    except MetaAPIError as exc:
        return {"subscribed": False, "fields": [], "error": str(exc)}

    await accounts.update(
        account["_id"],
        {"subscribed_fields": fields, "webhook_subscribed": "comments" in fields},
    )
    return {"subscribed": "comments" in fields, "fields": fields}


@router.post(
    "/{account_id}/refresh",
    response_model=InstagramAccountPublic,
    dependencies=[Depends(require("instagram.manage"))],
)
async def refresh_token(
    account_id: str,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    settings: SettingsDep,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> InstagramAccountPublic:
    """Refresh the long-lived token (§3.4).

    No workspace credentials needed: `ig_refresh_token` authenticates with the
    token itself, so a token keeps refreshing whichever app minted it.
    """
    account = await _load(accounts, account_id, workspace)
    try:
        bundle = await meta_auth.refresh_long_lived(_access_token(account, settings))
    except MetaAPIError as exc:
        await accounts.update(account["_id"], {"status": "token_error", "status_error": str(exc)})
        logger.error("token_refresh_failed", extra={"account_id": account_id})
        raise

    updated = await accounts.update(
        account["_id"],
        {
            "status": "connected",
            "status_error": None,
            **token_fields(bundle.access_token, bundle.expires_at, settings.encryption_key),
        },
    )
    await audit.record(
        user_id=user["_id"],
        action="instagram.token_refreshed",
        entity_type="instagram_account",
        entity_id=account["_id"],
    )
    logger.info("token_refreshed", extra={"account_id": account_id})
    return _public(updated or account)


@router.get("/{account_id}/token-status", dependencies=[Depends(require("instagram.view"))])
async def token_status(
    account_id: str, workspace: CurrentWorkspace, accounts: AccountsRepo
) -> dict[str, Any]:
    account = await _load(accounts, account_id, workspace)
    return {
        "expires_at": account.get("token_expires_at"),
        "expired": is_expired(account),
        "needs_refresh": needs_refresh(account),
    }


@router.post(
    "/{account_id}/default",
    response_model=InstagramAccountPublic,
    dependencies=[Depends(require("instagram.manage"))],
)
async def make_default(
    account_id: str,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> InstagramAccountPublic:
    """Choose which account an unqualified request acts on.

    Gated on `instagram.manage` rather than a module of its own: it already
    covers connecting and disconnecting, both strictly larger than changing which
    account the dashboard opens on, and the effect is confined to one workspace
    and instantly reversible. A new `Module` would also have to be excluded from
    Viewer by hand -- `permissions.py` builds Viewer by comprehension over
    `MODULES` -- for no gain.
    """
    account = await _load(accounts, account_id, workspace)
    await accounts.set_default(workspace.user_id, account["_id"])
    await audit.record(
        user_id=user["_id"],
        action="instagram.default_changed",
        entity_type="instagram_account",
        entity_id=account["_id"],
        changes={"username": account.get("username")},
    )
    logger.info("instagram_default_changed", extra={"account_id": account_id})
    updated = await accounts.get(account["_id"])
    return _public(updated or account)


@router.delete(
    "/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("instagram.manage"))],
)
async def disconnect(
    account_id: str,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> None:
    account = await _load(accounts, account_id, workspace)
    # Marked, not deleted. The row is keyed on `instagram_user_id`, so keeping it
    # means reconnecting the same account reuses it and its posts and automations
    # come back with it -- `media` and `rules` are keyed on this `_id`, and
    # deleting the row orphaned all of them with nothing able to remap them
    # afterwards. This is also what Meta's own deauthorize callback already does
    # above; the two paths disagreeing was the bug.
    await accounts.set_status(account["_id"], "disconnected")
    await audit.record(
        user_id=user["_id"],
        action="instagram.disconnected",
        entity_type="instagram_account",
        entity_id=account["_id"],
        changes={"username": account.get("username")},
    )
    logger.info("instagram_disconnected", extra={"account_id": account_id})


# --- Auto DM pause ----------------------------------------------------------
#
# Every route here carries a second path segment, so none of them is ambiguous
# with `GET /{account_id}` above. A workspace-level `/api/instagram/pause` would
# not be: FastAPI matches in registration order, so it would be swallowed and
# 422 on `to_object_id("pause")`.


@router.post(
    "/{account_id}/pause",
    response_model=InstagramAccountPublic,
    dependencies=[Depends(require("instagram.manage"))],
)
async def pause_automation(
    account_id: str,
    payload: PauseRequest,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> InstagramAccountPublic:
    """Hold every automated send for this account until a chosen instant.

    Gated on `instagram.manage` for the reason spelled out at `make_default`: it
    already covers connecting and disconnecting, both strictly larger than
    silencing an account for an afternoon, and a new `Module` would have to be
    excluded from Viewer by hand for no gain.

    Range-checked here rather than in the model so a refusal comes back in the
    app's error shape rather than pydantic's.
    """
    account = await _load(accounts, account_id, workspace)
    now = utcnow()
    if payload.until <= now:
        raise ValidationError("Choose a time in the future.")
    if payload.until - now > timedelta(days=MAX_PAUSE_DAYS):
        raise ValidationError(f"A pause can last at most {MAX_PAUSE_DAYS} days.")

    updated = await accounts.pause_automation(account["_id"], until=payload.until, now=now)
    await audit.record(
        user_id=user["_id"],
        action="instagram.paused",
        entity_type="instagram_account",
        entity_id=account["_id"],
        changes={"username": account.get("username"), "until": payload.until.isoformat()},
    )
    logger.info("automation_paused", extra={"account_id": account_id})
    return _public(updated or account)


@router.delete(
    "/{account_id}/pause",
    response_model=InstagramAccountPublic,
    dependencies=[Depends(require("instagram.manage"))],
)
async def resume_automation(
    account_id: str,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> InstagramAccountPublic:
    """Start sending again, now.

    Idempotent on an account that is not paused. Deliberately does NOT settle the
    backlog: what to do with the comments the pause held is a separate decision,
    and it is still owed after this call.
    """
    account = await _load(accounts, account_id, workspace)
    updated = await accounts.resume_automation(account["_id"], now=utcnow())
    await audit.record(
        user_id=user["_id"],
        action="instagram.resumed",
        entity_type="instagram_account",
        entity_id=account["_id"],
        changes={"username": account.get("username")},
    )
    logger.info("automation_resumed", extra={"account_id": account_id})
    return _public(updated or account)


@router.get("/{account_id}/pause", dependencies=[Depends(require("instagram.view"))])
async def pause_status(
    account_id: str,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
    executions: ExecutionsRepo,
) -> AccountPauseStatus:
    """The pause, and what it is holding.

    `held` and `deliverable` differ because a pause may run longer than Meta's
    7-day reply window, so part of a long backlog cannot be sent whatever the
    operator picks.

    Answers after the pause has lapsed, too -- nothing runs at expiry, so a pause
    nobody was present for still has its comments waiting here.
    """
    account = await _load(accounts, account_id, workspace)
    now = utcnow()
    counts = await executions.pause_backlog_counts(
        account["_id"],
        since=account.get("automation_backlog_since"),
        replayable_since=now - timedelta(days=PRIVATE_REPLY_WINDOW_DAYS),
    )
    return AccountPauseStatus(
        paused=pause_active(account, now),
        paused_until=account.get("automation_paused_until"),
        paused_at=account.get("automation_paused_at"),
        held=counts["held"],
        deliverable=counts["deliverable"],
    )


@router.post("/{account_id}/pause/backlog", dependencies=[Depends(require("instagram.manage"))])
async def act_on_pause_backlog(
    account_id: str,
    payload: PauseBacklogAction,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    accounts: AccountsRepo,
) -> dict[str, Any]:
    """Deliver or discard the comments the pause held. The only way to settle them."""
    account = await _load(accounts, account_id, workspace)

    if payload.action == "ignore":
        await accounts.close_pause_backlog(account["_id"], now=utcnow())
        logger.info("pause_backlog_ignored", extra={"account_id": account_id})
        return {"status": "ignored"}

    if pause_active(account):
        # Replaying into a live pause would just hold everything again.
        raise ValidationError("Resume sending before delivering the held comments.")

    # The window is advanced by the task, not here: if the queue were down, the
    # offer has to still be standing next time this page is opened.
    await queue.enqueue(DELIVER_BACKLOG_TASK, str(account["_id"]))
    logger.info("pause_backlog_delivery_queued", extra={"account_id": account_id})
    return {"status": "queued"}
