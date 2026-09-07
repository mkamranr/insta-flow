"""The mail settings routes: encryption at rest, and what may leave the server."""

import pytest

from app.db.repositories import AppSettingsRepository, EmailLogRepository
from app.services.email.config import AUTH_ERROR_KEY, AUTH_FAILED_AT_KEY, PASSWORD_KEY
from app.services.security.encryption import decrypt

from .conftest import TEST_PASSWORD

PASSWORD = "abcd efgh ijkl mnop"


@pytest.fixture
def queued(monkeypatch):
    """Capture enqueued jobs.

    The autouse `redis` fixture is a FakeRedis with no `enqueue_job`, so a route
    that queues needs this -- the seam `test_webhooks.py` already uses.
    """
    calls: list[dict] = []

    async def fake_enqueue(job, *args, **kwargs):
        calls.append({"job": job, **kwargs})
        return "job-1"

    monkeypatch.setattr("app.queue.enqueue", fake_enqueue)
    return calls


VALID = {
    "host": "smtp.gmail.com",
    "port": 587,
    "username": "sender@example.com",
    "password": PASSWORD,
    "from_address": "sender@example.com",
    "from_name": "Social Flow",
}


async def test_saving_stores_the_password_encrypted(signed_in, database, settings) -> None:
    response = await signed_in.put("/api/settings/email", json=VALID)
    assert response.status_code == 200, response.text

    stored = await AppSettingsRepository(database).load()
    assert decrypt(stored[PASSWORD_KEY], settings.encryption_key) == PASSWORD
    assert PASSWORD not in str(stored)


async def test_the_password_never_comes_back(signed_in) -> None:
    """Not in the GET, and not in the PUT's own response either.

    The ciphertext is withheld as well: it is a valid decryption-oracle target
    if the encryption key ever leaks.
    """
    saved = await signed_in.put("/api/settings/email", json=VALID)
    fetched = await signed_in.get("/api/settings/email")

    for response in (saved, fetched):
        assert PASSWORD not in response.text
        assert "password_encrypted" not in response.text
        body = response.json()
        assert "password" not in body
        assert body["password_set"] is True


async def test_identifiers_do_come_back(signed_in) -> None:
    """Host, port, username and sender are not secrets, and the form cannot be
    edited without them -- the line `geoip_status` draws for the account ID."""
    await signed_in.put("/api/settings/email", json=VALID)
    body = (await signed_in.get("/api/settings/email")).json()

    assert body["host"] == "smtp.gmail.com"
    assert body["port"] == 587
    assert body["username"] == "sender@example.com"
    assert body["from_address"] == "sender@example.com"
    assert body["configured"] is True


async def test_an_empty_password_keeps_the_stored_one(signed_in, database, settings) -> None:
    """Fixing a typo in the sender name must not blank the credential."""
    await signed_in.put("/api/settings/email", json=VALID)
    await signed_in.put("/api/settings/email", json={**VALID, "password": "", "from_name": "New"})

    stored = await AppSettingsRepository(database).load()
    assert decrypt(stored[PASSWORD_KEY], settings.encryption_key) == PASSWORD
    assert stored["smtp_from_name"] == "New"


async def test_the_first_save_requires_a_password(signed_in) -> None:
    response = await signed_in.put("/api/settings/email", json={**VALID, "password": ""})
    assert response.status_code == 422


async def test_a_bad_sender_address_is_refused(signed_in) -> None:
    response = await signed_in.put("/api/settings/email", json={**VALID, "from_address": "nope"})
    assert response.status_code == 422


async def test_delete_clears_every_field(signed_in, database) -> None:
    await signed_in.put("/api/settings/email", json=VALID)
    response = await signed_in.delete("/api/settings/email")

    assert response.status_code == 200
    assert response.json()["configured"] is False
    stored = await AppSettingsRepository(database).load()
    assert stored[PASSWORD_KEY] is None
    assert stored["smtp_host"] is None


async def test_an_undecryptable_password_is_reported_not_raised(signed_in, database) -> None:
    """Writing garbage ciphertext is what a changed ENCRYPTION_KEY looks like.

    It must surface as a legible "re-enter it" state, not a 500 -- the reasoning
    `credentials._secret` already carries.
    """
    await signed_in.put("/api/settings/email", json=VALID)
    await AppSettingsRepository(database).save({PASSWORD_KEY: "v1:not-real-ciphertext"})

    response = await signed_in.get("/api/settings/email")
    assert response.status_code == 200
    # The status endpoint reports only that one is stored; `resolve` is what
    # discovers it cannot be read, so the test-send path is where it surfaces.
    assert response.json()["password_set"] is True

    attempt = await signed_in.post("/api/settings/email/test", json={"to": "a@b.com"})
    assert attempt.status_code == 422


async def test_the_test_endpoint_needs_settings_first(signed_in) -> None:
    response = await signed_in.post("/api/settings/email/test", json={"to": "a@b.com"})
    assert response.status_code == 422


async def test_the_test_endpoint_queues_a_row(signed_in, database, queued) -> None:
    """It enqueues rather than sending inline, so the button exercises the same
    code path a real invitation will."""
    await signed_in.put("/api/settings/email", json=VALID)
    response = await signed_in.post("/api/settings/email/test", json={"to": "me@example.com"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queued"] is True

    row = await EmailLogRepository(database).by_message_id(body["message_id"])
    assert row["status"] == "queued"
    assert row["to_address"] == "me@example.com"
    assert row["purpose"] == "test"

    # Keyword arguments only, so a later field cannot break positional order in
    # an already-queued job.
    assert queued == [
        {
            "job": "send_email",
            "message_id": body["message_id"],
            "purpose": "test",
            "to_address": "me@example.com",
            "context": {},
        }
    ]


async def test_a_queue_failure_does_not_leave_the_row_pending(
    signed_in, database, monkeypatch
) -> None:
    """A `queued` row that will never run is indistinguishable from one still in
    flight, and there is no replayer for mail."""

    async def boom(*args, **kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.queue.enqueue", boom)
    await signed_in.put("/api/settings/email", json=VALID)
    response = await signed_in.post("/api/settings/email/test", json={"to": "me@example.com"})

    assert response.status_code == 503
    rows = await EmailLogRepository(database).recent(5)
    assert [row["status"] for row in rows] == ["failed"]


async def test_the_test_endpoint_is_throttled(signed_in, queued) -> None:
    await signed_in.put("/api/settings/email", json=VALID)
    statuses = [
        (
            await signed_in.post("/api/settings/email/test", json={"to": "me@example.com"})
        ).status_code
        for _ in range(4)
    ]
    assert statuses[:3] == [200, 200, 200]
    assert statuses[3] == 429


async def test_recent_rows_are_surfaced(signed_in, queued) -> None:
    """The card is worthless without them: a queued-only response is the silent
    nothing-happened failure."""
    await signed_in.put("/api/settings/email", json=VALID)
    await signed_in.post("/api/settings/email/test", json={"to": "me@example.com"})

    body = (await signed_in.get("/api/settings/email")).json()
    assert len(body["recent"]) == 1
    assert body["recent"][0]["to_address"] == "me@example.com"


async def test_saving_a_new_password_clears_an_earlier_rejection(signed_in, database) -> None:
    await signed_in.put("/api/settings/email", json=VALID)
    store = AppSettingsRepository(database)
    await store.save({AUTH_FAILED_AT_KEY: "2026-01-01", AUTH_ERROR_KEY: "refused"})

    await signed_in.put("/api/settings/email", json=VALID)

    saved = await store.load()
    assert saved[AUTH_FAILED_AT_KEY] is None


async def test_saving_writes_an_audit_entry_without_the_password(signed_in, database) -> None:
    """`changes` is written to `audit_logs` verbatim, so a model_dump would keep
    the credential there forever."""
    await signed_in.put("/api/settings/email", json=VALID)

    rows = [row async for row in database["audit_logs"].find({"action": "settings.email_saved"})]
    assert len(rows) == 1
    assert PASSWORD not in str(rows[0])


async def test_the_dry_run_flag_is_reported(signed_in, settings) -> None:
    """The card says plainly that mail is not gated by dry run. Without this the
    feature looks broken on a default install."""
    body = (await signed_in.get("/api/settings/email")).json()
    assert body["dry_run"] == settings.dry_run


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/settings/email"),
        ("put", "/api/settings/email"),
        ("delete", "/api/settings/email"),
        ("post", "/api/settings/email/test"),
    ],
)
async def test_the_mail_settings_are_owner_only(
    api, database, role_factory, user_factory, method, path
) -> None:
    """The walker proves *a* permission is declared; this pins which one.

    One mail account sends for every workspace, and the test endpoint emits
    traffic from the operator's own address -- so `settings.view`, which merely
    opens the page, must not reach it.
    """
    role = await role_factory("Page access only", ["settings.view"])
    await user_factory("wording@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "wording@example.com", "password": TEST_PASSWORD}
    )

    response = await api.request(method.upper(), path, json={})
    assert response.status_code == 403, response.text


async def test_instance_view_can_read_but_not_change(
    api, database, role_factory, user_factory
) -> None:
    role = await role_factory("Read the instance", ["instance.view"])
    await user_factory("reader@example.com", role)
    await api.post(
        "/api/auth/login", json={"email": "reader@example.com", "password": TEST_PASSWORD}
    )

    assert (await api.get("/api/settings/email")).status_code == 200
    assert (await api.put("/api/settings/email", json=VALID)).status_code == 403
    assert (await api.delete("/api/settings/email")).status_code == 403
    assert (await api.post("/api/settings/email/test", json={"to": "a@b.com"})).status_code == 403
