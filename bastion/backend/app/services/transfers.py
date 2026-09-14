"""文件传输审计记录（file_transfers）持久化辅助。"""
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import FileTransfer, User

STATUS_UPLOADING = "uploading"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_ABORTED = "aborted"


async def create_transfer(db: AsyncSession, *, transfer_id: str,
                          user_id: int, asset_id: int, direction: str,
                          filename: str, remote_path: str | None,
                          size: int) -> FileTransfer:
    rec = FileTransfer(
        transfer_id=transfer_id, user_id=user_id, asset_id=asset_id,
        direction=direction, filename=filename, remote_path=remote_path,
        size=size, bytes_done=0, status=STATUS_UPLOADING)
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    return rec


async def get_transfer(db: AsyncSession, transfer_id: str) -> FileTransfer | None:
    return await db.get(FileTransfer, transfer_id)


async def set_progress(db: AsyncSession, record: FileTransfer,
                       bytes_done: int) -> None:
    record.bytes_done = bytes_done
    await db.commit()


async def mark_success(db: AsyncSession, record: FileTransfer,
                       md5: str, bytes_done: int | None = None) -> None:
    record.status = STATUS_SUCCESS
    record.md5 = md5
    if bytes_done is not None:
        record.bytes_done = bytes_done
    record.finished_at = datetime.now(timezone.utc)
    await db.commit()


async def mark_failed(db: AsyncSession, record: FileTransfer,
                      error: str) -> None:
    if record.status in (STATUS_SUCCESS, STATUS_ABORTED):
        return
    record.status = STATUS_FAILED
    record.error = error[:1000]
    record.finished_at = datetime.now(timezone.utc)
    await db.commit()


async def mark_aborted(db: AsyncSession, record: FileTransfer) -> None:
    record.status = STATUS_ABORTED
    record.finished_at = datetime.now(timezone.utc)
    await db.commit()


async def list_transfers(db: AsyncSession, user: User,
                         limit: int = 200) -> list[dict]:
    stmt = (
        select(FileTransfer)
        .options(selectinload(FileTransfer.user),
                 selectinload(FileTransfer.asset))
        .order_by(FileTransfer.started_at.desc())
        .limit(limit)
    )
    rows = list(await db.scalars(stmt))
    return [
        {
            "id": r.id,
            "transfer_id": r.transfer_id,
            "user_id": r.user_id,
            "user_name": r.user.username if r.user else f"#{r.user_id}",
            "asset_id": r.asset_id,
            "asset_name": r.asset.name if r.asset else f"#{r.asset_id}",
            "direction": r.direction,
            "filename": r.filename,
            "remote_path": r.remote_path,
            "size": r.size,
            "bytes_done": r.bytes_done,
            "md5": r.md5,
            "status": r.status,
            "error": r.error,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }
        for r in rows
    ]
