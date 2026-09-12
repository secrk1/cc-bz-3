"""Guacamole 协议桥：浏览器 guacamole-common-js <-> guacd。

采用“服务端隧道”模式（与官方 guacamole webapp 的 WebSocketTunnel 等价）：

1. WebSocket 首帧向浏览器发送内部指令 ``0.,<len>.<uuid>;``，隧道据此进入 OPEN；
2. 代理浏览器与 guacd 完成经典握手：
   ``select`` -> ``args`` -> ``size/audio/video/image/connect`` -> ``ready``；
3. ``ready`` 之后双向透传全部指令（key/mouse/img/blob/sync 等）。

保活：浏览器隧道在 OPEN 后每 500ms 发送一条**内部指令**
（空操作码 ``0.,4.ping,<毫秒时间戳>;``），其 receiveTimeout 为 15s，每收到
一帧才重置。官方 webapp 端点会把 ping **原样回显**；本桥在握手开始前就启动
入站泵送任务，命中空操作码即回显、其余转发 guacd，且该任务贯穿整个会话——
否则在耗时登录或 RDP 空闲无画面时，浏览器会在收到 ready/下一帧前就超时关闭。

注意：Guacamole 元素是“长度前缀 + 内容”。img/blob 中的 PNG 等二进制数据
在协议层由 guacd 做 **base64 编码**，因此整条链路都是合法 UTF-8 文本，
浏览器 WebSocketTunnel 也只接受**文本帧**（收到二进制帧会直接解析失败）。
本桥解析仍基于 bytes（长度按字节计），向浏览器发送前按 UTF-8 还原为文本帧。
"""
import asyncio
import logging
import uuid

logger = logging.getLogger("guac")

DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
DEFAULT_DPI = 96

# 握手（含 guacd 连目标 RDP 登录）最长等待时间；浏览器侧靠 ping 回显保活，
# 这里只对 guacd 无响应做兜底
HANDSHAKE_TIMEOUT = 30


class GuacError(RuntimeError):
    """需要展示给浏览器端用户的 Guacamole 错误。"""

    def __init__(self, message: str, code: int = 511):
        super().__init__(message)
        self.code = code


def encode_error(message: str, code: int = 511) -> bytes:
    """构造发送给浏览器的 error 指令：error,<message>,<code>;"""
    safe = message.encode("utf-8", "replace")[:400].decode("utf-8", "replace")
    return encode("error", [safe, str(code)])


def encode(opcode: str, args: list[str] | None = None) -> bytes:
    """编码一条 Guacamole 指令（仅用于本桥发起的握手指令，均为文本）。"""
    parts = [f"{len(opcode.encode())}.{opcode}"]
    for value in args or []:
        raw = value.encode("utf-8")
        parts.append(f"{len(raw)}.{value}")
    return (",".join(parts) + ";").encode()


class GuacReader:
    """按长度前缀解析指令，元素保留原始 bytes。"""

    def __init__(self, reader: asyncio.StreamReader):
        self._reader = reader
        self._buf = b""

    async def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = await self._reader.read(65536)
            if not chunk:
                raise EOFError("guacd 连接中断")
            self._buf += chunk
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    async def _read_element(self) -> bytes:
        length = 0
        while True:
            digit = await self._read_exact(1)
            if digit == b".":
                break
            length = length * 10 + int(digit)
        return await self._read_exact(length)

    async def read_instruction(self) -> tuple[str, list[bytes]]:
        opcode = (await self._read_element()).decode("ascii")
        args: list[bytes] = []
        # opcode 与首个参数之间、参数彼此之间均以 ',' 分隔，指令以 ';' 结束
        delimiter = await self._read_exact(1)
        while delimiter == b",":
            args.append(await self._read_element())
            delimiter = await self._read_exact(1)
        if delimiter != b";":
            raise ValueError(f"非法指令分隔符: {delimiter!r}")
        return opcode, args


def _reencode(opcode: str, args: list[bytes]) -> bytes:
    parts = [f"{len(opcode.encode())}.{opcode}".encode()]
    for raw in args:
        parts.append(str(len(raw)).encode() + b"." + raw)
    return b",".join(parts) + b";"


def parse_instructions(frame: bytes) -> list[tuple[bytes, list[bytes], bytes]]:
    """把一个可能含多条指令的帧解析为 (opcode, args, 原始整段字节) 列表。

    全程按字节解析（长度即字节数），多字节 UTF-8 元素也安全。
    """
    results: list[tuple[bytes, list[bytes], bytes]] = []
    pos = 0
    n = len(frame)

    def read_element(p: int):
        dot = frame.find(b".", p)
        if dot == -1:
            return None
        try:
            length = int(frame[p:dot])
        except ValueError:
            return None
        end = dot + 1 + length
        return frame[dot + 1:end], end

    while pos < n:
        start = pos
        head = read_element(pos)
        if head is None:
            break
        opcode, pos = head
        args: list[bytes] = []
        while pos < n and frame[pos:pos + 1] == b",":
            pos += 1  # 跳过逗号
            elem = read_element(pos)
            if elem is None:
                break
            value, pos = elem
            args.append(value)
        if pos < n and frame[pos:pos + 1] == b";":
            pos += 1  # 消费分号
        results.append((opcode, args, frame[start:pos]))
    return results


def _build_connect_args(arg_names: list[str], *, target_host: str,
                        target_port: int, username: str, password: str,
                        width: int, height: int) -> list[str]:
    values = {
        "hostname": target_host,
        "port": str(target_port),
        "username": username,
        "password": password,
        "domain": "",
        # 留空 security：由 freerdp 与目标自动协商 NLA/TLS/RDP，
        # 切勿传 "any"（非合法值，会被 guacd 拒绝）。
        "security": "",
        "ignore-cert": "true",
        "disable-auth": "false",
        "width": str(width),
        "height": str(height),
        "dpi": str(DEFAULT_DPI),
        "resize-method": "display-update",
        "enable-font-smoothing": "true",
    }
    return [values.get(name, "") for name in arg_names]


async def _handshake(guac: "GuacReader", writer: asyncio.StreamWriter, *,
                     target_host: str, target_port: int, username: str,
                     password: str, width: int, height: int) -> list[str]:
    """完成到 ready 为止的 guacd 握手，成功返回 guacd 声明的参数名列表。"""
    writer.write(encode("select", ["rdp"]))
    await writer.drain()

    opcode, arg_names_b = await guac.read_instruction()
    if opcode != "args":
        raise GuacError(f"guacd 异常响应：{opcode}")
    arg_names = [b.decode() for b in arg_names_b]
    logger.info("guacd 返回 %d 个连接参数", len(arg_names))

    ordered = _build_connect_args(
        arg_names, target_host=target_host, target_port=target_port,
        username=username, password=password, width=width, height=height)
    logger.info("connect 参数: %s",
                {n: ("***" if n == "password" and v else v)
                 for n, v in zip(arg_names, ordered)})

    writer.write(encode("size", [str(width), str(height)]))
    writer.write(encode("audio", []))
    writer.write(encode("video", []))
    writer.write(encode("image", ["image/png", "image/jpeg"]))
    writer.write(encode("connect", ordered))
    await writer.drain()

    while True:
        sub_opcode, sub_args = await guac.read_instruction()
        if sub_opcode == "ready":
            logger.info("guacd 已就绪：%s:%s 登录成功", target_host, target_port)
            return arg_names
        if sub_opcode == "error":
            reason = (sub_args[0].decode("utf-8", "replace")
                      if sub_args else "未知错误")
            code = 511
            if len(sub_args) > 1 and sub_args[1].isdigit():
                code = int(sub_args[1])
            raise GuacError(f"目标 RDP 拒绝连接：{reason}", code)
        logger.info("ready 前指令：%s", sub_opcode)


async def probe_rdp(*, guacd_host: str, guacd_port: int, target_host: str,
                    target_port: int, username: str, password: str,
                    width: int = DEFAULT_WIDTH,
                    height: int = DEFAULT_HEIGHT) -> None:
    """连通性探测：走完整握手到 ready 后立即断开。失败抛 GuacError。"""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(guacd_host, guacd_port), timeout=5)
    try:
        guac = GuacReader(reader)
        await asyncio.wait_for(
            _handshake(guac, writer, target_host=target_host,
                       target_port=target_port, username=username,
                       password=password, width=width, height=height),
            timeout=HANDSHAKE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise GuacError(
            f"RDP 握手超时（{HANDSHAKE_TIMEOUT}s）：目标 {target_host}:"
            f"{target_port} 可达但未完成登录，请检查账号密码或桌面服务")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass


async def bridge(websocket, *, guacd_host: str, guacd_port: int,
                 target_host: str, target_port: int, username: str,
                 password: str, width: int = DEFAULT_WIDTH,
                 height: int = DEFAULT_HEIGHT) -> None:
    """建立到 guacd 的隧道并与给定 websocket 双向桥接，直到任一端关闭。"""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(guacd_host, guacd_port), timeout=5
    )
    logger.info("RDP 隧道开始：guacd=%s:%s -> 目标=%s:%s@%s",
                guacd_host, guacd_port, username, target_port, target_host)

    try:
        guac = GuacReader(reader)

        # 1) 首帧：内部指令携带隧道 UUID
        tunnel_uuid = str(uuid.uuid4())
        await websocket.send_text(f"0.,{len(tunnel_uuid)}.{tunnel_uuid};")

        # 2) 浏览器 -> guacd 泵送必须在握手期间就运行：
        #    浏览器每 500ms 发内部 ping，官方端点会原样回显，浏览器据此重置
        #    15s receiveTimeout。RDP 登录握手可能持续十几秒，若不回显，浏览器
        #    会在我们收到 ready 之前就因超时而关闭。
        async def ws_to_guacd() -> None:
            async for message in websocket.iter_text():
                for opcode, args, raw in parse_instructions(message.encode("utf-8")):
                    if opcode == b"":
                        # 内部指令不转发 guacd；仅 ping 原样回显（与官方端点一致）
                        if args and args[0] == b"ping":
                            await websocket.send_text(raw.decode("utf-8"))
                    else:
                        writer.write(raw)
                        await writer.drain()

        pump_task = asyncio.create_task(ws_to_guacd())

        # 3) 握手到 ready（此期间浏览器的 ping 由上面的泵送任务回显）
        try:
            await asyncio.wait_for(
                _handshake(guac, writer, target_host=target_host,
                           target_port=target_port, username=username,
                           password=password, width=width, height=height),
                timeout=HANDSHAKE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise GuacError(
                f"RDP 握手超时（{HANDSHAKE_TIMEOUT}s）：目标 {target_host}:"
                f"{target_port} 可达但未完成登录，请检查账号密码或目标桌面服务")

        # 4) ready 后启动 guacd -> 浏览器 透传，与入站泵一同运行
        async def guacd_to_ws() -> None:
            while True:
                op, args = await guac.read_instruction()
                # 协议为 UTF-8 文本（二进制经 base64），必须以文本帧下发
                await websocket.send_text(_reencode(op, args).decode("utf-8"))

        forward_task = asyncio.create_task(guacd_to_ws())
        await asyncio.wait({pump_task, forward_task},
                           return_when=asyncio.FIRST_COMPLETED)
        for task in (pump_task, forward_task):
            task.cancel()
        await asyncio.gather(pump_task, forward_task, return_exceptions=True)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass
