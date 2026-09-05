from app.config import Settings

# The URLs that must match a Meta app registration byte-for-byte are derived by
# `services/meta/credentials.py` now, because they depend on which workspace's
# app is acting. They are pinned in `test_meta_credentials.py`.


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
    blank = Settings(encryption_key="", session_secret="")
    assert blank.missing_required() == ["ENCRYPTION_KEY", "SESSION_SECRET"]


def test_missing_required_is_empty_when_configured():
    assert Settings(encryption_key="4", session_secret="5").missing_required() == []


def test_meta_credentials_are_not_required_instance_wide():
    """An instance where every workspace brings its own Meta app is healthy.

    The three META_* variables became a default rather than a requirement when
    workspaces arrived. Reporting /ready as not_ready for their absence would
    make a correctly-configured multi-workspace deployment look broken.
    """
    settings = Settings(
        meta_app_id="",
        meta_app_secret="",
        meta_webhook_verify_token="",
        encryption_key="4",
        session_secret="5",
    )
    assert settings.missing_required() == []
