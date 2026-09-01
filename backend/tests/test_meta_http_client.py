"""Phase 10: the real client, driven entirely against mocked HTTP.

CI must never touch a live Meta account (§54), so every request here is served
by httpx.MockTransport.
"""

import json

import httpx
import pytest

from app.config import Settings
from app.errors import MetaAPIError, MetaRateLimitError, MetaTokenError
from app.services.meta.client import InstagramClient
from app.services.meta.http_client import MetaInstagramClient

TOKEN = "long-lived-token"
IG_ID = "17841400000000000"
COMMENT_ID = "17900000000000000"


def settings():
    return Settings(meta_app_id="1", meta_app_secret="s", meta_api_version="v25.0")


def make_client(handler):
    transport = httpx.MockTransport(handler)
    return MetaInstagramClient(settings(), httpx.AsyncClient(transport=transport))


def test_real_client_satisfies_the_protocol():
    assert isinstance(make_client(lambda r: httpx.Response(200, json={})), InstagramClient)


# --- endpoints match docs/meta-api.md ---------------------------------------


async def test_private_reply_uses_the_documented_endpoint_and_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["json"] = httpx.Request("POST", "http://x", content=request.content).content
        return httpx.Response(200, json={"recipient_id": "526", "message_id": "aWdfZ"})

    result = await make_client(handler).send_private_reply(TOKEN, IG_ID, COMMENT_ID, "Hey!")

    assert seen["method"] == "POST"
    assert seen["url"] == f"https://graph.instagram.com/v25.0/{IG_ID}/messages"
    assert seen["auth"] == f"Bearer {TOKEN}"
    body = seen["json"].decode()
    assert '"comment_id"' in body and COMMENT_ID in body and "Hey!" in body
    assert result.success is True
    assert result.message_id == "aWdfZ"
    assert result.recipient_id == "526"


async def test_get_account_reads_user_id():
    def handler(request):
        assert request.url.path == "/v25.0/me"
        return httpx.Response(
            200, json={"user_id": IG_ID, "username": "myshop", "account_type": "BUSINESS"}
        )

    account = await make_client(handler).get_account(TOKEN)
    assert account.instagram_user_id == IG_ID
    assert account.username == "myshop"


async def test_get_account_falls_back_to_id():
    """Some responses return `id` rather than `user_id`."""

    def handler(request):
        return httpx.Response(200, json={"id": IG_ID, "username": "myshop"})

    assert (await make_client(handler).get_account(TOKEN)).instagram_user_id == IG_ID


async def test_subscribe_posts_the_documented_fields():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["fields"] = request.url.params.get("subscribed_fields")
        return httpx.Response(200, json={"success": True})

    assert await make_client(handler).subscribe_webhooks(TOKEN, IG_ID) is True
    assert seen["path"] == "/v25.0/me/subscribed_apps"
    assert "comments" in seen["fields"]
    assert "messaging_postbacks" in seen["fields"]


async def test_subscribed_fields_are_flattened():
    def handler(request):
        return httpx.Response(200, json={"data": [{"subscribed_fields": ["comments"]}]})

    assert await make_client(handler).get_subscribed_fields(TOKEN, IG_ID) == ["comments"]


async def test_subscribed_fields_handles_object_shaped_entries():
    def handler(request):
        return httpx.Response(200, json={"data": [{"subscribed_fields": [{"name": "comments"}]}]})

    assert await make_client(handler).get_subscribed_fields(TOKEN, IG_ID) == ["comments"]


async def test_get_media_parses_timestamps():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "179",
                        "media_product_type": "REELS",
                        "caption": "hi",
                        "timestamp": "2026-08-01T10:00:00+0000",
                    }
                ]
            },
        )

    media = await make_client(handler).get_media(TOKEN, IG_ID)
    assert media[0].instagram_media_id == "179"
    assert media[0].published_at is not None


# --- error mapping ----------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected,retryable",
    [
        (400, MetaAPIError, False),
        (403, MetaAPIError, False),
        (404, MetaAPIError, False),
        (401, MetaTokenError, False),
        (429, MetaRateLimitError, True),
        (500, MetaAPIError, True),
        (503, MetaAPIError, True),
    ],
)
async def test_http_status_maps_to_the_right_error(status, expected, retryable):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "boom", "code": 1}})

    with pytest.raises(expected) as excinfo:
        await make_client(handler).send_private_reply(TOKEN, IG_ID, COMMENT_ID, "hi")
    assert excinfo.value.retryable is retryable
    assert excinfo.value.meta_status_code == status


async def test_a_timeout_is_retryable():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(MetaAPIError) as excinfo:
        await make_client(handler).get_account(TOKEN)
    assert excinfo.value.retryable is True


async def test_a_connection_failure_is_retryable():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(MetaAPIError) as excinfo:
        await make_client(handler).get_account(TOKEN)
    assert excinfo.value.retryable is True


async def test_a_200_carrying_an_error_body_raises():
    def handler(request):
        return httpx.Response(200, json={"error": {"message": "nope", "code": 10}})

    with pytest.raises(MetaAPIError):
        await make_client(handler).send_private_reply(TOKEN, IG_ID, COMMENT_ID, "hi")


async def test_a_non_json_error_body_does_not_crash():
    def handler(request):
        return httpx.Response(502, text="<html>bad gateway</html>")

    with pytest.raises(MetaAPIError) as excinfo:
        await make_client(handler).get_account(TOKEN)
    assert excinfo.value.retryable is True


async def test_the_error_message_prefers_the_user_facing_text():
    def handler(request):
        return httpx.Response(
            400, json={"error": {"message": "internal", "error_user_msg": "Comment too old"}}
        )

    with pytest.raises(MetaAPIError) as excinfo:
        await make_client(handler).send_private_reply(TOKEN, IG_ID, COMMENT_ID, "hi")
    assert "Comment too old" in str(excinfo.value)


# --- unverified endpoints ---------------------------------------------------


async def test_public_reply_uses_the_verified_endpoint():
    """POST /{comment_id}/replies, verified against Meta's docs 2026-08-31."""
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "17900000000000001"})

    result = await make_client(handler).send_public_reply(TOKEN, COMMENT_ID, "Thanks!")

    assert seen["method"] == "POST"
    assert seen["path"] == f"/v25.0/{COMMENT_ID}/replies"
    assert "Thanks!" in seen["body"]
    assert result.message_id == "17900000000000001"


async def test_private_reply_with_buttons_sends_a_generic_template():
    seen = {}

    def handler(request):
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"recipient_id": "1", "message_id": "m"})

    await make_client(handler).send_private_reply(
        TOKEN,
        IG_ID,
        COMMENT_ID,
        "Here you go!",
        [{"type": "postback", "title": "Send me the link", "payload": "RULE:1"}],
    )
    body = json.loads(seen["body"])
    payload = body["message"]["attachment"]["payload"]
    assert payload["template_type"] == "generic"
    assert payload["elements"][0]["buttons"][0]["type"] == "postback"
    assert payload["elements"][0]["buttons"][0]["title"] == "Send me the link"


async def test_a_long_message_with_buttons_sends_two_api_calls():
    """The template caps at 160 characters; the text must not be truncated."""
    calls: list[str] = []

    def handler(request):
        calls.append(request.content.decode())
        return httpx.Response(200, json={"recipient_id": "1", "message_id": "m"})

    long_text = "x" * 400
    button = {"type": "web_url", "title": "Open", "url": "https://e.com"}
    await make_client(handler).send_direct_message(TOKEN, IG_ID, "IGSID", long_text, [button])
    assert len(calls) == 2
    assert long_text in calls[0]
    assert "web_url" in calls[1]


async def test_user_profile_requests_the_follow_flag():
    def handler(request):
        assert "is_user_follow_business" in request.url.params["fields"]
        return httpx.Response(
            200, json={"id": "IGSID", "username": "john123", "is_user_follow_business": True}
        )

    profile = await make_client(handler).get_user_profile(TOKEN, "IGSID")
    assert profile.is_user_follow_business is True
    assert profile.username == "john123"
