"""Attributing a click to a visitor from behind a proxy."""

from app.services.net import client_ip, describe_chain

CADDY = "172.25.0.7"
# A genuinely routable address. The documentation ranges (203.0.113.0/24,
# 2001:db8::/32) are reserved, so `is_global` rejects them -- correctly, but it
# makes them useless as stand-ins for a real visitor.
PUBLIC = "8.8.8.8"
PUBLIC_V6 = "2606:4700:4700::1111"


def test_the_socket_peer_alone_is_the_proxy_not_the_visitor():
    """The bug this fixes.

    Every public request arrives from Caddy's container address. Geolocating it
    returns nothing, silently, so the report simply stayed empty.
    """
    assert client_ip(None, CADDY) is None


def test_the_visitor_is_taken_from_the_forwarded_chain():
    assert client_ip(f"{PUBLIC}, {CADDY}", CADDY) == PUBLIC


def test_a_longer_chain_still_finds_the_visitor():
    assert client_ip(f"{PUBLIC}, 100.64.0.1, {CADDY}", CADDY) == PUBLIC


def test_a_chain_of_only_private_hops_yields_nothing():
    """None, not a private address: a caller must not mistake a hop for a person."""
    assert client_ip(f"10.0.0.4, {CADDY}", CADDY) is None
    assert client_ip("127.0.0.1", CADDY) is None


def test_ports_and_ipv6_brackets_are_tolerated():
    assert client_ip(f"{PUBLIC}:51234, {CADDY}", CADDY) == PUBLIC
    assert client_ip(f"[{PUBLIC_V6}]", CADDY) == PUBLIC_V6


def test_rubbish_in_the_header_is_ignored():
    assert client_ip(f"not-an-ip, {PUBLIC}, {CADDY}", CADDY) == PUBLIC
    assert client_ip("not-an-ip", CADDY) is None


def test_the_chain_description_contains_no_addresses():
    """It is logged on every unplaced click, so it must not leak what we chose
    not to store."""
    described = describe_chain(f"{PUBLIC}, {CADDY}", CADDY)

    assert described == "public>private>private"
    assert PUBLIC not in described
    assert CADDY not in described


def test_a_public_address_survives_a_proxy_appending_its_own_hop():
    """The shape Caddy produces once it trusts the funnel: the visitor's
    address first, the proxy's own view appended.

    Before `trusted_proxies` was set, Caddy REPLACED the inbound header with
    its own peer, and every click was recorded unlocatable. Verified live: a
    phone on mobile data reached Caddy as 94.59.71.70 and the app saw a fully
    private chain.
    """
    from app.services.net import client_ip

    assert client_ip("94.59.71.70, 172.25.0.1", "172.25.0.1") == "94.59.71.70"


def test_a_tailscale_cgnat_address_is_not_treated_as_a_visitor():
    """A request arriving over the tailnet rather than the funnel carries a
    100.64.0.0/10 address. It is not routable, so it is not a location -- and a
    test run from inside the tailnet cannot prove the funnel path works."""
    from app.services.net import client_ip

    assert client_ip("100.118.190.118, 172.25.0.1", "172.25.0.1") is None
