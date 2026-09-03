"""Country and city for a link click, resolved locally.

MaxMind offers two products. This uses the **downloadable database**, not the web
service: every lookup happens on this machine, so no visitor IP is ever sent to a
third party. That matters because Social Flow otherwise makes no outbound request
on a visitor's behalf.

Only the *derived* country and city are ever stored. The IP is used for the
lookup and dropped -- it is the identifying part, and keeping it would turn a
click counter into a location log.

Download mechanics verified against MaxMind's docs 2026-09-03:
<https://dev.maxmind.com/geoip/updating-databases/>
"""

import io
import logging
import tarfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

EDITION = "GeoLite2-City"
DOWNLOAD_URL = f"https://download.maxmind.com/geoip/databases/{EDITION}/download"
DATABASE_NAME = f"{EDITION}.mmdb"
DOWNLOAD_TIMEOUT = 120.0


class GeoIpError(RuntimeError):
    """Download or extraction failed. Never raised by a lookup."""


@dataclass(frozen=True)
class Location:
    country: str | None = None
    country_code: str | None = None
    city: str | None = None

    @property
    def known(self) -> bool:
        return bool(self.country_code or self.city)


def database_path(directory: Path) -> Path:
    return directory / DATABASE_NAME


async def remote_build_date(account_id: str, license_key: str) -> datetime | None:
    """When MaxMind last rebuilt the database.

    A HEAD request, which their docs say does not count against the daily
    download limit -- so this is safe to call on a schedule.
    """
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.head(
            DOWNLOAD_URL, params={"suffix": "tar.gz"}, auth=(account_id, license_key)
        )
        if response.status_code == 401:
            raise GeoIpError("MaxMind rejected the account ID or license key.")
        response.raise_for_status()
        stamp = response.headers.get("last-modified")
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=UTC)
    except ValueError:
        return None


async def download(account_id: str, license_key: str, directory: Path) -> Path:
    """Fetch the database and unpack just the .mmdb into `directory`."""
    directory.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        response = await client.get(
            DOWNLOAD_URL, params={"suffix": "tar.gz"}, auth=(account_id, license_key)
        )
        if response.status_code == 401:
            raise GeoIpError("MaxMind rejected the account ID or license key.")
        if response.status_code == 429:
            raise GeoIpError("MaxMind download limit reached for today; try again tomorrow.")
        response.raise_for_status()
        payload = response.content

    target = database_path(directory)
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            # The archive nests the database under a dated directory. Pull that
            # one member out by name rather than extracting the whole tree --
            # an archive is untrusted input and extractall can write anywhere.
            member = next(
                (m for m in archive.getmembers() if m.isfile() and m.name.endswith(DATABASE_NAME)),
                None,
            )
            if member is None:
                raise GeoIpError(f"No {DATABASE_NAME} inside the downloaded archive.")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise GeoIpError("Could not read the database out of the archive.")
            target.write_bytes(extracted.read())
    except tarfile.TarError as exc:
        raise GeoIpError(f"The download was not a readable archive: {exc}") from exc

    logger.info("geoip_database_updated", extra={"bytes": target.stat().st_size})
    return target


class GeoIpReader:
    """Opens the database once and keeps it open.

    Reopening per click would mean a file open on every redirect; the database is
    replaced rarely, so `reload()` is called by the refresh job instead.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._reader: Any | None = None
        self._loaded_from: float | None = None

    def _open(self) -> Any | None:
        path = database_path(self._directory)
        if not path.is_file():
            return None
        stamp = path.stat().st_mtime
        if self._reader is not None and self._loaded_from == stamp:
            return self._reader
        try:
            import maxminddb

            self.close()
            self._reader = maxminddb.open_database(str(path))
            self._loaded_from = stamp
        except Exception:
            logger.warning("geoip_database_unreadable", exc_info=True)
            self._reader = None
        return self._reader

    def close(self) -> None:
        if self._reader is not None:
            # Closing is best effort: the reader is being replaced either way,
            # and a failure here must not stop the new database loading.
            with suppress(Exception):
                self._reader.close()
            self._reader = None

    def lookup(self, ip_address: str | None) -> Location:
        """Best-effort. A click is never lost because geolocation failed."""
        if not ip_address:
            return Location()
        reader = self._open()
        if reader is None:
            return Location()
        try:
            record = reader.get(ip_address)
        except Exception:
            # Private ranges and malformed addresses land here. Not worth a log
            # line per click.
            return Location()
        if not isinstance(record, dict):
            return Location()

        country = record.get("country") or record.get("registered_country") or {}
        city = record.get("city") or {}
        return Location(
            country=(country.get("names") or {}).get("en"),
            country_code=country.get("iso_code"),
            city=(city.get("names") or {}).get("en"),
        )
