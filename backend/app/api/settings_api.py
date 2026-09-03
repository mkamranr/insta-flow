"""Operator-editable settings. Currently the MaxMind GeoLite2 credentials.

The license key is encrypted at rest with the same key that protects Meta tokens,
and is never returned by any endpoint — only whether one is configured (§4).
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.deps import AppSettingsRepo, AuditRepo, CurrentUser, SettingsDep
from app.errors import ValidationError
from app.services.geoip import GeoIpError, database_path, download, remote_build_date
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


@router.get("/disclosure")
async def get_disclosure(user: CurrentUser, store: AppSettingsRepo) -> dict[str, Any]:
    """The automated-experience notice appended to the first automated message."""
    saved = await store.load()
    return {
        "enabled": bool(saved.get("automation_disclosure_enabled", True)),
        "text": saved.get("automation_disclosure") or DEFAULT_DISCLOSURE,
    }


@router.put("/disclosure")
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


@router.get("/geoip")
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


@router.put("/geoip")
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


@router.post("/geoip/refresh")
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


@router.delete("/geoip")
async def clear_geoip(
    user: CurrentUser, store: AppSettingsRepo, settings: SettingsDep
) -> dict[str, Any]:
    """Forget the credentials. Existing rows keep the locations already derived."""
    await store.save({"maxmind_account_id": None, "maxmind_license_key_encrypted": None})
    database_path(Path(settings.geoip_dir)).unlink(missing_ok=True)
    return {"configured": False, "database": _database_state(settings)}
