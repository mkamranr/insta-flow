"""Redeeming an invitation: the unauthenticated half.

These are the security tests for the feature. The recurring assertion is that a
*rejected* request must leave the invite usable -- a 422 for a short password or
a taken slug must not burn a single-use token, or the recipient is locked out of
an account nobody can recreate for them.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.db.repositories import InvitesRepository
from app.services import slugs

GOOD = "another-correct-horse"


@pytest.fixture
def queued(monkeypatch):
    calls: list[dict] = []

    async def fake_enqueue(job, *args, **kwargs):
        calls.append({"job": job, **kwargs})
        return "job-1"

    monkeypatch.setattr("app.queue.enqueue", fake_enqueue)
    return calls


@pytest.fixture
async def invited(signed_in, queued):
    """An outstanding invitation, and the raw token from its email."""

    async def make(email: str = "invitee@example.com") -> tuple[str, dict]:
        response = await signed_in.post("/api/invites", json={"emails": [email]})
        assert response.status_code == 200, response.text
        invite = response.json()["sent"][0]
        return queued[-1]["context"]["invite_url"].split("token=", 1)[1], invite

    return make


def _accept(token: str, slug: str = "invitee", password: str = GOOD) -> dict:
    return {
        "token": token,
        "password": password,
        "password_confirm": password,
        "slug": slug,
    }


# --- lookup -----------------------------------------------------------------


async def test_the_lookup_returns_the_invited_email_and_nothing_else(api, invited):
    """Not the inviter, not the role, not the invite id, not whether other
    invites exist for the address."""
    token, _ = await invited()
    response = await api.post("/api/invite/lookup", json={"token": token})

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"email", "expires_at", "suggested_slug"}
    assert response.json()["email"] == "invitee@example.com"
    assert response.json()["suggested_slug"] == "invitee"


async def test_an_unknown_token_is_a_404(api, invited):
    await invited()
    assert (await api.post("/api/invite/lookup", json={"token": "nope"})).status_code == 404


async def test_a_tampered_token_is_a_404(api, invited):
    token, _ = await invited()
    flipped = ("b" if token[0] == "a" else "a") + token[1:]
    assert (await api.post("/api/invite/lookup", json={"token": flipped})).status_code == 404


@pytest.mark.parametrize("token", ["x", "../../etc/passwd", "a" * 512, "%00", "sel ect", "🙂"])
async def test_a_malformed_token_does_not_500(api, token):
    """Hashed unconditionally, whatever arrived, then looked up. No branch on
    shape, so nothing to crash on."""
    response = await api.post("/api/invite/lookup", json={"token": token})
    assert response.status_code == 404


async def test_an_expired_token_is_a_410_not_a_404(api, database, invited):
    """A deliberate departure from the house 404 rule, so it gets its own test.

    Only a holder of a 256-bit token reaches this path and no guesser can
    produce a once-valid one, so revealing "this was ours" costs nothing --
    while collapsing it into 404 would make clicking three days late
    indistinguishable from a corrupted link, which is the support question that
    actually happens.
    """
    token, _ = await invited()
    await database["invites"].update_one(
        {"email": "invitee@example.com"},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    response = await api.post("/api/invite/lookup", json={"token": token})
    assert response.status_code == 410
    assert "expired" in response.text.lower()


async def test_a_revoked_token_is_a_410(api, signed_in, invited):
    token, invite = await invited()
    await signed_in.post(f"/api/invites/{invite['id']}/revoke")

    response = await api.post("/api/invite/lookup", json={"token": token})
    assert response.status_code == 410
    assert "withdrawn" in response.text.lower()


async def test_the_expiry_boundary_is_exclusive(api, database, invited):
    token, _ = await invited()

    await database["invites"].update_one(
        {"email": "invitee@example.com"},
        {"$set": {"expires_at": datetime.now(UTC) + timedelta(seconds=30)}},
    )
    assert (await api.post("/api/invite/lookup", json={"token": token})).status_code == 200

    await database["invites"].update_one(
        {"email": "invitee@example.com"},
        {"$set": {"expires_at": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    assert (await api.post("/api/invite/lookup", json={"token": token})).status_code == 410


async def test_the_suggestion_does_not_reveal_taken_slugs(
    api, database, role_factory, user_factory, invited
):
    """The oracle test.

    `claim_slug` probes for a free name in a loop, so returning a suffixed
    `invitee-2` would tell an unauthenticated caller that `invitee` is taken --
    a free workspace-existence oracle, which is exactly what
    `current_workspace`'s 404-not-403 exists to prevent.
    """
    role = await role_factory("Someone", ["automations.view"])
    await user_factory("invitee@other.example", role)
    await database["users"].update_one(
        {"email": "invitee@other.example"}, {"$set": {"slug": "invitee"}}
    )

    token, _ = await invited()
    response = await api.post("/api/invite/lookup", json={"token": token})

    assert response.json()["suggested_slug"] == "invitee"


# --- accept -----------------------------------------------------------------


async def test_accepting_creates_the_user_with_the_invited_address(api, database, invited):
    token, _ = await invited()
    response = await api.post("/api/invite/accept", json=_accept(token))

    assert response.status_code == 201, response.text
    assert response.json()["email"] == "invitee@example.com"
    assert response.json()["role"]["slug"] == "workspace-owner"
    created = await database["users"].find_one({"email": "invitee@example.com"})
    assert created["slug"] == "invitee"
    assert created["is_active"] is True


async def test_an_email_in_the_request_body_is_ignored(api, database, invited):
    """The non-editable guarantee, enforced server-side.

    The form's `readOnly` attribute is presentation; this is the actual reason
    an invitee cannot sign up as somebody else.
    """
    token, _ = await invited()
    response = await api.post(
        "/api/invite/accept",
        json={**_accept(token), "email": "attacker@example.com"},
    )

    assert response.status_code == 201
    assert response.json()["email"] == "invitee@example.com"
    assert await database["users"].find_one({"email": "attacker@example.com"}) is None


async def test_the_role_comes_from_the_invite_not_the_request(api, database, invited):
    token, _ = await invited()
    owner = await database["roles"].find_one({"slug": "owner"})

    response = await api.post(
        "/api/invite/accept", json={**_accept(token), "role_id": str(owner["_id"])}
    )

    assert response.status_code == 201
    assert response.json()["role"]["slug"] == "workspace-owner"


async def test_accepting_signs_the_new_user_in(api, invited):
    """They have just chosen a password; asking for it again would be strange."""
    token, _ = await invited()
    await api.post("/api/invite/accept", json=_accept(token))

    me = await api.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "invitee@example.com"


async def test_an_invite_can_be_accepted_only_once(api, database, invited):
    token, _ = await invited()
    first = await api.post("/api/invite/accept", json=_accept(token))
    second = await api.post("/api/invite/accept", json=_accept(token, slug="second"))

    assert first.status_code == 201
    assert second.status_code == 410
    assert await database["users"].count_documents({"email": "invitee@example.com"}) == 1


async def test_two_simultaneous_accepts_produce_one_user(api, other_client, database, invited):
    """The status flip is the reservation, so the loser matches nothing."""
    import asyncio

    token, _ = await invited()
    results = await asyncio.gather(
        api.post("/api/invite/accept", json=_accept(token, slug="one")),
        other_client.post("/api/invite/accept", json=_accept(token, slug="two")),
        return_exceptions=True,
    )

    codes = sorted(r.status_code for r in results if hasattr(r, "status_code"))
    assert codes == [201, 410]
    assert await database["users"].count_documents({"email": "invitee@example.com"}) == 1


# --- rejected requests must not burn the invite -----------------------------


@pytest.mark.parametrize(
    ("payload_patch", "reason"),
    [
        ({"password": "short", "password_confirm": "short"}, "too short"),
        ({"password_confirm": "does-not-match-at-all"}, "mismatched"),
        ({"slug": "api"}, "reserved"),
        ({"slug": "A"}, "uppercase"),
        ({"slug": "-abc"}, "leading hyphen"),
        ({"slug": "abc-"}, "trailing hyphen"),
        ({"slug": "a--b"}, "doubled hyphen"),
        ({"slug": "a"}, "one character"),
        ({"slug": "x" * 33}, "too long"),
        ({"slug": "hello world"}, "space"),
    ],
)
async def test_a_rejected_request_leaves_the_invite_usable(
    api, database, invited, payload_patch, reason
):
    """Slug and password rules live on the pydantic model precisely so they run
    *before* the handler -- a 422 must not spend a single-use token."""
    token, _ = await invited()
    response = await api.post("/api/invite/accept", json={**_accept(token), **payload_patch})

    assert response.status_code == 422, f"{reason}: {response.text}"
    invite = await InvitesRepository(database).pending_for_email("invitee@example.com")
    assert invite is not None, f"{reason} burnt the invite"
    # ...and the invitee can still complete signup.
    assert (await api.post("/api/invite/accept", json=_accept(token))).status_code == 201


async def test_every_reserved_slug_is_refused(api, invited):
    token, _ = await invited()
    for reserved in sorted(slugs.RESERVED):
        response = await api.post("/api/invite/accept", json=_accept(token, slug=reserved))
        assert response.status_code == 422, reserved


async def test_a_taken_slug_is_a_conflict_and_the_invite_survives(
    api, database, role_factory, user_factory, invited
):
    """`uq_user_slug` decides, not a pre-check -- and the compensating release
    is what lets the invitee try again with a different name."""
    role = await role_factory("Holder", ["automations.view"])
    await user_factory("holder@example.com", role)
    await database["users"].update_one({"email": "holder@example.com"}, {"$set": {"slug": "taken"}})

    token, _ = await invited()
    clash = await api.post("/api/invite/accept", json=_accept(token, slug="taken"))

    assert clash.status_code == 409
    assert "taken" in clash.text.lower()
    invite = await InvitesRepository(database).pending_for_email("invitee@example.com")
    assert invite is not None, "the reservation was not released"

    retry = await api.post("/api/invite/accept", json=_accept(token, slug="mine"))
    assert retry.status_code == 201


async def test_an_address_registered_meanwhile_is_a_conflict(
    api, database, role_factory, user_factory, invited
):
    token, _ = await invited()
    role = await role_factory("Racer", ["automations.view"])
    await user_factory("invitee@example.com", role)

    response = await api.post("/api/invite/accept", json=_accept(token))
    assert response.status_code == 409
    assert "already has an account" in response.text.lower()


# --- what must never leak ---------------------------------------------------


async def test_no_response_on_this_path_contains_a_token(api, signed_in, invited):
    token, invite = await invited()
    responses = [
        await api.post("/api/invite/lookup", json={"token": token}),
        await signed_in.get("/api/invites"),
        await api.post("/api/invite/accept", json=_accept(token)),
    ]
    for response in responses:
        assert token not in response.text
        assert "token_hash" not in response.text


async def test_the_new_user_holds_no_cross_tenant_permission(api, database, invited):
    """Tenant isolation, proven through the created account rather than asserted
    about the role definition."""
    token, _ = await invited()
    await api.post("/api/invite/accept", json=_accept(token))

    me = await api.get("/api/auth/me")
    granted = set(me.json()["permissions"])
    for forbidden in (
        "users.view",
        "users.manage",
        "roles.view",
        "workspaces.view",
        "instance.view",
        "instance.manage",
    ):
        assert forbidden not in granted, forbidden
    assert "instagram.manage" in granted
    assert "settings.view" in granted


async def test_the_new_user_cannot_reach_another_workspace(api, admin, invited):
    """404, not 403: a 403 would confirm the slug exists."""
    token, _ = await invited()
    await api.post("/api/invite/accept", json=_accept(token))

    response = await api.get(f"/u/{admin['slug']}/api/rules")
    assert response.status_code == 404


async def test_the_new_user_cannot_list_people_or_touch_the_server(api, invited):
    token, _ = await invited()
    await api.post("/api/invite/accept", json=_accept(token))

    for path in ("/api/users", "/api/roles", "/api/settings/instance", "/api/settings/email"):
        assert (await api.get(path)).status_code == 403, path


async def test_the_new_user_can_configure_their_own_meta_app(api, invited):
    """The whole point of the role: `instagram.manage` on their own workspace."""
    token, _ = await invited()
    await api.post("/api/invite/accept", json=_accept(token))

    response = await api.get("/api/settings/meta-app")
    assert response.status_code == 200


async def test_the_new_user_sees_no_contacts_from_another_workspace(api, database, admin, invited):
    """A contact belonging to the Owner's account must not appear for a tenant.

    `analytics._account_id` derives the account from the *workspace*, so a
    fresh user with no connected account has nothing to read -- but that has to
    be asserted against a database that actually holds somebody else's row,
    otherwise the test would pass on an empty collection either way.
    """
    await database["contacts"].insert_one(
        {
            "instagram_account_id": admin["_id"],
            "instagram_user_id": "17841400000000000",
            "email": "someone.elses.lead@example.com",
            "sources": [],
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )

    token, _ = await invited()
    await api.post("/api/invite/accept", json=_accept(token))

    response = await api.get("/api/analytics/contacts")
    assert response.status_code == 200
    assert "someone.elses.lead@example.com" not in response.text


# --- throttling -------------------------------------------------------------


async def test_the_lookup_is_throttled(api, invited, settings):
    token, _ = await invited()
    codes = []
    for _ in range(settings.invite_lookup_limit + 1):
        codes.append((await api.post("/api/invite/lookup", json={"token": token})).status_code)
    assert codes[-1] == 429
    assert codes[0] == 200


async def test_the_accept_endpoint_is_throttled(api, invited, settings):
    token, _ = await invited()
    codes = []
    for _ in range(settings.invite_accept_limit + 1):
        codes.append(
            (await api.post("/api/invite/accept", json=_accept(token, slug="nope"))).status_code
        )
    assert codes[-1] == 429
