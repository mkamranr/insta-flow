"""The webhook, once more than one Meta app can call it.

This is the reason workspaces are a URL prefix rather than a header or a cookie.
Both endpoints have to pick a secret *before* they can trust anything else in the
request, and the path is the only thing available at that point.

A rejection here is 403: `WebhookValidationError`. Meta retries a 5xx and gives
up on a 4xx, which is right -- a payload we cannot verify will never verify on a
retry either.
"""

import json

import pytest

from app.config import get_settings
from app.db.repositories import MetaAppsRepository, RolesRepository, UsersRepository
from app.services.meta.webhooks import sign
from app.services.security.encryption import encrypt

ZOE_SECRET = "zoes-app-secret"
ZOE_TOKEN = "zoes-verify-token"  # noqa: S105 - a fixture value, not a real secret


def payload(account_ig_id="17841499999999999"):
    return {
        "object": "instagram",
        "entry": [
            {
                "id": account_ig_id,
                "time": 1520383571,
                "changes": [
                    {
                        "field": "comments",
                        "value": {
                            "from": {"id": "17800000000000000", "username": "someone"},
                            "comment_id": "17900000000000001",
                            "text": "send me the link",
                            "media": {"id": "17890000000000000", "media_product_type": "FEED"},
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
async def zoe(database):
    """A workspace with its own Meta app, distinct from the environment's."""
    settings = get_settings()
    user = await UsersRepository(database).create(
        "zoe@example.com", "hash", role_id=await RolesRepository(database).ensure_owner()
    )
    await MetaAppsRepository(database).save(
        user["_id"],
        {
            "app_id": "zoes-app",
            "app_secret_encrypted": encrypt(ZOE_SECRET, settings.encryption_key),
            "verify_token_encrypted": encrypt(ZOE_TOKEN, settings.encryption_key),
        },
    )
    return user


@pytest.fixture
def enqueued(monkeypatch):
    calls: list[tuple] = []

    async def fake_enqueue(job, *args, **kwargs):
        calls.append((job, args))
        return "job-id"

    monkeypatch.setattr("app.queue.enqueue", fake_enqueue)
    return calls


# --- the handshake ----------------------------------------------------------


async def test_the_handshake_uses_this_workspaces_token(api, zoe):
    response = await api.get(
        f"/u/{zoe['slug']}/webhooks/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": ZOE_TOKEN,
            "hub.challenge": "challenge-42",
        },
    )
    assert response.status_code == 200
    assert response.text == "challenge-42"
    assert response.headers["content-type"].startswith("text/plain")


async def test_one_workspaces_token_does_not_open_another(api, zoe, database):
    """With N apps there are N valid tokens. `hub.verify_token` says only that it
    is one of them, never which -- so the path has to decide."""
    settings = get_settings()
    other = await UsersRepository(database).create(
        "rae@example.com", "hash", role_id=await RolesRepository(database).ensure_owner()
    )
    await MetaAppsRepository(database).save(
        other["_id"],
        {
            "app_id": "raes-app",
            "app_secret_encrypted": encrypt("raes-secret", settings.encryption_key),
            "verify_token_encrypted": encrypt("raes-token", settings.encryption_key),
        },
    )

    response = await api.get(
        f"/u/{other['slug']}/webhooks/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": ZOE_TOKEN,
            "hub.challenge": "nope",
        },
    )
    assert response.status_code == 403


async def test_an_unknown_slug_never_verifies(api):
    """It must not fall back to the instance token: that would answer a handshake
    at a URL no Meta app was ever configured with."""
    settings = get_settings()
    response = await api.get(
        "/u/nobody-here/webhooks/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.meta_webhook_verify_token or "x",
            "hub.challenge": "nope",
        },
    )
    assert response.status_code == 403


# --- the signature ----------------------------------------------------------


async def test_a_payload_signed_by_this_workspaces_app_is_accepted(api, zoe, database, enqueued):
    body = json.dumps(payload()).encode()
    response = await api.post(
        f"/u/{zoe['slug']}/webhooks/instagram",
        content=body,
        headers={"X-Hub-Signature-256": sign(body, ZOE_SECRET), "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"
    assert enqueued


async def test_a_payload_signed_by_another_app_is_rejected(api, zoe, database):
    """The body is unverified until the signature passes, so the account id
    inside it cannot be trusted to choose the key that would verify it. Only the
    path can, and here it names a workspace whose secret does not match."""
    settings = get_settings()
    other = await UsersRepository(database).create(
        "rae@example.com", "hash", role_id=await RolesRepository(database).ensure_owner()
    )
    await MetaAppsRepository(database).save(
        other["_id"],
        {
            "app_id": "raes-app",
            "app_secret_encrypted": encrypt("raes-secret", settings.encryption_key),
        },
    )

    body = json.dumps(payload()).encode()
    response = await api.post(
        f"/u/{other['slug']}/webhooks/instagram",
        content=body,
        headers={"X-Hub-Signature-256": sign(body, ZOE_SECRET), "Content-Type": "application/json"},
    )
    assert response.status_code == 403


async def test_the_unprefixed_route_still_uses_the_environment(api, database, enqueued):
    """The regression that matters on upgrade: the already-live Meta app calls
    the bare URL and must keep working, with no `meta_apps` row anywhere."""
    secret = get_settings().meta_app_secret
    if not secret:
        pytest.skip("no META_APP_SECRET configured in this environment")

    body = json.dumps(payload(account_ig_id="17841400000000000")).encode()
    response = await api.post(
        "/webhooks/instagram",
        content=body,
        headers={"X-Hub-Signature-256": sign(body, secret), "Content-Type": "application/json"},
    )
    assert response.status_code == 200


async def test_a_workspace_without_its_own_app_falls_back_to_the_environment(
    api, database, enqueued
):
    """Forgiving on purpose: a workspace that has not configured an app yet is
    still a real workspace, and the instance app is the only one that could be
    calling."""
    secret = get_settings().meta_app_secret
    if not secret:
        pytest.skip("no META_APP_SECRET configured in this environment")

    user = await UsersRepository(database).create(
        "noapp@example.com", "hash", role_id=await RolesRepository(database).ensure_owner()
    )
    body = json.dumps(payload()).encode()
    response = await api.post(
        f"/u/{user['slug']}/webhooks/instagram",
        content=body,
        headers={"X-Hub-Signature-256": sign(body, secret), "Content-Type": "application/json"},
    )
    assert response.status_code == 200


# --- the legal pages Meta requires ------------------------------------------


async def test_each_workspace_gets_its_own_legal_urls(api, zoe):
    """A reviewer reading `/u/<slug>/privacy` must be shown that workspace's
    deletion callback, not somebody else's."""
    response = await api.get(f"/u/{zoe['slug']}/privacy")
    assert response.status_code == 200
    assert f"/u/{zoe['slug']}/data-deletion" in response.text
