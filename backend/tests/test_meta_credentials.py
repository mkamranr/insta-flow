"""Which Meta app acts, and which URLs go with it.

These two are one decision, not two. A Meta app only accepts the exact redirect
URI registered against it, so credentials that travel with the wrong base URL
fail at Meta with an error that names neither.
"""

from app.config import Settings
from app.services.meta.credentials import (
    ORIGIN_KEY,
    MetaCredentials,
    origin_for,
    resolve,
    resolve_for,
    unconfigured,
    workspace_urls,
)
from app.services.security.encryption import encrypt

KEY = "k" * 64


def settings(**kw):
    return Settings(
        meta_app_id="instance-app",
        meta_app_secret="instance-secret",
        meta_webhook_verify_token="instance-token",
        public_base_url="https://your-machine.your-tailnet.ts.net",
        encryption_key=KEY,
        **kw,
    )


def workspace_app(app_id="workspace-app"):
    return {
        "app_id": app_id,
        "app_secret_encrypted": encrypt("workspace-secret", KEY),
        "verify_token_encrypted": encrypt("workspace-token", KEY),
    }


# --- the origin -------------------------------------------------------------


def test_the_settings_override_wins_over_the_environment():
    assert origin_for(settings(), {ORIGIN_KEY: "https://elsewhere.ts.net"}) == (
        "https://elsewhere.ts.net"
    )


def test_the_environment_is_the_fallback():
    assert origin_for(settings(), {}) == "https://your-machine.your-tailnet.ts.net"
    assert origin_for(settings(), None) == "https://your-machine.your-tailnet.ts.net"


def test_a_trailing_slash_never_produces_a_double_slash():
    origin = origin_for(settings(), {ORIGIN_KEY: "https://example.ts.net/"})
    creds = resolve(settings(), slug="userabc", origin=origin, app=workspace_app())
    assert creds.webhook_url == "https://example.ts.net/u/userabc/webhooks/instagram"


# --- resolution -------------------------------------------------------------


def test_a_workspace_with_its_own_app_gets_prefixed_urls():
    creds = resolve(
        settings(),
        slug="userabc",
        origin="https://your-machine.your-tailnet.ts.net",
        app=workspace_app(),
    )
    assert creds.source == "workspace"
    assert creds.app_id == "workspace-app"
    assert creds.app_secret == "workspace-secret"
    assert creds.verify_token == "workspace-token"
    assert creds.public_urls() == {
        "webhook_url": "https://your-machine.your-tailnet.ts.net/u/userabc/webhooks/instagram",
        "redirect_uri": (
            "https://your-machine.your-tailnet.ts.net/u/userabc/api/auth/instagram/callback"
        ),
        "privacy_url": "https://your-machine.your-tailnet.ts.net/u/userabc/privacy",
        "data_deletion_url": "https://your-machine.your-tailnet.ts.net/u/userabc/data-deletion",
    }


def test_falling_back_to_the_instance_app_also_falls_back_to_unprefixed_urls():
    """The regression that matters most on upgrade.

    The already-live Meta app has the bare URLs registered against it. Handing it
    a `/u/<slug>/` redirect URI it has never heard of would break reconnecting on
    the one deployment that was working, so the URL layout follows the
    credentials rather than the request.
    """
    creds = resolve(
        settings(),
        slug="ameera-alshehhi",
        origin="https://your-machine.your-tailnet.ts.net",
        app=None,
    )
    assert creds.source == "instance"
    assert creds.app_id == "instance-app"
    assert creds.verify_token == "instance-token"
    assert creds.redirect_uri == (
        "https://your-machine.your-tailnet.ts.net/api/auth/instagram/callback"
    )
    assert "/u/" not in creds.webhook_url


def test_a_row_without_an_app_id_is_not_a_configured_workspace():
    """A half-saved row must not shadow the instance credentials with blanks."""
    creds = resolve(settings(), slug="userabc", origin="https://x.ts.net", app={"app_id": "  "})
    assert creds.source == "instance"


def test_an_unreadable_secret_degrades_to_empty_rather_than_raising():
    """A rotated ENCRYPTION_KEY must not 500 on Meta's retry path.

    An empty app secret makes `verify_signature` reject, which is visible and
    safe; an exception inside the webhook handler is neither.
    """
    creds = resolve(
        settings(),
        slug="userabc",
        origin="https://x.ts.net",
        app={"app_id": "a", "app_secret_encrypted": "not-decryptable"},
    )
    assert creds.app_secret == ""
    assert creds.configured is False


def test_an_unknown_slug_gets_nothing_not_the_instance_secret():
    """Otherwise a POST to /u/anything/webhooks/instagram would be verified
    against the environment's app secret -- accepting traffic at a URL no Meta
    app was ever told about."""
    creds = unconfigured("https://x.ts.net", "nobody")
    assert creds.app_secret == ""
    assert creds.verify_token == ""
    assert creds.source == "unknown"
    assert creds.configured is False


def test_resolve_for_reads_the_origin_out_of_the_saved_settings():
    creds = resolve_for(
        settings(), {ORIGIN_KEY: "https://moved.ts.net"}, workspace_app(), "userxyz"
    )
    assert creds.webhook_url == "https://moved.ts.net/u/userxyz/webhooks/instagram"


def test_configured_needs_both_halves():
    both = MetaCredentials("id", "secret", "t", "https://x", "workspace")
    assert both.configured is True
    assert MetaCredentials("id", "", "t", "https://x", "workspace").configured is False
    assert MetaCredentials("", "secret", "t", "https://x", "workspace").configured is False


def test_workspace_urls_do_not_depend_on_which_app_is_acting():
    """A workspace still on the instance app must still be able to see the URLs
    it needs in order to configure one of its own."""
    assert workspace_urls("https://x.ts.net", "userabc") == {
        "webhook_url": "https://x.ts.net/u/userabc/webhooks/instagram",
        "redirect_uri": "https://x.ts.net/u/userabc/api/auth/instagram/callback",
        "privacy_url": "https://x.ts.net/u/userabc/privacy",
        "data_deletion_url": "https://x.ts.net/u/userabc/data-deletion",
    }
