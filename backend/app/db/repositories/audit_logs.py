from typing import Any

from .base import BaseRepository, utcnow


class AuditLogsRepository(BaseRepository[Any]):
    collection_name = "audit_logs"

    async def record(
        self,
        *,
        user_id: Any,
        action: str,
        entity_type: str,
        entity_id: Any = None,
        changes: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        await self.collection.insert_one(
            {
                "user_id": user_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "changes": changes or {},
                "ip_address": ip_address,
                "user_agent": user_agent,
                "created_at": utcnow(),
            }
        )
