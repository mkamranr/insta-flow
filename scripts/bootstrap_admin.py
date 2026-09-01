#!/usr/bin/env python3
"""Create the initial admin user.

Reads ADMIN_EMAIL / ADMIN_PASSWORD from the environment, or prompts when a TTY is
available. The password is never echoed, never logged, and only its Argon2 hash
is stored.

    make bootstrap-admin
"""

import asyncio
import os
import sys
from getpass import getpass

from app.config import get_settings
from app.db import mongodb
from app.db.indexes import create_indexes
from app.db.repositories import UsersRepository
from app.logging import configure_logging
from app.services.security.passwords import MIN_PASSWORD_LENGTH, hash_password


def collect_credentials() -> tuple[str, str]:
    email = os.environ.get("ADMIN_EMAIL", "").strip()
    password = os.environ.get("ADMIN_PASSWORD", "")

    if not email:
        if not sys.stdin.isatty():
            print("Set ADMIN_EMAIL and ADMIN_PASSWORD, or run with a TTY.", file=sys.stderr)
            raise SystemExit(2)
        email = input("Admin email: ").strip()

    if not password:
        if not sys.stdin.isatty():
            print("Set ADMIN_PASSWORD, or run with a TTY.", file=sys.stderr)
            raise SystemExit(2)
        password = getpass("Admin password: ")
        if password != getpass("Confirm password: "):
            print("Passwords do not match.", file=sys.stderr)
            raise SystemExit(1)

    if "@" not in email:
        print(f"'{email}' does not look like an email address.", file=sys.stderr)
        raise SystemExit(1)
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        raise SystemExit(1)

    return email.lower(), password


async def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level, service="scripts")
    email, password = collect_credentials()

    database = await mongodb.connect(settings.mongo_url, settings.mongo_database)
    try:
        await create_indexes(database)
        users = UsersRepository(database)

        if await users.get_by_email(email) is not None:
            print(f"A user with email {email} already exists. Nothing to do.")
            return 0

        user = await users.create(email, hash_password(password))
        print(f"Created admin user {email} (id {user['_id']}).")
        return 0
    finally:
        await mongodb.disconnect()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
