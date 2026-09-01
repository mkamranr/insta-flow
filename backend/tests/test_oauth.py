"""Meta OAuth: URL construction, token exchange, and single-use state."""

import httpx
import pytest

from app.config import Settings
from app.errors import MetaAPIError, MetaTokenError
from app.services.meta import auth as meta_auth
from app.services.security import oauth_state

SECRET = "s" * 64


def settings():
    return Settings(
        meta_app_id="1234567890123456",
        meta_app_secret="app-secret",
        public_base_url="https://example.ts.net",
        session_secret=SECRET,
    )


class FakeRedis:
    """Minimal set/delete, matching how oauth_state uses Redis."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0


def transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- authorize URL ----------------------------------------------------------


def test_authorize_url_matches_the_documented_endpoint():
    url = meta_auth.build_authorize_url(settings(), "state-123")
    assert url.startswith("https://www.instagram.com/oauth/authorize?")

    params = httpx.URL(url).params
    assert params["client_id"] == "1234567890123456"
    assert params["response_type"] == "code"
    assert params["state"] == "state-123"
    assert params["redirect_uri"] == "https://example.ts.net/api/auth/instagram/callback"


def test_authorize_url_requests_all_three_scopes():
    scopes = httpx.URL(meta_auth.build_authorize_url(settings(), "s")).params["scope"].split(",")
    assert set(scopes) == {
        "instagram_business_basic",
        "instagram_business_manage_comments",
        "instagram_business_manage_messages",
    }


# --- single-use state -------------------------------------------------------


async def test_state_round_trips():
    redis = FakeRedis()
    state = await oauth_state.issue(redis, SECRET, "user-1")
    assert await oauth_state.consume(redis, SECRET, state) == "user-1"


async def test_state_cannot_be_replayed():
    """The signature alone would allow the same callback URL to be resubmitted."""
    redis = FakeRedis()
    state = await oauth_state.issue(redis, SECRET, "user-1")
    assert await oauth_state.consume(redis, SECRET, state) == "user-1"
    assert await oauth_state.consume(redis, SECRET, state) is None


async def test_state_signed_with_another_secret_is_rejected():
    redis = FakeRedis()
    state = await oauth_state.issue(redis, "other" * 12, "user-1")
    assert await oauth_state.consume(redis, SECRET, state) is None


async def test_a_forged_state_is_rejected():
    assert await oauth_state.consume(FakeRedis(), SECRET, "forged") is None
    assert await oauth_state.consume(FakeRedis(), SECRET, "") is None


# --- code exchange ----------------------------------------------------------


async def test_code_exchange_posts_the_documented_form():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"access_token": "short-token", "user_id": 178414})

    async with transport(handler) as client:
        bundle = await meta_auth.exchange_code(settings(), "the-code", client=client)

    assert seen["url"] == "https://api.instagram.com/oauth/access_token"
    assert "grant_type=authorization_code" in seen["body"]
    assert "code=the-code" in seen["body"]
    assert bundle.access_token == "short-token"
    assert bundle.instagram_user_id == "178414"


async def test_permissions_are_parsed_from_either_shape():
    def handler(request):
        return httpx.Response(
            200, json={"access_token": "t", "permissions": "instagram_business_basic,x"}
        )

    async with transport(handler) as client:
        bundle = await meta_auth.exchange_code(settings(), "c", client=client)
    assert bundle.permissions == ("instagram_business_basic", "x")


async def test_a_rejected_code_raises_a_token_error():
    def handler(request):
        return httpx.Response(400, json={"error": {"message": "Invalid code", "code": 100}})

    async with transport(handler) as client:
        with pytest.raises(MetaTokenError) as excinfo:
            await meta_auth.exchange_code(settings(), "bad", client=client)
    assert "Invalid code" in str(excinfo.value)


async def test_a_server_error_during_exchange_raises_meta_api_error():
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "oops"}})

    async with transport(handler) as client:
        with pytest.raises(MetaAPIError):
            await meta_auth.exchange_code(settings(), "c", client=client)


# --- long-lived exchange and refresh ---------------------------------------


async def test_long_lived_exchange_sets_an_expiry():
    def handler(request):
        assert request.url.params["grant_type"] == "ig_exchange_token"
        return httpx.Response(200, json={"access_token": "long-token", "expires_in": 5183944})

    async with transport(handler) as client:
        bundle = await meta_auth.exchange_for_long_lived(settings(), "short", client=client)

    assert bundle.access_token == "long-token"
    assert bundle.expires_at is not None


async def test_refresh_uses_the_documented_grant_type():
    def handler(request):
        assert str(request.url).startswith("https://graph.instagram.com/refresh_access_token")
        assert request.url.params["grant_type"] == "ig_refresh_token"
        return httpx.Response(200, json={"access_token": "refreshed", "expires_in": 5183944})

    async with transport(handler) as client:
        bundle = await meta_auth.refresh_long_lived(settings(), "old", client=client)
    assert bundle.access_token == "refreshed"


async def test_a_missing_expires_in_leaves_expiry_unset():
    """Callers fall back to the documented 60 days rather than inventing one."""

    def handler(request):
        return httpx.Response(200, json={"access_token": "t"})

    async with transport(handler) as client:
        bundle = await meta_auth.exchange_for_long_lived(settings(), "s", client=client)
    assert bundle.expires_at is None


async def test_a_200_carrying_an_error_body_still_raises():
    """Meta sometimes returns 200 with an error payload."""

    def handler(request):
        return httpx.Response(200, json={"error": {"message": "nope", "code": 190}})

    async with transport(handler) as client:
        with pytest.raises(MetaAPIError):
            await meta_auth.refresh_long_lived(settings(), "t", client=client)
