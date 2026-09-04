from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.permissions import ALL


class RoleWrite(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=200)
    permissions: list[str] = Field(default_factory=list)

    @field_validator("permissions")
    @classmethod
    def known_permissions_only(cls, values: list[str]) -> list[str]:
        """Rejected here rather than in the handler, so a bad string never reaches
        the database. A stored permission outside the catalogue would be invisible
        in the grid and impossible to remove through the UI."""
        unknown = sorted(set(values) - ALL)
        if unknown:
            raise ValueError(f"unknown permissions: {', '.join(unknown)}")
        return sorted(set(values))


class RoleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    permissions: list[str] | None = None

    @field_validator("permissions")
    @classmethod
    def known_permissions_only(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        unknown = sorted(set(values) - ALL)
        if unknown:
            raise ValueError(f"unknown permissions: {', '.join(unknown)}")
        return sorted(set(values))


class RolePublic(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    permissions: list[str]
    is_system: bool
    user_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_document(cls, document: dict, user_count: int = 0) -> "RolePublic":
        return cls(
            id=str(document["_id"]),
            slug=document["slug"],
            name=document["name"],
            description=document.get("description", ""),
            permissions=sorted(str(p) for p in document.get("permissions", ())),
            is_system=bool(document.get("is_system", False)),
            user_count=user_count,
            created_at=document.get("created_at"),
            updated_at=document.get("updated_at"),
        )
