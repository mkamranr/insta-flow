"""Public link redirect.

Unauthenticated by necessity: the person clicking is an Instagram user, not a
signed-in operator.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.api.deps import ClickEventsRepo, GeoIpDep, LinkClicksRepo
from app.errors import NotFoundError
from app.services.links import is_safe_target
from app.services.net import client_ip, describe_chain

logger = logging.getLogger(__name__)
router = APIRouter(tags=["links"])


@router.get("/r/{token}")
async def follow_link(
    token: str,
    request: Request,
    links: LinkClicksRepo,
    events: ClickEventsRepo,
    geoip: GeoIpDep,
) -> RedirectResponse:
    # `request.client.host` is the proxy, never the visitor. The real address,
    # if the chain carried one, is in X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for")
    ip_address = client_ip(forwarded, request.client.host if request.client else None)

    # Local database lookup, so this costs microseconds and no network call --
    # the redirect stays instant. Only the derived place is stored; the address
    # itself is dropped.
    place = geoip.lookup(ip_address)
    if not place.known:
        # Shape only, no addresses: enough to tell "no database installed" apart
        # from "the visitor's address never reached us".
        logger.info(
            "click_not_located",
            extra={
                "token": token,
                "chain": describe_chain(forwarded, request.client.host if request.client else None),
            },
        )

    link = await links.record_click(
        token,
        user_agent=request.headers.get("user-agent"),
        ip_address=ip_address,
        country=place.country,
        country_code=place.country_code,
        city=place.city,
    )
    if link is None:
        raise NotFoundError("This link has expired or does not exist.")

    target = str(link.get("target_url", ""))
    if not is_safe_target(target):
        # Refuse to become an open redirect, even though the URL was validated
        # on the way in — storage is not a trust boundary.
        logger.error("unsafe_redirect_target_blocked", extra={"token": token})
        raise NotFoundError("This link is not available.")

    # A per-click row, so clicks can be placed in time later. The counter on the
    # link is kept as the fast path for totals.
    #
    # Best effort, and skipped for a link with no account on it: analytics must
    # never be the reason a redirect fails. The person clicking wants the page,
    # not our bookkeeping.
    account_id = link.get("instagram_account_id")
    if account_id is not None:
        try:
            await events.record(
                instagram_account_id=account_id,
                rule_id=link.get("rule_id"),
                token=token,
                country=place.country,
                country_code=place.country_code,
                city=place.city,
            )
        except Exception:
            logger.warning("click_event_not_recorded", extra={"token": token}, exc_info=True)

    logger.info("link_clicked", extra={"token": token, "rule_id": str(link.get("rule_id"))})
    # 302, not 301: a permanent redirect would be cached and later clicks lost.
    return RedirectResponse(target, status_code=302)
