"""WebSocket 接入：SSH 终端 / RDP 桌面（guacd）/ 原生 TCP 中继。

浏览器无法在 WebSocket 握手上设置 Authorization 头，因此通过 ?token=
查询参数传递 JWT。每个连接：
1. 校验 JWT -> 用户；
2. 加载资产并解密凭证（经 Redis 做短期凭证缓存）；
3. 创建会话记录（DB + Redis 活跃索引）；
4. 进入对应代理桥；
5. 结束时关闭会话、记录审计。
"""
import json
import logging
import os
import uuid

import jwt
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.config import settings
from app.crypto import decrypt_secret
from app.database import SessionLocal
from app.models import Asset, User
from app.proxy import guac, tcp_relay
from app.proxy.ssh_tunnel import run_ssh_session
from app.redis_client import credential_key, redis_client
from app.services.audit import close_session, open_session, write_audit

logger = logging.getLogger("ws")
router = APIRouter()


async def _authenticate(websocket: WebSocket, token: str | None) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret,
                             algorithms=[settings.jwt_algorithm])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
    async with SessionLocal() as db:
        return await db.get(User, user_id)


async def _send_guac_error(websocket: WebSocket, message: str,
                          code: int = 511) -> None:
    """下发 Guacamole error 指令后优雅关闭，让前端结束等待并展示原因。"""
    try:
        await websocket.send_text(guac.encode_error(message, code))
        await websocket.close(code=1000)
    except Exception:  # noqa: BLE001
        pass


async def _load_credential(asset: Asset) -> str | None:
    """解密凭证并写入 Redis 短期缓存；命中缓存则直接复用。"""
    key = credential_key(asset.id)
    cached = await redis_client.get(key)
    if cached is not None:
        return json.loads(cached).get("password")
    password = decrypt_secret(asset.secret_encrypted)
    await redis_client.set(key, json.dumps({"password": password}),
                           ex=settings.credential_ttl)
    return password


@router.websocket("/ws/ssh/{asset_id}")
async def ws_ssh(
    websocket: WebSocket,
    asset_id: int,
    token: str | None = Query(default=None),
    cols: int = Query(default=120, ge=10, le=500),
    rows: int = Query(default=30, ge=5, le=100),
) -> None:
    user = await _authenticate(websocket, token)
    if user is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()

    async with SessionLocal() as db:
        asset = await db.get(Asset, asset_id)
        if asset is None or asset.protocol != "ssh":
            await websocket.close(code=1008)
            return

        password = await _load_credential(asset)
        session_id = uuid.uuid4().hex
        os.makedirs(settings.recording_dir, exist_ok=True)
        recording_path = os.path.join(settings.recording_dir, f"{session_id}.cast")
        client_ip = websocket.client.host if websocket.client else None

        await open_session(db, session_id=session_id, user_id=user.id,
                           asset_id=asset.id, protocol="ssh", client_ip=client_ip,
                           recording_path=recording_path)
        await write_audit(db, session_id, "login", f"SSH 登录 {asset.host}")

        async def on_command(cmd: str) -> None:
            await write_audit(db, session_id, "command", cmd)

        async def on_close() -> None:
            await write_audit(db, session_id, "close", "会话结束")
            await close_session(db, session_id)

        try:
            await run_ssh_session(
                websocket, asset=asset, password=password, session_id=session_id,
                cols=cols, rows=rows, on_command=on_command, on_close=on_close,
                recording_path=recording_path,
            )
        except WebSocketDisconnect:
            pass


@router.websocket("/ws/rdp/{asset_id}")
async def ws_rdp(
    websocket: WebSocket,
    asset_id: int,
    token: str | None = Query(default=None),
    width: int = Query(default=guac.DEFAULT_WIDTH, ge=320, le=4096),
    height: int = Query(default=guac.DEFAULT_HEIGHT, ge=240, le=2160),
) -> None:
    """RDP 桌面：浏览器 Guacamole 协议 <-> guacd <-> 目标 3389。"""
    user = await _authenticate(websocket, token)
    if user is None:
        await websocket.close(code=1008)
        return

    # guacamole-common-js 的 WebSocketTunnel 以 "guacamole" 子协议建连；
    # 若服务端不在 101 握手里回显该子协议，浏览器会按 RFC 6455 立即断开。
    # 本端点仅服务于 Guacamole 客户端，子协议固定为 guacamole。
    await websocket.accept(subprotocol="guacamole")

    async with SessionLocal() as db:
        asset = await db.get(Asset, asset_id)
        if asset is None or asset.protocol != "rdp":
            await websocket.close(code=1008)
            return

        password = await _load_credential(asset)
        session_id = uuid.uuid4().hex
        client_ip = websocket.client.host if websocket.client else None
        await open_session(db, session_id=session_id, user_id=user.id,
                           asset_id=asset.id, protocol="rdp", client_ip=client_ip)
        await write_audit(db, session_id, "login",
                          f"RDP 连接 {asset.host}:{asset.port}")
        try:
            await guac.bridge(
                websocket, guacd_host=settings.guacd_host,
                guacd_port=settings.guacd_port, target_host=asset.host,
                target_port=asset.port, username=asset.username,
                password=password or "", width=width, height=height,
            )
        except guac.GuacError as exc:
            logger.warning("RDP 握手失败 [%s] -> %s", asset.name, exc)
            await _send_guac_error(websocket, str(exc), exc.code)
        except (ConnectionError, OSError, EOFError) as exc:
            logger.warning("RDP 会话 %s 网络异常: %s", session_id, exc)
            await _send_guac_error(websocket, f"网关/目标网络异常：{exc}")
        finally:
            await write_audit(db, session_id, "close", "RDP 会话结束")
            await close_session(db, session_id)


@router.websocket("/ws/tcp/{asset_id}")
async def ws_tcp(
    websocket: WebSocket,
    asset_id: int,
    token: str | None = Query(default=None),
) -> None:
    """协议无关的裸 TCP 中继（可承载任意 RDP 客户端数据，作扩展网关）。"""
    user = await _authenticate(websocket, token)
    if user is None:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    async with SessionLocal() as db:
        asset = await db.get(Asset, asset_id)
        if asset is None:
            await websocket.close(code=1008)
            return
        session_id = uuid.uuid4().hex
        await open_session(db, session_id=session_id, user_id=user.id,
                           asset_id=asset.id, protocol=asset.protocol,
                           client_ip=websocket.client.host if websocket.client
                           else None)
        try:
            await tcp_relay.bridge_binary(
                websocket, target_host=asset.host, target_port=asset.port)
        except (ConnectionError, OSError) as exc:
            logger.info("TCP 中继 %s 结束: %s", session_id, exc)
        finally:
            await close_session(db, session_id)
