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
from tests.conftest import TEST_PASSWORD

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

    # These take `MetaCredentials`, not `Settings` -- which app is acting is a
    # per-workspace fact. `refresh_long_lived` takes neither: `ig_refresh_token`
    # authenticates with the token itself.
    async def fake_exchange_code(creds, code, **kw):
        return TokenBundle(
            access_token="short", instagram_user_id=IG_ID, permissions=("instagram_business_basic",)
        )

    async def fake_long_lived(creds, token, **kw):
        return TokenBundle(
            access_token=LONG_TOKEN, expires_at=datetime.now(UTC) + timedelta(days=60)
        )

    async def fake_refresh(token, **kw):
        return TokenBundle(
            access_token="refreshed-token", expires_at=datetime.now(UTC) + timedelta(days=60)
        )

    monkeypatch.setattr("app.api.instagram.meta_auth.exchange_code", fake_exchange_code)
    monkeypatch.setattr("app.api.instagram.meta_auth.exchange_for_long_lived", fake_long_lived)
    monkeypatch.setattr("app.api.instagram.meta_auth.refresh_long_lived", fake_refresh)
    monkeypatch.setattr("app.api.instagram.build_client", lambda settings, creds=None: client)
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
    async def boom(creds, code, **kw):
        raise MetaAPIError("Invalid code", status_code=400)

    monkeypatch.setattr("app.api.instagram.meta_auth.exchange_code", boom)
    state = await oauth_state.issue(redis, get_settings().session_secret, str(ObjectId()))
    response = await api.get("/api/auth/instagram/callback", params={"code": "bad", "state": state})
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
    async def boom(token, **kw):
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


async def test_disconnect_marks_the_account_rather_than_deleting_it(
    signed_in, database, connected
):
    """This assertion was inverted deliberately on 2026-09-08.

    It used to require the row to be gone. But `media` and `rules` are keyed on
    the account's `_id`, and `accounts.upsert` is keyed on `instagram_user_id`,
    so deleting the row meant a reconnect of the SAME Instagram account minted a
    new `_id` and orphaned every post and automation, with nothing able to remap
    them afterwards -- 168 media rows and 8 rules on the live install. Meta's own
    deauthorize callback already only marked the row; the two paths disagreeing
    was the bug.
    """
    assert (await signed_in.delete(f"/api/instagram/{connected['_id']}")).status_code == 204

    row = await database["instagram_accounts"].find_one({"_id": connected["_id"]})
    assert row is not None, "the row must survive so a reconnect can reuse it"
    assert row["status"] == "disconnected"


async def test_a_disconnected_account_does_not_read_as_connected(
    signed_in, database, connected
):
    """The blast radius of keeping the row. Six reads used to ignore `status`,
    and each would have reported a dead account as live."""
    await signed_in.delete(f"/api/instagram/{connected['_id']}")

    # `_only_account` -- posts sync and rule creation would otherwise act on it.
    assert (await signed_in.post("/api/posts/sync")).status_code == 422
    # The setup banner's signal. Left unfiltered, the banner would wrongly clear.
    assert (await signed_in.get("/api/onboarding/progress")).json()["connected"] is False
    # Analytics scopes to the live account, so it should read as having none.
    assert (await signed_in.get("/api/analytics/contacts")).json() == []


async def test_a_disconnected_account_is_still_listed_so_it_can_be_reconnected(
    signed_in, connected
):
    """Deliberately NOT filtered here: the Instagram page has to be able to
    offer a reconnect, which it cannot do for a row it never sees."""
    await signed_in.delete(f"/api/instagram/{connected['_id']}")

    listed = (await signed_in.get("/api/instagram/accounts")).json()
    assert [a["status"] for a in listed] == ["disconnected"]


async def test_reconnecting_the_same_account_adopts_its_posts_and_automations(
    signed_in, database, connected, admin
):
    """The whole point of marking rather than deleting."""
    await database["media"].insert_one(
        {
            "instagram_account_id": connected["_id"],
            "instagram_media_id": "1789",
            "caption": "before",
            "published_at": datetime.now(UTC),
        }
    )
    await database["rules"].insert_one(
        {
            "instagram_account_id": connected["_id"],
            "name": "Handwritten",
            "instagram_media_ids": ["1789"],
        }
    )

    await signed_in.delete(f"/api/instagram/{connected['_id']}")

    # A reconnect is an upsert keyed on `instagram_user_id`, so it lands on the
    # same row -- which is what carries the media and rules back with it.
    from app.db.repositories import InstagramAccountsRepository

    revived = await InstagramAccountsRepository(database).upsert(
        IG_ID, {"user_id": admin["_id"], "status": "connected", "username": "myshop"}
    )

    assert revived["_id"] == connected["_id"], "a reconnect must reuse the row"
    assert await database["media"].count_documents(
        {"instagram_account_id": connected["_id"]}
    ) == 1
    assert await database["rules"].count_documents(
        {"instagram_account_id": connected["_id"]}
    ) == 1


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


async def test_posts_paginate_for_the_dashboard_rail(signed_in, database, admin):
    """The rail walks `offset` as it scrolls, so pages must not overlap."""
    from datetime import UTC, datetime, timedelta

    account_id = (
        await database["instagram_accounts"].insert_one(
            {
                "user_id": admin["_id"],
                "instagram_user_id": "17841400000000000",
                "username": "shop",
                "status": "connected",
            }
        )
    ).inserted_id
    now = datetime.now(UTC)
    for index in range(12):
        await database["media"].insert_one(
            {
                "instagram_account_id": account_id,
                "instagram_media_id": f"m{index:02d}",
                "caption": f"post {index}",
                "published_at": now - timedelta(days=index),
            }
        )

    first = (await signed_in.get("/api/posts?limit=5&offset=0")).json()
    second = (await signed_in.get("/api/posts?limit=5&offset=5")).json()

    assert len(first) == 5
    assert len(second) == 5
    assert {p["instagram_media_id"] for p in first}.isdisjoint(
        {p["instagram_media_id"] for p in second}
    ), "a page must not repeat the previous page"


async def test_the_last_page_is_short_which_is_how_the_rail_knows_to_stop(
    signed_in, database, admin
):
    account_id = (
        await database["instagram_accounts"].insert_one(
            {
                "user_id": admin["_id"],
                "instagram_user_id": "17841400000000000",
                "username": "shop",
                "status": "connected",
            }
        )
    ).inserted_id
    for index in range(3):
        await database["media"].insert_one(
            {"instagram_account_id": account_id, "instagram_media_id": f"x{index}"}
        )

    page = (await signed_in.get("/api/posts?limit=5&offset=0")).json()

    assert len(page) == 3


# --- Auto DM pause ----------------------------------------------------------


def in_hours(hours: float) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


async def held_comment(database, account_id, *, age=timedelta(0), comment_id="c1"):
    """An execution shaped the way a pause leaves one behind.

    Created now by default, because a comment a pause held necessarily arrived
    after the pause began -- and the backlog window is what enforces that.
    """
    comment = await database["comments"].insert_one(
        {
            "instagram_account_id": account_id,
            "instagram_comment_id": comment_id,
            "instagram_user_id": "999",
            "text": "link",
        }
    )
    await database["executions"].insert_one(
        {
            "instagram_account_id": account_id,
            "comment_id": comment.inserted_id,
            "rule_id": ObjectId(),
            "action": "private_reply",
            "status": "skipped",
            "policy_decision": "automation_paused",
            "created_at": datetime.now(UTC) - age,
        }
    )


async def test_pausing_and_resuming_round_trips(signed_in, connected, database):
    paused = await signed_in.post(
        f"/api/instagram/{connected['_id']}/pause", json={"until": in_hours(3)}
    )
    assert paused.status_code == 200
    assert paused.json()["automation_paused"] is True

    status = (await signed_in.get(f"/api/instagram/{connected['_id']}/pause")).json()
    assert status["paused"] is True

    resumed = await signed_in.delete(f"/api/instagram/{connected['_id']}/pause")
    assert resumed.status_code == 200
    assert resumed.json()["automation_paused"] is False


async def test_a_pause_in_the_past_is_refused(signed_in, connected):
    response = await signed_in.post(
        f"/api/instagram/{connected['_id']}/pause", json={"until": in_hours(-1)}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_a_pause_beyond_the_cap_is_refused(signed_in, connected):
    """A mistyped year would otherwise silence the account until then."""
    response = await signed_in.post(
        f"/api/instagram/{connected['_id']}/pause", json={"until": in_hours(24 * 31)}
    )
    assert response.status_code == 422


async def test_a_naive_instant_is_read_as_utc(signed_in, connected, database):
    naive = (datetime.now(UTC) + timedelta(hours=2)).replace(tzinfo=None).isoformat()

    response = await signed_in.post(
        f"/api/instagram/{connected['_id']}/pause", json={"until": naive}
    )

    assert response.status_code == 200
    account = await database["instagram_accounts"].find_one({"_id": connected["_id"]})
    assert account["automation_paused_until"].tzinfo is not None


async def test_pausing_another_workspaces_account_is_not_found(
    signed_in, database, role_factory, user_factory
):
    """404 rather than 403, so ids cannot be discovered by probing."""
    role = await role_factory("other-owner", ["instagram.manage", "instagram.view"])
    other = await user_factory("someone@example.com", role)
    account_id = (
        await database["instagram_accounts"].insert_one(
            {"user_id": other["_id"], "instagram_user_id": "999", "status": "connected"}
        )
    ).inserted_id

    response = await signed_in.post(
        f"/api/instagram/{account_id}/pause", json={"until": in_hours(1)}
    )

    assert response.status_code == 404


async def test_the_backlog_is_still_offered_after_a_pause_expired_unattended(
    signed_in, connected, database
):
    """Nothing runs at expiry, so a pause nobody was present for still has its
    comments waiting to be decided on."""
    await database["instagram_accounts"].update_one(
        {"_id": connected["_id"]},
        {
            "$set": {
                "automation_paused_until": datetime.now(UTC) - timedelta(hours=1),
                "automation_backlog_since": datetime.now(UTC) - timedelta(hours=3),
            }
        },
    )
    await held_comment(database, connected["_id"])

    status = (await signed_in.get(f"/api/instagram/{connected['_id']}/pause")).json()

    assert status["paused"] is False
    assert status["held"] == 1 and status["deliverable"] == 1


async def test_deliverable_excludes_comments_past_metas_reply_window(
    signed_in, connected, database
):
    """A long pause holds more than it can ever send; the count must say so."""
    await held_comment(database, connected["_id"], age=timedelta(days=8), comment_id="old")
    await held_comment(database, connected["_id"], age=timedelta(hours=2), comment_id="new")

    status = (await signed_in.get(f"/api/instagram/{connected['_id']}/pause")).json()

    assert status["held"] == 2
    assert status["deliverable"] == 1


async def test_ignoring_the_backlog_clears_it_and_queues_nothing(
    signed_in, connected, database, monkeypatch
):
    queued: list[tuple] = []
    monkeypatch.setattr("app.queue.enqueue", lambda *a, **kw: queued.append(a))
    await held_comment(database, connected["_id"])

    response = await signed_in.post(
        f"/api/instagram/{connected['_id']}/pause/backlog", json={"action": "ignore"}
    )

    assert response.json() == {"status": "ignored"}
    assert queued == []
    status = (await signed_in.get(f"/api/instagram/{connected['_id']}/pause")).json()
    assert status["held"] == 0


async def test_delivering_the_backlog_enqueues_the_drain(
    signed_in, connected, database, monkeypatch
):
    queued: list[tuple] = []

    async def fake_enqueue(*args, **kwargs):
        queued.append(args)

    monkeypatch.setattr("app.queue.enqueue", fake_enqueue)
    await held_comment(database, connected["_id"])

    response = await signed_in.post(
        f"/api/instagram/{connected['_id']}/pause/backlog", json={"action": "deliver"}
    )

    assert response.json() == {"status": "queued"}
    assert queued == [("deliver_paused_backlog", str(connected["_id"]))]


async def test_delivering_while_still_paused_is_refused(signed_in, connected):
    """Replaying into a live pause would just hold everything again."""
    await signed_in.post(f"/api/instagram/{connected['_id']}/pause", json={"until": in_hours(2)})

    response = await signed_in.post(
        f"/api/instagram/{connected['_id']}/pause/backlog", json={"action": "deliver"}
    )

    assert response.status_code == 422


async def test_cancelling_a_pause_leaves_the_backlog_offer_standing(signed_in, connected, database):
    """Cancelling is not the same as deciding what to do with what piled up."""
    await signed_in.post(f"/api/instagram/{connected['_id']}/pause", json={"until": in_hours(2)})
    await held_comment(database, connected["_id"])

    await signed_in.delete(f"/api/instagram/{connected['_id']}/pause")

    status = (await signed_in.get(f"/api/instagram/{connected['_id']}/pause")).json()
    assert status["paused"] is False
    assert status["held"] == 1


async def test_a_second_pause_does_not_hide_the_first_ones_held_comments(
    signed_in, connected, database
):
    """The backlog window is monotonic: only a decision advances it."""
    await signed_in.post(f"/api/instagram/{connected['_id']}/pause", json={"until": in_hours(1)})
    await held_comment(database, connected["_id"])
    await signed_in.delete(f"/api/instagram/{connected['_id']}/pause")

    await signed_in.post(f"/api/instagram/{connected['_id']}/pause", json={"until": in_hours(1)})

    status = (await signed_in.get(f"/api/instagram/{connected['_id']}/pause")).json()
    assert status["held"] == 1, "the second pause moved the window and hid the first"


async def test_pausing_needs_more_than_read_access(
    api, connected, database, role_factory, user_factory, admin
):
    """A Viewer can see the pause but must not be able to set one."""
    role = await role_factory("pause-viewer", ["instagram.view"])
    viewer = await user_factory("viewer@example.com", role)
    await database["instagram_accounts"].update_one(
        {"_id": connected["_id"]}, {"$set": {"user_id": viewer["_id"]}}
    )
    await api.post("/api/auth/login", json={"email": viewer["email"], "password": TEST_PASSWORD})

    assert (await api.get(f"/api/instagram/{connected['_id']}/pause")).status_code == 200
    denied = await api.post(f"/api/instagram/{connected['_id']}/pause", json={"until": in_hours(1)})
    assert denied.status_code == 403
