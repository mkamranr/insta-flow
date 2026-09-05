"""No log call may reuse a reserved `LogRecord` attribute name.

`logging.Logger.makeRecord` raises `KeyError: "Attempt to overwrite 'x' in
LogRecord"` when `extra` names one of its own attributes. It raises at the log
call, so the damage lands wherever that line happens to sit: `rules.py::clone_rule`
logged `extra={"created": ...}` *after* inserting the clones, which turned a
completed write into a 500 and told the operator the clone had failed while the
new automations sat in the database.

A unit test cannot catch this -- it only fires when that exact line runs, and
these lines are the ones that run on success paths nobody asserts on. So the
source is inspected instead, the same way `test_authorization.py` walks routes
rather than trusting each handler to have remembered its own check.
"""

import ast
import pathlib

import pytest

# `logging.LogRecord.__init__` sets these on every record, and `makeRecord`
# refuses to let `extra` shadow any of them. Kept as a literal set rather than
# introspected: the point is to state the contract, not to re-derive it from the
# same standard library that would happily change underneath us.
RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

APP = pathlib.Path(__file__).resolve().parent.parent / "app"


def _offending_extra_keys() -> list[str]:
    found: list[str] = []
    for path in sorted(APP.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                    continue
                for key in keyword.value.keys:
                    if isinstance(key, ast.Constant) and key.value in RESERVED:
                        found.append(
                            f"{path.relative_to(APP.parent)}:{key.lineno} -> {key.value!r}"
                        )
    return found


def test_no_log_extra_shadows_a_logrecord_attribute() -> None:
    offenders = _offending_extra_keys()
    assert not offenders, (
        "These `extra=` keys collide with reserved LogRecord attributes and will raise "
        "KeyError when the line executes:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_would_actually_catch_one() -> None:
    """The guard above passes trivially if the scan is broken, so prove it bites."""
    tree = ast.parse('logger.info("x", extra={"created": 1})')
    keys = [
        key.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict)
        for key in keyword.value.keys
        if isinstance(key, ast.Constant) and key.value in RESERVED
    ]
    assert keys == ["created"]


def test_logging_really_rejects_a_reserved_key() -> None:
    """Pin the behaviour this whole file exists for, so it is not folklore.

    The level has to be set explicitly. `Logger.info` consults `isEnabledFor`
    before it ever builds a record, so with INFO disabled the offending call is a
    silent no-op -- which is precisely how a line like this reaches production:
    it is inert under a default test configuration and raises the moment a real
    deployment turns INFO on.
    """
    import logging

    logger = logging.getLogger("test_reserved_extra")
    logger.setLevel(logging.INFO)
    logging.disable(logging.NOTSET)

    with pytest.raises(KeyError, match="Attempt to overwrite 'created'"):
        logger.info("boom", extra={"created": 1})
