"""The real Instagram client: HTTP against graph.instagram.com.

Every endpoint, parameter, and permission below is recorded in docs/meta-api.md,
verified against official Meta documentation. Do not add an endpoint here that is
not documented there first.
"""

import logging
from datetime import datetime
from typing import Any

import httpx

from app.config import Settings
from app.errors import MetaAPIError
from app.services.meta.client import (
    DEFAULT_WEBHOOK_FIELDS,
    MetaAccount,
    MetaMedia,
    MetaSendResult,
    MetaUserProfile,
    error_for_status,
)
from app.services.meta.messages import build_messages

logger = logging.getLogger(__name__)

ACCOUNT_FIELDS = "user_id,username,account_type,name,profile_picture_url"
PROFILE_FIELDS = (
    "name,username,profile_pic,follower_count,"
    "is_user_follow_business,is_business_follow_user"
)
MEDIA_FIELDS_BASE = "id,media_type,media_product_type,caption,permalink,thumbnail_url,timestamp"
# Engagement counts are not available on every media type or account, and asking
# for an unsupported field fails the whole request — so they are requested
# separately and dropped on rejection rather than losing the sync.
MEDIA_FIELDS = MEDIA_FIELDS_BASE + ",like_count,comments_count"

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class MetaInstagramClient:
    """Implements `InstagramClient` against the live Graph API."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        self._owns_client = client is None

    @property
    def base(self) -> str:
        return self._settings.meta_graph_base

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- transport ---------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        bearer: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {bearer}"} if bearer else None

        try:
            response = await self._client.request(
                method, url, params=params, json=json_body, headers=headers
            )
        except httpx.TimeoutException as exc:
            # Retryable: the request may well have arrived, so the caller relies
            # on the execution reservation rather than assuming nothing happened.
            raise MetaAPIError(
                "Meta request timed out.", status_code=None, retryable=True
            ) from exc
        except httpx.RequestError as exc:
            raise MetaAPIError(
                f"Could not reach Meta: {type(exc).__name__}", status_code=None, retryable=True
            ) from exc

        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}

        if response.is_success and "error" not in body:
            return body if isinstance(body, dict) else {"data": body}

        error = body.get("error") if isinstance(body, dict) else None
        error = error if isinstance(error, dict) else {}
        message = error.get("error_user_msg") or error.get("message") or "Meta request failed."

        raise error_for_status(
            response.status_code,
            message,
            error_code=error.get("code"),
            raw_response=body if isinstance(body, dict) else {},
        )

    # -- InstagramClient ---------------------------------------------------

    async def get_account(self, access_token: str) -> MetaAccount:
        body = await self._request(
            "GET", "me", params={"fields": ACCOUNT_FIELDS, "access_token": access_token}
        )
        return MetaAccount(
            # Instagram Login returns `user_id`; `id` appears on some responses.
            instagram_user_id=str(body.get("user_id") or body.get("id") or ""),
            username=str(body.get("username", "")),
            account_type=str(body.get("account_type", "")),
            name=body.get("name"),
            profile_picture_url=body.get("profile_picture_url"),
            raw=body,
        )

    async def get_media(
        self, access_token: str, instagram_user_id: str, limit: int = 25
    ) -> list[MetaMedia]:
        try:
            body = await self._request(
                "GET",
                "me/media",
                params={"fields": MEDIA_FIELDS, "limit": limit, "access_token": access_token},
            )
        except MetaAPIError as exc:
            if exc.retryable or exc.meta_status_code != 400:
                raise
            # Almost certainly an unsupported field. Retry without the optional
            # ones: losing engagement counts beats losing the whole sync.
            logger.warning("media_fields_rejected_retrying_base", extra={"error": str(exc)})
            body = await self._request(
                "GET",
                "me/media",
                params={
                    "fields": MEDIA_FIELDS_BASE,
                    "limit": limit,
                    "access_token": access_token,
                },
            )
        return [_media_from(item) for item in body.get("data") or []]

    async def subscribe_webhooks(
        self,
        access_token: str,
        instagram_user_id: str,
        fields: tuple[str, ...] = DEFAULT_WEBHOOK_FIELDS,
    ) -> bool:
        body = await self._request(
            "POST",
            "me/subscribed_apps",
            params={"subscribed_fields": ",".join(fields), "access_token": access_token},
        )
        return bool(body.get("success", False))

    async def get_subscribed_fields(
        self, access_token: str, instagram_user_id: str
    ) -> list[str]:
        body = await self._request(
            "GET", "me/subscribed_apps", params={"access_token": access_token}
        )
        # Meta returns either bare field names or objects carrying a name.
        raw_fields: list[Any] = []
        for entry in body.get("data") or []:
            raw_fields.extend(entry.get("subscribed_fields") or [])
        return [
            str(f["name"]) if isinstance(f, dict) and "name" in f else str(f)
            for f in raw_fields
        ]

    async def _send(
        self,
        access_token: str,
        instagram_user_id: str,
        recipient: dict[str, str],
        text: str,
        buttons: list[dict[str, str]] | None,
    ) -> MetaSendResult:
        """Send one logical message, which may need more than one API call.

        A long message with buttons cannot fit the 160-character template, so
        `build_messages` splits it. The first response is the one reported back,
        since that is the message the recipient sees first.
        """
        payloads = build_messages(text, buttons)
        if not payloads:
            raise MetaAPIError("Nothing to send.", status_code=None, retryable=False)

        first: dict[str, Any] = {}
        for index, message in enumerate(payloads):
            body = await self._request(
                "POST",
                f"{instagram_user_id}/messages",
                json_body={"recipient": recipient, "message": message},
                bearer=access_token,
            )
            if index == 0:
                first = body

        return MetaSendResult(
            success=True,
            message_id=first.get("message_id"),
            recipient_id=first.get("recipient_id"),
            raw_response=first,
        )

    async def send_private_reply(
        self,
        access_token: str,
        instagram_user_id: str,
        comment_id: str,
        text: str,
        buttons: list[dict[str, str]] | None = None,
    ) -> MetaSendResult:
        """POST /{ig_user_id}/messages with recipient.comment_id (docs §4).

        One per commenter, within 7 days. The policy engine enforces the window
        before we get here, so a call we know will fail is never made.

        A private reply is limited to one message, so `buttons` must fit the
        template — callers pass a short opening message when using buttons.
        """
        return await self._send(
            access_token, instagram_user_id, {"comment_id": comment_id}, text, buttons
        )

    async def send_direct_message(
        self,
        access_token: str,
        instagram_user_id: str,
        recipient_id: str,
        text: str,
        buttons: list[dict[str, str]] | None = None,
    ) -> MetaSendResult:
        """Message an IGSID. Only valid inside an open 24-hour window."""
        return await self._send(
            access_token, instagram_user_id, {"id": recipient_id}, text, buttons
        )

    async def send_public_reply(
        self, access_token: str, comment_id: str, text: str
    ) -> MetaSendResult:
        """POST /{comment_id}/replies (docs §4b). Verified 2026-08-31."""
        body = await self._request(
            "POST",
            f"{comment_id}/replies",
            json_body={"message": text},
            bearer=access_token,
        )
        return MetaSendResult(success=True, message_id=body.get("id"), raw_response=body)

    async def get_user_profile(
        self, access_token: str, instagram_user_id: str
    ) -> MetaUserProfile:
        """Messaging User Profile API — backs "require follow before DM"."""
        body = await self._request(
            "GET",
            instagram_user_id,
            params={"fields": PROFILE_FIELDS, "access_token": access_token},
        )
        return MetaUserProfile(
            instagram_user_id=str(body.get("id") or instagram_user_id),
            username=str(body.get("username", "")),
            name=body.get("name"),
            profile_pic=body.get("profile_pic"),
            follower_count=body.get("follower_count"),
            is_user_follow_business=body.get("is_user_follow_business"),
            is_business_follow_user=body.get("is_business_follow_user"),
            raw=body,
        )


def _media_from(item: dict[str, Any]) -> MetaMedia:
    return MetaMedia(
        instagram_media_id=str(item.get("id", "")),
        media_type=item.get("media_type"),
        media_product_type=item.get("media_product_type"),
        caption=item.get("caption"),
        permalink=item.get("permalink"),
        thumbnail_url=item.get("thumbnail_url"),
        published_at=_parse_timestamp(item.get("timestamp")),
        like_count=item.get("like_count"),
        comments_count=item.get("comments_count"),
        raw=item,
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("+0000", "+00:00"))
    except ValueError:
        return None
