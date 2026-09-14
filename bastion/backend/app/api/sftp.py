"""Web-SFTP：基于 asyncssh 的远程目录浏览与文本在线编辑。

每个请求独立建立 SSH + SFTP 连接（无状态、用完即关），凭证复用与终端相同的
Redis 缓存 + Fernet 解密。仅支持 SSH 协议资产。

接口：
- GET  /api/sftp/{asset_id}/list?path=     列目录（path 空表示登录主目录）
- GET  /api/sftp/{asset_id}/content?path=  读取文本文件内容（在线编辑）
- POST /api/sftp/{asset_id}/content        保存文本文件

文件上传/下载（分片、进度、MD5 审计、并发限制）见 api/transfers.py。
"""
from __future__ import annotations

import asyncio
import logging
import posixpath
import stat

import asyncssh
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Asset, User
from app.security import get_current_user
from app.services.credentials import get_asset_password

logger = logging.getLogger("sftp")
router = APIRouter(prefix="/api/sftp", tags=["sftp"])

CONNECT_TIMEOUT = 10
OP_TIMEOUT = 20
# 在线编辑允许的最大文件大小（1 MiB），超出请走下载
MAX_EDIT_BYTES = 1024 * 1024
CHUNK = 64 * 1024


async def _open_sftp(asset: Asset) -> tuple[asyncssh.SSHClientConnection,
                                             asyncssh.SFTPClient]:
    password = await get_asset_password(asset)
    kwargs: dict = {
        "host": asset.host,
        "port": asset.port,
        "username": asset.username,
        "known_hosts": None,
        "connect_timeout": CONNECT_TIMEOUT,
    }
    if password:
        kwargs["password"] = password
    try:
        conn = await asyncio.wait_for(asyncssh.connect(**kwargs),
                                      CONNECT_TIMEOUT)
    except (asyncssh.Error, OSError) as exc:
        raise HTTPException(status_code=502,
                            detail=f"SSH 连接失败：{exc}") from exc
    try:
        sftp = await conn.start_sftp_client()
    except (asyncssh.Error, OSError) as exc:
        conn.close()
        raise HTTPException(status_code=502,
                            detail=f"SFTP 子系统不可用：{exc}") from exc
    return conn, sftp


def _http_error(exc: Exception) -> HTTPException:
    """把 asyncssh SFTP/SSH 异常映射为合适的 HTTP 错误。"""
    if isinstance(exc, asyncssh.sftp.SFTPNoSuchFile):
        return HTTPException(status_code=404, detail="远程路径不存在")
    if isinstance(exc, asyncssh.sftp.SFTPPermissionDenied):
        return HTTPException(status_code=403, detail="远程权限不足")
    if isinstance(exc, asyncssh.sftp.SFTPFailure):
        return HTTPException(status_code=400, detail=f"远程操作失败：{exc}")
    if isinstance(exc, (asyncssh.Error, OSError)):
        return HTTPException(status_code=502, detail=f"远程错误：{exc}")
    return HTTPException(status_code=500, detail=str(exc))


def _entry_payload(name: str, attrs) -> dict:
    mode = attrs.permissions or 0
    return {
        "name": name,
        "is_dir": stat.S_ISDIR(mode),
        "is_link": stat.S_ISLNK(mode),
        "size": attrs.size or 0,
        "mtime": attrs.mtime or 0,
        "permissions": mode,
    }


async def _get_asset(db: AsyncSession, asset_id: int) -> Asset:
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    if asset.protocol != "ssh":
        raise HTTPException(status_code=400,
                            detail="Web-SFTP 仅支持 SSH 协议资产")
    return asset


@router.get("/{asset_id}/list")
async def list_dir(
    asset_id: int,
    path: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    asset = await _get_asset(db, asset_id)
    conn, sftp = await _open_sftp(asset)
    try:
        target = path or "."
        try:
            entries = await asyncio.wait_for(sftp.readdir(target), OP_TIMEOUT)
        except (asyncssh.Error, OSError) as exc:
            raise _http_error(exc)

        items = [_entry_payload(e.filename, e.attrs) for e in entries]
        # 目录优先、名称次之，均不区分大小写
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

        # 规范化当前路径，供前端面包屑使用
        try:
            real = await sftp.realpath(target)
        except (asyncssh.Error, OSError):
            real = target
        parent = posixpath.dirname(real.rstrip("/")) or None
        return {"path": real, "parent": parent, "entries": items}
    finally:
        conn.close()


@router.get("/{asset_id}/content")
async def get_content(
    asset_id: int,
    path: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    asset = await _get_asset(db, asset_id)
    conn, sftp = await _open_sftp(asset)
    try:
        try:
            attrs = await asyncio.wait_for(sftp.stat(path), OP_TIMEOUT)
        except (asyncssh.Error, OSError) as exc:
            raise _http_error(exc)
        if stat.S_ISDIR(attrs.permissions or 0):
            raise HTTPException(status_code=400, detail="目标是目录，无法编辑")
        if (attrs.size or 0) > MAX_EDIT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"文件超过 {MAX_EDIT_BYTES // 1024} KiB，请下载后本地编辑")
        data = bytearray()
        try:
            async with sftp.open(path, "rb") as rf:
                while len(data) < MAX_EDIT_BYTES:
                    chunk = await rf.read(CHUNK)
                    if not chunk:
                        break
                    data.extend(chunk)
        except (asyncssh.Error, OSError) as exc:
            raise _http_error(exc)
        try:
            text = bytes(data).decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=415,
                                detail="二进制文件不支持在线文本编辑")
        return {"path": path, "content": text, "size": len(data)}
    finally:
        conn.close()


@router.post("/{asset_id}/content")
async def save_content(
    asset_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    asset = await _get_asset(db, asset_id)
    path = payload.get("path")
    content = payload.get("content")
    if not path or not isinstance(content, str):
        raise HTTPException(status_code=422, detail="需要 path 与 content 字段")
    data = content.encode("utf-8")
    if len(data) > MAX_EDIT_BYTES:
        raise HTTPException(status_code=413, detail="内容超过在线编辑大小上限")
    conn, sftp = await _open_sftp(asset)
    try:
        try:
            async with sftp.open(path, "wb") as wf:
                await wf.write(data)
        except (asyncssh.Error, OSError) as exc:
            raise _http_error(exc)
        logger.info("用户 %s 经 Web-SFTP 保存 %s:%s",
                    user.username, asset.name, path)
        return {"ok": True, "path": path, "bytes": len(data)}
    finally:
        conn.close()
