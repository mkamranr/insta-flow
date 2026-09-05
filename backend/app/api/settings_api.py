"""Operator-editable settings, in three scopes that must not be confused.

* **Server-wide** -- the public address and the MaxMind credentials. One value
  for the whole deployment, so changing it changes it for every workspace at
  once. Gated on `instance.*`, which no seeded role but Owner holds.
* **Per workspace** -- the Meta app. Two users configure two different ones and
  neither can see the other's. Gated on `instagram.manage`.
* **The disclosure line** -- `settings.*`, as before.

The split exists because those are genuinely different powers. Left on one
permission, "may edit the wording of an automated reply" also meant "may
re-point the server's public address", which is not a trade anyone chose.

The MaxMind license key and the Meta app secret are encrypted at rest with the
same key that protects Meta access tokens, and neither is ever returned — only
whether one is configured (§4).
"""

import logging
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import (
    AppSettingsRepo,
    AuditRepo,
    CurrentUser,
    CurrentWorkspace,
    MetaAppsRepo,
    SettingsDep,
    WorkspaceCredentials,
    require,
)
from app.errors import ValidationError
from app.services import origins
from app.services.geoip import GeoIpError, database_path, download, remote_build_date
from app.services.meta.credentials import ORIGIN_KEY, origin_for, resolve_for, workspace_urls
from app.services.security.encryption import EncryptionError, decrypt, encrypt

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


class Disclosure(BaseModel):
    enabled: bool = True
    text: str = Field(default="", max_length=200)


class GeoIpCredentials(BaseModel):
    account_id: str = Field(min_length=1, max_length=64)
    license_key: str = Field(min_length=1, max_length=256)


def _database_state(settings: Any) -> dict[str, Any]:
    path = database_path(Path(settings.geoip_dir))
    if not path.is_file():
        return {"present": False}
    stat = path.stat()
    return {
        "present": True,
        "size_bytes": stat.st_size,
        "updated_at": stat.st_mtime,
    }


DEFAULT_DISCLOSURE = "Automated reply."


@router.get("/disclosure", dependencies=[Depends(require("settings.view"))])
async def get_disclosure(user: CurrentUser, store: AppSettingsRepo) -> dict[str, Any]:
    """The automated-experience notice appended to the first automated message."""
    saved = await store.load()
    return {
        "enabled": bool(saved.get("automation_disclosure_enabled", True)),
        "text": saved.get("automation_disclosure") or DEFAULT_DISCLOSURE,
    }


@router.put("/disclosure", dependencies=[Depends(require("settings.edit"))])
async def save_disclosure(
    payload: Disclosure, user: CurrentUser, store: AppSettingsRepo
) -> dict[str, Any]:
    """Meta requires disclosing an automated experience to users in California
    and Germany. The obligation is the operator's, so the wording is theirs and
    it can be switched off -- but silence by default would be the wrong default.
    """
    await store.save(
        {
            "automation_disclosure_enabled": payload.enabled,
            "automation_disclosure": payload.text.strip() or DEFAULT_DISCLOSURE,
        }
    )
    return {"saved": True}


@router.get("/geoip", dependencies=[Depends(require("instance.view"))])
async def geoip_status(
    user: CurrentUser, store: AppSettingsRepo, settings: SettingsDep
) -> dict[str, Any]:
    """Whether geolocation is set up. Never includes the license key."""
    saved = await store.load()
    return {
        "configured": bool(saved.get("maxmind_license_key_encrypted")),
        # The account ID is an identifier, not a secret, so it can come back to
        # fill the field in. The key cannot.
        "account_id": saved.get("maxmind_account_id"),
        "database": _database_state(settings),
    }


@router.put("/geoip", dependencies=[Depends(require("instance.manage"))])
async def save_geoip(
    payload: GeoIpCredentials,
    request: Request,
    user: CurrentUser,
    store: AppSettingsRepo,
    settings: SettingsDep,
    audit: AuditRepo,
) -> dict[str, Any]:
    """Verify the credentials against MaxMind, then save and fetch the database.

    Verifying first means a typo is reported here rather than discovered later
    as a quietly empty geography report. The HEAD request used does not count
    against the daily download limit.
    """
    account_id = payload.account_id.strip()
    license_key = payload.license_key.strip()

    try:
        await remote_build_date(account_id, license_key)
    except GeoIpError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ValidationError(f"Could not reach MaxMind: {exc}") from exc

    await store.save(
        {
            "maxmind_account_id": account_id,
            "maxmind_license_key_encrypted": encrypt(license_key, settings.encryption_key),
        }
    )
    await audit.record(
        user_id=user["_id"],
        action="settings.geoip_saved",
        entity_type="settings",
        ip_address=request.client.host if request.client else None,
    )

    try:
        await download(account_id, license_key, Path(settings.geoip_dir))
    except Exception as exc:
        # Credentials are saved and valid; the download can be retried. Reporting
        # this as a save failure would be misleading.
        logger.warning("geoip_initial_download_failed", extra={"error": str(exc)})
        return {"saved": True, "database": _database_state(settings), "warning": str(exc)}

    return {"saved": True, "database": _database_state(settings)}


@router.post("/geoip/refresh", dependencies=[Depends(require("instance.manage"))])
async def refresh_geoip(
    user: CurrentUser, store: AppSettingsRepo, settings: SettingsDep
) -> dict[str, Any]:
    """Pull the current database now, rather than waiting for the weekly job."""
    saved = await store.load()
    account_id = saved.get("maxmind_account_id")
    blob = saved.get("maxmind_license_key_encrypted")
    if not account_id or not blob:
        raise ValidationError("Add your MaxMind account ID and license key first.")

    try:
        license_key = decrypt(blob, settings.encryption_key)
        await download(str(account_id), license_key, Path(settings.geoip_dir))
    except (GeoIpError, EncryptionError) as exc:
        raise ValidationError(str(exc)) from exc

    return {"database": _database_state(settings)}


@router.delete("/geoip", dependencies=[Depends(require("instance.manage"))])
async def clear_geoip(
    user: CurrentUser, store: AppSettingsRepo, settings: SettingsDep
) -> dict[str, Any]:
    """Forget the credentials. Existing rows keep the locations already derived."""
    await store.save({"maxmind_account_id": None, "maxmind_license_key_encrypted": None})
    database_path(Path(settings.geoip_dir)).unlink(missing_ok=True)
    return {"configured": False, "database": _database_state(settings)}


# --- the instance origin ----------------------------------------------------


class InstanceOrigin(BaseModel):
    origin: str = Field(min_length=1, max_length=255)


def _clean_origin(raw: str) -> str:
    """Validate a bare origin: scheme and host, nothing after them.

    The rule itself lives in `services/origins.py`, because `PUBLIC_BASE_URL`
    has to enforce the identical thing and previously did not -- which is how a
    scheme-less origin reached a live deployment and silently disabled click
    tracking. Here it is a 422; there it fails startup.
    """
    try:
        return origins.normalise(raw)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


@router.get("/instance", dependencies=[Depends(require("instance.view"))])
async def get_instance(
    user: CurrentUser, store: AppSettingsRepo, settings: SettingsDep
) -> dict[str, Any]:
    saved = await store.load()
    return {
        "origin": origin_for(settings, saved),
        # Which of the two won, so the page can say whether editing here has any
        # effect or whether PUBLIC_BASE_URL is still supplying the value.
        "source": "settings" if saved.get(ORIGIN_KEY) else "env",
        "env_value": settings.public_base_url,
        # Reported on every read, not only when saving. A non-HTTPS origin turns
        # click tracking and file attachments off, and the only symptom is a
        # click-through rate of zero -- which looks exactly like nobody clicking,
        # so the page has to say it where somebody investigating that zero will
        # actually be looking.
        "trackable": origins.is_trackable(origin_for(settings, saved)),
    }


@router.put("/instance", dependencies=[Depends(require("instance.manage"))])
async def save_instance(
    payload: InstanceOrigin,
    request: Request,
    user: CurrentUser,
    store: AppSettingsRepo,
    audit: AuditRepo,
) -> dict[str, Any]:
    """Set the public origin every workspace URL is derived from.

    Changing this changes nothing at Meta. Each workspace's Meta app has the old
    URLs registered against it and will keep calling them until somebody edits
    them by hand, so the response says so rather than letting the operator assume
    a save was enough.
    """
    origin = _clean_origin(payload.origin)
    await store.save({ORIGIN_KEY: origin})
    await audit.record(
        user_id=user["_id"],
        action="settings.instance_origin_saved",
        entity_type="settings",
        changes={"origin": origin},
        ip_address=request.client.host if request.client else None,
    )
    warning = None
    if not origin.startswith("https://"):
        # Meta rejects a non-HTTPS web_url button, and `tracked_buttons` silently
        # stops rewriting links when the origin is not HTTPS -- click-through
        # simply reads zero forever, with nothing to indicate why.
        warning = "Meta requires HTTPS. Link click tracking stays off until this is https://."
    return {
        "saved": True,
        "origin": origin,
        "warning": warning,
        "note": (
            "Meta is not updated automatically. Re-paste the URLs below into each "
            "Meta app that was configured with the old origin."
        ),
    }


# --- this workspace's Meta app ----------------------------------------------


class MetaAppCredentials(BaseModel):
    app_id: str = Field(min_length=1, max_length=64)
    # Optional so the App ID can be corrected without retyping the secret. An
    # empty string means "leave the stored one alone", never "clear it" --
    # clearing is what DELETE is for.
    app_secret: str = Field(default="", max_length=256)


def _meta_app_state(
    app: dict[str, Any] | None, creds: Any, workspace: Any, origin: str
) -> dict[str, Any]:
    """What the Settings card needs, with two distinct sets of URLs.

    `urls` answers "what do I register in my own Meta app?" and is therefore
    always workspace-prefixed. `active_urls` answers "what is in force right
    now?" -- which differs only while a workspace is still riding on the
    instance-wide app from `.env`.

    They were one field to begin with, showing the active set. That made the
    card useless for the only job it has: you could not see the URLs to register
    until you had already saved the app that uses them.
    """
    return {
        "configured": creds.configured,
        "source": creds.source,
        "app_id": creds.app_id,
        "secret_set": bool(creds.app_secret),
        # Returned in full, unlike the app secret. This is a deliberate, narrow
        # exception to "secrets never reach the frontend" (§4): the operator has
        # to type this value into Meta's dashboard and re-check it whenever a
        # handshake fails, and a write-only field they cannot verify turns every
        # mismatch into a regenerate-and-resubscribe cycle. It is gated on
        # `instagram.manage`, scoped to one workspace, listed in the log
        # redaction keys, and already redacted out of Caddy's access log. Do not
        # "fix" this to a boolean without changing the workflow that needs it.
        "verify_token": creds.verify_token,
        "urls": workspace_urls(origin, workspace.slug),
        "active_urls": creds.public_urls(),
        "workspace": {"slug": workspace.slug, "is_own": workspace.is_own},
        # True when this workspace is riding on META_* from the environment. The
        # page uses it to explain why the URLs shown have no /u/<slug>/ segment.
        "using_instance_app": creds.source == "instance",
        "has_own_row": app is not None,
    }


@router.get("/meta-app", dependencies=[Depends(require("instagram.manage"))])
async def get_meta_app(
    workspace: CurrentWorkspace,
    creds: WorkspaceCredentials,
    apps: MetaAppsRepo,
    store: AppSettingsRepo,
    settings: SettingsDep,
) -> dict[str, Any]:
    """This workspace's Meta app, and the URLs it must be configured with."""
    origin = origin_for(settings, await store.load())
    return _meta_app_state(await apps.for_user(workspace.user_id), creds, workspace, origin)


@router.put("/meta-app", dependencies=[Depends(require("instagram.manage"))])
async def save_meta_app(
    payload: MetaAppCredentials,
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    apps: MetaAppsRepo,
    store: AppSettingsRepo,
    settings: SettingsDep,
    audit: AuditRepo,
) -> dict[str, Any]:
    """Point this workspace at its own Meta app.

    Nothing here is verified against Meta. There is no cheap call that proves an
    App ID and Secret belong together without also completing an OAuth round
    trip, and inventing one would mean guessing at an endpoint -- so the honest
    check is the one the operator performs next: the webhook handshake.
    """
    app_id = payload.app_id.strip()
    secret = payload.app_secret.strip()

    existing = await apps.for_user(workspace.user_id)
    if not secret and not (existing or {}).get("app_secret_encrypted"):
        raise ValidationError("An App Secret is required the first time you save an app.")

    values: dict[str, Any] = {"app_id": app_id}
    if secret:
        values["app_secret_encrypted"] = encrypt(secret, settings.encryption_key)
    if not (existing or {}).get("verify_token_encrypted"):
        # Minted on first save rather than left empty. An app saved without one
        # cannot complete Meta's handshake, and "generate a token" is a step
        # people skip and then spend an hour debugging as a webhook that never
        # verifies.
        values["verify_token_encrypted"] = encrypt(
            secrets.token_urlsafe(32), settings.encryption_key
        )

    await apps.save(workspace.user_id, values)
    await audit.record(
        user_id=user["_id"],
        action="settings.meta_app_saved",
        entity_type="meta_app",
        entity_id=workspace.user_id,
        changes={"app_id": app_id, "workspace": workspace.slug},
        ip_address=request.client.host if request.client else None,
    )
    logger.info("meta_app_saved", extra={"workspace": workspace.slug, "app_id": app_id})

    saved = await apps.for_user(workspace.user_id)
    stored_settings = await store.load()
    creds = resolve_for(settings, stored_settings, saved, workspace.slug)
    return _meta_app_state(saved, creds, workspace, origin_for(settings, stored_settings))


@router.post("/meta-app/verify-token", dependencies=[Depends(require("instagram.manage"))])
async def regenerate_verify_token(
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    apps: MetaAppsRepo,
    store: AppSettingsRepo,
    settings: SettingsDep,
    audit: AuditRepo,
) -> dict[str, Any]:
    """Mint a fresh verify token for this workspace.

    The old one stops working the moment this returns, so Meta's subscription has
    to be verified again with the new value. Said plainly in the response rather
    than discovered later as a webhook that quietly stopped arriving.
    """
    token = secrets.token_urlsafe(32)
    await apps.save(
        workspace.user_id,
        {"verify_token_encrypted": encrypt(token, settings.encryption_key)},
    )
    await audit.record(
        user_id=user["_id"],
        action="settings.meta_verify_token_regenerated",
        entity_type="meta_app",
        entity_id=workspace.user_id,
        changes={"workspace": workspace.slug},
        ip_address=request.client.host if request.client else None,
    )
    saved = await apps.for_user(workspace.user_id)
    stored_settings = await store.load()
    creds = resolve_for(settings, stored_settings, saved, workspace.slug)
    return {
        **_meta_app_state(saved, creds, workspace, origin_for(settings, stored_settings)),
        "note": (
            "The previous token no longer works. Paste this one into your Meta app's "
            "webhook configuration and click 'Verify and save' again."
        ),
    }


@router.delete("/meta-app", dependencies=[Depends(require("instagram.manage"))])
async def clear_meta_app(
    request: Request,
    user: CurrentUser,
    workspace: CurrentWorkspace,
    apps: MetaAppsRepo,
    store: AppSettingsRepo,
    settings: SettingsDep,
    audit: AuditRepo,
) -> dict[str, Any]:
    """Forget this workspace's app, falling it back to the instance's.

    The connected Instagram account is left alone: its access token stays valid
    for the 60 days Meta granted it, whichever app minted it.
    """
    await apps.clear(workspace.user_id)
    await audit.record(
        user_id=user["_id"],
        action="settings.meta_app_cleared",
        entity_type="meta_app",
        entity_id=workspace.user_id,
        changes={"workspace": workspace.slug},
        ip_address=request.client.host if request.client else None,
    )
    stored_settings = await store.load()
    creds = resolve_for(settings, stored_settings, None, workspace.slug)
    return _meta_app_state(None, creds, workspace, origin_for(settings, stored_settings))
