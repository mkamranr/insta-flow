"""Phase 4. The mock must simulate every failure mode in §77 and honour the real
Meta restrictions, so that a double-send or out-of-window bug fails a test."""

import pytest

from app.errors import MetaAPIError, MetaRateLimitError, MetaTokenError
from app.services.meta import (
    SIMULATED_STATUSES,
    InstagramClient,
    MetaAccount,
    MockInstagramClient,
    SimulatedNetworkError,
    SimulatedTimeout,
    error_for_status,
)

TOKEN = "mock-token"
IG_ID = "17841400000000000"
COMMENT_ID = "17900000000000000"


@pytest.fixture
def client():
    return MockInstagramClient()


# --- the protocol -----------------------------------------------------------


def test_mock_satisfies_the_client_protocol():
    """If this fails, the mock and the real client have drifted apart."""
    assert isinstance(MockInstagramClient(), InstagramClient)


# --- error mapping ----------------------------------------------------------


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_errors_are_retryable(status):
    assert error_for_status(status).retryable is True


def test_rate_limit_is_its_own_type_and_retryable():
    error = error_for_status(429)
    assert isinstance(error, MetaRateLimitError)
    assert error.retryable is True


def test_token_error_is_its_own_type_and_not_retryable():
    """A 401 must pause the account, never spin on retries with a dead token."""
    error = error_for_status(401)
    assert isinstance(error, MetaTokenError)
    assert error.retryable is False


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_other_client_errors_are_not_retryable(status):
    assert error_for_status(status).retryable is False


def test_timeout_and_network_errors_are_retryable():
    assert SimulatedTimeout().retryable is True
    assert SimulatedNetworkError().retryable is True


# --- happy paths ------------------------------------------------------------


async def test_get_account(client):
    account = await client.get_account(TOKEN)
    assert isinstance(account, MetaAccount)
    assert account.instagram_user_id == IG_ID


async def test_get_media_respects_limit(client):
    assert len(await client.get_media(TOKEN, IG_ID, limit=1)) == 1


async def test_subscribe_then_read_back_fields(client):
    assert await client.subscribe_webhooks(TOKEN, IG_ID) is True
    fields = await client.get_subscribed_fields(TOKEN, IG_ID)
    # messaging_postbacks carries the button tap that opens the 24h window.
    assert "comments" in fields
    assert "messaging_postbacks" in fields


async def test_private_reply_returns_a_normalised_result(client):
    result = await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "Hey there!")
    assert result.success is True
    assert result.message_id and result.recipient_id
    assert client.sent_private_replies[0]["text"] == "Hey there!"


async def test_public_reply_is_tracked_separately(client):
    await client.send_public_reply(TOKEN, COMMENT_ID, "thanks!")
    assert len(client.sent_public_replies) == 1
    assert client.sent_private_replies == []


# --- simulated failures -----------------------------------------------------


@pytest.mark.parametrize("status", SIMULATED_STATUSES)
async def test_every_required_http_status_can_be_simulated(client, status):
    client.fail("send_private_reply", status)
    with pytest.raises(MetaAPIError) as excinfo:
        await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "hi")
    assert excinfo.value.meta_status_code == status


async def test_timeout_can_be_simulated(client):
    client.fail("send_private_reply", SimulatedTimeout())
    with pytest.raises(SimulatedTimeout):
        await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "hi")


async def test_network_failure_can_be_simulated(client):
    client.fail("get_account", SimulatedNetworkError())
    with pytest.raises(SimulatedNetworkError):
        await client.get_account(TOKEN)


async def test_queued_failures_are_consumed_then_the_call_succeeds(client):
    """Models the retry path: two 429s, then success on the third attempt."""
    client.fail("send_private_reply", 429, times=2)

    for _ in range(2):
        with pytest.raises(MetaRateLimitError):
            await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "hi")

    result = await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "hi")
    assert result.success is True


async def test_a_failed_call_is_still_recorded(client):
    """Attempts must be observable even when they fail, for attempt counting."""
    client.fail("send_private_reply", 500)
    with pytest.raises(MetaAPIError):
        await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "hi")
    assert len(client.calls_to("send_private_reply")) == 1


# --- Meta's real restrictions ----------------------------------------------


async def test_second_private_reply_to_the_same_comment_is_rejected(client):
    """Meta allows one private reply per comment. The mock enforces it so a
    double-send bug cannot pass a test."""
    await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "first")
    with pytest.raises(MetaAPIError) as excinfo:
        await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "second")
    assert excinfo.value.retryable is False
    assert len(client.sent_private_replies) == 1


async def test_a_different_comment_can_still_receive_a_reply(client):
    await client.send_private_reply(TOKEN, IG_ID, "comment-a", "hi")
    await client.send_private_reply(TOKEN, IG_ID, "comment-b", "hi")
    assert len(client.sent_private_replies) == 2


async def test_reply_outside_the_seven_day_window_is_rejected(client):
    client.set_comment_age(COMMENT_ID, days_old=8)
    with pytest.raises(MetaAPIError) as excinfo:
        await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "too late")
    assert excinfo.value.retryable is False
    assert "7-day" in str(excinfo.value)


async def test_reply_inside_the_window_is_allowed(client):
    client.set_comment_age(COMMENT_ID, days_old=6.9)
    assert (await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "just in time")).success


# --- dry-run support --------------------------------------------------------


async def test_reads_are_not_counted_as_mutations(client):
    """The dry-run assertion in Phase 12 depends on this distinction."""
    await client.get_account(TOKEN)
    await client.get_media(TOKEN, IG_ID)
    await client.get_subscribed_fields(TOKEN, IG_ID)
    assert client.mutation_calls == []


async def test_sends_are_counted_as_mutations(client):
    await client.send_private_reply(TOKEN, IG_ID, COMMENT_ID, "hi")
    assert [c.method for c in client.mutation_calls] == ["send_private_reply"]
