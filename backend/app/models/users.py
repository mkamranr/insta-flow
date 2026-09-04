from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.services.security.passwords import MIN_PASSWORD_LENGTH


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RoleSummary(BaseModel):
    """A role as it appears on a user. Never carries the permission list.

    That omission is the point: `GET /api/users` returns one of these per user, and
    embedding permissions there would broadcast the full capability set of every
    account to anyone holding `users.view`.
    """

    id: str
    slug: str
    name: str

    @classmethod
    def from_document(cls, document: dict) -> "RoleSummary":
        return cls(
            id=str(document["_id"]), slug=document["slug"], name=document["name"]
        )


class UserPublic(BaseModel):
    """Everything the frontend is allowed to know about a user.

    Deliberately excludes `password_hash` and `session_epoch` (§76). Build these
    with `from_document` rather than `serialise()`, which copies every key and
    would put the password hash on the wire.
    """

    id: str
    email: str
    is_active: bool
    role: RoleSummary | None = None
    created_at: datetime | None = None
    last_login_at: datetime | None = None

    @classmethod
    def from_document(cls, document: dict, role: dict | None = None) -> "UserPublic":
        return cls(
            id=str(document["_id"]),
            email=document["email"],
            is_active=bool(document.get("is_active", False)),
            role=RoleSummary.from_document(role) if role else None,
            created_at=document.get("created_at"),
            last_login_at=document.get("last_login_at"),
        )


class MePublic(UserPublic):
    """Your own identity, which additionally carries your effective permissions.

    Separate from `UserPublic` on purpose -- see `RoleSummary`. You may learn your
    own capabilities; you may not learn everyone else's from a user list.
    """

    permissions: list[str] = Field(default_factory=list)

    @classmethod
    def for_self(
        cls, document: dict, role: dict | None, permissions: list[str]
    ) -> "MePublic":
        base = UserPublic.from_document(document, role)
        return cls(**base.model_dump(), permissions=sorted(permissions))


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    role_id: str


class UpdateUserRequest(BaseModel):
    """Every field optional: a PATCH that only flips `is_active` must not have to
    resend the role."""

    role_id: str | None = None
    is_active: bool | None = None


class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class ChangeOwnPasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
