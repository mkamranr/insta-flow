"""Enforces the claim that all SMTP lives in one module.

The working notes and `DEVELOPMENT.md` already assert a boundary of this shape
for `services/meta/` -- "enforced by grep in the test run" -- and no such test
was ever written. `docs/architecture.md` is the honest one: it says review.
A convention nothing checks is one that has already drifted somewhere.

So this file scans the source rather than trusting each module, the same way
`test_logging_fields.py` walks the AST rather than trusting every call site. The
helper is deliberately generic, so closing the Meta claim is one more test.
"""

import ast
import pathlib

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _importers(*modules: str) -> set[str]:
    """Every file under `app/` that imports any of these top-level modules."""
    wanted = set(modules)
    found: set[str] = set()

    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name.split(".")[0] in wanted for name in names):
                found.add(str(path.relative_to(APP.parent)))

    return found


def test_only_the_transport_module_speaks_smtp() -> None:
    """`aiosmtplib` and `smtplib` are reachable from exactly one file.

    Everything else depends on the `MailerProtocol`, which is what lets
    `MockMailer` substitute in tests -- and what keeps a blocking stdlib
    `smtplib` call from ever appearing on the worker's event loop.
    """
    assert _importers("aiosmtplib", "smtplib") == {"app/services/email/transport.py"}


def test_the_scan_would_actually_catch_one(tmp_path: pathlib.Path) -> None:
    """A silently broken scanner passes forever.

    `test_logging_fields.py` carries the same companion for the same reason: the
    test above is only worth having if a violation makes it fail.
    """
    offender = APP / "_boundary_probe.py"
    offender.write_text("import aiosmtplib\n")
    try:
        assert "app/_boundary_probe.py" in _importers("aiosmtplib", "smtplib")
    finally:
        offender.unlink()


def test_nothing_outside_the_email_package_imports_the_transport() -> None:
    """The mailer is reached through the factory, never by constructing the
    transport directly -- otherwise `EMAIL_CLIENT=mock` would be bypassable."""
    offenders = {
        path
        for path in _importers("app")
        if "services/email" not in path
        and "transport" in (APP.parent / path).read_text()
        and "email.transport" in (APP.parent / path).read_text()
    }
    assert offenders == set()
