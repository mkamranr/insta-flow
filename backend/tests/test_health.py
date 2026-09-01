"""/health must not touch MongoDB, Redis, or Meta (§67), so it is testable
without any infrastructure running."""

import httpx
import pytest

from app.main import app


@pytest.fixture
def client():
    # ASGITransport skips the lifespan, which is the point: no DB required.
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_health_is_ok_without_any_dependency(client):
    async with client as c:
        response = await c.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "instaflow"}


async def test_ready_reports_503_when_dependencies_are_down(client):
    async with client as c:
        response = await c.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["mongodb"] is False
    assert body["checks"]["redis"] is False


async def test_ready_never_leaks_secret_values(client):
    """/ready may name which config keys are unset, but never their values (§52)."""
    from app.config import get_settings

    settings = get_settings()
    async with client as c:
        response = await c.get("/ready")

    body = response.text
    for secret in (
        settings.meta_app_secret,
        settings.session_secret,
        settings.encryption_key,
        settings.meta_webhook_verify_token,
    ):
        if secret:
            assert secret not in body

    # Missing keys are reported by name only.
    for name in response.json().get("missing_config", []):
        assert name.isupper()
