"""Email capture.

Extracting an address from a free-text DM reply, which is what "collect email
before sending" actually receives — people write "sure, it's me@example.com".
"""

import re

# Deliberately permissive: the goal is to find an address in a sentence, not to
# adjudicate RFC 5322. Over-strict matching rejects real addresses.
EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)

MAX_EMAIL_LENGTH = 254


def extract_email(text: str) -> str | None:
    """First address in the text, lowercased, or None."""
    if not text:
        return None
    match = EMAIL_PATTERN.search(text)
    if match is None:
        return None
    email = match.group(0).rstrip(".").lower()
    return email if len(email) <= MAX_EMAIL_LENGTH else None
