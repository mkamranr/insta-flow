"""Configuring a workspace's own Meta app from the Settings page.

The App Secret must never come back. The verify token deliberately does -- see
the comment on `_meta_app_state` in `api/settings_api.py` for why that exception
exists and what fences it.
"""

import pytest

from app.config import get_settings
from app.db.repositories import MetaAppsRepository, RolesRepository, UsersRepository
from app.services.security.encryption import decrypt
from app.services.security.passwords import hash_password
from tests.conftest import TEST_PASSWORD

APP_SECRET = "the-app-secret-value"  # noqa: S105 - a fixture value


@pytest.fixture
async def other(database):
    return await UsersRepository(database).create(
        "zoe@example.com",
        hash_password(TEST_PASSWORD),
        role_id=await RolesRepository(database).ensure_owner(),
    )


async def test_saving_an_app_stores_the_secret_encrypted(signed_in, database, admin):
    response = await signed_in.put(
        "/api/settings/meta-app", json={"app_id": "1234567890", "app_secret": APP_SECRET}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True
    assert body["source"] == "workspace"
    assert body["app_id"] == "1234567890"

    stored = await MetaAppsRepository(database).for_user(admin["_id"])
    assert APP_SECRET not in str(stored)
    assert decrypt(stored["app_secret_encrypted"], get_settings().encryption_key) == APP_SECRET


async def test_the_app_secret_never_comes_back(signed_in):
    await signed_in.put(
        "/api/settings/meta-app", json={"app_id": "1234567890", "app_secret": APP_SECRET}
    )
    response = await signed_in.get("/api/settings/meta-app")
    assert APP_SECRET not in response.text
    assert response.json()["secret_set"] is True


async def test_a_verify_token_is_minted_on_first_save(signed_in):
    """Left to a separate step, this is what people skip and then spend an hour
    debugging as a webhook that never verifies."""
    body = (
        await signed_in.put(
            "/api/settings/meta-app", json={"app_id": "1234567890", "app_secret": APP_SECRET}
        )
    ).json()
    assert len(body["verify_token"]) > 20


async def test_the_app_id_can_be_corrected_without_retyping_the_secret(signed_in, database, admin):
    await signed_in.put(
        "/api/settings/meta-app", json={"app_id": "1234567890", "app_secret": APP_SECRET}
    )
    response = await signed_in.put("/api/settings/meta-app", json={"app_id": "9999999999"})
    assert response.status_code == 200
    assert response.json()["app_id"] == "9999999999"

    stored = await MetaAppsRepository(database).for_user(admin["_id"])
    assert decrypt(stored["app_secret_encrypted"], get_settings().encryption_key) == APP_SECRET


async def test_the_first_save_demands_a_secret(signed_in):
    response = await signed_in.put("/api/settings/meta-app", json={"app_id": "1234567890"})
    assert response.status_code == 422


async def test_regenerating_the_token_changes_it_and_says_what_that_costs(signed_in):
    first = (
        await signed_in.put(
            "/api/settings/meta-app", json={"app_id": "1234567890", "app_secret": APP_SECRET}
        )
    ).json()["verify_token"]

    body = (await signed_in.post("/api/settings/meta-app/verify-token")).json()
    assert body["verify_token"] != first
    assert "Verify and save" in body["note"]


async def test_the_urls_carry_the_workspace_prefix(signed_in, admin):
    body = (
        await signed_in.put(
            "/api/settings/meta-app", json={"app_id": "1234567890", "app_secret": APP_SECRET}
        )
    ).json()
    for url in body["urls"].values():
        assert f"/u/{admin['slug']}/" in url


async def test_the_urls_to_register_are_shown_before_an_app_is_saved(signed_in, admin):
    """The card's whole job, and it used to be impossible.

    `urls` answers "what do I register in my own Meta app?", so it must be
    workspace-prefixed even while the workspace is still riding on the
    instance-wide app. Showing the active set here meant you could not see the
    URLs to register until you had already saved the app that uses them.
    """
    body = (await signed_in.get("/api/settings/meta-app")).json()
    assert body["using_instance_app"] is True
    for url in body["urls"].values():
        assert f"/u/{admin['slug']}/" in url

    # And the active set still tells the truth about what is in force today.
    assert "/u/" not in body["active_urls"]["webhook_url"]


async def test_clearing_falls_the_workspace_back_to_the_instance(signed_in):
    await signed_in.put(
        "/api/settings/meta-app", json={"app_id": "1234567890", "app_secret": APP_SECRET}
    )
    body = (await signed_in.delete("/api/settings/meta-app")).json()
    assert body["source"] == "instance"
    assert body["has_own_row"] is False
    # What is *in force* goes back to unprefixed, because that is what the
    # instance app has registered...
    assert "/u/" not in body["active_urls"]["webhook_url"]
    # ...while the URLs to register in your own app stay prefixed, so the card
    # can still be acted on.
    assert "/u/" in body["urls"]["webhook_url"]


async def test_each_workspace_configures_its_own_app(signed_in, admin, other, database):
    """Two users, two Meta apps, no leakage between them."""
    await signed_in.put(
        "/api/settings/meta-app", json={"app_id": "admins-app", "app_secret": APP_SECRET}
    )
    await signed_in.put(
        f"/u/{other['slug']}/api/settings/meta-app",
        json={"app_id": "zoes-app", "app_secret": "zoes-secret"},
    )

    mine = (await signed_in.get("/api/settings/meta-app")).json()
    theirs = (await signed_in.get(f"/u/{other['slug']}/api/settings/meta-app")).json()
    assert mine["app_id"] == "admins-app"
    assert theirs["app_id"] == "zoes-app"
    assert mine["verify_token"] != theirs["verify_token"]

    apps = MetaAppsRepository(database)
    assert (await apps.for_user(admin["_id"]))["app_id"] == "admins-app"
    assert (await apps.for_user(other["_id"]))["app_id"] == "zoes-app"


# --- the instance origin ----------------------------------------------------


async def test_the_origin_override_beats_the_environment(signed_in):
    response = await signed_in.put(
        "/api/settings/instance", json={"origin": "https://your-machine.your-tailnet.ts.net/"}
    )
    assert response.status_code == 200
    assert response.json()["origin"] == "https://your-machine.your-tailnet.ts.net"
    # A save changes nothing at Meta, and the response has to say so.
    assert "Meta is not updated automatically" in response.json()["note"]

    body = (await signed_in.get("/api/settings/instance")).json()
    assert body["source"] == "settings"
    assert body["origin"] == "https://your-machine.your-tailnet.ts.net"


async def test_the_new_origin_flows_into_every_derived_url(signed_in, admin):
    await signed_in.put("/api/settings/instance", json={"origin": "https://moved.ts.net"})
    await signed_in.put(
        "/api/settings/meta-app", json={"app_id": "1234567890", "app_secret": APP_SECRET}
    )
    body = (await signed_in.get("/api/settings/meta-app")).json()
    assert body["urls"]["webhook_url"] == (
        f"https://moved.ts.net/u/{admin['slug']}/webhooks/instagram"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "your-machine.your-tailnet.ts.net",  # no scheme
        "https://",  # no host
        "https://host.ts.net/social",  # a path would be doubled onto every URL
        "ftp://host.ts.net",
    ],
)
async def test_a_malformed_origin_is_refused(signed_in, bad):
    assert (await signed_in.put("/api/settings/instance", json={"origin": bad})).status_code == 422


async def test_a_plain_http_origin_saves_but_warns(signed_in):
    """Meta rejects a non-HTTPS web_url button, and `tracked_buttons` silently
    stops rewriting links -- click-through then reads zero forever with nothing
    to say why."""
    body = (
        await signed_in.put("/api/settings/instance", json={"origin": "http://localhost:8080"})
    ).json()
    assert body["saved"] is True
    assert "HTTPS" in body["warning"]


# --- server-wide settings are Owner-only ------------------------------------


async def test_click_geography_is_out_of_reach_for_manager_and_viewer(
    api, database, role_factory, user_factory
):
    """Click geography and the public address are one setting for the whole
    server, so editing them edits them for every workspace at once. They were on
    `settings.*` alongside the disclosure wording, which meant "may reword an
    automated reply" also meant "may re-point the server".
    """
    role = await role_factory(
        "Runs automations", ["settings.view", "automations.view"]
    )
    await user_factory("runner@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "runner@example.com", "password": TEST_PASSWORD}
    )

    assert (await api.get("/api/settings/geoip")).status_code == 403
    assert (await api.get("/api/settings/instance")).status_code == 403
    assert (
        await api.put("/api/settings/instance", json={"origin": "https://evil.example"})
    ).status_code == 403

    # The disclosure line went the same way on 2026-09-07, and this assertion
    # was inverted deliberately. It had been treated as "per-deployment wording,
    # not server plumbing" -- but `workers/tasks._with_disclosure` takes only a
    # database handle and a message, so the one line it reads is appended to
    # EVERY workspace's replies. That is server-wide blast radius wearing a
    # per-workspace name, which is the exact mistake `instance.*` exists to
    # prevent. `settings.edit` guarded nothing else and was retired with it.
    assert (await api.get("/api/settings/disclosure")).status_code == 403
    assert (
        await api.put("/api/settings/disclosure", json={"enabled": False, "text": "x"})
    ).status_code == 403


async def test_the_owner_still_reaches_them(signed_in):
    assert (await signed_in.get("/api/settings/geoip")).status_code == 200
    assert (await signed_in.get("/api/settings/instance")).status_code == 200


async def test_viewing_does_not_imply_editing(api, database, role_factory, user_factory):
    role = await role_factory("Auditor", ["settings.view", "instance.view"])
    await user_factory("auditor@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "auditor@example.com", "password": TEST_PASSWORD}
    )

    assert (await api.get("/api/settings/geoip")).status_code == 200
    assert (await api.delete("/api/settings/geoip")).status_code == 403
    assert (
        await api.put("/api/settings/instance", json={"origin": "https://moved.ts.net"})
    ).status_code == 403


async def test_the_disclosure_is_one_line_for_the_whole_server(signed_in, database):
    """Pins the reason it is Owner-only.

    `_with_disclosure` reads the `app_settings` singleton and takes no account or
    workspace, so whatever is saved here is appended to every workspace's
    automated replies. If that ever becomes per-workspace, this test should fail
    and the permission should move back.
    """
    import inspect

    from app.workers.tasks import _with_disclosure

    parameters = set(inspect.signature(_with_disclosure).parameters)
    assert parameters == {"db", "message"}, (
        "if _with_disclosure gained an account or workspace parameter, the "
        "disclosure is no longer server-wide and instance.* is the wrong scope"
    )

    await signed_in.put(
        "/api/settings/disclosure", json={"enabled": True, "text": "Automated."}
    )
    saved = await database["app_settings"].find_one({"_id": "app"})
    assert saved["automation_disclosure"] == "Automated."
