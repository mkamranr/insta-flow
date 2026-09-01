"""Instagram account connection: OAuth, webhook subscription, lifecycle.

Access tokens are encrypted before storage and never appear in a response
(§50, §51). The only shape returned is `InstagramAccountPublic`, which has no
token field at all.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app import queue
from app.api.deps import AccountsRepo, AuditRepo, CurrentUser, SettingsDep
from app.db.repositories import to_object_id, utcnow
from app.errors import MetaAPIError, NotFoundError, ValidationError
from app.models.instagram import InstagramAccountPublic
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


def _object_id(value: str, field: str) -> Any:
    try:
        return to_object_id(value)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid id.") from exc


def _public(account: dict[str, Any]) -> InstagramAccountPublic:
    return InstagramAccountPublic.from_document(account, token_expired=is_expired(account))


async def _load(accounts: Any, account_id: str, user: dict[str, Any]) -> dict[str, Any]:
    """Load an account the signed-in user owns.

    A missing account and someone else's account both raise NotFoundError, so the
    response cannot be used to discover which ids exist.
    """
    account = await accounts.get(_object_id(account_id, "account_id"))
    if account is None or account.get("user_id") != user["_id"]:
        raise NotFoundError("Instagram account not found.")
    return account


def _access_token(account: dict[str, Any], settings: Any) -> str:
    return decrypt(account.get("access_token_encrypted", ""), settings.encryption_key)


# --- OAuth ------------------------------------------------------------------


@auth_router.get("/start")
async def start(user: CurrentUser, settings: SettingsDep) -> dict[str, str]:
    """Return the Meta consent URL plus a single-use state.

    Returns JSON rather than redirecting so the SPA controls navigation and the
    session cookie is not involved in a cross-site redirect.
    """
    if not settings.meta_app_id or not settings.meta_app_secret:
        raise ValidationError("Meta app credentials are not configured. See docs/meta-api.md.")

    state = await oauth_state.issue(queue.get_pool(), settings.session_secret, str(user["_id"]))
    return {
        "authorize_url": meta_auth.build_authorize_url(settings, state),
        "redirect_uri": settings.oauth_redirect_uri,
    }


@auth_router.get("/callback")
async def callback(
    request: Request, settings: SettingsDep, accounts: AccountsRepo, audit: AuditRepo
) -> RedirectResponse:
    """Meta redirects the browser here after consent (§3.1-§3.3).

    This route is unauthenticated by necessity — Meta drives the redirect — so
    the signed single-use state is the only thing binding it to a real session.
    """
    params = request.query_params
    dashboard = f"{settings.public_base_url.rstrip('/')}/instagram"

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
        short = await meta_auth.exchange_code(settings, code)
        long_lived = await meta_auth.exchange_for_long_lived(settings, short.access_token)
        profile = await build_client(settings).get_account(long_lived.access_token)
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
            "permissions": list(short.permissions) or list(meta_auth.SCOPES),
            "status": "connected",
            "status_error": None,
            "updated_at": utcnow(),
            **token_fields(long_lived.access_token, long_lived.expires_at, settings.encryption_key),
        },
    )

    await audit.record(
        user_id=to_object_id(user_id),
        action="instagram.connected",
        entity_type="instagram_account",
        entity_id=account["_id"] if account else None,
        changes={"username": profile.username},
    )
    logger.info("instagram_connected", extra={"username": profile.username})
    return RedirectResponse(f"{dashboard}?connected=1", 303)


async def _signed_request_user(request: Request, settings: Any) -> str | None:
    """Verify Meta's signed_request and return the Instagram-scoped user id."""
    try:
        form = await request.form()
    except Exception:
        return None
    payload = signed_request.parse(str(form.get("signed_request", "")), settings.meta_app_secret)
    return str(payload.get("user_id", "")) if payload else None


@auth_router.post("/deauthorize")
async def deauthorize(
    request: Request, settings: SettingsDep, accounts: AccountsRepo
) -> dict[str, str]:
    """Meta calls this when someone removes the app from their Instagram account.

    The signed_request is verified before acting: this URL is public, so an
    unverified payload would let anyone disconnect an account by POSTing to it.
    """
    user_id = await _signed_request_user(request, settings)
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
        "<body style=\"font:16px/1.6 system-ui;max-width:34rem;margin:3rem auto;padding:0 1rem\">"
        "<h1 style=font-size:1.3rem>Deauthorize callback</h1>"
        "<p>This endpoint exists for Meta, not for people. Meta sends a signed "
        "POST here when someone removes this app from their Instagram account, "
        "and it disconnects that account.</p>"
        "<p>Looking for something? See <a href=/privacy>Privacy</a> or "
        "<a href=/data-deletion>Data Deletion</a>.</p></body>"
    )


@auth_router.post("/data-deletion")
async def data_deletion(
    request: Request, settings: SettingsDep, accounts: AccountsRepo
) -> dict[str, Any]:
    """Meta's data deletion callback.

    Same verification as deauthorize. The response shape (a status URL and a
    confirmation code) is what Meta expects so a user can check on the request.
    """
    user_id = await _signed_request_user(request, settings)
    status_url = f"{settings.public_base_url.rstrip('/')}/data-deletion"

    if user_id is None:
        logger.warning("data_deletion_unverified")
        return {"url": status_url, "confirmation_code": "unverified"}

    account = await accounts.get_by_instagram_user_id(user_id)
    if account is not None:
        await accounts.delete(account["_id"])
        logger.info("account_data_deleted", extra={"instagram_user_id": user_id})

    return {"url": status_url, "confirmation_code": f"ig-{user_id}"}


@auth_router.get("/data-deletion", response_class=HTMLResponse)
async def data_deletion_info(settings: SettingsDep) -> HTMLResponse:
    """As with deauthorize: a person here should get an explanation, not a 405."""
    return HTMLResponse(
        "<!doctype html><meta charset=utf-8><title>Data deletion callback</title>"
        "<body style=\"font:16px/1.6 system-ui;max-width:34rem;margin:3rem auto;padding:0 1rem\">"
        "<h1 style=font-size:1.3rem>Data deletion callback</h1>"
        "<p>This endpoint exists for Meta, not for people. Meta sends a signed "
        "POST here to request deletion of an account's data.</p>"
        "<p>If you want your data removed, the instructions are at "
        "<a href=/data-deletion>Data Deletion</a>.</p></body>"
    )


# --- account management -----------------------------------------------------


@router.get("/accounts", response_model=list[InstagramAccountPublic])
async def list_accounts(user: CurrentUser, accounts: AccountsRepo) -> list[InstagramAccountPublic]:
    return [_public(a) for a in await accounts.list({"user_id": user["_id"]})]


@router.get("/{account_id}", response_model=InstagramAccountPublic)
async def get_account(
    account_id: str, user: CurrentUser, accounts: AccountsRepo
) -> InstagramAccountPublic:
    return _public(await _load(accounts, account_id, user))


@router.post("/{account_id}/subscribe", response_model=InstagramAccountPublic)
async def subscribe(
    account_id: str,
    user: CurrentUser,
    settings: SettingsDep,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> InstagramAccountPublic:
    """Subscribe the account to the `comments` webhook field (§5.3)."""
    account = await _load(accounts, account_id, user)
    client = build_client(settings)

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


@router.get("/{account_id}/subscription")
async def subscription_status(
    account_id: str, user: CurrentUser, settings: SettingsDep, accounts: AccountsRepo
) -> dict[str, Any]:
    """Ask Meta what this account is actually subscribed to.

    Read from Meta rather than trusting our stored flag: a subscription can be
    removed in the app dashboard without InstaFlow ever hearing about it.
    """
    account = await _load(accounts, account_id, user)
    try:
        fields = await build_client(settings).get_subscribed_fields(
            _access_token(account, settings), account["instagram_user_id"]
        )
    except MetaAPIError as exc:
        return {"subscribed": False, "fields": [], "error": str(exc)}

    await accounts.update(
        account["_id"],
        {"subscribed_fields": fields, "webhook_subscribed": "comments" in fields},
    )
    return {"subscribed": "comments" in fields, "fields": fields}


@router.post("/{account_id}/refresh", response_model=InstagramAccountPublic)
async def refresh_token(
    account_id: str,
    user: CurrentUser,
    settings: SettingsDep,
    accounts: AccountsRepo,
    audit: AuditRepo,
) -> InstagramAccountPublic:
    """Refresh the long-lived token (§3.4)."""
    account = await _load(accounts, account_id, user)
    try:
        bundle = await meta_auth.refresh_long_lived(settings, _access_token(account, settings))
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


@router.get("/{account_id}/token-status")
async def token_status(
    account_id: str, user: CurrentUser, accounts: AccountsRepo
) -> dict[str, Any]:
    account = await _load(accounts, account_id, user)
    return {
        "expires_at": account.get("token_expires_at"),
        "expired": is_expired(account),
        "needs_refresh": needs_refresh(account),
    }


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect(
    account_id: str, user: CurrentUser, accounts: AccountsRepo, audit: AuditRepo
) -> None:
    account = await _load(accounts, account_id, user)
    await accounts.delete(account["_id"])
    await audit.record(
        user_id=user["_id"],
        action="instagram.disconnected",
        entity_type="instagram_account",
        entity_id=account["_id"],
        changes={"username": account.get("username")},
    )
    logger.info("instagram_disconnected", extra={"account_id": account_id})
