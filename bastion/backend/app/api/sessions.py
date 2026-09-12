"""会话记录与指令审计查询；录屏回放文件下载。"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AuditLog, SessionRecord, User
from app.schemas import AuditOut, SessionOut
from app.security import get_current_user

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[SessionRecord]:
    return list(await db.scalars(
        select(SessionRecord).order_by(SessionRecord.started_at.desc()).limit(200)
    ))


@router.get("/{session_id}/audit", response_model=list[AuditOut])
async def session_audit(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[AuditLog]:
    return list(await db.scalars(
        select(AuditLog)
        .where(AuditLog.session_id == session_id)
        .order_by(AuditLog.id)
    ))


@router.get("/{session_id}/recording")
async def download_recording(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> FileResponse:
    record = await db.get(SessionRecord, session_id)
    if record is None or not record.recording_path:
        raise HTTPException(status_code=404, detail="该会话无录屏文件")
    return FileResponse(
        record.recording_path, media_type="application/x-asciicast",
        filename=f"{session_id}.cast",
    )
