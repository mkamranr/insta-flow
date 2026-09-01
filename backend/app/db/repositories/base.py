"""Shared repository behaviour.

Repositories own every read and write against MongoDB. API routes and workers
must depend on a repository, never on a collection handle — that boundary is
what lets the campaign engine and worker be tested without a live database.
"""

from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from app.errors import NotFoundError

T = TypeVar("T")

Document = dict[str, Any]


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_object_id(value: str | ObjectId) -> ObjectId:
    """Parse an id from the wire. Raises ValueError rather than leaking bson errors.

    None and empty strings are rejected explicitly: `ObjectId(None)` quietly mints
    a brand-new id, which would turn a missing id into a query that matches
    nothing instead of an error.
    """
    if isinstance(value, ObjectId):
        return value
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid id: {value!r}")
    try:
        return ObjectId(value)
    except (InvalidId, TypeError) as exc:
        raise ValueError(f"invalid id: {value!r}") from exc


def serialise(document: Document | None) -> Document | None:
    """Convert a raw Mongo document into something JSON-encodable.

    ObjectIds become strings and `_id` becomes `id`, so the frontend never has to
    know about bson.
    """
    if document is None:
        return None
    out: Document = {}
    for key, value in document.items():
        key = "id" if key == "_id" else key
        if isinstance(value, ObjectId):
            out[key] = str(value)
        elif isinstance(value, list):
            out[key] = [str(v) if isinstance(v, ObjectId) else v for v in value]
        elif isinstance(value, dict):
            out[key] = serialise(value)
        else:
            out[key] = value
    return out


def serialise_required(document: Document | None) -> Document:
    """Serialise a document that must exist.

    Raises rather than returning JSON `null`, which is what a route would
    otherwise emit if a document vanished between write and read-back.
    """
    if document is None:
        raise NotFoundError("Resource not found.")
    result = serialise(document)
    assert result is not None
    return result


class BaseRepository(Generic[T]):
    collection_name: str

    def __init__(self, database: AsyncDatabase[Document]) -> None:
        self.db = database

    @property
    def collection(self) -> AsyncCollection[Document]:
        return self.db[self.collection_name]

    async def insert(self, document: Document) -> ObjectId:
        now = utcnow()
        document.setdefault("created_at", now)
        document.setdefault("updated_at", now)
        result = await self.collection.insert_one(document)
        return result.inserted_id

    async def get(self, doc_id: str | ObjectId) -> Document | None:
        return await self.collection.find_one({"_id": to_object_id(doc_id)})

    async def find_one(self, query: Document) -> Document | None:
        return await self.collection.find_one(query)

    async def update(self, doc_id: str | ObjectId, changes: Document) -> Document | None:
        changes = {**changes, "updated_at": utcnow()}
        return await self.collection.find_one_and_update(
            {"_id": to_object_id(doc_id)},
            {"$set": changes},
            return_document=True,
        )

    async def delete(self, doc_id: str | ObjectId) -> bool:
        result = await self.collection.delete_one({"_id": to_object_id(doc_id)})
        return result.deleted_count == 1

    async def count(self, query: Document | None = None) -> int:
        return await self.collection.count_documents(query or {})

    async def list(
        self,
        query: Document | None = None,
        *,
        sort_field: str = "created_at",
        descending: bool = True,
        limit: int = 50,
        skip: int = 0,
    ) -> list[Document]:
        limit = max(1, min(limit, 200))
        cursor = (
            self.collection.find(query or {})
            .sort(sort_field, DESCENDING if descending else ASCENDING)
            .skip(max(0, skip))
            .limit(limit)
        )
        return [doc async for doc in cursor]

    async def paginate(
        self,
        query: Document | None = None,
        *,
        sort_field: str = "created_at",
        descending: bool = True,
        limit: int = 50,
        skip: int = 0,
    ) -> dict[str, Any]:
        """Offset pagination with a total, shaped for the dashboard tables."""
        items = await self.list(
            query, sort_field=sort_field, descending=descending, limit=limit, skip=skip
        )
        return {
            "items": [serialise(item) for item in items],
            "total": await self.count(query),
            "limit": limit,
            "skip": skip,
        }
