"""The permission catalogue, as plain data.

Same reasoning as `constants.py`: a declaration, not logic. Nothing here imports
from the rest of the app, so the grid the admin screen renders and the checks the
API enforces read one list and cannot drift apart. Adding a permission is one line
in `MODULES`, and `require()` refuses any string this file cannot produce.

A permission is `<module>.<action>`. The grid is deliberately **sparse**: not every
module supports every action, and `Module.actions` says which cells exist. A cell
that does not exist is rendered as a dash rather than an unchecked box -- "this
module cannot do that" and "this role was not granted that" are different claims
and must not look the same.

Unrelated, despite the name: `instagram_accounts.permissions` holds Meta OAuth
scopes, which are a different thing entirely.
"""

from typing import Final, NamedTuple


class Action(NamedTuple):
    key: str
    label: str


class Module(NamedTuple):
    key: str
    label: str
    description: str
    actions: tuple[str, ...]


# The grid's columns, in order. Keep this vocabulary small and closed: once it
# grows past five or six the matrix stops being readable as a matrix, and an
# action that only one module understands belongs in that module's `edit`.
ACTIONS: Final[tuple[Action, ...]] = (
    Action("view", "View"),
    Action("create", "Create"),
    Action("edit", "Edit"),
    Action("delete", "Delete"),
    Action("manage", "Manage"),
)

# Cells whose plain verb would mislead. "Edit" on Posts & stories means "pull
# fresh ones from Instagram"; an operator ticking a box they misread is a
# permissions bug, not a copy bug.
LABEL_OVERRIDES: Final[dict[str, str]] = {"content.edit": "Sync"}

MODULES: Final[tuple[Module, ...]] = (
    Module(
        "automations",
        "Automations",
        "Rules that answer comments, story replies and DMs.",
        ("view", "create", "edit", "delete"),
    ),
    Module(
        "content",
        "Posts & stories",
        "Browse synced posts and stories, and pull fresh ones from Instagram.",
        ("view", "edit"),
    ),
    Module(
        "analytics",
        "Analytics",
        "Performance, trends, the activity log and click geography.",
        ("view",),
    ),
    Module(
        "contacts",
        "Contacts",
        "People who engaged, including any email addresses collected.",
        ("view",),
    ),
    Module(
        "instagram",
        "Instagram account",
        "Connect, disconnect, refresh the token, manage webhook subscriptions.",
        ("view", "manage"),
    ),
    Module(
        "settings",
        "Settings",
        "The automation disclosure line and the GeoLite2 credentials.",
        ("view", "edit"),
    ),
    Module(
        "users",
        "Users",
        "Add people, change their role, deactivate them.",
        ("view", "manage"),
    ),
    Module(
        "roles",
        "Roles",
        "Create roles and choose what each one may do.",
        ("view", "manage"),
    ),
)

ALL: Final[frozenset[str]] = frozenset(f"{m.key}.{a}" for m in MODULES for a in m.actions)

_ACTION_LABELS: Final[dict[str, str]] = {a.key: a.label for a in ACTIONS}


def is_valid(permission: str) -> bool:
    return permission in ALL


def label_for(module_key: str, action_key: str) -> str:
    return LABEL_OVERRIDES.get(f"{module_key}.{action_key}", _ACTION_LABELS[action_key])


def for_grid() -> dict[str, object]:
    """Everything the admin screen needs to draw the grid, and nothing else.

    Sent to the browser so the frontend never hardcodes a permission list. A
    module that gains an action ships without a frontend change.
    """
    return {
        "actions": [{"key": a.key, "label": a.label} for a in ACTIONS],
        "modules": [
            {
                "key": m.key,
                "label": m.label,
                "description": m.description,
                "actions": [
                    {
                        "key": action,
                        "label": label_for(m.key, action),
                        "permission": f"{m.key}.{action}",
                    }
                    for action in m.actions
                ],
            }
            for m in MODULES
        ],
    }


class SystemRole(NamedTuple):
    slug: str
    name: str
    description: str
    permissions: frozenset[str]
    # True = the seeder owns this role forever: it is re-synced on every boot and
    # the API refuses to edit or delete it. False = seeded once as a starting
    # point, then owned entirely by the operator and never touched again.
    locked: bool


OWNER_SLUG: Final[str] = "owner"

SYSTEM_ROLES: Final[tuple[SystemRole, ...]] = (
    # Owner is ALL by construction, not by a hand-written list, and is re-synced
    # on every boot. A permission added to MODULES therefore reaches Owner with
    # nothing else to remember and no migration to write.
    SystemRole(
        OWNER_SLUG,
        "Owner",
        "Full control, including people and roles.",
        ALL,
        locked=True,
    ),
    SystemRole(
        "manager",
        "Manager",
        "Runs the automations end to end. Cannot change who gets in.",
        ALL - {"users.manage", "roles.manage", "settings.edit"},
        locked=False,
    ),
    SystemRole(
        "viewer",
        "Viewer",
        "Read-only across the dashboard.",
        frozenset(f"{m.key}.view" for m in MODULES if m.key not in {"users", "roles"}),
        locked=False,
    ),
)
