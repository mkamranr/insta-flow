"""Sending, resending and revoking invitations."""

import pytest

from app.db.repositories import InvitesRepository
from app.db.repositories.invites import hash_token
from app.services.invites import INVITE_EXPIRY_KEY

from .conftest import TEST_PASSWORD


@pytest.fixture
def queued(monkeypatch):
    """Capture enqueued mail jobs. The autouse FakeRedis has no `enqueue_job`."""
    calls: list[dict] = []

    async def fake_enqueue(job, *args, **kwargs):
        calls.append({"job": job, **kwargs})
        return "job-1"

    monkeypatch.setattr("app.queue.enqueue", fake_enqueue)
    return calls


def _url(queued: list[dict]) -> str:
    return queued[-1]["context"]["invite_url"]


async def test_an_invite_is_created_and_the_email_is_enqueued(signed_in, database, queued):
    response = await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["email"] for i in body["sent"]] == ["new@example.com"]
    assert body["skipped"] == []
    assert body["sent"][0]["status"] == "pending"
    # The role defaults to the seeded tenant role, never Owner.
    assert body["sent"][0]["role"]["slug"] == "workspace-owner"

    assert len(queued) == 1
    assert queued[0]["job"] == "send_email"
    assert queued[0]["purpose"] == "invite"
    assert queued[0]["to_address"] == "new@example.com"


async def test_the_token_is_never_stored_in_plaintext(signed_in, database, queued):
    """A database dump must yield no usable links: MongoDB has no auth."""
    await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})

    token = _url(queued).split("token=", 1)[1]
    row = await database["invites"].find_one({"email": "new@example.com"})

    assert token not in str(row)
    assert row["token_hash"] == hash_token(token)


async def test_the_response_never_carries_the_hash(signed_in, queued):
    """`InvitePublic.from_document`, never `serialise()` -- the trap `UserPublic`
    documents about `password_hash`."""
    created = await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})
    listed = await signed_in.get("/api/invites")

    for response in (created, listed):
        assert "token_hash" not in response.text
    # The raw link IS returned once, by the route that mints it, so an install
    # with no mail configured is still usable.
    assert created.json()["sent"][0]["url"].startswith("http")
    assert listed.json()["invites"][0]["url"] is None


async def test_the_link_uses_the_configured_origin_not_the_host_header(signed_in, queued, settings):
    """Otherwise anyone who can reach the API with a forged Host -- or point a
    second hostname at the same Caddy -- could make the server email their own
    domain a live token."""
    await signed_in.post(
        "/api/invites",
        json={"emails": ["new@example.com"]},
        headers={"Host": "evil.example"},
    )
    url = _url(queued)
    assert "evil.example" not in url
    assert url.startswith(settings.public_base_url.rstrip("/"))


async def test_the_link_uses_the_saved_origin_override(signed_in, database, queued):
    from app.db.repositories import AppSettingsRepository

    await AppSettingsRepository(database).save({"public_base_url": "https://saved.example"})
    await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})

    assert _url(queued).startswith("https://saved.example/invite?token=")


async def test_a_second_invite_to_a_live_address_is_skipped(signed_in, database, queued):
    """`uq_invite_pending_email` refuses it. Caught, never pre-checked."""
    await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})
    second = await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})

    assert second.json()["sent"] == []
    assert second.json()["skipped"] == [{"email": "new@example.com", "reason": "already_invited"}]
    assert await database["invites"].count_documents({"email": "new@example.com"}) == 1


async def test_an_address_that_already_has_an_account_is_skipped(signed_in, admin, queued):
    response = await signed_in.post("/api/invites", json={"emails": [admin["email"]]})

    assert response.json()["skipped"] == [{"email": admin["email"], "reason": "already_registered"}]
    assert queued == []


async def test_a_duplicate_address_in_one_request_is_sent_once(signed_in, queued):
    """One person captured by two automations on two posts arrives twice."""
    response = await signed_in.post(
        "/api/invites", json={"emails": ["dup@example.com", "DUP@example.com"]}
    )
    assert len(response.json()["sent"]) == 1
    assert len(queued) == 1


async def test_revoking_frees_the_address_for_a_new_invite(signed_in, database, queued):
    """Pins the value-based partial filter: a plain unique index on `email`
    would block re-inviting the same person forever."""
    created = await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})
    invite_id = created.json()["sent"][0]["id"]

    await signed_in.post(f"/api/invites/{invite_id}/revoke")
    again = await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})

    assert len(again.json()["sent"]) == 1
    assert await database["invites"].count_documents({"email": "new@example.com"}) == 2


async def test_resending_rotates_the_token_and_kills_the_old_link(signed_in, queued):
    """Exactly one live link per invite at any moment, so a forwarded first
    email stops working the instant the operator resends."""
    created = await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})
    invite_id = created.json()["sent"][0]["id"]
    first_token = _url(queued).split("token=", 1)[1]

    resent = await signed_in.post(f"/api/invites/{invite_id}/resend")
    assert resent.status_code == 200, resent.text
    second_token = _url(queued).split("token=", 1)[1]
    assert first_token != second_token
    assert resent.json()["sent_count"] == 2

    # The old link is dead, the new one works.
    assert (
        await signed_in.post("/api/invite/lookup", json={"token": first_token})
    ).status_code == 404
    assert (
        await signed_in.post("/api/invite/lookup", json={"token": second_token})
    ).status_code == 200


async def test_resending_cannot_revive_a_revoked_invite(signed_in, queued):
    created = await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})
    invite_id = created.json()["sent"][0]["id"]
    await signed_in.post(f"/api/invites/{invite_id}/revoke")

    response = await signed_in.post(f"/api/invites/{invite_id}/resend")
    assert response.status_code == 409


async def test_revoking_twice_is_a_conflict_not_a_silent_success(signed_in, queued):
    created = await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})
    invite_id = created.json()["sent"][0]["id"]

    assert (await signed_in.post(f"/api/invites/{invite_id}/revoke")).status_code == 200
    assert (await signed_in.post(f"/api/invites/{invite_id}/revoke")).status_code == 409


async def test_an_unknown_invite_id_is_a_404(signed_in):
    from bson import ObjectId

    response = await signed_in.post(f"/api/invites/{ObjectId()}/revoke")
    assert response.status_code == 404


async def test_a_malformed_invite_id_is_a_422(signed_in):
    assert (await signed_in.post("/api/invites/not-an-id/revoke")).status_code == 422


async def test_the_expiry_window_comes_from_the_setting(signed_in, database, queued):
    from app.db.repositories import AppSettingsRepository

    await AppSettingsRepository(database).save({INVITE_EXPIRY_KEY: 1})
    await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})

    invite = await database["invites"].find_one({"email": "new@example.com"})
    from datetime import UTC, datetime

    hours = (invite["expires_at"] - datetime.now(UTC)).total_seconds() / 3600
    assert 0.9 < hours < 1.1


async def test_changing_the_setting_does_not_move_an_existing_deadline(signed_in, database, queued):
    """The email states a deadline in words. Lengthening the setting must not
    resurrect links the operator believed dead."""
    from app.db.repositories import AppSettingsRepository

    store = AppSettingsRepository(database)
    await store.save({INVITE_EXPIRY_KEY: 1})
    await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})
    before = (await database["invites"].find_one({"email": "new@example.com"}))["expires_at"]

    await store.save({INVITE_EXPIRY_KEY: 720})
    after = (await database["invites"].find_one({"email": "new@example.com"}))["expires_at"]
    assert before == after


async def test_the_admin_list_reports_expired_without_storing_it(signed_in, database, queued):
    """`expired` is derived from the clock. A stored one would need a sweeper,
    and until it ran the database would be lying about its own state."""
    from datetime import UTC, datetime, timedelta

    await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})
    await database["invites"].update_one(
        {"email": "new@example.com"},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(hours=1)}},
    )

    listed = await signed_in.get("/api/invites")
    assert listed.json()["invites"][0]["status"] == "expired"
    stored = await database["invites"].find_one({"email": "new@example.com"})
    assert stored["status"] == "pending"


async def test_the_expired_filter_and_the_pending_filter_do_not_overlap(
    signed_in, database, queued
):
    from datetime import UTC, datetime, timedelta

    await signed_in.post("/api/invites", json={"emails": ["live@example.com"]})
    await signed_in.post("/api/invites", json={"emails": ["stale@example.com"]})
    await database["invites"].update_one(
        {"email": "stale@example.com"},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(hours=1)}},
    )

    pending = await signed_in.get("/api/invites?invite_status=pending")
    expired = await signed_in.get("/api/invites?invite_status=expired")

    assert [i["email"] for i in pending.json()["invites"]] == ["live@example.com"]
    assert [i["email"] for i in expired.json()["invites"]] == ["stale@example.com"]


async def test_an_invite_records_an_audit_entry_without_the_token(signed_in, database, queued):
    await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})

    token = _url(queued).split("token=", 1)[1]
    rows = [r async for r in database["audit_logs"].find({"action": "invite.created"})]
    assert len(rows) == 1
    assert token not in str(rows[0])
    assert hash_token(token) not in str(rows[0])


async def test_the_role_cannot_be_escalated_beyond_the_inviter(
    api, database, role_factory, user_factory, queued
):
    """`role_you_may_grant`: `users.manage` without `roles.manage` must not be
    enough to hand somebody the Owner role and take the deployment."""
    from app.db.repositories import RolesRepository

    limited = await role_factory("Inviter", ["users.manage", "users.view"])
    await user_factory("inviter@example.com", limited)
    await api.post(
        "/api/auth/login", json={"email": "inviter@example.com", "password": TEST_PASSWORD}
    )

    owner = await RolesRepository(database).get_by_slug("owner")
    response = await api.post(
        "/api/invites",
        json={"emails": ["new@example.com"], "role_id": str(owner["_id"])},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/api/invites", "/u/{slug}/api/invites"])
async def test_only_users_manage_may_invite(api, database, role_factory, user_factory, admin, path):
    """The walker proves *a* permission is declared; this pins which one, at
    both mounts."""
    viewer = await role_factory("Can see people", ["users.view"])
    await user_factory("seer@example.com", viewer)
    await api.post("/api/auth/login", json={"email": "seer@example.com", "password": TEST_PASSWORD})

    url = path.replace("{slug}", admin["slug"])
    assert (await api.post(url, json={"emails": ["a@b.com"]})).status_code == 403
    # ...but listing is allowed, matching the Users page split.
    assert (await api.get(url)).status_code == 200


async def test_an_inviter_who_is_later_disabled_leaves_the_invite_live(
    signed_in, other_client, database, role_factory, user_factory, queued
):
    """Not cascaded, deliberately: the invite's authority came from the
    permission held when it was created, and rotating staff must not silently
    kill a dozen legitimate pending invites. Surfaced instead, so an Owner can
    revoke them on purpose.

    Two sessions, because `signed_in` shares its cookie jar with `api`: the
    departing admin sends the invite, then the Owner deactivates them and looks
    at the list.
    """
    role = await role_factory("Departing admin", ["users.manage", "users.view"])
    leaver = await user_factory("leaver@example.com", role)
    await other_client.post(
        "/api/auth/login", json={"email": "leaver@example.com", "password": TEST_PASSWORD}
    )
    await other_client.post("/api/invites", json={"emails": ["new@example.com"]})

    await database["users"].update_one({"_id": leaver["_id"]}, {"$set": {"is_active": False}})

    listed = await signed_in.get("/api/invites")
    assert listed.status_code == 200
    row = listed.json()["invites"][0]
    assert row["status"] == "pending"
    assert row["invited_by_email"] == "leaver@example.com"
    assert row["invited_by_active"] is False


async def test_a_failed_enqueue_leaves_the_invite_usable(signed_in, database, monkeypatch):
    """The invite is valid; only the mail failed. The operator can still hand
    over the link, so rolling back would destroy the wrong thing."""

    async def boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.queue.enqueue", boom)
    response = await signed_in.post("/api/invites", json={"emails": ["new@example.com"]})

    assert response.status_code == 200
    assert response.json()["sent"][0]["url"].startswith("http")
    invite = await InvitesRepository(database).pending_for_email("new@example.com")
    assert invite is not None
    assert invite["last_send_status"] == "failed"


# --- the expiry setting -----------------------------------------------------


async def test_the_expiry_setting_defaults_without_being_written(signed_in):
    """`saved.get(key, DEFAULT)`, so an install that never opened the page
    behaves identically to one that saved 48."""
    response = await signed_in.get("/api/settings/invites")
    assert response.status_code == 200
    assert response.json()["expiry_hours"] == 48
    assert response.json()["source"] == "default"


async def test_the_expiry_setting_can_be_changed(signed_in):
    saved = await signed_in.put("/api/settings/invites", json={"expiry_hours": 72})
    assert saved.status_code == 200
    assert saved.json()["expiry_hours"] == 72
    assert saved.json()["source"] == "settings"
    # Said in the response rather than left for the operator to assume.
    assert "already sent" in saved.json()["note"]
    assert (await signed_in.get("/api/settings/invites")).json()["expiry_hours"] == 72


@pytest.mark.parametrize("hours", [0, -1, 721, 100000])
async def test_the_expiry_setting_is_bounded(signed_in, hours):
    """A bearer credential that mints an account and lasts a quarter is not an
    invitation, it is a back door. One hour at the other end, because below that
    the mail may not arrive in time."""
    response = await signed_in.put("/api/settings/invites", json={"expiry_hours": hours})
    assert response.status_code == 422


async def test_the_expiry_setting_is_owner_only(api, database, role_factory, user_factory):
    """One window for the whole deployment, so `instance.*`. `settings.view`
    opens the page but must not carry any server-wide power with it."""
    role = await role_factory("Page access only", ["settings.view"])
    await user_factory("wording@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "wording@example.com", "password": TEST_PASSWORD}
    )

    assert (await api.get("/api/settings/invites")).status_code == 403
    assert (await api.put("/api/settings/invites", json={"expiry_hours": 72})).status_code == 403


# --- contacts integration ---------------------------------------------------


async def test_a_contact_is_badged_once_invited(signed_in, database, admin, queued):
    """Computed into the contacts payload rather than fetched separately: both
    `/api/invites` and `/api/users` need permissions a `contacts.view`-only role
    does not hold, so a second request would 403 for exactly the person the
    Contacts page is built for."""
    from datetime import UTC, datetime

    from app.db.repositories import InstagramAccountsRepository

    account = await InstagramAccountsRepository(database).upsert(
        "17841400000000001",
        {
            "user_id": admin["_id"],
            "username": "tester",
            "is_default": True,
            "status": "active",
        },
    )
    await database["contacts"].insert_one(
        {
            "instagram_account_id": account["_id"],
            "instagram_user_id": "1",
            "email": "lead@example.com",
            "sources": [],
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )

    before = await signed_in.get("/api/analytics/contacts")
    assert before.json()[0]["contacts"][0]["invite_state"] == "none"

    await signed_in.post("/api/invites", json={"emails": ["lead@example.com"]})

    after = await signed_in.get("/api/analytics/contacts")
    assert after.json()[0]["contacts"][0]["invite_state"] == "invited"
