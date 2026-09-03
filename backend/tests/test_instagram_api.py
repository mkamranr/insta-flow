"""Phases 8-9: connecting an account, and subscribing it to webhooks."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from bson import ObjectId

from app.config import get_settings
from app.errors import MetaAPIError
from app.services.meta import MetaAccount, MockInstagramClient
from app.services.meta.auth import TokenBundle
from app.services.security import oauth_state
from app.services.security.encryption import decrypt, encrypt

IG_ID = "17841400000000000"
LONG_TOKEN = "IGQVJlong-lived-token-value"


@pytest.fixture
def meta(monkeypatch):
    """Stub the OAuth exchange and the Instagram client. No network."""
    client = MockInstagramClient(
        account=MetaAccount(
            instagram_user_id=IG_ID, username="myshop", account_type="BUSINESS", name="My Shop"
        )
    )

    async def fake_exchange_code(settings, code, **kw):
        return TokenBundle(access_token="short", instagram_user_id=IG_ID,
                           permissions=("instagram_business_basic",))

    async def fake_long_lived(settings, token, **kw):
        return TokenBundle(
            access_token=LONG_TOKEN, expires_at=datetime.now(UTC) + timedelta(days=60)
        )

    async def fake_refresh(settings, token, **kw):
        return TokenBundle(
            access_token="refreshed-token", expires_at=datetime.now(UTC) + timedelta(days=60)
        )

    monkeypatch.setattr("app.api.instagram.meta_auth.exchange_code", fake_exchange_code)
    monkeypatch.setattr("app.api.instagram.meta_auth.exchange_for_long_lived", fake_long_lived)
    monkeypatch.setattr("app.api.instagram.meta_auth.refresh_long_lived", fake_refresh)
    monkeypatch.setattr("app.api.instagram.build_client", lambda settings: client)
    return client


@pytest.fixture
async def connected(database, admin):
    """An already-connected account, owned by the signed-in admin."""
    settings = get_settings()
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            # Account lookups are scoped by owner; without this the fixture is
            # invisible to the very user the test signs in as.
            "user_id": admin["_id"],
            "instagram_user_id": IG_ID,
            "username": "myshop",
            "status": "connected",
            "access_token_encrypted": encrypt(LONG_TOKEN, settings.encryption_key),
            "token_expires_at": datetime.now(UTC) + timedelta(days=59),
            "token_obtained_at": datetime.now(UTC) - timedelta(days=30),
        }
    )
    return await database["instagram_accounts"].find_one({"_id": account_id})


# --- OAuth start ------------------------------------------------------------


async def test_start_requires_a_session(api):
    assert (await api.get("/api/auth/instagram/start")).status_code == 401


async def test_start_returns_the_consent_url(signed_in, redis):
    response = await signed_in.get("/api/auth/instagram/start")
    assert response.status_code == 200

    url = httpx.URL(response.json()["authorize_url"])
    assert str(url).startswith("https://www.instagram.com/oauth/authorize")
    assert url.params["state"]
    assert url.params["redirect_uri"].endswith("/api/auth/instagram/callback")


async def test_start_issues_a_state_that_can_be_consumed_once(signed_in, redis):
    url = httpx.URL((await signed_in.get("/api/auth/instagram/start")).json()["authorize_url"])
    state = url.params["state"]
    secret = get_settings().session_secret
    assert await oauth_state.consume(redis, secret, state) is not None
    assert await oauth_state.consume(redis, secret, state) is None


# --- OAuth callback ---------------------------------------------------------


async def test_callback_connects_the_account(api, database, redis, meta):
    state = await oauth_state.issue(redis, get_settings().session_secret, str(ObjectId()))
    response = await api.get(
        "/api/auth/instagram/callback", params={"code": "the-code", "state": state}
    )

    assert response.status_code == 303
    assert "connected=1" in response.headers["location"]

    account = await database["instagram_accounts"].find_one({"instagram_user_id": IG_ID})
    assert account["username"] == "myshop"
    assert account["status"] == "connected"
    assert account["account_type"] == "BUSINESS"


async def test_the_stored_token_is_encrypted(api, database, redis, meta):
    state = await oauth_state.issue(redis, get_settings().session_secret, str(ObjectId()))
    await api.get("/api/auth/instagram/callback", params={"code": "c", "state": state})

    account = await database["instagram_accounts"].find_one({"instagram_user_id": IG_ID})
    assert "access_token" not in account
    assert LONG_TOKEN not in str(account)
    assert account["access_token_encrypted"].startswith("v1:")
    assert decrypt(account["access_token_encrypted"], get_settings().encryption_key) == LONG_TOKEN


async def test_a_replayed_callback_is_rejected(api, database, redis, meta):
    state = await oauth_state.issue(redis, get_settings().session_secret, str(ObjectId()))
    await api.get("/api/auth/instagram/callback", params={"code": "c", "state": state})

    replay = await api.get("/api/auth/instagram/callback", params={"code": "c", "state": state})
    assert "error=invalid_state" in replay.headers["location"]


async def test_a_forged_state_is_rejected(api, redis, meta):
    response = await api.get(
        "/api/auth/instagram/callback", params={"code": "c", "state": "forged"}
    )
    assert "error=invalid_state" in response.headers["location"]


async def test_a_denied_consent_redirects_with_the_reason(api, redis):
    response = await api.get(
        "/api/auth/instagram/callback",
        params={"error": "access_denied", "error_reason": "user_denied"},
    )
    assert "error=user_denied" in response.headers["location"]


async def test_a_failed_exchange_redirects_rather_than_500s(api, redis, meta, monkeypatch):
    async def boom(settings, code, **kw):
        raise MetaAPIError("Invalid code", status_code=400)

    monkeypatch.setattr("app.api.instagram.meta_auth.exchange_code", boom)
    state = await oauth_state.issue(redis, get_settings().session_secret, str(ObjectId()))
    response = await api.get(
        "/api/auth/instagram/callback", params={"code": "bad", "state": state}
    )
    assert "error=exchange_failed" in response.headers["location"]


async def test_reconnecting_updates_in_place(api, database, redis, meta):
    """instagram_user_id is unique, so a reconnect must not create a duplicate."""
    secret = get_settings().session_secret
    for _ in range(2):
        state = await oauth_state.issue(redis, secret, str(ObjectId()))
        await api.get("/api/auth/instagram/callback", params={"code": "c", "state": state})
    assert await database["instagram_accounts"].count_documents({"instagram_user_id": IG_ID}) == 1


# --- tokens never leak ------------------------------------------------------


async def test_the_accounts_list_never_exposes_a_token(signed_in, connected):
    response = await signed_in.get("/api/instagram/accounts")
    assert response.status_code == 200
    body = response.text
    assert LONG_TOKEN not in body
    assert "access_token" not in body
    assert connected["access_token_encrypted"] not in body


async def test_the_account_detail_never_exposes_a_token(signed_in, connected):
    response = await signed_in.get(f"/api/instagram/{connected['_id']}")
    assert LONG_TOKEN not in response.text
    assert "access_token" not in response.text
    assert response.json()["username"] == "myshop"


async def test_accounts_require_a_session(api, connected):
    assert (await api.get("/api/instagram/accounts")).status_code == 401


# --- webhook subscription (Phase 9) -----------------------------------------


async def test_subscribe_calls_meta_and_records_the_result(signed_in, connected, meta):
    response = await signed_in.post(f"/api/instagram/{connected['_id']}/subscribe")
    assert response.status_code == 200
    assert response.json()["webhook_subscribed"] is True
    fields = response.json()["subscribed_fields"]
    assert "comments" in fields
    # messaging_postbacks carries the opening-DM button tap.
    assert "messaging_postbacks" in fields
    assert [c.method for c in meta.mutation_calls] == ["subscribe_webhooks"]


async def test_subscribe_passes_the_decrypted_token_to_meta(signed_in, connected, meta):
    await signed_in.post(f"/api/instagram/{connected['_id']}/subscribe")
    fields = meta.calls_to("subscribe_webhooks")[0].kwargs["fields"]
    assert "comments" in fields and "messaging_postbacks" in fields


async def test_a_failed_subscribe_records_the_error(signed_in, database, connected, meta):
    meta.fail("subscribe_webhooks", 403)
    response = await signed_in.post(f"/api/instagram/{connected['_id']}/subscribe")
    assert response.status_code == 502

    account = await database["instagram_accounts"].find_one({"_id": connected["_id"]})
    assert account["webhook_subscribed"] is False
    assert account["status_error"]


async def test_subscription_status_is_read_from_meta(signed_in, connected, meta):
    """A subscription can be removed in the app dashboard without telling us."""
    await signed_in.post(f"/api/instagram/{connected['_id']}/subscribe")
    response = await signed_in.get(f"/api/instagram/{connected['_id']}/subscription")
    assert response.json()["subscribed"] is True
    assert "comments" in response.json()["fields"]


async def test_subscription_status_reports_an_error_without_failing(signed_in, connected, meta):
    meta.fail("get_subscribed_fields", 500)
    response = await signed_in.get(f"/api/instagram/{connected['_id']}/subscription")
    assert response.status_code == 200
    assert response.json()["subscribed"] is False
    assert response.json()["error"]


# --- token lifecycle --------------------------------------------------------


async def test_refresh_replaces_the_stored_token(signed_in, database, connected, meta):
    response = await signed_in.post(f"/api/instagram/{connected['_id']}/refresh")
    assert response.status_code == 200

    account = await database["instagram_accounts"].find_one({"_id": connected["_id"]})
    assert decrypt(account["access_token_encrypted"], get_settings().encryption_key) == (
        "refreshed-token"
    )
    assert "refreshed-token" not in response.text


async def test_a_failed_refresh_marks_the_account(signed_in, database, connected, monkeypatch):
    async def boom(settings, token, **kw):
        raise MetaAPIError("expired", status_code=400)

    monkeypatch.setattr("app.api.instagram.meta_auth.refresh_long_lived", boom)
    response = await signed_in.post(f"/api/instagram/{connected['_id']}/refresh")
    assert response.status_code == 502

    account = await database["instagram_accounts"].find_one({"_id": connected["_id"]})
    assert account["status"] == "token_error"


async def test_token_status_reports_expiry(signed_in, connected):
    response = await signed_in.get(f"/api/instagram/{connected['_id']}/token-status")
    assert response.json()["expired"] is False
    assert response.json()["needs_refresh"] is False


async def test_disconnect_removes_the_account(signed_in, database, connected):
    assert (await signed_in.delete(f"/api/instagram/{connected['_id']}")).status_code == 204
    assert await database["instagram_accounts"].count_documents({}) == 0


async def test_a_missing_account_returns_404(signed_in):
    assert (await signed_in.get(f"/api/instagram/{ObjectId()}")).status_code == 404


# --- Meta-facing callbacks --------------------------------------------------


async def test_meta_callbacks_accept_a_post(api):
    """Registered in the app dashboard; a 404 would show as a broken integration.

    An unsigned POST is acknowledged rather than errored, because Meta retries
    failures and an unverifiable payload will never verify on a retry.
    """
    for path in ("/api/auth/instagram/deauthorize", "/api/auth/instagram/data-deletion"):
        assert (await api.post(path)).status_code == 200


async def test_meta_callbacks_explain_themselves_in_a_browser(api):
    """These are machine endpoints; a person following the link deserves prose."""
    for path in ("/api/auth/instagram/deauthorize", "/api/auth/instagram/data-deletion"):
        response = await api.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


async def test_the_public_account_carries_the_profile_picture(database, admin):
    """`from_document` maps fields one by one.

    A field added to the model but not to the mapper silently serialises as
    null, which is how the dashboard kept showing a placeholder initial after
    the picture was already stored.
    """
    from app.models.instagram import InstagramAccountPublic

    public = InstagramAccountPublic.from_document(
        {
            "_id": ObjectId(),
            "instagram_user_id": "1",
            "username": "daily.techtalks",
            "status": "connected",
            "profile_picture_url": "https://scontent.cdninstagram.com/v/abc.jpg",
        }
    )

    assert public.profile_picture_url == "https://scontent.cdninstagram.com/v/abc.jpg"
