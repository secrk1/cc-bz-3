"""会话记录与指令审计查询；录屏回放文件下载。"""
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import AuditLog, SessionRecord, User
from app.schemas import AuditOut, SessionOut
from app.security import get_current_user

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    status: str | None = Query(default=None, pattern="^(active|closed)$"),
    protocol: str | None = Query(default=None, pattern="^(ssh|rdp|tcp)$"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[dict]:
    stmt = (
        select(SessionRecord)
        .options(selectinload(SessionRecord.asset),
                 selectinload(SessionRecord.user))
        .order_by(SessionRecord.started_at.desc())
        .limit(200)
    )
    if status:
        stmt = stmt.where(SessionRecord.status == status)
    if protocol:
        stmt = stmt.where(SessionRecord.protocol == protocol)
    records = list(await db.scalars(stmt))
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "user_name": r.user.username if r.user else f"#{r.user_id}",
            "asset_id": r.asset_id,
            "asset_name": r.asset.name if r.asset else f"#{r.asset_id}",
            "protocol": r.protocol,
            "status": r.status,
            "client_ip": r.client_ip,
            "recording_path": r.recording_path,
            "started_at": r.started_at,
            "ended_at": r.ended_at,
        }
        for r in records
    ]


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
    # 活跃会话的录像仍在写入（SSH 时间轴未封口 / guacd 未 flush 收尾），
    # 仅允许回放离线会话
    if record.status != "closed":
        raise HTTPException(status_code=409, detail="会话仍在线，结束后才可回放")

    path = record.recording_path
    if record.protocol == "ssh":
        # SSH: asciicast v2 文件（建会话时即带 .cast 后缀）
        if not os.path.isfile(path) or os.path.getsize(path) < 32:
            raise HTTPException(status_code=404, detail="录屏文件不存在或已被清理")
        return FileResponse(
            path, media_type="application/x-asciicast",
            filename=f"{session_id}.cast",
        )

    # RDP: guacd 原生录制文件（无扩展名）。个别 guacd 版本会额外落
    # .guac 后缀或在目标已存在时加时间戳，这里按候选路径依次探测。
    # 握手即失败时 guacd 可能留下过小的空文件，低于阈值视为无有效录像。
    candidates = [p for p in (path, path + ".guac")
                  if os.path.isfile(p) and os.path.getsize(p) >= 32]
    if not candidates:
        raise HTTPException(status_code=404, detail="录屏文件不存在或已被清理")
    return FileResponse(
        candidates[0], media_type="application/octet-stream",
        filename=f"{session_id}.guac",
    )
