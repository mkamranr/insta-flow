"""The log redaction filter, for secrets that arrive in a URL.

This is the enforcement point rather than a convention, so it is tested against
the exact shapes seen in production rather than invented ones.
"""
def test_the_webhook_verify_token_is_redacted_in_both_spellings():
    """Meta sends the verify token under `hub.verify_token` AND
    `hub_verify_token` in the same request. Redacting only the documented
    dotted form leaves the secret in the log anyway -- observed live."""
    from app.logging import redact

    line = (
        'GET /webhooks/instagram?hub.mode=subscribe'
        "&hub.verify_token=f47e4fdc0e5c707132cb5aae98ccb2d8"
        "&hub_verify_token=f47e4fdc0e5c707132cb5aae98ccb2d8 HTTP/1.1"
    )

    cleaned = redact(line)

    assert "f47e4fdc" not in cleaned
    assert cleaned.count("[REDACTED]") == 2
    # The rest of the line must survive, or the log stops being useful.
    assert "hub.mode=subscribe" in cleaned
