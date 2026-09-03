"""Working out who actually made a request, from behind a reverse proxy.

uvicorn reports the socket peer, and behind Caddy that is always Caddy — every
public request arrives as a `172.x` container address. Geolocating that gives
nothing at all, silently, which is exactly how a geography report ends up empty
with no error anywhere.
"""

import ipaddress
import logging

logger = logging.getLogger(__name__)


def _clean(candidate: str) -> str:
    """Strip an `:port` suffix and IPv6 brackets, leaving a bare address."""
    value = candidate.strip()
    if value.startswith("["):
        return value.partition("]")[0].lstrip("[")
    # Exactly one colon means IPv4:port. More means IPv6, which has no port here.
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


def is_public(candidate: str) -> bool:
    try:
        return ipaddress.ip_address(_clean(candidate)).is_global
    except ValueError:
        return False


def client_ip(forwarded_for: str | None, peer: str | None) -> str | None:
    """The visitor's address, or None if the chain never carried a public one.

    `X-Forwarded-For` is read right to left and the first public address wins.
    The rightmost entries are hops we added ourselves and are always private; a
    client can prepend whatever it likes to the left, so this is a best-effort
    attribution for a statistic, not an authentication signal — nothing is
    authorised on the strength of it.

    Returns None rather than a private address, so a caller cannot mistake "the
    proxy" for "the visitor".
    """
    chain = [part for part in (forwarded_for or "").split(",") if part.strip()]
    chain.append(peer or "")
    for candidate in reversed(chain):
        cleaned = _clean(candidate)
        if cleaned and is_public(cleaned):
            return cleaned
    return None


def describe_chain(forwarded_for: str | None, peer: str | None) -> str:
    """The shape of the proxy chain, with no addresses in it.

    Deployments differ in how many proxies sit in front of this, and the only
    way to know is to look. Logging the addresses themselves would defeat the
    point of not storing them, so this logs `private>private>public` instead.
    """
    chain = [part for part in (forwarded_for or "").split(",") if part.strip()]
    chain.append(peer or "")
    return ">".join("public" if is_public(part) else "private" for part in chain) or "none"
