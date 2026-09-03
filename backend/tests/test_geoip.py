"""Click geolocation.

The privacy claim is the thing worth pinning: the IP is used for the lookup and
never written down, only the country and city derived from it. A regression there
would quietly turn a click counter into a location log.
"""

import io
import tarfile
from pathlib import Path

import httpx
import pytest
from bson import ObjectId

from app.db.repositories import LinkClicksRepository
from app.services.geoip import (
    DATABASE_NAME,
    GeoIpError,
    GeoIpReader,
    database_path,
    download,
    remote_build_date,
)

ACCOUNT_ID = "123456"
LICENSE_KEY = "not-a-real-key"


def tarball(inner_name: str = f"GeoLite2-City_20260903/{DATABASE_NAME}") -> bytes:
    """A gzipped tar shaped like MaxMind's, database nested under a dated dir."""
    payload = b"\xab\xcd" + b"mmdb-ish bytes" * 8
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(inner_name)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


# Bound before any monkeypatching, or the replacement lambda re-enters the name
# it just replaced and recurses.
REAL_ASYNC_CLIENT = httpx.AsyncClient


def serve(monkeypatch, handler) -> None:
    """Route every httpx.AsyncClient in the code under test to `handler`."""
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: REAL_ASYNC_CLIENT(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture
def geo_dir(tmp_path: Path) -> Path:
    return tmp_path / "geoip"


# -- lookups are best effort and never raise ---------------------------------


def test_lookup_is_empty_when_no_database_is_installed(geo_dir):
    """Geolocation is opt-in; before setup every click is simply unplaced."""
    reader = GeoIpReader(geo_dir)
    assert reader.lookup("8.8.8.8").known is False


def test_lookup_of_nothing_is_empty(geo_dir):
    assert GeoIpReader(geo_dir).lookup(None).known is False
    assert GeoIpReader(geo_dir).lookup("").known is False


def test_a_corrupt_database_does_not_raise(geo_dir):
    """A click must never be lost because geolocation broke."""
    geo_dir.mkdir(parents=True)
    database_path(geo_dir).write_bytes(b"this is not an mmdb")

    assert GeoIpReader(geo_dir).lookup("8.8.8.8").known is False


# -- download ----------------------------------------------------------------


async def test_download_extracts_only_the_database(geo_dir, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["suffix"] == "tar.gz"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, content=tarball())

    serve(monkeypatch, handler)
    path = await download(ACCOUNT_ID, LICENSE_KEY, geo_dir)

    assert path == database_path(geo_dir)
    assert path.is_file()
    # The dated directory from inside the archive must not be recreated on disk.
    assert [p.name for p in geo_dir.iterdir()] == [DATABASE_NAME]


async def test_bad_credentials_are_reported_clearly(geo_dir, monkeypatch):
    serve(monkeypatch, lambda request: httpx.Response(401, json={}))
    with pytest.raises(GeoIpError, match="rejected"):
        await download(ACCOUNT_ID, LICENSE_KEY, geo_dir)


async def test_hitting_the_daily_limit_says_so(geo_dir, monkeypatch):
    serve(monkeypatch, lambda request: httpx.Response(429, json={}))
    with pytest.raises(GeoIpError, match="limit"):
        await download(ACCOUNT_ID, LICENSE_KEY, geo_dir)


async def test_an_archive_without_a_database_is_rejected(geo_dir, monkeypatch):
    serve(monkeypatch, lambda request: httpx.Response(200, content=tarball("readme/notes.txt")))
    with pytest.raises(GeoIpError, match="No GeoLite2-City.mmdb"):
        await download(ACCOUNT_ID, LICENSE_KEY, geo_dir)


async def test_build_date_comes_from_the_head_request(monkeypatch):
    """A HEAD does not count against the download limit, so the job uses it."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        return httpx.Response(200, headers={"last-modified": "Wed, 03 Sep 2026 04:00:00 GMT"})

    serve(monkeypatch, handler)
    built = await remote_build_date(ACCOUNT_ID, LICENSE_KEY)

    assert seen["method"] == "HEAD"
    assert built is not None and built.year == 2026


# -- what gets written down --------------------------------------------------


async def make_link(database) -> str:
    token = f"tok{ObjectId()}"
    await database["link_clicks"].insert_one(
        {
            "token": token,
            "instagram_account_id": ObjectId(),
            "rule_id": ObjectId(),
            "target_url": "https://example.com",
            "click_count": 0,
        }
    )
    return token


async def test_the_ip_address_is_never_stored(database):
    """The privacy guarantee, asserted rather than assumed.

    `record_click` takes an ip_address so the caller need not special-case it,
    and deliberately drops it: the address identifies a person, the country does
    not.
    """
    repo = LinkClicksRepository(database)
    token = await make_link(database)

    await repo.record_click(
        token, ip_address="203.0.113.7", country="Germany", country_code="DE", city="Berlin"
    )

    row = await database["link_clicks"].find_one({"token": token})
    assert "ip_address" not in row
    assert "203.0.113.7" not in str(row)
    assert row["country"] == "Germany"
    assert row["city"] == "Berlin"


async def test_an_unplaceable_click_stores_no_location_keys(database):
    repo = LinkClicksRepository(database)
    token = await make_link(database)

    await repo.record_click(token, ip_address="10.0.0.1")

    row = await database["link_clicks"].find_one({"token": token})
    assert row["click_count"] == 1
    assert "country" not in row


async def test_clicks_group_by_country(database):
    repo = LinkClicksRepository(database)
    rule_id = ObjectId()
    for country, code, city, clicks in [
        ("Germany", "DE", "Berlin", 5),
        ("Germany", "DE", "Munich", 3),
        ("India", "IN", "Bengaluru", 4),
    ]:
        await database["link_clicks"].insert_one(
            {
                "token": f"t{ObjectId()}",
                "rule_id": rule_id,
                "target_url": "https://example.com",
                "click_count": clicks,
                "country": country,
                "country_code": code,
                "city": city,
            }
        )

    by_country = await repo.clicks_by_place(rule_id)
    assert by_country[0] == {
        "country": "Germany",
        "country_code": "DE",
        "city": None,
        "clicks": 8,
    }

    by_city = await repo.clicks_by_place(rule_id, by_city=True)
    assert {row["city"] for row in by_city} == {"Berlin", "Munich", "Bengaluru"}


async def test_unplaced_rows_are_left_out_rather_than_bucketed(database):
    """Before the database is installed every row is unplaced.

    A chart that is 100% "Unknown" reads as a finding about the audience rather
    than a missing setup step, so those rows are simply absent.
    """
    repo = LinkClicksRepository(database)
    rule_id = ObjectId()
    await database["link_clicks"].insert_one(
        {"token": f"t{ObjectId()}", "rule_id": rule_id, "click_count": 9}
    )

    assert await repo.clicks_by_place(rule_id) == []
