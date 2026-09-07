"""Email templates, and the boundary between their vocabulary and the DM one.

The renderer was shared between direct messages and email rather than copied, so
the risk worth testing is that sharing it quietly merged the two allowlists --
which would let a mail variable be rendered into a DM.
"""

import pytest

from app.errors import TemplateError
from app.services import templating
from app.services.campaigns import ALLOWED_VARIABLES as DM_VARIABLES
from app.services.campaigns import render as render_dm
from app.services.email.templates import (
    EMAIL_MAX_LENGTH,
    EMAIL_VARIABLES,
    TEMPLATES,
    render_template,
)


def test_every_template_renders_with_its_declared_variables() -> None:
    context = dict.fromkeys(EMAIL_VARIABLES, "x")
    for key in TEMPLATES:
        subject, body = render_template(key, context)
        assert subject and body


def test_an_unknown_template_key_is_refused() -> None:
    with pytest.raises(TemplateError):
        render_template("no-such-template", {})


def test_an_unknown_variable_in_an_email_template_is_refused() -> None:
    with pytest.raises(TemplateError):
        templating.render(
            "Hello {{not_a_variable}}",
            {},
            allowed=EMAIL_VARIABLES,
            max_length=EMAIL_MAX_LENGTH,
        )


def test_the_dm_allowlist_still_rejects_an_email_variable() -> None:
    """The regression guard for having widened the renderer.

    If the DM default ever becomes the union of both vocabularies, this is what
    catches it -- an automation author must not be able to render `{{invite_url}}`
    into a direct message.
    """
    assert "invite_url" not in DM_VARIABLES
    with pytest.raises(TemplateError):
        render_dm("Here you go {{invite_url}}", {"invite_url": "https://example.com"})


def test_the_email_allowlist_still_rejects_a_dm_variable() -> None:
    """And the same in the other direction, so the two stay genuinely separate."""
    with pytest.raises(TemplateError):
        templating.render(
            "Hi {{username}}", {}, allowed=EMAIL_VARIABLES, max_length=EMAIL_MAX_LENGTH
        )


def test_a_substituted_value_is_never_rescanned() -> None:
    """The guarantee carried over from the DM renderer: a value that looks like
    a placeholder is text, not a second variable."""
    rendered = templating.render(
        "Link: {{invite_url}}",
        {"invite_url": "{{origin}}", "origin": "https://evil.example"},
        allowed=EMAIL_VARIABLES,
        max_length=EMAIL_MAX_LENGTH,
    )
    assert rendered == "Link: {{origin}}"
    assert "evil.example" not in rendered


def test_an_email_body_may_be_far_longer_than_a_dm() -> None:
    """The DM cap is what Meta will carry; an email has no such constraint."""
    body = "a" * 5_000
    assert (
        len(templating.render(body, {}, allowed=EMAIL_VARIABLES, max_length=EMAIL_MAX_LENGTH))
        == 5_000
    )
    with pytest.raises(TemplateError):
        render_dm(body, {})


def test_the_primitive_demands_an_explicit_vocabulary() -> None:
    """`allowed` and `max_length` are keyword-only with no defaults on purpose:
    a caller cannot inherit somebody else's allowlist by omission."""
    with pytest.raises(TypeError):
        templating.render("hi", {})  # type: ignore[call-arg]
