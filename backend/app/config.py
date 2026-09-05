"""Application configuration, loaded from environment variables.

Every secret lives here and nowhere else. Nothing in this module may be logged
or returned to the frontend; see `app.logging` for the redaction filter.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["development", "production"] = "development"
    log_level: str = "INFO"

    # Public HTTPS origin, and the fallback for it: an operator can override this
    # from the Settings page, which is where `credentials.origin_for` looks first.
    # Every workspace URL is derived from whichever wins.
    public_base_url: str = "http://localhost:8080"

    mongo_url: str = "mongodb://mongodb:27017"
    mongo_database: str = "instaflow"
    # Meta fetches attachments by URL rather than accepting bytes, so uploads
    # have to live somewhere this server can serve publicly (§4c).
    upload_dir: str = "/app/uploads"
    # GeoLite2-City.mmdb lives beside the uploads so it survives a rebuild.
    geoip_dir: str = "/app/uploads/.geoip"
    redis_url: str = "redis://redis:6379/0"

    # Meta credentials. These are the *Instagram* App ID/Secret. See docs/meta-api.md.
    # The instance-wide default, used by any workspace that has not configured its
    # own Meta app. A workspace falling back to these also falls back to the
    # unprefixed URL layout -- see `services/meta/credentials.resolve`.
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_webhook_verify_token: str = ""
    meta_api_version: str = "v25.0"
    # auto = real client when credentials are present, mock otherwise.
    meta_client: Literal["auto", "real", "mock"] = "auto"

    encryption_key: str = ""
    session_secret: str = ""

    # When true the full pipeline runs but no Meta mutation is ever sent.
    dry_run: bool = True

    # Session cookie. `secure` must be True in production; it is off by default so
    # the dashboard is reachable over plain http://localhost during development.
    session_lifetime_hours: int = 12
    session_cookie_name: str = "instaflow_session"
    session_cookie_secure: bool = False

    # Failed logins allowed per identity per window before refusing.
    login_attempt_limit: int = 10
    login_attempt_window_seconds: int = 900

    # One-time secret that lets the very first admin be created through the web
    # UI, for deployments with no shell access. Empty disables that route
    # entirely -- an unset secret must never mean "no secret required".
    admin_setup_secret: str = ""

    ai_enabled: bool = False
    ai_base_url: str = "http://host.docker.internal:11434/v1"
    ai_model: str = "qwen3:8b"
    ai_timeout_seconds: float = 10.0
    ai_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @property
    def meta_graph_base(self) -> str:
        """Base URL for Instagram Graph calls. Only `services.meta` may use this."""
        return f"https://graph.instagram.com/{self.meta_api_version}"

    def missing_required(self) -> list[str]:
        """Config required before the app can run at all, reported by /ready.

        The three `META_*` variables are deliberately absent. They are now a
        *default*, not a requirement: each workspace may bring its own Meta app
        (`services/meta/credentials.py`), so an instance where every user has done
        so has no need of them and must not be reported unhealthy for it. Whether
        a given workspace can talk to Meta is a per-workspace question, answered
        on its Settings and Instagram pages.

        These two are different in kind. Without them nothing works for anyone:
        no session can be signed, and no stored secret can be read back.
        """
        required = {
            "ENCRYPTION_KEY": self.encryption_key,
            "SESSION_SECRET": self.session_secret,
        }
        return sorted(name for name, value in required.items() if not value)


@lru_cache
def get_settings() -> Settings:
    return Settings()
