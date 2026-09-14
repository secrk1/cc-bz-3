"""实时旁观直播中继：会话下行帧 -> 有界队列 -> Redis Pub/Sub 广播。

设计目标与录像器一致：**广播绝不拖慢会话转发**。

- SSH/RDP 下行热路径只调用一次 ``feed_output``：纯内存 ``put_nowait``，
  不 await Redis；
- 独立 worker 协程攒批（20ms 窗口），用**一条 pipeline** 完成
  ``RPUSH 回看缓冲 + LTRIM 限长 + EXPIRE + 逐条 PUBLISH``，把每批的
  Redis 往返压到 1 次；
- 队列满时丢帧计数，不反压终端/桌面。

旁观接入（见 api/observe.py）：
1. ``LRANGE livebuf:{id}`` 取近期缓冲帧——SSH 用于接入即见近期回显，
   RDP 用于从头重建完整桌面画面；
2. 再订阅 ``live:{id}`` 实时续播。帧头带单调序号，观察者据此去重，
   消除“读缓冲”与“订阅”之间的重叠窗口。

帧封装（9 字节头 + payload）::

    byte 0      : 方向（"o" 下行 / "i" 上行）
    byte 1..8   : uint64 大端单调序号
    byte 9..    : 原始负载（SSH 为终端字节，RDP 为下行 Guacamole 指令文本 UTF-8）

强踢/结束走独立文本频道 ``sessctrl:{id}``（命令如 kick / end）。
"""
from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import AsyncIterator

from app.redis_client import (
    control_channel,
    live_buffer_key,
    live_channel,
    redis_client_bytes,
)

logger = logging.getLogger("live")

# 单中继待广播队列上限：磁盘/Redis 卡死时丢帧而不拖慢会话
QUEUE_MAX = 20_000
# worker 攒批窗口
BATCH_WINDOW = 0.02
# 回看缓冲保留帧数（RDP 需足够从头重建画面，SSH 只需近期回显）
BUFFER_FRAMES_RDP = 20_000
BUFFER_FRAMES_SSH = 2_000
# 会话结束后缓冲仍保留多久供刚断开的旁观页取最后一帧
BUFFER_TTL_SECONDS = 15 * 60

_HEADER = struct.Struct(">BQ")
_DIR_OUT = ord("o")
_DIR_IN = ord("i")


def encode_frame(seq: int, output: bool, payload: bytes) -> bytes:
    return _HEADER.pack(_DIR_OUT if output else _DIR_IN, seq) + payload


def decode_frame(raw: bytes) -> tuple[int, bool, bytes]:
    direction, seq = _HEADER.unpack(raw[:_HEADER.size])
    return seq, direction == _DIR_OUT, raw[_HEADER.size:]


class LiveRelay:
    """单会话直播中继：热路径 feed，worker 批量写缓冲并发布。"""

    def __init__(self, session_id: str, protocol: str, *,
                 buffer_items: int | None = None):
        self.session_id = session_id
        self.protocol = protocol
        self.buffer_items = (buffer_items
                             or (BUFFER_FRAMES_RDP if protocol == "rdp"
                                 else BUFFER_FRAMES_SSH))
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._seq = 0
        self._dropped = 0
        self._closed = False
        self._worker: asyncio.Task | None = None

    def feed_output(self, data: bytes) -> None:
        """热路径：下行帧入队（不 await Redis）。"""
        if self._closed or not data:
            return
        self._seq += 1
        try:
            self._queue.put_nowait((self._seq, True, bytes(data)))
        except asyncio.QueueFull:
            self._dropped += 1
            if self._dropped == 1 or self._dropped % 2000 == 0:
                logger.warning("直播 %s 队列满，累计丢帧 %d",
                               self.session_id, self._dropped)

    async def _run_writer(self) -> None:
        key = live_buffer_key(self.session_id)
        chan = live_channel(self.session_id)
        while True:
            first = await self._queue.get()
            if first is None:
                break
            batch = [first]
            deadline = asyncio.get_running_loop().time() + BATCH_WINDOW
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), remaining)
                except asyncio.TimeoutError:
                    break
                if item is None:
                    break
                batch.append(item)

            frames = [encode_frame(seq, output, payload)
                      for seq, output, payload in batch]
            client = redis_client_bytes
            pipe = client.pipeline()
            pipe.rpush(key, *frames)
            pipe.ltrim(key, -self.buffer_items, -1)
            pipe.expire(key, BUFFER_TTL_SECONDS)
            for frame in frames:
                pipe.publish(chan, frame)
            try:
                await pipe.execute()
            except Exception:  # noqa: BLE001
                logger.exception("直播广播失败: %s", self.session_id)

    async def start(self) -> "LiveRelay":
        self._worker = asyncio.create_task(self._run_writer())
        return self

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._worker is None:
            return
        await self._queue.put(None)
        try:
            await asyncio.wait_for(self._worker, timeout=3)
        except asyncio.TimeoutError:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        # 缓冲保留一小段时间（上面 EXPIRE 已对在写批次设置），
        # 再补一次 TTL，防止最后无流量时永不过期
        try:
            await redis_client_bytes.expire(
                live_buffer_key(self.session_id), BUFFER_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            pass
        if self._dropped:
            logger.warning("直播 %s 结束，累计丢帧 %d",
                           self.session_id, self._dropped)


async def fetch_buffer(session_id: str) -> list[bytes]:
    """读取回看缓冲（原始封装帧，按写入顺序）。"""
    return await redis_client_bytes.lrange(
        live_buffer_key(session_id), 0, -1)


async def publish_control(session_id: str, command: str) -> None:
    """向会话控制频道发布命令（kick / end）。"""
    await redis_client_bytes.publish(
        control_channel(session_id), command.encode())


async def control_events(session_id: str) -> AsyncIterator[str]:
    """订阅会话控制频道，逐条 yield 文本命令（kick/end/...）。"""
    pubsub = redis_client_bytes.pubsub()
    await pubsub.subscribe(control_channel(session_id))
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            yield message["data"].decode("utf-8", "replace")
    finally:
        await pubsub.unsubscribe(control_channel(session_id))
        await pubsub.aclose()
