"""Roles: naming a set of permissions so it can be handed to people."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pymongo.errors import DuplicateKeyError

from app.api.deps import AuditRepo, CurrentGrant, CurrentUser, RolesRepo, UsersRepo, require
from app.db.repositories import to_object_id
from app.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.models.roles import RolePublic, RoleUpdate, RoleWrite
from app.permissions import for_grid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/roles", tags=["roles"])
# Its own router rather than `/api/roles/catalogue`, which would have to be
# declared before `/{role_id}` or be swallowed by it.
catalogue_router = APIRouter(prefix="/api/permissions", tags=["roles"])


@catalogue_router.get("", dependencies=[Depends(require("roles.view"))])
async def catalogue(user: CurrentUser) -> dict[str, Any]:
    """The permission catalogue, so the admin grid is never hardcoded in the SPA."""
    return for_grid()


def _object_id(value: str, field: str) -> Any:
    try:
        return to_object_id(value)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid id.") from exc


async def _editable(roles: Any, role_id: str) -> dict[str, Any]:
    """Load a role that may be changed.

    A system role's permissions are owned by the seeder and re-synced on every
    boot. Allowing an edit here would mean silently undoing the operator's change
    at the next restart, with nothing to say it had happened -- so the edit is
    refused and duplicating is offered instead.
    """
    role = await roles.get(_object_id(role_id, "role_id"))
    if role is None:
        raise NotFoundError("Role not found.")
    if role.get("is_system"):
        raise ConflictError("Built-in roles cannot be changed. Duplicate it instead.")
    return role


def _refuse_granting_what_you_lack(permissions: list[str], grant: Any) -> None:
    """You cannot hand out access you do not hold yourself.

    Without this, anyone with `roles.manage` could mint a role carrying every
    permission and assign it to themselves -- which makes every other guardrail
    in this file decorative.
    """
    excess = sorted(set(permissions) - grant.permissions)
    if excess:
        raise AuthorizationError("You cannot grant access you do not have yourself.")


@router.get("", dependencies=[Depends(require("roles.view"))])
async def list_roles(user: CurrentUser, roles: RolesRepo, users: UsersRepo) -> list[RolePublic]:
    found = await roles.list_all()
    return [
        RolePublic.from_document(role, await users.count_with_role(role["_id"]))
        for role in found
    ]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("roles.manage"))],
)
async def create_role(
    payload: RoleWrite,
    request: Request,
    user: CurrentUser,
    grant: CurrentGrant,
    roles: RolesRepo,
    audit: AuditRepo,
) -> RolePublic:
    _refuse_granting_what_you_lack(payload.permissions, grant)
    try:
        role = await roles.create(
            name=payload.name, description=payload.description, permissions=payload.permissions
        )
    except DuplicateKeyError as exc:
        raise ConflictError("A role with that name already exists.") from exc

    await audit.record(
        user_id=user["_id"],
        action="role.created",
        entity_type="role",
        entity_id=role["_id"],
        changes={"name": role["name"], "permissions": ",".join(role["permissions"])},
        ip_address=request.client.host if request.client else None,
    )
    return RolePublic.from_document(role)


@router.get("/{role_id}", dependencies=[Depends(require("roles.view"))])
async def get_role(
    role_id: str, user: CurrentUser, roles: RolesRepo, users: UsersRepo
) -> RolePublic:
    role = await roles.get(_object_id(role_id, "role_id"))
    if role is None:
        raise NotFoundError("Role not found.")
    return RolePublic.from_document(role, await users.count_with_role(role["_id"]))


@router.patch("/{role_id}", dependencies=[Depends(require("roles.manage"))])
async def update_role(
    role_id: str,
    payload: RoleUpdate,
    request: Request,
    user: CurrentUser,
    grant: CurrentGrant,
    roles: RolesRepo,
    users: UsersRepo,
    audit: AuditRepo,
) -> RolePublic:
    role = await _editable(roles, role_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise ValidationError("No fields to update.")

    if "permissions" in changes:
        _refuse_granting_what_you_lack(changes["permissions"], grant)
    if "name" in changes:
        changes["slug"] = _slug(changes["name"])

    try:
        updated = await roles.update(role["_id"], changes)
    except DuplicateKeyError as exc:
        raise ConflictError("A role with that name already exists.") from exc
    if updated is None:  # pragma: no cover - loaded a moment ago
        raise NotFoundError("Role not found.")

    before = set(role.get("permissions", ()))
    after = set(updated.get("permissions", ()))
    await audit.record(
        user_id=user["_id"],
        action="role.updated",
        entity_type="role",
        entity_id=role["_id"],
        # A diff, not the whole list: that is what someone auditing this searches for.
        changes={
            "added": ",".join(sorted(after - before)),
            "removed": ",".join(sorted(before - after)),
        },
        ip_address=request.client.host if request.client else None,
    )
    return RolePublic.from_document(updated, await users.count_with_role(role["_id"]))


@router.delete(
    "/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("roles.manage"))],
)
async def delete_role(
    role_id: str,
    request: Request,
    user: CurrentUser,
    roles: RolesRepo,
    users: UsersRepo,
    audit: AuditRepo,
) -> None:
    role = await _editable(roles, role_id)

    assigned = await users.count_with_role(role["_id"])
    if assigned:
        # Deleting anyway would leave those people resolving to no permissions at
        # all -- a silent, total lockout that reads as a bug rather than a decision.
        people = "person" if assigned == 1 else "people"
        raise ConflictError(
            f"{assigned} {people} still have this role. Move them to another role first."
        )

    await roles.delete(role["_id"])
    await audit.record(
        user_id=user["_id"],
        action="role.deleted",
        entity_type="role",
        entity_id=role["_id"],
        changes={"name": role["name"]},
        ip_address=request.client.host if request.client else None,
    )
    logger.info("role_deleted", extra={"role_id": role_id})


def _slug(name: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in name.lower())
    return "-".join(part for part in cleaned.split("-") if part) or "role"
