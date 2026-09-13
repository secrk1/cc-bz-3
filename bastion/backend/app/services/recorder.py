"""异步无阻塞 asciicast v2 录屏引擎。

设计目标：录制逻辑绝不拖慢终端转发。

- 转发循环对每个 I/O 数据块只做一次 ``queue.put_nowait`` —— 纯内存操作
  （向有界 deque 追加引用），绝不 ``await`` 磁盘；
- 独立 worker 协程从队列取事件、攒批，再用 ``asyncio.to_thread`` 在线程中
  执行阻塞 write/flush；写盘再慢也只积压在队列里，不影响
  PTY <-> WebSocket 主循环；
- 有界队列满时**丢输出事件并计数**（审计录像允许偶发丢帧，不允许阻塞用户
  终端）；输入事件体积小、更关键，满时退化为最多等待 50ms 入队；
- 会话结束时 ``aclose`` 给一个限定的排空窗口，正常情况下完整落盘。

asciicast v2 文件结构::

    {"version":2,"width":..,"height":..,"timestamp":..,"env":{...}}
    [elapsed_seconds, "o", "终端输出文本"]
    [elapsed_seconds, "i", "用户输入文本"]
    ...

时间偏移量在入队时用独立单调钟盖戳（而不是写盘时），保证时间轴真实，
且不受系统墙钟跳变影响。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

logger = logging.getLogger("recorder")


def ensure_recording_dir(path: str) -> None:
    """准备录像目录并放开为 1777。

    backend 容器默认以 root 运行，而 guacd 容器以 uid 1000 运行，两者经同一
    命名卷共享该目录：若目录是 root:root 0755，guacd 无法写入图形录像。
    设为粘滞位 1777（同 /tmp）允许双端读写、互不可删彼此文件。
    """
    try:
        os.makedirs(path, exist_ok=True)
        os.chmod(path, 0o1777)
    except OSError:
        logger.warning("录像目录权限设置失败（继续）: %s", path, exc_info=True)

# 单录像 pending 队列上限。按每个事件约 256B~4KB 估算，封顶约十几 MB 内存；
# 磁盘长期卡死时丢帧并告警，而不是无限吃内存或拖慢终端。
DEFAULT_QUEUE_MAX = 20_000

# worker 攒批窗口：终端空闲时最多多等这么久就把已收到的事件落盘，
# 保证“边录边看/意外断电”时文件也基本是新的。
BATCH_WINDOW = 0.04

# 输入帧在队列满时最多愿意等待的入队时间。
INPUT_ENQUEUE_TIMEOUT = 0.05

_SENTINEL = object()


class CastEvent:
    """一条待落盘的 asciicast 事件：``[offset, direction, payload]``。"""

    __slots__ = ("offset", "direction", "data")

    def __init__(self, offset: float, direction: str, data: str):
        self.offset = offset
        self.direction = direction  # "o" = 终端输出, "i" = 用户输入
        self.data = data

    def line(self) -> str:
        return json.dumps([round(self.offset, 6), self.direction, self.data],
                          ensure_ascii=False)


class AsyncCastRecorder:
    """asciicast v2 录像器：主循环同步入队，worker 异步批量写盘。"""

    def __init__(self, path: str, cols: int, rows: int, *,
                 queue_max: int = DEFAULT_QUEUE_MAX):
        self.path = path
        self.cols = cols
        self.rows = rows
        self._t0 = time.monotonic()
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=queue_max)
        self._dropped_o = 0
        self._dropped_i = 0
        self._closed = False
        self._worker: asyncio.Task | None = None

    # ------------------------------------------------------------------ #
    # 主循环热路径：必须是非 async 的 O(1) 内存操作
    # ------------------------------------------------------------------ #
    @property
    def elapsed(self) -> float:
        """自录像开始的单调时间偏移（秒）。"""
        return time.monotonic() - self._t0

    def record_output(self, text: str) -> None:
        """记录 PTY 下行数据（终端回显 / 命令响应）。队列满即丢帧。"""
        if self._closed or not text:
            return
        try:
            self._queue.put_nowait(CastEvent(self.elapsed, "o", text))
        except asyncio.QueueFull:
            self._dropped_o += 1
            if self._dropped_o == 1 or self._dropped_o % 1000 == 0:
                logger.warning("录像 %s 队列已满，累计丢弃输出帧 %d",
                               self.path, self._dropped_o)

    def record_input(self, text: str) -> None:
        """记录用户键入数据。队列满时退化为最多等待 50ms 的异步入队。"""
        if self._closed or not text:
            return
        event = CastEvent(self.elapsed, "i", text)
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped_i += 1
            asyncio.ensure_future(self._enqueue_input_with_wait(event))

    async def _enqueue_input_with_wait(self, event: CastEvent) -> None:
        try:
            await asyncio.wait_for(self._queue.put(event),
                                   timeout=INPUT_ENQUEUE_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("录像 %s 输入帧入队超时被丢弃", self.path)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    @classmethod
    async def open(cls, path: str, *, cols: int, rows: int,
                   queue_max: int = DEFAULT_QUEUE_MAX) -> "AsyncCastRecorder":
        """在线程中阻塞式建文件、写 header，然后启动独立写盘 worker。"""
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        rec = cls(path, cols, rows, queue_max=queue_max)
        header = json.dumps({
            "version": 2,
            "width": cols,
            "height": rows,
            "timestamp": int(time.time()),
            "env": {"SHELL": "/bin/sh", "TERM": "xterm-256color"},
        }, ensure_ascii=False) + "\n"

        def _create_with_header() -> None:
            with open(path, "w", encoding="utf-8") as fp:
                fp.write(header)
                fp.flush()

        await asyncio.to_thread(_create_with_header)
        rec._worker = asyncio.create_task(rec._run_writer())
        return rec

    async def _run_writer(self) -> None:
        """独立写盘协程：攒批取事件，在线程中 write + flush。"""
        fp = await asyncio.to_thread(open, self.path, "a", encoding="utf-8")
        try:
            while True:
                first = await self._queue.get()
                if first is _SENTINEL:
                    self._queue.task_done()
                    break
                events = [first]
                closing = False
                # 攒批：最多再等 BATCH_WINDOW，把就绪事件一次性写盘，
                # 减少线程调度与 write(2) 次数
                deadline = time.monotonic() + BATCH_WINDOW
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        ev = await asyncio.wait_for(self._queue.get(), remaining)
                    except asyncio.TimeoutError:
                        break
                    if ev is _SENTINEL:
                        self._queue.task_done()
                        closing = True
                        break
                    events.append(ev)

                payload = "\n".join(e.line() for e in events) + "\n"

                def _write_and_flush(text: str = payload) -> None:
                    fp.write(text)
                    fp.flush()

                await asyncio.to_thread(_write_and_flush)
                for _ in events:
                    self._queue.task_done()
                if closing:
                    break
        except asyncio.CancelledError:  # 排空超时被强制取消
            pass
        except Exception:  # noqa: BLE001
            logger.exception("录像写盘 worker 异常: %s", self.path)
        finally:
            await asyncio.to_thread(fp.close)
            if self._dropped_o or self._dropped_i:
                logger.warning("录像 %s 关闭，丢弃输出帧 %d / 输入帧 %d",
                               self.path, self._dropped_o, self._dropped_i)

    async def aclose(self, *, drain_timeout: float = 5.0) -> None:
        """停止接收新帧，等 worker 排空队列后关文件，最多等 drain_timeout。"""
        if self._closed:
            return
        self._closed = True
        if self._worker is None:
            return
        await self._queue.put(_SENTINEL)
        try:
            await asyncio.wait_for(self._worker, timeout=drain_timeout)
        except asyncio.TimeoutError:
            logger.warning("录像 %s 排空超时（%ss），强制结束",
                           self.path, drain_timeout)
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
