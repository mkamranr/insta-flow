"""Trackable links: rewriting, redirecting, and counting."""

import pytest
from bson import ObjectId

from app.db.repositories import LinkClicksRepository
from app.services.links import is_safe_target, new_token, tracking_url


def test_tokens_are_unique_and_url_safe():
    tokens = {new_token() for _ in range(200)}
    assert len(tokens) == 200
    assert all(t.isascii() and "/" not in t and "+" not in t for t in tokens)


def test_tracking_url_joins_cleanly():
    assert tracking_url("https://x.ts.net", "abc") == "https://x.ts.net/r/abc"
    assert tracking_url("https://x.ts.net/", "abc") == "https://x.ts.net/r/abc"


@pytest.mark.parametrize("url", ["https://example.com", "http://example.com/x?y=1"])
def test_http_targets_are_allowed(url):
    assert is_safe_target(url) is True


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html,<script>", "file:///etc/passwd", ""]
)
def test_non_http_targets_are_refused(url):
    """An open redirect would turn our own domain into an attack vector."""
    assert is_safe_target(url) is False


async def test_a_click_is_recorded_and_redirects(api, database):
    links = LinkClicksRepository(database)
    await links.insert(
        {
            "token": "tok123",
            "instagram_account_id": ObjectId(),
            "rule_id": ObjectId(),
            "target_url": "https://example.com/landing",
            "click_count": 0,
        }
    )

    response = await api.get("/r/tok123")
    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/landing"

    stored = await links.get_by_token("tok123")
    assert stored["click_count"] == 1
    assert stored["last_clicked_at"] is not None


async def test_repeat_clicks_all_count(api, database):
    """Repeat clicks are real engagement; collapsing them understates the metric."""
    links = LinkClicksRepository(database)
    await links.insert(
        {"token": "tok2", "target_url": "https://example.com", "click_count": 0}
    )
    for _ in range(3):
        await api.get("/r/tok2")
    assert (await links.get_by_token("tok2"))["click_count"] == 3


async def test_an_unknown_token_is_a_404(api):
    assert (await api.get("/r/nope")).status_code == 404


async def test_a_stored_unsafe_target_is_refused(api, database):
    """Storage is not a trust boundary, so the check runs again on the way out."""
    await LinkClicksRepository(database).insert(
        {"token": "evil", "target_url": "javascript:alert(1)", "click_count": 0}
    )
    response = await api.get("/r/evil")
    assert response.status_code == 404
    assert "javascript" not in response.text


async def test_the_redirect_needs_no_session(api, database):
    """The clicker is an Instagram user, not a signed-in operator."""
    await LinkClicksRepository(database).insert(
        {"token": "public", "target_url": "https://example.com", "click_count": 0}
    )
    assert (await api.get("/r/public")).status_code == 302


async def test_clicks_are_summed_not_counted(database):
    links = LinkClicksRepository(database)
    account = ObjectId()
    for count in (3, 5):
        await links.insert(
            {
                "token": new_token(),
                "instagram_account_id": account,
                "rule_id": ObjectId(),
                "target_url": "https://example.com",
                "click_count": count,
            }
        )
    assert await links.count_clicks({"instagram_account_id": account}) == 8


async def test_a_redirect_survives_a_link_with_no_account(api, database):
    """Analytics must never be the reason a redirect fails.

    A link row without an `instagram_account_id` used to raise KeyError and
    return 500 — the visitor lost the page over our own bookkeeping.
    """
    await database["link_clicks"].insert_one(
        {"token": "orphan-link", "target_url": "https://example.com/ok", "click_count": 0}
    )

    response = await api.get("/r/orphan-link", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "https://example.com/ok"
