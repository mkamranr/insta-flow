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
        "Open the Settings page. What is editable there depends on the other modules.",
        ("view",),
    ),
    Module(
        "instance",
        "Server settings",
        "The public address, the outbound mail account, the GeoLite2 credentials "
        "and the automated-experience disclosure. One value each for the whole "
        "server, shared by every workspace.",
        ("view", "manage"),
    ),
    Module(
        "workspaces",
        "Workspaces",
        "See and switch into other people's connected accounts.",
        ("view",),
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
WORKSPACE_OWNER_SLUG: Final[str] = "workspace-owner"


def _of(modules: frozenset[str]) -> frozenset[str]:
    """Every permission belonging to any of these modules."""
    return frozenset(f"{m.key}.{a}" for m in MODULES for a in m.actions if m.key in modules)


# Modules no seeded role but Owner may touch at all. Two reasons, same outcome:
# `workspaces` opens somebody else's connected account, and `instance` is one
# setting for the whole server -- a Manager editing it would be editing it for
# every workspace at once, which is not what "runs the automations" means.
OWNER_ONLY_MODULES: Final[frozenset[str]] = frozenset({"workspaces", "instance"})

# Every module that reaches past a single workspace. Two roles are built by
# subtracting it, which is why it is named for what it *is* rather than for one
# of the roles that excludes it.
#
# `users` and `roles` are in here because `GET /api/users` returns every user's
# email, role and last sign-in. That is fine for a Manager helping run one
# deployment, and wrong for someone who was invited to run their own workspace
# and is a stranger to everybody else on the install.
_BEYOND_ONE_WORKSPACE: Final[frozenset[str]] = OWNER_ONLY_MODULES | {"users", "roles"}

# Hidden from Viewer for the same reason, plus one of its own: a Manager may
# legitimately *see* who has an account without being able to change anything
# about them, so this set being broader than OWNER_ONLY_MODULES is deliberate.
# Aliased rather than duplicated -- two lists that must stay equal are two lists
# that will not.
_HIDDEN_FROM_VIEWER: Final[frozenset[str]] = _BEYOND_ONE_WORKSPACE

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
        "Runs the automations end to end. Cannot change who gets in, "
        "or anything that applies to the whole server.",
        ALL - {"users.manage", "roles.manage"} - _of(OWNER_ONLY_MODULES),
        locked=False,
    ),
    # The role an invited person receives. Locked, and that is the point: every
    # tenant holds this one role, so an operator widening it by hand would widen
    # it for all of them at once. Locked also means the API refuses to edit it,
    # which is the precondition `ensure_system_roles` requires before it may
    # re-sync permissions on every boot.
    #
    # Built by subtraction so a permission added to MODULES reaches it without a
    # migration -- but see `test_the_workspace_owner_role_is_exactly_these_permissions`:
    # that same property means a NEW module silently lands in every existing
    # tenant's role on the next boot, and only a frozen literal catches it.
    #
    # Nothing needs excluding beyond `_BEYOND_ONE_WORKSPACE`: the disclosure
    # wording, which used to need its own exclusion here, moved to `instance.*`
    # once it was clear that it is one line for the whole deployment and every
    # workspace's worker reads it.
    SystemRole(
        WORKSPACE_OWNER_SLUG,
        "Workspace owner",
        "Runs their own workspace: their Meta app, their Instagram account, "
        "their automations. Sees nothing outside it.",
        ALL - _of(_BEYOND_ONE_WORKSPACE),
        locked=True,
    ),
    SystemRole(
        "viewer",
        "Viewer",
        "Read-only across the dashboard.",
        # Subtracted, not merely omitted: this set is built by comprehension over
        # MODULES, so a module added later would otherwise hand Viewer its
        # `.view` automatically -- which is how a read-only role would silently
        # acquire the run of every other person's workspace, or sight of the
        # server's own configuration.
        frozenset(f"{m.key}.view" for m in MODULES if m.key not in _HIDDEN_FROM_VIEWER),
        locked=False,
    ),
)
