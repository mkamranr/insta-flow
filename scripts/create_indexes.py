#!/usr/bin/env python3
"""Create/verify every MongoDB index from project-plan.md §18.

Idempotent. The API also does this at startup; this script exists for
operational use (a fresh volume, or verifying a running deployment).
"""

import asyncio
import sys

from app.config import get_settings
from app.db import mongodb
from app.db.indexes import COLLECTIONS, create_indexes
from app.logging import configure_logging


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, service="scripts")

    database = await mongodb.connect(settings.mongo_url, settings.mongo_database)
    try:
        created = await create_indexes(database)
        for collection in COLLECTIONS:
            names = created.get(collection, [])
            print(f"  {collection:<20} {', '.join(names) if names else '(no extra indexes)'}")
        total = sum(len(v) for v in created.values())
        print(f"\n{total} indexes across {len(COLLECTIONS)} collections.")
        return 0
    finally:
        await mongodb.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
