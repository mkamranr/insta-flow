"""Standby events are recorded, never acted on.

Standby means another app holds thread control, so Meta routes messaging events
to `entry[].standby[]` instead of `entry[].messaging[]`. An app without thread
control cannot send, so replying to one would fail at Meta.

They are logged because the two states are otherwise indistinguishable from our
side: a message routed to standby and a message never sent both look like
silence. Telling them apart once cost an hour of live debugging.
"""

import json

from app.config import get_settings

SECRET = get_settings().meta_app_secret or "test-secret"


def standby_payload(text: str = "link") -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": "17841400000000000",
                "time": 1,
                "standby": [
                    {
                        "sender": {"id": "1234"},
                        "recipient": {"id": "17841400000000000"},
                        "timestamp": 1,
                        "message": {"mid": "m1", "text": text},
                    }
                ],
            }
        ],
    }


async def post_webhook(api, payload):
    import hashlib
    import hmac

    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return await api.post(
        "/webhooks/instagram",
        content=body,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )


async def test_a_standby_message_is_never_acted_on(api, database):
    response = await post_webhook(api, standby_payload())

    assert response.status_code == 200
    assert response.json()["queued"] == 0
    assert await database["executions"].count_documents({}) == 0


async def test_a_standby_message_is_logged_with_its_shape(api, caplog):
    import logging

    caplog.set_level(logging.WARNING)

    await post_webhook(api, standby_payload())

    record = next((r for r in caplog.records if r.message == "webhook_standby_events"), None)
    assert record is not None, "a standby event must be visible, or it looks like silence"
    assert record.count == 1
    assert "message" in record.keys_


async def test_a_normal_message_logs_no_standby_warning(api, caplog):
    """The diagnostic must stay quiet in the ordinary case, or it is noise."""
    import logging

    caplog.set_level(logging.WARNING)
    payload = standby_payload()
    payload["entry"][0]["messaging"] = payload["entry"][0].pop("standby")

    await post_webhook(api, payload)

    assert not any(r.message == "webhook_standby_events" for r in caplog.records)
