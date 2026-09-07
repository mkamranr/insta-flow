"""Which Meta app is this request acting as?

Before workspaces there was one answer, baked into the environment. Now every
Meta-facing operation has to ask, because two users may each have their own Meta
app, with their own App ID, App Secret and webhook verify token.

Three endpoints cannot answer this from the request body, which is why the
workspace has to come from the URL path:

* `POST .../webhooks/instagram` -- the signature is an HMAC of the raw body under
  the app secret. Until the right secret is chosen the body is unverified, so the
  account id inside it cannot be used to choose the secret.
* `GET .../webhooks/instagram` -- the handshake carries only `hub.verify_token`.
  With N apps there are N valid tokens and nothing else to discriminate on.
* `deauthorize` / `data-deletion` -- Meta's `signed_request` is signed with the
  app secret and carries no state parameter.

Trying every stored secret in turn would work, but it is an oracle and it gets
slower with every user added.

This module is the only place either secret is decrypted.
"""

import logging
from dataclasses import dataclass
from typing import Any

from app.services import slugs
from app.services.security.encryption import EncryptionError, decrypt

logger = logging.getLogger(__name__)

# Where the instance origin lives once an operator overrides PUBLIC_BASE_URL from
# the Settings page. One key, in the existing `app_settings` singleton.
ORIGIN_KEY = "public_base_url"


@dataclass(frozen=True)
class MetaCredentials:
    """One Meta app, plus the URLs that app must be configured with.

    `base_url` is not decoration: a Meta app only accepts the exact redirect URI
    registered against it, so the credentials and the URLs are a single unit and
    must never be mixed between workspaces.
    """

    app_id: str
    app_secret: str
    verify_token: str
    base_url: str
    # "workspace" -- this user's own Meta app, from `meta_apps`.
    # "instance"  -- the environment's META_* variables.
    source: str

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/api/auth/instagram/callback"

    @property
    def webhook_url(self) -> str:
        return f"{self.base_url}/webhooks/instagram"

    @property
    def privacy_url(self) -> str:
        return f"{self.base_url}/privacy"

    @property
    def data_deletion_url(self) -> str:
        return f"{self.base_url}/data-deletion"

    @property
    def deauthorize_url(self) -> str:
        return f"{self.base_url}/api/auth/instagram/deauthorize"

    def public_urls(self) -> dict[str, str]:
        """The URLs the app *currently acting* is configured with."""
        return urls_for(self.base_url)


def urls_for(base_url: str) -> dict[str, str]:
    """Everything that must be pasted into a Meta app dashboard, from one base.

    A free function rather than only a method on `MetaCredentials`, because the
    Settings page needs these for an app that does not exist yet: until a
    workspace saves an App ID it is still riding on the instance credentials, so
    asking the *acting* credentials for its URLs answers a different question than
    "which URLs do I register in the app I am about to create?" -- and answers it
    with the unprefixed set, which is wrong for that task.

    One definition, so the page and the docs cannot list a different set: a URL
    missing here is a webhook that never arrives, and the symptom is silence.
    """
    return {
        "webhook_url": f"{base_url}/webhooks/instagram",
        "redirect_uri": f"{base_url}/api/auth/instagram/callback",
        # `Set up Instagram business login` on Meta's dashboard has THREE fields,
        # not two: alongside the redirect URI it wants a deauthorize callback and
        # a data deletion request URL. This one was missing from the set for the
        # whole life of the Settings page, so anybody who configured their app by
        # copying from the dashboard left it blank -- and the endpoint has existed
        # at `api/instagram.py` all along. See docs/meta-api.md step 7.
        "deauthorize_url": f"{base_url}/api/auth/instagram/deauthorize",
        "privacy_url": f"{base_url}/privacy",
        "data_deletion_url": f"{base_url}/data-deletion",
    }


def workspace_urls(origin: str, slug: str | None) -> dict[str, str]:
    """The URLs for a Meta app owned by this workspace, whatever it is using now."""
    return urls_for(slugs.workspace_url(origin, slug))


def origin_for(settings: Any, saved: dict[str, Any] | None = None) -> str:
    """The instance's public origin: the Settings override, else PUBLIC_BASE_URL."""
    override = str((saved or {}).get(ORIGIN_KEY) or "").strip()
    return (override or settings.public_base_url).rstrip("/")


def _secret(document: dict[str, Any], field: str, key: str) -> str:
    blob = str(document.get(field) or "")
    if not blob:
        # Not an error, and must not be logged as one. A workspace can hold an
        # App ID and Secret before a verify token has been generated, and that
        # state is reached on every webhook until the operator generates one --
        # an ERROR per request would bury the real decryption failures below.
        return ""
    try:
        return decrypt(blob, key)
    except EncryptionError:
        # Almost always a rotated ENCRYPTION_KEY. Returning "" rather than
        # raising keeps the failure contained and legible: an empty app secret
        # makes `verify_signature` reject, and an empty verify token makes the
        # handshake fail -- both visible, neither a 500 on Meta's retry path.
        logger.error("meta_app_secret_undecryptable", extra={"field": field})
        return ""


def resolve(
    settings: Any,
    *,
    slug: str | None,
    origin: str,
    app: dict[str, Any] | None,
) -> MetaCredentials:
    """Credentials for one workspace.

    The workspace's own Meta app when it has one, otherwise the environment's.

    **The URL layout follows the credentials, not the request.** A workspace with
    its own app gets `/u/<slug>/...` URLs, because those are what its owner was
    told to register. A workspace falling back to the instance credentials gets
    the bare, unprefixed URLs -- which is exactly the layout the already-live
    Meta app has registered today. Deriving prefixed URLs for the environment's
    app would hand the live deployment a redirect URI its Meta app has never
    heard of, and reconnecting would start failing for no visible reason.
    """
    if app:
        app_id = str(app.get("app_id") or "").strip()
        if app_id:
            return MetaCredentials(
                app_id=app_id,
                app_secret=_secret(app, "app_secret_encrypted", settings.encryption_key),
                verify_token=_secret(app, "verify_token_encrypted", settings.encryption_key),
                base_url=slugs.workspace_url(origin, slug),
                source="workspace",
            )

    return MetaCredentials(
        app_id=settings.meta_app_id,
        app_secret=settings.meta_app_secret,
        verify_token=settings.meta_webhook_verify_token,
        base_url=slugs.workspace_url(origin, None),
        source="instance",
    )


def resolve_for(
    settings: Any,
    saved: dict[str, Any] | None,
    app: dict[str, Any] | None,
    slug: str | None,
) -> MetaCredentials:
    """`resolve`, with the origin worked out from the saved settings document.

    The two are always wanted together; splitting them is how one caller ends up
    deriving URLs from PUBLIC_BASE_URL while another uses the Settings override.
    """
    return resolve(settings, slug=slug, origin=origin_for(settings, saved), app=app)


def unconfigured(origin: str, slug: str | None) -> MetaCredentials:
    """No app at all -- what an unrecognised workspace slug resolves to.

    Deliberately NOT the instance credentials. Falling back to those would mean a
    POST to `/u/anything/webhooks/instagram` gets verified against the
    environment's app secret, quietly accepting traffic at a URL no Meta app was
    ever told about. An empty secret makes `verify_signature` return False, which
    is the truthful answer: nothing here can vouch for that request.
    """
    return MetaCredentials(
        app_id="",
        app_secret="",
        verify_token="",
        base_url=slugs.workspace_url(origin, slug),
        source="unknown",
    )
