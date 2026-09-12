"""会话与审计的持久化辅助。"""
import json
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, SessionRecord
from app.redis_client import redis_client, session_key


async def open_session(db: AsyncSession, *, session_id: str, user_id: int,
                       asset_id: int, protocol: str, client_ip: str | None,
                       recording_path: str | None = None) -> SessionRecord:
    record = SessionRecord(
        id=session_id, user_id=user_id, asset_id=asset_id, protocol=protocol,
        status="active", client_ip=client_ip, recording_path=recording_path,
    )
    db.add(record)
    await db.commit()

    # Redis：活跃会话索引（TTL 12h 兜底，关闭时主动删除）
    await redis_client.set(session_key(session_id), json.dumps({
        "id": session_id, "user_id": user_id, "asset_id": asset_id,
        "protocol": protocol, "started_at": datetime.now(timezone.utc).isoformat(),
    }), ex=60 * 60 * 12)
    return record


async def close_session(db: AsyncSession, session_id: str) -> None:
    record = await db.get(SessionRecord, session_id)
    if record and record.status == "active":
        record.status = "closed"
        record.ended_at = datetime.now(timezone.utc)
        await db.commit()
    await redis_client.delete(session_key(session_id))


async def write_audit(db: AsyncSession, session_id: str, event_type: str,
                      content: str | None = None) -> None:
    db.add(AuditLog(session_id=session_id, event_type=event_type, content=content))
    await db.commit()
