"""User administration: who exists, and which role they hold."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from pymongo.errors import DuplicateKeyError

from app.api.deps import (
    AuditRepo,
    CurrentGrant,
    CurrentUser,
    Grant,
    RolesRepo,
    UsersRepo,
    require,
)
from app.db.repositories import to_object_id
from app.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.models.users import (
    CreateUserRequest,
    SetPasswordRequest,
    UpdateUserRequest,
    UserPublic,
)
from app.permissions import OWNER_SLUG
from app.services.security.passwords import hash_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


def _object_id(value: str, field: str) -> Any:
    try:
        return to_object_id(value)
    except ValueError as exc:
        raise ValidationError(f"{field} is not a valid id.") from exc


async def _target(
    users: Any, roles: Any, user_id: str, grant: Grant, actor: dict[str, Any]
) -> dict[str, Any]:
    """Load a user you are allowed to administer.

    You may act only on someone whose permissions are a subset of your own.
    Without this, a role carrying `users.manage` but not `roles.manage` could
    reset the Owner's password and take the deployment -- the horizontal
    escalation that an otherwise correct permission grid does not close.
    """
    target = await users.get(_object_id(user_id, "user_id"))
    if target is None:
        raise NotFoundError("User not found.")
    if target["_id"] == actor["_id"]:
        return target

    role = await roles.get_for_user(target)
    held = frozenset(str(p) for p in (role or {}).get("permissions", ()))
    if not held <= grant.permissions:
        raise AuthorizationError("You cannot manage someone with more access than you.")
    return target


async def _role_you_may_grant(roles: Any, role_id: str, grant: Grant) -> dict[str, Any]:
    role = await roles.get(_object_id(role_id, "role_id"))
    if role is None:
        raise ValidationError("That role does not exist.")
    held = frozenset(str(p) for p in role.get("permissions", ()))
    if not held <= grant.permissions:
        raise AuthorizationError("You cannot grant access you do not have yourself.")
    return role


async def _refuse_removing_the_last_owner(users: Any, roles: Any, target: dict[str, Any]) -> None:
    """There must always be one active Owner left to restore everything else.

    Counted at the moment of the change rather than tracked as a flag: a flag
    would need updating from four places and would be wrong the first time one
    was missed.
    """
    owner = await roles.get_by_slug(OWNER_SLUG)
    if owner is None or target.get("role_id") != owner["_id"]:
        return
    if not target.get("is_active", False):
        return
    if await users.count_active_with_role(owner["_id"]) <= 1:
        raise ConflictError("This is the last active owner. Promote someone else first.")


@router.get("", dependencies=[Depends(require("users.view"))])
async def list_users(user: CurrentUser, users: UsersRepo, roles: RolesRepo) -> list[UserPublic]:
    """Built with `UserPublic.from_document`, never `serialise()`.

    `serialise()` copies every key, which would put `password_hash` and
    `session_epoch` straight on the wire.
    """
    found = await users.list({}, sort_field="email", descending=False, limit=200)
    by_id = {role["_id"]: role for role in await roles.list_all()}
    return [UserPublic.from_document(row, by_id.get(row.get("role_id"))) for row in found]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require("users.manage"))],
)
async def create_user(
    payload: CreateUserRequest,
    request: Request,
    user: CurrentUser,
    grant: CurrentGrant,
    users: UsersRepo,
    roles: RolesRepo,
    audit: AuditRepo,
) -> UserPublic:
    role = await _role_you_may_grant(roles, payload.role_id, grant)
    try:
        created = await users.create(
            payload.email, hash_password(payload.password), role_id=role["_id"]
        )
    except DuplicateKeyError as exc:
        raise ConflictError("A user with that email already exists.") from exc

    await audit.record(
        user_id=user["_id"],
        action="user.created",
        entity_type="user",
        entity_id=created["_id"],
        # The email and role, never the password or its hash.
        changes={"email": created["email"], "role": role["name"]},
        ip_address=request.client.host if request.client else None,
    )
    return UserPublic.from_document(created, role)


@router.patch("/{user_id}", dependencies=[Depends(require("users.manage"))])
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    request: Request,
    user: CurrentUser,
    grant: CurrentGrant,
    users: UsersRepo,
    roles: RolesRepo,
    audit: AuditRepo,
) -> UserPublic:
    target = await _target(users, roles, user_id, grant, user)
    is_self = target["_id"] == user["_id"]
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise ValidationError("No fields to update.")

    if "role_id" in changes:
        if is_self:
            # Self-lockout: an owner who demotes themselves may have nobody left
            # who can put them back.
            raise ValidationError("You cannot change your own role.")
        new_role = await _role_you_may_grant(roles, changes["role_id"], grant)
        if new_role["_id"] != target.get("role_id"):
            await _refuse_removing_the_last_owner(users, roles, target)
        changes["role_id"] = new_role["_id"]

    if "is_active" in changes and not changes["is_active"]:
        if is_self:
            raise ValidationError("You cannot deactivate your own account.")
        await _refuse_removing_the_last_owner(users, roles, target)

    updated = await users.update(target["_id"], changes)
    if updated is None:  # pragma: no cover - loaded a moment ago
        raise NotFoundError("User not found.")

    if "is_active" in changes and not changes["is_active"]:
        # `is_active` already blocks the next request, but revoking the cookies
        # means an open tab stops working immediately rather than looking fine.
        await users.bump_session_epoch(target["_id"])

    if "role_id" in changes:
        before = await roles.get_for_user(target)
        after = await roles.get(changes["role_id"])
        await audit.record(
            user_id=user["_id"],
            action="user.role_changed",
            entity_type="user",
            entity_id=target["_id"],
            # Names rather than ids, so the log stays readable after a role is deleted.
            changes={
                "from": (before or {}).get("name", "none"),
                "to": (after or {}).get("name", "none"),
            },
            ip_address=request.client.host if request.client else None,
        )
    if "is_active" in changes:
        await audit.record(
            user_id=user["_id"],
            action="user.activated" if changes["is_active"] else "user.deactivated",
            entity_type="user",
            entity_id=target["_id"],
            ip_address=request.client.host if request.client else None,
        )

    return UserPublic.from_document(updated, await roles.get_for_user(updated))


@router.post(
    "/{user_id}/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("users.manage"))],
)
async def reset_password(
    user_id: str,
    payload: SetPasswordRequest,
    request: Request,
    user: CurrentUser,
    grant: CurrentGrant,
    users: UsersRepo,
    roles: RolesRepo,
    audit: AuditRepo,
) -> None:
    target = await _target(users, roles, user_id, grant, user)
    await users.set_password(target["_id"], hash_password(payload.password))
    # Every session the old password opened is now invalid -- which is the point
    # of an administrator resetting it.
    await users.bump_session_epoch(target["_id"])
    await audit.record(
        user_id=user["_id"],
        action="user.password_reset",
        entity_type="user",
        entity_id=target["_id"],
        ip_address=request.client.host if request.client else None,
    )
    logger.info("user_password_reset", extra={"user_id": str(target["_id"])})


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require("users.manage"))],
)
async def delete_user(
    user_id: str,
    request: Request,
    user: CurrentUser,
    grant: CurrentGrant,
    users: UsersRepo,
    roles: RolesRepo,
    audit: AuditRepo,
) -> None:
    target = await _target(users, roles, user_id, grant, user)
    if target["_id"] == user["_id"]:
        raise ValidationError("You cannot delete your own account.")
    await _refuse_removing_the_last_owner(users, roles, target)

    await users.delete(target["_id"])
    await audit.record(
        user_id=user["_id"],
        action="user.deleted",
        entity_type="user",
        entity_id=target["_id"],
        changes={"email": target["email"]},
        ip_address=request.client.host if request.client else None,
    )
    logger.info("user_deleted", extra={"user_id": user_id})
