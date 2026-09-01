from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.services.security.passwords import MIN_PASSWORD_LENGTH


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserPublic(BaseModel):
    """Everything the frontend is allowed to know about a user.

    Deliberately excludes `password_hash` and `session_epoch` (§76).
    """

    id: str
    email: str
    is_active: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None

    @classmethod
    def from_document(cls, document: dict) -> "UserPublic":
        return cls(
            id=str(document["_id"]),
            email=document["email"],
            is_active=bool(document.get("is_active", False)),
            created_at=document.get("created_at"),
            last_login_at=document.get("last_login_at"),
        )


class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
