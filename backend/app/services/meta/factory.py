"""Chooses the Instagram client implementation."""

import logging

from app.config import Settings
from app.services.meta.client import InstagramClient
from app.services.meta.http_client import MetaInstagramClient
from app.services.meta.mock import MockInstagramClient

logger = logging.getLogger(__name__)


def build_client(settings: Settings) -> InstagramClient:
    """Real client when Meta credentials are configured; mock otherwise.

    Defaulting to the mock when credentials are absent means a half-configured
    deployment fails visibly in the dashboard rather than throwing auth errors
    from deep inside the worker. `META_CLIENT` forces either one.

    Note this is independent of DRY_RUN: the real client may be selected while
    dry run still blocks every mutation. Dry run is enforced by the policy
    engine, not by swapping the client.
    """
    choice = settings.meta_client
    if choice == "auto":
        choice = "real" if (settings.meta_app_id and settings.meta_app_secret) else "mock"

    if choice == "mock":
        logger.warning("instagram_client_selected", extra={"implementation": "mock"})
        return MockInstagramClient()

    logger.info("instagram_client_selected", extra={"implementation": "real"})
    return MetaInstagramClient(settings)
