"""Meta's signed_request. These callbacks are public URLs, so verification is
the only thing standing between them and anyone disconnecting an account."""

import base64
import hashlib
import hmac
import json

import pytest
from bson import ObjectId

from app.config import get_settings
from app.services.meta.signed_request import parse
from app.services.security.encryption import encrypt

SECRET = "app-secret"
IG_ID = "17841400000000000"


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def make(payload: dict, secret: str = SECRET) -> str:
    encoded = b64(json.dumps(payload).encode())
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{b64(signature)}.{encoded}"


# --- verification -----------------------------------------------------------


def test_a_genuine_request_parses():
    payload = {"algorithm": "HMAC-SHA256", "user_id": IG_ID, "issued_at": 1788196868}
    assert parse(make(payload), SECRET)["user_id"] == IG_ID


def test_a_request_signed_with_another_secret_is_rejected():
    assert parse(make({"user_id": IG_ID}, "other-secret"), SECRET) is None


def test_a_tampered_payload_is_rejected():
    """Swapping the payload keeps the old signature, which must no longer match."""
    signature = make({"user_id": IG_ID}).split(".")[0]
    forged = b64(json.dumps({"user_id": "someone-else"}).encode())
    assert parse(f"{signature}.{forged}", SECRET) is None


@pytest.mark.parametrize("value", ["", "nodot", ".", "a.b", "!!!.???"])
def test_malformed_input_is_rejected(value):
    assert parse(value, SECRET) is None


def test_an_empty_secret_never_verifies():
    assert parse(make({"user_id": IG_ID}), "") is None


def test_an_unexpected_algorithm_is_rejected():
    """The field exists so a surprise can be refused rather than assumed."""
    assert parse(make({"algorithm": "NONE", "user_id": IG_ID}), SECRET) is None


def test_padding_is_restored_before_decoding():
    """Meta strips base64 padding; a decoder that demands it fails at random."""
    for user_id in ("1", "12", "123", "1234"):
        assert parse(make({"user_id": user_id}), SECRET)["user_id"] == user_id


# --- the endpoints ----------------------------------------------------------


@pytest.fixture
async def connected(database):
    settings = get_settings()
    account_id = ObjectId()
    await database["instagram_accounts"].insert_one(
        {
            "_id": account_id,
            "instagram_user_id": IG_ID,
            "username": "myshop",
            "status": "connected",
            "webhook_subscribed": True,
            "access_token_encrypted": encrypt("a-real-token", settings.encryption_key),
        }
    )
    return account_id


async def test_a_verified_deauthorize_drops_the_token(api, database, connected):
    """Consent is withdrawn, so we must stop being able to act as them."""
    settings = get_settings()
    payload = make({"algorithm": "HMAC-SHA256", "user_id": IG_ID}, settings.meta_app_secret)

    response = await api.post(
        "/api/auth/instagram/deauthorize", data={"signed_request": payload}
    )
    assert response.status_code == 200

    account = await database["instagram_accounts"].find_one({"_id": connected})
    assert account["status"] == "disconnected"
    assert account["access_token_encrypted"] == ""
    assert account["webhook_subscribed"] is False


async def test_an_unverified_deauthorize_changes_nothing(api, database, connected):
    """Otherwise anyone could disconnect the account by POSTing to a public URL."""
    response = await api.post(
        "/api/auth/instagram/deauthorize", data={"signed_request": make({"user_id": IG_ID})}
    )
    assert response.json()["status"] == "ignored"

    account = await database["instagram_accounts"].find_one({"_id": connected})
    assert account["status"] == "connected"
    assert account["access_token_encrypted"] != ""


async def test_a_deauthorize_with_no_payload_is_acknowledged_not_errored(api, database, connected):
    """Meta retries failures, and a payload we cannot verify never will verify."""
    response = await api.post("/api/auth/instagram/deauthorize")
    assert response.status_code == 200
    assert (await database["instagram_accounts"].find_one({"_id": connected}))["status"] == (
        "connected"
    )


async def test_a_verified_data_deletion_removes_the_account(api, database, connected):
    settings = get_settings()
    payload = make({"algorithm": "HMAC-SHA256", "user_id": IG_ID}, settings.meta_app_secret)

    response = await api.post(
        "/api/auth/instagram/data-deletion", data={"signed_request": payload}
    )
    body = response.json()
    assert body["url"].endswith("/data-deletion")
    assert body["confirmation_code"] == f"ig-{IG_ID}"
    assert await database["instagram_accounts"].count_documents({}) == 0


async def test_an_unverified_data_deletion_removes_nothing(api, database, connected):
    response = await api.post(
        "/api/auth/instagram/data-deletion", data={"signed_request": make({"user_id": IG_ID})}
    )
    assert response.json()["confirmation_code"] == "unverified"
    assert await database["instagram_accounts"].count_documents({}) == 1


async def test_visiting_deauthorize_in_a_browser_explains_itself(api):
    """A human landing here should not be shown raw JSON."""
    response = await api.get("/api/auth/instagram/deauthorize")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Deauthorize callback" in response.text
