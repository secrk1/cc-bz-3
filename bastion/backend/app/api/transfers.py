"""分片流式上传 / 下载 + 文件传输审计。

上传协议（前端按 CHUNK_SIZE 切片，顺序发送，支持断点续传）：
1. POST /api/sftp/{asset_id}/transfers/init   申请传输（占用并发槽，建审计记录与临时文件）
2. GET  /api/sftp/{asset_id}/transfers/{tid}  查询已收字节（断线恢复偏移）
3. PUT  /api/sftp/{asset_id}/transfers/{tid}/chunk
       请求体为原始分片字节，头 X-Chunk-Start 声明起始偏移；服务端校验连续性
4. POST /api/sftp/{asset_id}/transfers/{tid}/complete
       单遍读取临时分片：边算 MD5 边写入远端 SFTP，校验大小后完成
   或 POST .../{tid}/abort 中止并清理

下载同样占用并发槽，流式计算 MD5 落审计。单用户同时进行的传输最多 2 个。
全局传输记录：GET /api/transfers（管理员看全部，普通用户看自己）。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import posixpath
import stat
import uuid
from urllib.parse import quote

import asyncssh
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sftp import _get_asset, _http_error, _open_sftp
from app.config import settings
from app.database import SessionLocal, get_db
from app.models import User
from app.security import get_current_user, require_admin
from app.services import transfers as audit
from app.services.transfer_slots import acquire, refresh, release

logger = logging.getLogger("transfers")

router = APIRouter(prefix="/api/sftp", tags=["sftp-transfers"])
audit_router = APIRouter(prefix="/api/transfers", tags=["sftp-transfers"],
                         dependencies=[Depends(require_admin)])

CHUNK_SIZE = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = 5 * 1024 * 1024 * 1024
CHUNK_READ = 256 * 1024
OP_TIMEOUT = 20


def _tmp_path(transfer_id: str) -> str:
    return os.path.join(settings.sftp_tmp_dir, f"{transfer_id}.part")


def _ensure_tmp_dir() -> None:
    os.makedirs(settings.sftp_tmp_dir, exist_ok=True)
    try:
        os.chmod(settings.sftp_tmp_dir, 0o1777)
    except OSError:
        pass


async def _owned_transfer(db: AsyncSession, tid: str,
                          user: User):
    rec = await audit.get_transfer(db, tid)
    if rec is None or rec.user_id != user.id:
        raise HTTPException(status_code=404, detail="传输任务不存在")
    return rec


@audit_router.get("")
async def list_my_transfers(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return await audit.list_transfers(db, user)


@router.post("/{asset_id}/transfers/init")
async def init_transfer(
    asset_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    asset = await _get_asset(db, asset_id)
    filename = posixpath.basename((payload.get("filename") or "").strip())
    if not filename:
        raise HTTPException(status_code=422, detail="文件名不合法")
    size = int(payload.get("size") or 0)
    if size < 0 or size > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
                            detail="文件为空或超过 5 GiB 上传上限")
    target_dir = (payload.get("path") or ".").strip()
    remote_path = posixpath.join(target_dir, filename)

    # 单用户并发槽：最多 2 个同时进行的传输
    if not await acquire(user.id):
        raise HTTPException(
            status_code=429,
            detail="并发传输超限：同一用户最多同时进行 2 个文件传输")

    tid = uuid.uuid4().hex
    try:
        await audit.create_transfer(
            db, transfer_id=tid, user_id=user.id, asset_id=asset.id,
            direction="upload", filename=filename, remote_path=remote_path,
            size=size)
        _ensure_tmp_dir()
        # 预建临时文件（存在则截断，新传输从头开始）
        open(_tmp_path(tid), "wb").close()
    except Exception:
        await release(user.id)
        raise
    logger.info("用户 %s 初始化上传 %s -> %s:%s（%d 字节，传输 %s）",
                user.username, filename, asset.name, remote_path, size, tid)
    return {"transfer_id": tid, "chunk_size": CHUNK_SIZE,
            "received": 0, "remote_path": remote_path}


@router.get("/{asset_id}/transfers/{tid}")
async def transfer_status(
    asset_id: int,
    tid: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    rec = await _owned_transfer(db, tid, user)
    path = _tmp_path(tid)
    received = os.path.getsize(path) if os.path.isfile(path) else rec.bytes_done
    return {"transfer_id": tid, "status": rec.status,
            "received": received, "size": rec.size, "md5": rec.md5}


@router.put("/{asset_id}/transfers/{tid}/chunk")
async def put_chunk(
    request: Request,
    asset_id: int,
    tid: str,
    x_chunk_start: int = Header(default=0, alias="X-Chunk-Start"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    await _get_asset(db, asset_id)
    rec = await _owned_transfer(db, tid, user)
    if rec.status != "uploading":
        raise HTTPException(status_code=409, detail=f"传输已{rec.status}")
    path = _tmp_path(tid)
    if not os.path.isfile(path):
        raise HTTPException(status_code=409, detail="临时分片已失效，请重新开始")

    received = os.path.getsize(path)
    # 必须连续追加；客户端断线重传时按返回的 received 定位，实现续传
    if x_chunk_start != received:
        raise HTTPException(
            status_code=409, detail="分片偏移不连续",
            headers={"X-Received": str(received)})

    body = await request.body()
    if not body:
        raise HTTPException(status_code=422, detail="空分片")
    if received + len(body) > rec.size:
        raise HTTPException(status_code=413, detail="累计分片超过声明大小")

    with open(path, "ab") as fp:
        fp.write(body)
    received += len(body)
    rec.bytes_done = received
    await db.commit()
    await refresh(user.id)
    return {"received": received}


@router.post("/{asset_id}/transfers/{tid}/complete")
async def complete_transfer(
    asset_id: int,
    tid: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    asset = await _get_asset(db, asset_id)
    rec = await _owned_transfer(db, tid, user)
    path = _tmp_path(tid)
    try:
        received = os.path.getsize(path) if os.path.isfile(path) else 0
        if received != rec.size:
            raise HTTPException(
                status_code=409,
                detail=f"分片不完整：已收 {received}/{rec.size}")

        conn, sftp = await _open_sftp(asset)
        md5 = hashlib.md5()
        try:
            # 单遍：读本地临时分片 -> 算 MD5 -> 写远端。
            # 读盘放入线程，避免 5GB 级文件的同步 read 阻塞事件循环。
            async with sftp.open(rec.remote_path, "wb") as wf:
                with open(path, "rb") as fp:
                    while True:
                        chunk = await asyncio.to_thread(fp.read, CHUNK_READ)
                        if not chunk:
                            break
                        md5.update(chunk)
                        await wf.write(chunk)
                        await refresh(user.id)
        except (asyncssh.Error, OSError) as exc:
            raise _http_error(exc)
        finally:
            conn.close()

        digest = md5.hexdigest()
        await audit.mark_success(db, rec, digest, received)
        logger.info("用户 %s 上传完成 %s:%s md5=%s（%d 字节）",
                    user.username, asset.name, rec.remote_path,
                    digest, received)
        return {"ok": True, "transfer_id": tid, "md5": digest,
                "size": received}
    except HTTPException as exc:
        await audit.mark_failed(db, rec, exc.detail if isinstance(exc.detail, str)
                                else "完成失败")
        raise
    except Exception as exc:  # noqa: BLE001
        await audit.mark_failed(db, rec, str(exc))
        raise HTTPException(status_code=500, detail=f"完成失败：{exc}")
    finally:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
        await release(user.id)


@router.post("/{asset_id}/transfers/{tid}/abort")
async def abort_transfer(
    asset_id: int,
    tid: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    await _get_asset(db, asset_id)
    rec = await _owned_transfer(db, tid, user)
    path = _tmp_path(tid)
    await audit.mark_aborted(db, rec)
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
    await release(user.id)
    return {"ok": True}


@router.get("/{asset_id}/download-audit")
async def download_with_audit(
    asset_id: int,
    path: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StreamingResponse:
    """流式下载并审计（占用并发槽，落 MD5 与传输状态）。"""
    asset = await _get_asset(db, asset_id)
    if not await acquire(user.id):
        raise HTTPException(
            status_code=429,
            detail="并发传输超限：同一用户最多同时进行 2 个文件传输")

    conn, sftp = await _open_sftp(asset)
    try:
        try:
            attrs = await asyncio.wait_for(sftp.stat(path), OP_TIMEOUT)
        except (asyncssh.Error, OSError) as exc:
            conn.close()
            await release(user.id)
            raise _http_error(exc)
        if stat.S_ISDIR(attrs.permissions or 0):
            conn.close()
            await release(user.id)
            raise HTTPException(status_code=400, detail="不能下载目录")
    except HTTPException:
        raise

    tid = uuid.uuid4().hex
    filename = posixpath.basename(path.rstrip("/")) or "download"
    total = attrs.size or 0
    rec = await audit.create_transfer(
        db, transfer_id=tid, user_id=user.id, asset_id=asset.id,
        direction="download", filename=filename, remote_path=path,
        size=total)

    remote_file = None
    md5 = hashlib.md5()
    done = 0

    async def stream():
        nonlocal remote_file, done
        try:
            remote_file = await sftp.open(path, "rb")
            while True:
                if await request.is_disconnected():
                    break
                chunk = await remote_file.read(CHUNK_READ)
                if not chunk:
                    break
                md5.update(chunk)
                done += len(chunk)
                yield chunk
        except (asyncssh.Error, OSError) as exc:
            logger.warning("下载中断 %s: %s", path, exc)
        finally:
            if remote_file is not None:
                try:
                    await remote_file.close()
                except (asyncssh.Error, OSError):
                    pass
            conn.close()
            # 用独立 DB 会话落最终状态（请求级会话不应在响应生成器里使用）
            async with SessionLocal() as sdb:
                fresh = await audit.get_transfer(sdb, tid)
                if fresh is not None:
                    if total == 0 or done == total:
                        await audit.mark_success(sdb, fresh, md5.hexdigest(), done)
                    else:
                        await audit.mark_failed(
                            sdb, fresh, f"下载中断：{done}/{total}")
            await release(user.id)

    disposition = (f"attachment; filename=\"{filename.encode('ascii', 'replace').decode()}\"; "
                   f"filename*=UTF-8''{quote(filename)}")
    return StreamingResponse(
        stream(), media_type="application/octet-stream",
        headers={"Content-Disposition": disposition})
