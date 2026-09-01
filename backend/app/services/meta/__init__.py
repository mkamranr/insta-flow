"""Meta API boundary.

Nothing outside this package may construct a Meta URL or read an access token.
Depend on `InstagramClient`, not on a concrete implementation.
"""

from app.constants import PRIVATE_REPLY_WINDOW_DAYS
from app.services.meta.client import (
    DEFAULT_WEBHOOK_FIELDS,
    InstagramClient,
    MetaAccount,
    MetaMedia,
    MetaSendResult,
    error_for_status,
)
from app.services.meta.mock import (
    SIMULATED_STATUSES,
    MockInstagramClient,
    SimulatedNetworkError,
    SimulatedTimeout,
)

__all__ = [
    "DEFAULT_WEBHOOK_FIELDS",
    "PRIVATE_REPLY_WINDOW_DAYS",
    "SIMULATED_STATUSES",
    "InstagramClient",
    "MetaAccount",
    "MetaMedia",
    "MetaSendResult",
    "MockInstagramClient",
    "SimulatedNetworkError",
    "SimulatedTimeout",
    "error_for_status",
]
