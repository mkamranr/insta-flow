"""Public link redirect.

Unauthenticated by necessity: the person clicking is an Instagram user, not a
signed-in operator.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.api.deps import LinkClicksRepo
from app.errors import NotFoundError
from app.services.links import is_safe_target

logger = logging.getLogger(__name__)
router = APIRouter(tags=["links"])


@router.get("/r/{token}")
async def follow_link(token: str, request: Request, links: LinkClicksRepo) -> RedirectResponse:
    link = await links.record_click(
        token,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    if link is None:
        raise NotFoundError("This link has expired or does not exist.")

    target = str(link.get("target_url", ""))
    if not is_safe_target(target):
        # Refuse to become an open redirect, even though the URL was validated
        # on the way in — storage is not a trust boundary.
        logger.error("unsafe_redirect_target_blocked", extra={"token": token})
        raise NotFoundError("This link is not available.")

    logger.info("link_clicked", extra={"token": token, "rule_id": str(link.get("rule_id"))})
    # 302, not 301: a permanent redirect would be cached and later clicks lost.
    return RedirectResponse(target, status_code=302)
