"""Plain `{{variable}}` substitution against a caller-supplied allowlist.

Extracted from `services/campaigns/renderer.py` when outbound email arrived and
needed the same substitution with a different vocabulary and a much larger size
limit. The guarantee that matters is preserved exactly: **substituted values are
never re-scanned**, so a comment containing `{{link}}` cannot inject a variable,
and a template can never become code.

Deliberately not Jinja and not `eval`. DM templates are edited through the
dashboard, so a template language would turn that form into remote code
execution -- and email bodies now render values that originated as comment text.

`allowed` and `max_length` are keyword-only and have **no defaults**. Every
caller states its own vocabulary and its own ceiling, which is what stops one
surface's allowlist quietly becoming the union of both: an email variable must
not be renderable in a DM. `services/campaigns/renderer.py` supplies the DM
pair; `services/email/templates.py` supplies the mail pair.
"""

import re
from typing import Final

from app.errors import TemplateError

# Matches {{ name }} with optional surrounding whitespace. Names are restricted
# to identifiers, so nothing expression-like can appear here at all.
_PLACEHOLDER: Final = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def extract_variables(body: str) -> set[str]:
    return set(_PLACEHOLDER.findall(body or ""))


def validate(body: str, *, allowed: frozenset[str], max_length: int) -> set[str]:
    """Check a template at save time. Returns the variables it uses.

    Rejecting unknown names here means a bad template can never reach the worker,
    where the failure would be invisible until a real comment arrived.
    """
    if not body or not body.strip():
        raise TemplateError("Template body cannot be empty.")

    unknown = extract_variables(body) - allowed
    if unknown:
        permitted = ", ".join(f"{{{{{name}}}}}" for name in sorted(allowed))
        raise TemplateError(
            f"Unknown template variable(s): {', '.join(sorted(unknown))}. Allowed: {permitted}"
        )

    # A stray single brace is almost always a typo for a placeholder.
    stripped = _PLACEHOLDER.sub("", body)
    if "{{" in stripped or "}}" in stripped:
        raise TemplateError("Unclosed or malformed {{placeholder}}. Check for a missing brace.")

    return extract_variables(body)


def render(
    body: str,
    context: dict[str, str | None],
    *,
    allowed: frozenset[str],
    max_length: int,
) -> str:
    """Substitute allowed variables. Unknown names raise; known-but-absent render empty.

    The substituted values are never re-scanned, so a value containing
    `{{link}}` cannot inject another variable.
    """
    validate(body, allowed=allowed, max_length=max_length)

    def substitute(match: re.Match[str]) -> str:
        return str(context.get(match.group(1)) or "")

    rendered = _PLACEHOLDER.sub(substitute, body).strip()

    if not rendered:
        raise TemplateError("Rendered message is empty.")
    if len(rendered) > max_length:
        raise TemplateError(
            f"Rendered message is {len(rendered)} characters; the limit is {max_length}."
        )
    return rendered
