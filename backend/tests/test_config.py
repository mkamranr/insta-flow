from app.config import Settings


def test_derived_urls_match_the_meta_app_registration():
    """These two strings must match the Meta dashboard byte-for-byte (docs/meta-api.md)."""
    settings = Settings(
        public_base_url="https://your-machine.your-tailnet.ts.net",
        meta_api_version="v25.0",
    )
    assert (
        settings.oauth_redirect_uri
        == "https://your-machine.your-tailnet.ts.net/api/auth/instagram/callback"
    )
    assert (
        settings.webhook_url == "https://your-machine.your-tailnet.ts.net/webhooks/instagram"
    )


def test_trailing_slash_does_not_produce_a_double_slash():
    settings = Settings(public_base_url="https://example.ts.net/")
    assert settings.webhook_url == "https://example.ts.net/webhooks/instagram"


def test_graph_base_uses_the_instagram_login_host():
    settings = Settings(meta_api_version="v25.0")
    assert settings.meta_graph_base == "https://graph.instagram.com/v25.0"


def test_dry_run_defaults_to_true(monkeypatch):
    """§93 Rule 9. A deployment that forgets to set DRY_RUN must not send DMs.

    The variable is cleared explicitly: this asserts the *default*, and a real
    DRY_RUN in the environment would otherwise decide the result.
    """
    monkeypatch.delenv("DRY_RUN", raising=False)
    assert Settings().dry_run is True


def test_ai_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("AI_ENABLED", raising=False)
    assert Settings().ai_enabled is False


def test_missing_required_lists_every_unset_secret():
    # Explicit blanks, so the result does not depend on the ambient .env.
    blank = Settings(
        meta_app_id="",
        meta_app_secret="",
        meta_webhook_verify_token="",
        encryption_key="",
        session_secret="",
    )
    assert blank.missing_required() == [
        "ENCRYPTION_KEY",
        "META_APP_ID",
        "META_APP_SECRET",
        "META_WEBHOOK_VERIFY_TOKEN",
        "SESSION_SECRET",
    ]


def test_missing_required_is_empty_when_configured():
    settings = Settings(
        meta_app_id="1",
        meta_app_secret="2",
        meta_webhook_verify_token="3",
        encryption_key="4",
        session_secret="5",
    )
    assert settings.missing_required() == []
