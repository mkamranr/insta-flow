"""§52: secrets must never reach a log sink. These tests are the enforcement check."""

import json
import logging

from app.logging import JsonFormatter, redact

LONG_TOKEN = "IGQVJYb2hkTWpuYVdrZAmVlaGF0Za1234567890abcdef"


def format_record(msg: str, **extra) -> dict:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return json.loads(JsonFormatter().format(record))


def test_query_string_access_token_is_redacted():
    assert "SECRET123" not in redact("GET /access_token?access_token=SECRET123&x=1")


def test_bearer_header_is_redacted():
    assert redact("Authorization: Bearer SECRET123") == "Authorization: Bearer [REDACTED]"


def test_oauth_authorization_code_is_redacted():
    assert "AQBxyz" not in redact("callback?code=AQBxyz&state=abc")


def test_meta_token_shape_is_redacted_even_without_a_key():
    assert LONG_TOKEN not in redact(f"sending with {LONG_TOKEN}")


def test_sensitive_dict_keys_are_replaced_wholesale():
    out = redact({"password": "hunter2", "access_token": LONG_TOKEN, "username": "john"})
    assert out == {
        "password": "[REDACTED]",
        "access_token": "[REDACTED]",
        "username": "john",
    }


def test_redaction_reaches_nested_structures():
    out = redact({"account": {"tokens": [{"access_token": LONG_TOKEN}]}})
    assert out["account"]["tokens"][0]["access_token"] == "[REDACTED]"


def test_non_secret_values_survive_untouched():
    assert redact({"comment_id": "17900", "count": 3, "ok": True}) == {
        "comment_id": "17900",
        "count": 3,
        "ok": True,
    }


def test_formatter_emits_the_structured_shape_from_the_spec():
    out = format_record("private_reply_success", comment_id="17900", campaign_id="abc")
    assert out["level"] == "INFO"
    assert out["event"] == "private_reply_success"
    assert out["comment_id"] == "17900"
    assert out["campaign_id"] == "abc"
    assert "timestamp" in out and "service" in out


def test_formatter_redacts_extras():
    out = format_record("oauth_exchange", access_token=LONG_TOKEN)
    assert out["access_token"] == "[REDACTED]"
