"""Phase 6. Covers the nine cases §79 requires, plus the four parser corrections
recorded in docs/meta-api.md §5.6."""

import dataclasses
import json

import pytest

from app.config import get_settings
from app.services.meta.webhooks import (
    ParsedComment,
    parse_comment_events,
    sign,
    verify_signature,
    verify_subscription,
)

SECRET = get_settings().meta_app_secret or "test-secret"
VERIFY_TOKEN = get_settings().meta_webhook_verify_token
ACCOUNT_IG_ID = "17841400000000000"
COMMENTER_IG_ID = "17800000000000000"


def comment_payload(
    *,
    text="Can you send me the tools?",
    comment_id="17900000000000000",
    from_id=COMMENTER_IG_ID,
    username="john123",
    id_key="comment_id",
):
    value = {
        "from": {"id": from_id, "username": username},
        id_key: comment_id,
        "text": text,
        "media": {"id": "17890000000000000", "media_product_type": "REELS"},
    }
    return {
        "object": "instagram",
        "entry": [
            {
                "id": ACCOUNT_IG_ID,
                "time": 1520383571,
                "changes": [{"field": "comments", "value": value}],
            }
        ],
    }


@pytest.fixture
def enqueued(monkeypatch):
    """Capture enqueue calls instead of needing a live Redis."""
    calls: list[tuple] = []

    async def fake_enqueue(job, *args, **kwargs):
        calls.append((job, args))
        return "job-id"

    monkeypatch.setattr("app.queue.enqueue", fake_enqueue)
    return calls


async def post_webhook(api, payload, *, secret=SECRET):
    body = json.dumps(payload).encode()
    return await api.post(
        "/webhooks/instagram",
        content=body,
        headers={"X-Hub-Signature-256": sign(body, secret), "Content-Type": "application/json"},
    )


# --- REQUIRED: verification handshake ---------------------------------------


def test_verification_returns_the_challenge():
    assert verify_subscription("subscribe", "tok", "12345", "tok") == "12345"


def test_verification_rejects_a_wrong_token():
    assert verify_subscription("subscribe", "wrong", "12345", "tok") is None


def test_verification_rejects_a_wrong_mode():
    assert verify_subscription("unsubscribe", "tok", "12345", "tok") is None


def test_verification_rejects_when_no_token_is_configured():
    """An unset verify token must never accept every request."""
    assert verify_subscription("subscribe", "anything", "1", "") is None


async def test_get_verification_succeeds(api):
    response = await api.get(
        "/webhooks/instagram",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": VERIFY_TOKEN,
            "hub.challenge": "1158201444",
        },
    )
    assert response.status_code == 200
    # Must be the bare challenge as text/plain -- not JSON-quoted.
    assert response.text == "1158201444"
    assert response.headers["content-type"].startswith("text/plain")


async def test_get_verification_fails_with_a_bad_token(api):
    response = await api.get(
        "/webhooks/instagram",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "1"},
    )
    assert response.status_code == 403


# --- REQUIRED: signature validation -----------------------------------------


def test_valid_signature_passes():
    body = b'{"object":"instagram"}'
    assert verify_signature(body, sign(body, SECRET), SECRET) is True


def test_signature_over_different_bytes_fails():
    """Re-serialised JSON has a different digest even for the same document."""
    assert verify_signature(b'{"a":1}', sign(b'{"a": 1}', SECRET), SECRET) is False


def test_missing_signature_header_fails():
    assert verify_signature(b"{}", None, SECRET) is False


def test_signature_without_the_sha256_prefix_fails():
    assert verify_signature(b"{}", "abc123", SECRET) is False


def test_signature_with_the_wrong_secret_fails():
    body = b'{"object":"instagram"}'
    assert verify_signature(body, sign(body, "other-secret"), SECRET) is False


def test_empty_app_secret_never_validates():
    body = b"{}"
    assert verify_signature(body, sign(body, ""), "") is False


async def test_post_with_an_invalid_signature_is_rejected(api, enqueued):
    body = json.dumps(comment_payload()).encode()
    response = await api.post(
        "/webhooks/instagram",
        content=body,
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert response.status_code == 403
    assert enqueued == []


async def test_a_tampered_body_is_rejected(api, enqueued):
    """Signature computed over the original, body altered in flight."""
    original = json.dumps(comment_payload()).encode()
    tampered = original.replace(b"tools", b"toolz")
    response = await api.post(
        "/webhooks/instagram",
        content=tampered,
        headers={"X-Hub-Signature-256": sign(original, SECRET)},
    )
    assert response.status_code == 403
    assert enqueued == []


# --- REQUIRED: comment events -----------------------------------------------


async def test_a_comment_event_is_stored_and_queued(api, database, enqueued):
    response = await post_webhook(api, comment_payload())
    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "queued": 1}

    assert await database["webhook_events"].count_documents({}) == 1
    assert len(enqueued) == 1
    job, args = enqueued[0]
    assert job == "process_comment_event"
    assert args[1:] == (ACCOUNT_IG_ID, "17900000000000000")


# --- REQUIRED: duplicate events ---------------------------------------------


async def test_a_duplicate_delivery_is_ignored(api, database, enqueued):
    payload = comment_payload()
    first = await post_webhook(api, payload)
    second = await post_webhook(api, payload)

    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert await database["webhook_events"].count_documents({}) == 1
    assert len(enqueued) == 1, "a redelivery must not enqueue a second job"


async def test_a_genuinely_different_event_is_not_treated_as_duplicate(api, database, enqueued):
    await post_webhook(api, comment_payload(comment_id="111"))
    await post_webhook(api, comment_payload(comment_id="222"))
    assert await database["webhook_events"].count_documents({}) == 2
    assert len(enqueued) == 2


# --- REQUIRED: malformed and unsupported payloads ---------------------------


async def test_malformed_json_is_acknowledged_not_retried(api, enqueued):
    """Signed but unparseable. A 4xx/5xx would make Meta redeliver forever."""
    body = b"{not json"
    response = await api.post(
        "/webhooks/instagram",
        content=body,
        headers={"X-Hub-Signature-256": sign(body, SECRET)},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert enqueued == []


async def test_an_unsupported_field_is_ignored(api, enqueued):
    payload = {
        "object": "instagram",
        "entry": [
            {"id": ACCOUNT_IG_ID, "time": 1, "changes": [{"field": "mentions", "value": {}}]}
        ],
    }
    response = await post_webhook(api, payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert enqueued == []


async def test_an_empty_entry_list_is_ignored(api, enqueued):
    response = await post_webhook(api, {"object": "instagram", "entry": []})
    assert response.status_code == 200
    assert enqueued == []


# --- REQUIRED: queue failure ------------------------------------------------


async def test_a_queue_failure_still_persists_the_event(api, database, monkeypatch):
    """Redis down must not lose the event: it is already durable in Mongo and
    can be replayed from webhook_events."""

    async def boom(*args, **kwargs):
        raise RuntimeError("redis is down")

    monkeypatch.setattr("app.queue.enqueue", boom)

    response = await post_webhook(api, comment_payload())
    assert response.status_code == 200
    assert response.json()["queued"] == 0
    assert await database["webhook_events"].count_documents({}) == 1


# --- parser corrections from docs/meta-api.md §5.6 --------------------------


def test_field_is_read_from_changes_not_entry():
    """§24's sketch reads entry['field'] and would match nothing."""
    events = parse_comment_events(comment_payload())
    assert len(events) == 1
    assert events[0].instagram_comment_id == "17900000000000000"


def test_comment_id_key_may_be_comment_id_or_id():
    """Meta's reference page and examples page disagree; accept both."""
    for key in ("comment_id", "id"):
        events = parse_comment_events(comment_payload(id_key=key))
        assert len(events) == 1, f"{key} was not accepted"
        assert events[0].instagram_comment_id == "17900000000000000"


def test_optional_fields_may_all_be_absent():
    """A KeyError here would 500 and be redelivered forever."""
    payload = {
        "object": "instagram",
        "entry": [
            {"id": ACCOUNT_IG_ID, "changes": [{"field": "comments", "value": {"id": "1"}}]}
        ],
    }
    event = parse_comment_events(payload)[0]
    assert event.instagram_comment_id == "1"
    assert event.text == "" and event.username == "" and event.parent_id == ""


def test_self_comments_are_flagged():
    """The account's own replies fire this webhook too."""
    own = parse_comment_events(comment_payload(from_id=ACCOUNT_IG_ID))[0]
    other = parse_comment_events(comment_payload(from_id=COMMENTER_IG_ID))[0]
    assert own.is_self_comment is True
    assert other.is_self_comment is False


async def test_a_self_comment_is_not_queued(api, enqueued):
    response = await post_webhook(api, comment_payload(from_id=ACCOUNT_IG_ID))
    assert response.status_code == 200
    assert response.json()["queued"] == 0
    assert enqueued == []


def test_is_from_also_matches_on_username():
    """from.id is an Instagram-scoped id and need not equal the account id."""
    event = parse_comment_events(comment_payload(from_id="different", username="myshop"))[0]
    assert event.is_from({"instagram_user_id": ACCOUNT_IG_ID, "username": "MyShop"}) is True
    assert event.is_from({"instagram_user_id": ACCOUNT_IG_ID, "username": "someoneelse"}) is False


# --- parser robustness ------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"object": "page", "entry": [{"id": "1"}]},
        {"object": "instagram", "entry": "not-a-list"},
        {"object": "instagram", "entry": [None]},
        {"object": "instagram", "entry": [{"id": "1", "changes": "nope"}]},
        {"object": "instagram", "entry": [{"id": "1", "changes": [{"field": "comments"}]}]},
    ],
)
def test_parser_never_raises_on_odd_payloads(payload):
    assert parse_comment_events(payload) == []


def test_a_comment_without_an_id_is_dropped():
    payload = {
        "object": "instagram",
        "entry": [{"id": "1", "changes": [{"field": "comments", "value": {"text": "hi"}}]}],
    }
    assert parse_comment_events(payload) == []


def test_entry_time_becomes_an_aware_datetime():
    event = parse_comment_events(comment_payload())[0]
    assert event.commented_at is not None
    assert event.commented_at.tzinfo is not None


def test_a_bad_timestamp_does_not_break_parsing():
    payload = comment_payload()
    payload["entry"][0]["time"] = "not-a-time"
    assert parse_comment_events(payload)[0].commented_at is None


def test_multiple_comments_in_one_delivery_are_all_parsed():
    payload = comment_payload()
    payload["entry"].append(
        {
            "id": ACCOUNT_IG_ID,
            "time": 1520383572,
            "changes": [
                {"field": "comments", "value": {"comment_id": "2", "text": "second"}}
            ],
        }
    )
    assert len(parse_comment_events(payload)) == 2


def test_parsed_comment_is_immutable():
    """Parsed events are passed around freely; mutation would be a hidden bug."""
    event = ParsedComment(instagram_account_id="1", instagram_comment_id="2")
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.text = "mutated"  # type: ignore[misc]


# --- timestamp units --------------------------------------------------------


def test_second_timestamps_are_parsed():
    payload = comment_payload()
    payload["entry"][0]["time"] = 1788187373
    assert parse_comment_events(payload)[0].commented_at.year == 2026


def test_millisecond_timestamps_are_parsed():
    """Observed in live messaging traffic: Meta sends ms there, seconds for
    comments. Treating ms as seconds overflows to an unrepresentable year."""
    payload = comment_payload()
    payload["entry"][0]["time"] = 1788187373415
    parsed = parse_comment_events(payload)[0]
    assert parsed.commented_at is not None
    assert parsed.commented_at.year == 2026


def test_the_two_units_agree_to_the_second():
    seconds, millis = comment_payload(), comment_payload()
    seconds["entry"][0]["time"] = 1788187373
    millis["entry"][0]["time"] = 1788187373000
    assert (
        parse_comment_events(seconds)[0].commented_at
        == parse_comment_events(millis)[0].commented_at
    )
