"""Click geography: the per-click row, and what the map does with it.

This path had no test at all. It went unnoticed for a day that every click was
being recorded unlocatable, because the only symptom was an empty chart -- and
an empty chart is also what "nobody clicked" looks like.

The distinction these tests pin is `located` vs `total_clicks`. Equal means
every click was placed; clicks with nothing located means locations are not
being derived; both zero means nobody clicked.
"""

from datetime import UTC, datetime, timedelta

from bson import ObjectId

from app.db.repositories import ClickEventsRepository, LinkClicksRepository
from app.services.geoip import Location

ACCOUNT = ObjectId()


def place_at(monkeypatch, country="United Arab Emirates", code="AE", city="Abu Dhabi"):
    """Make every lookup resolve, without needing the 65MB database in CI."""
    from app.services import geoip

    monkeypatch.setattr(
        geoip.GeoIpReader,
        "lookup",
        lambda self, ip: Location(country=country, country_code=code, city=city),
    )


async def add_link(database, token, *, account_id=ACCOUNT, rule_id=None):
    await LinkClicksRepository(database).insert(
        {
            "token": token,
            "instagram_account_id": account_id,
            "rule_id": rule_id or ObjectId(),
            "target_url": "https://example.com/landing",
            "click_count": 0,
        }
    )


# -- a click writes a row ------------------------------------------------------


async def test_a_click_writes_exactly_one_click_event(api, database, monkeypatch):
    """`link_clicks` holds a counter and cannot say *when*. A token clicked five
    times over three days is one row with one timestamp."""
    place_at(monkeypatch)
    await add_link(database, "geo1")

    await api.get("/r/geo1")

    rows = await database["click_events"].find({"token": "geo1"}).to_list(None)
    assert len(rows) == 1
    assert rows[0]["country_code"] == "AE"
    assert rows[0]["city"] == "Abu Dhabi"


async def test_three_clicks_write_three_rows(api, database, monkeypatch):
    place_at(monkeypatch)
    await add_link(database, "geo2")

    for _ in range(3):
        await api.get("/r/geo2")

    assert await database["click_events"].count_documents({"token": "geo2"}) == 3


async def test_the_ip_is_never_stored(api, database, monkeypatch):
    """The address is used for the lookup and dropped. Only the derived place
    is kept, here and on the link row."""
    place_at(monkeypatch)
    await add_link(database, "geo3")

    await api.get("/r/geo3", headers={"X-Forwarded-For": "94.59.71.70"})

    row = await database["click_events"].find_one({"token": "geo3"})
    assert "94.59.71.70" not in str(row)
    assert not any("ip" in key for key in row)


async def test_an_unlocated_click_still_writes_a_row(api, database, monkeypatch):
    """Otherwise `total_clicks` would equal `located` by construction, and the
    chart could never say "clicks happened, none could be placed"."""
    from app.services import geoip

    monkeypatch.setattr(geoip.GeoIpReader, "lookup", lambda self, ip: Location())
    await add_link(database, "geo4")

    await api.get("/r/geo4")

    row = await database["click_events"].find_one({"token": "geo4"})
    assert row is not None
    assert row["country_code"] is None


async def test_a_link_with_no_account_redirects_but_records_no_row(api, database, monkeypatch):
    """A deliberate skip, not an accident: the row is keyed by account and there
    is nothing to key it to. The visitor still gets their page."""
    place_at(monkeypatch)
    await LinkClicksRepository(database).insert(
        {"token": "geo5", "target_url": "https://example.com", "click_count": 0}
    )

    response = await api.get("/r/geo5")

    assert response.status_code == 302
    assert await database["click_events"].count_documents({"token": "geo5"}) == 0


# -- what the map does with the rows -------------------------------------------


async def add_event(database, *, country="France", code="FR", city="Paris", days_ago=0):
    await database["click_events"].insert_one(
        {
            "instagram_account_id": ACCOUNT,
            "rule_id": None,
            "token": "t",
            "country": country,
            "country_code": code,
            "city": city,
            "clicked_at": datetime.now(UTC) - timedelta(days=days_ago),
        }
    )


async def test_unplaced_clicks_are_excluded_not_bucketed_as_unknown(database):
    """Before the database is installed every click is unplaced, and a chart
    that is entirely "Unknown" reads as a finding rather than a missing setup."""
    events = ClickEventsRepository(database)
    await add_event(database)
    await add_event(database, country=None, code=None, city=None)

    rows = await events.by_place(ACCOUNT, datetime.now(UTC) - timedelta(days=7))

    assert [r["country_code"] for r in rows] == ["FR"]
    assert sum(r["clicks"] for r in rows) == 1


async def test_by_city_drops_a_row_that_has_a_country_but_no_city(database):
    """GeoLite2 returns no city for many mobile ranges, so the City tab can be
    empty while Country has rows. That is correct, not a bug to chase."""
    events = ClickEventsRepository(database)
    await add_event(database, city=None)

    assert await events.by_place(ACCOUNT, datetime.now(UTC) - timedelta(days=7)) != []
    assert await events.by_place(ACCOUNT, datetime.now(UTC) - timedelta(days=7), by_city=True) == []


async def test_a_click_outside_the_window_is_not_counted(database):
    events = ClickEventsRepository(database)
    await add_event(database, days_ago=30)

    assert await events.by_place(ACCOUNT, datetime.now(UTC) - timedelta(days=7)) == []


# -- the endpoint shape --------------------------------------------------------


async def test_places_returns_the_full_shape_with_no_account(api, signed_in):
    """The TypeScript type declares every field required, and the UI needs
    `total_clicks` and `links` to say anything true about an empty chart. This
    used to return only {rows, located}."""
    response = await api.get("/api/analytics/places")

    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"by", "rows", "located", "total_clicks", "countries", "links"}
    assert body["total_clicks"] == 0
