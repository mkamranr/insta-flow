"""The public origin is one value with two front doors, and both must check it.

A live deployment ran with `PUBLIC_BASE_URL=home-srv-1.example.ts.net` -- no
scheme. Nothing failed. `docker compose ps` said healthy, `/ready` said ready,
webhooks arrived and DMs sent. But `tracked_buttons` decides trackability with
`startswith("https://")`, so every button shipped its raw target, no
`link_clicks` row was ever written, and the click-through rate read zero for
weeks in a way that is indistinguishable from nobody clicking.

The Settings page rejected exactly that input. The environment variable did not.
These tests pin both doors to the same rule, and pin the deliberate fallback so
it is never "fixed" into shipping a button Meta will reject.
"""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.config import Settings
from app.services import origins


def settings_with(origin: str) -> Settings:
    return Settings(
        public_base_url=origin,
        encryption_key="k" * 64,
        session_secret="s" * 32,
    )


# -- the shared rule --------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://host.ts.net", "https://host.ts.net"),
        ("https://host.ts.net/", "https://host.ts.net"),
        ("  https://host.ts.net  ", "https://host.ts.net"),
        ("http://localhost:8080", "http://localhost:8080"),
    ],
)
def test_a_well_formed_origin_is_normalised(raw: str, expected: str) -> None:
    assert origins.normalise(raw) == expected


def test_a_scheme_less_origin_is_rejected() -> None:
    """The exact value that broke the live deployment."""
    with pytest.raises(ValueError, match="must start with http"):
        origins.normalise("home-srv-1.example.ts.net")


def test_an_origin_with_a_path_is_rejected() -> None:
    """`/u/<slug>/` is appended by this app; a path here would be doubled."""
    with pytest.raises(ValueError, match="host only"):
        origins.normalise("https://host.ts.net/u/kamran")


def test_http_is_allowed_because_local_development_needs_it() -> None:
    """The rule is "must have a scheme", not "must be https".

    `http://localhost:8080` is the shipped default. Requiring https here would
    fail every developer's first run.
    """
    assert origins.normalise("http://localhost:8080") == "http://localhost:8080"
    assert origins.is_trackable("http://localhost:8080") is False


# -- the config door --------------------------------------------------------


def test_config_rejects_a_scheme_less_origin() -> None:
    """Loud at startup beats a healthy container serving a broken feature."""
    with pytest.raises(PydanticValidationError, match="must start with http"):
        settings_with("home-srv-1.example.ts.net")


def test_config_accepts_and_normalises_a_good_origin() -> None:
    assert settings_with("https://host.ts.net/").public_base_url == "https://host.ts.net"


def test_the_shipped_default_is_valid() -> None:
    """Whatever else changes, `Settings()` must still construct."""
    assert origins.normalise(
        Settings(encryption_key="k" * 64, session_secret="s" * 32).public_base_url
    )


# -- trackability -----------------------------------------------------------


def test_only_https_is_trackable() -> None:
    assert origins.is_trackable("https://host.ts.net") is True
    assert origins.is_trackable("http://host.ts.net") is False
    # The bug: no scheme reads as not-https, so tracking silently switched off.
    assert origins.is_trackable("home-srv-1.example.ts.net") is False
