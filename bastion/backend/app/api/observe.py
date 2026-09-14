"""管理员实时旁观 WebSocket：/ws/observe/{session_id}。

数据流（只读镜像，绝不向资产侧写入任何东西）：

1. 校验 JWT 且必须是管理员；
2. 先订阅直播频道，再读 Redis 回看缓冲，按单调序号去重，把缓冲帧
   （RDP 含首帧内部 UUID/ready/绘图，可从头重建桌面；SSH 为近期回显）
   按序发给观察者；
3. 持续转发订阅到的实时帧；
4. 监听控制频道：会话被强踢/正常结束时通知观察者并关闭。

帧编码见 services/live.py（9 字节头：方向 + uint64 序号）。
RDP 侧观察者就是一个普通 Guacamole 客户端，浏览器重发的 connect/select
被忽略、空操作码 ping 原样回显以保活——与会话桥的约定一致。
"""
import asyncio
import logging
import uuid

import jwt
from fastapi import APIRouter, Query, WebSocket

from app.config import settings
from app.database import SessionLocal
from app.models import SessionRecord, User
from app.proxy import guac
from app.redis_client import live_channel
from app.redis_client import redis_client_bytes
from app.services.live import control_events, decode_frame, fetch_buffer

logger = logging.getLogger("observe")
router = APIRouter()


async def _auth_admin(websocket: WebSocket, token: str | None) -> User | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret,
                             algorithms=[settings.jwt_algorithm])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
    async with SessionLocal() as db:
        user = await db.get(User, user_id)
        if user is not None and user.is_admin:
            return user
    return None


async def _send_history_and_live(websocket: WebSocket, session_id: str,
                                 protocol: str) -> None:
    """回放缓冲后无缝续播实时帧。"""
    pubsub = redis_client_bytes.pubsub()
    await pubsub.subscribe(live_channel(session_id))

    async def emit(payload: bytes) -> None:
        if protocol == "rdp":
            await websocket.send_text(payload.decode("utf-8", "replace"))
        else:
            await websocket.send_bytes(payload)

    try:
        # 先订阅再取缓冲：其间产生的帧会进入订阅队列，靠序号去重，
        # 既不丢帧也不重复
        history = await fetch_buffer(session_id)
        max_seq = -1
        for raw in history:
            seq, is_output, payload = decode_frame(raw)
            max_seq = max(max_seq, seq)
            if is_output:
                await emit(payload)

        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            seq, is_output, payload = decode_frame(message["data"])
            if seq <= max_seq or not is_output:
                continue
            max_seq = seq
            await emit(payload)
    finally:
        await pubsub.unsubscribe(live_channel(session_id))
        await pubsub.aclose()


async def _watch_session_control(websocket: WebSocket, session_id: str,
                                 protocol: str) -> None:
    """会话被强踢或结束时通知观察者。返回触发的命令。"""
    async for command in control_events(session_id):
        if command in ("kick", "end"):
            notice = ("会话已被管理员强制结束" if command == "kick"
                      else "会话已结束")
            try:
                if protocol == "rdp":
                    await websocket.send_text(guac.encode_error(notice, 521))
                else:
                    await websocket.send_text(
                        f"\r\n\x1b[33m[堡垒机旁观] {notice}\x1b[0m\r\n")
            except Exception:  # noqa: BLE001
                pass
            return


async def _poll_session_status(session_id: str) -> None:
    """兜底：Pub/Sub 的 end 不可持久，订阅晚于结束发布会错过。
    周期查库，会话不再 active 即返回，驱动旁观 WS 关闭。"""
    while True:
        await asyncio.sleep(3)
        async with SessionLocal() as db:
            record = await db.get(SessionRecord, session_id)
        if record is None or record.status != "active":
            return


async def _rdp_keepalive(websocket: WebSocket) -> None:
    """回显观察者浏览器的 nop/ping 保活帧，忽略其余上行（只读）。"""
    async for frame in websocket.iter_text():
        for opcode, args in guac.parse_client_tunnel(frame):
            if not opcode:
                await websocket.send_text(guac._wrap(opcode, args))
            # connect/select/key/mouse 等全部忽略：旁观不可交互


@router.websocket("/ws/observe/{session_id}")
async def ws_observe(
    websocket: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
) -> None:
    admin = await _auth_admin(websocket, token)
    if admin is None:
        await websocket.close(code=1008)
        return

    async with SessionLocal() as db:
        record = await db.get(SessionRecord, session_id)
    if record is None or record.protocol not in ("ssh", "rdp"):
        await websocket.close(code=1008)
        return
    protocol = record.protocol

    if protocol == "rdp":
        await websocket.accept(subprotocol="guacamole")
        # 主桥的隧道 UUID 首帧与 ready 都在握手阶段直发、不经过 relay，
        # 这里为观察者补齐，否则客户端无法 setUUID/进入 CONNECTED 渲染
        tunnel_uuid = str(uuid.uuid4())
        await websocket.send_text(guac._wrap("", [tunnel_uuid]))
        await websocket.send_text(guac._wrap("ready", [tunnel_uuid]))
    else:
        await websocket.accept()
        try:
            await websocket.send_text(
                "\x1b[36m[堡垒机旁观模式] 只读镜像，以下为该会话实时画面\x1b[0m\r\n")
        except Exception:  # noqa: BLE001
            pass

    logger.info("管理员 %s 开始旁观会话 %s (%s)",
                admin.username, session_id, protocol)

    tasks = {
        asyncio.create_task(
            _send_history_and_live(websocket, session_id, protocol)),
        asyncio.create_task(
            _watch_session_control(websocket, session_id, protocol)),
        asyncio.create_task(_poll_session_status(session_id)),
    }
    if protocol == "rdp":
        tasks.add(asyncio.create_task(_rdp_keepalive(websocket)))

    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await websocket.close(code=1000)
        except Exception:  # noqa: BLE001
            pass
