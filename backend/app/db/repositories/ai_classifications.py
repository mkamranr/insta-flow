from typing import Any

from .base import BaseRepository, Document, to_object_id


class AiClassificationsRepository(BaseRepository[Any]):
    collection_name = "ai_classifications"

    async def list_for_comment(self, comment_id: Any) -> list[Document]:
        return await self.list({"comment_id": to_object_id(comment_id)})
