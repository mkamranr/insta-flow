"""MongoDB connection lifecycle.

Uses pymongo's native async client (`AsyncMongoClient`, pymongo >= 4.9). Motor is
deprecated in favour of it, so do not reintroduce Motor here.
"""

import logging
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

_client: AsyncMongoClient[dict[str, Any]] | None = None
_database: AsyncDatabase[dict[str, Any]] | None = None


async def connect(mongo_url: str, database: str) -> AsyncDatabase[dict[str, Any]]:
    global _client, _database
    _client = AsyncMongoClient(mongo_url, tz_aware=True, serverSelectionTimeoutMS=5000)
    _database = _client[database]
    await _client.admin.command("ping")
    logger.info("mongodb_connected", extra={"database": database})
    return _database


async def disconnect() -> None:
    global _client, _database
    if _client is not None:
        await _client.close()
        logger.info("mongodb_disconnected")
    _client = None
    _database = None


def get_database() -> AsyncDatabase[dict[str, Any]]:
    if _database is None:
        raise RuntimeError("MongoDB is not connected; call connect() during startup")
    return _database


async def ping() -> bool:
    """Cheap liveness probe used by /ready. Never raises."""
    try:
        if _client is None:
            return False
        await _client.admin.command("ping")
        return True
    except Exception:
        logger.warning("mongodb_ping_failed", exc_info=True)
        return False
