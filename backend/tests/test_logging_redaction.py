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


def test_an_smtp_password_is_redacted_by_key():
    """`_SENSITIVE_KEYS` matching is exact, not by substring, so the plain
    "password" entry does not cover these two spellings."""
    from app.logging import redact

    payload = redact({"smtp_password": "hunter2", "smtp_password_encrypted": "v1:abc"})
    assert payload == {"smtp_password": "[REDACTED]", "smtp_password_encrypted": "[REDACTED]"}


def test_a_bare_password_assignment_in_free_text_is_redacted():
    """A mail server can echo the AUTH attempt back inside a rejection message,
    and that message is stored on the email_log row."""
    from app.logging import redact

    assert "hunter2" not in redact("refused: password=hunter2")


def test_an_invite_token_in_a_url_is_redacted():
    """ARQ logs a job's arguments verbatim, so an invitation's activation URL
    reaches the log unless the pattern catches it -- which would undo the point
    of storing only the token's hash."""
    from app.logging import redact

    line = "send_email(context={'invite_url': 'https://host/invite?token=s3cr3tvalue'})"
    assert "s3cr3tvalue" not in redact(line)


def test_an_invite_token_hash_is_redacted_by_key():
    from app.logging import redact

    assert redact({"token_hash": "abc123", "invite_token": "raw"}) == {
        "token_hash": "[REDACTED]",
        "invite_token": "[REDACTED]",
    }
