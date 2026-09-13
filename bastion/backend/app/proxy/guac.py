"""Guacamole 协议桥：浏览器 guacamole-common-js <-> guacd。

采用“服务端隧道”模式：后端依据资产凭据自行驱动到 guacd 的握手，
浏览器只负责渲染画面与回传键鼠输入。

guacamole-common-js@1.5.0 的 WebSocketTunnel 行为（已从 npm 源码逐字确认）：
- 浏览器把 connect 数据（token/分辨率）放在 WebSocket **查询串** 中，
  不会在 socket 上发送任何 “4…” 包装帧；
- socket 上收到什么就按长度前缀直接喂给 ``Guacamole.Parser``，
  **不存在 '0'/'1' 隧道包装层**；
- ``Parser`` 按 **Unicode 码点（字符）** 计元素长度并逐字符截取（非 UTF-8 字节），
  故服务端下行必须按字符计数，多字节（剪贴板/文件名等）才不会被截歪；
- 客户端收到的**首条指令**若为空操作码（``""``）且恰有 1 个参数，则被视为隧道
  UUID（``INTERNAL_DATA_OPCODE``），据此 ``setUUID`` 并进入 ``OPEN``。

因此本桥的协议约定如下：
1. 首帧下发空操作码 + 单参数 tunnel UUID：``0.,<len>.<uuid>;``；
2. 之后把 guacd 的 Guacamole 协议流（select/connect/size/ready/img/key…）
   按**字符计数**原样转发浏览器；
3. 浏览器上行的是**字符计数**的真实 Guacamole 指令，按字符解析后，再用**字节计数**
   重新编码转发给 guacd（guacd 按字节计长）。浏览器重发的 connect/select 已被本桥
   在握手阶段自行发往 guacd，忽略；空操作码（ping/nop 保活）原样回显浏览器。

注意：Guacamole 元素是“长度前缀 + 内容”。img/blob 中的 PNG 等二进制数据在协议层由
guacd 做 base64 编码，因此整条链路都是合法 UTF-8 文本，浏览器 WebSocketTunnel 也只
接受文本帧。
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


def encode(opcode: str, args: list[str] | None = None) -> bytes:
    """编码一条 Guacamole 指令（**字节计数**），用于：
    - 本桥发起的握手指令（均为文本）；
    - 把浏览器上行（字符计数）的指令重新编码后转发给 guacd。
    guacd（C 实现）按 UTF-8 字节计长度，故此处长度取 ``encode('utf-8')`` 的字节数。
    """
    parts = [f"{len(opcode.encode())}.{opcode}"]
    for value in args or []:
        raw = value.encode("utf-8")
        parts.append(f"{len(raw)}.{value}")
    return (",".join(parts) + ";").encode()


def _wrap(opcode: str, args: list[str] | None = None) -> str:
    """编码一条发给浏览器的 Guacamole 指令（**字符/码点计数**）。

    guacamole-common-js 的 ``Parser`` 按 Unicode 码点计长度并逐字符截取，
    故下行必须按字符计数，剪贴板/文件名等多字节元素才不会被截歪。
    """
    parts = [f"{len(opcode)}.{opcode}"]
    for value in args or []:
        parts.append(f"{len(value)}.{value}")
    return ",".join(parts) + ";"


def encode_error(message: str, code: int = 511) -> str:
    """构造发给浏览器的 error 指令帧（普通 Guacamole 指令，client.onerror 处理）。"""
    safe = message.encode("utf-8", "replace")[:400].decode("utf-8", "replace")
    return _wrap("error", [safe, str(code)])


class GuacReader:
    """按长度前缀（字节计数）解析 guacd 下行指令，元素保留原始 bytes。"""

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


def parse_client_tunnel(frame: str) -> list[tuple[str, list[str]]]:
    """按 Guacamole 指令（**字符计数**）解析浏览器上行文本帧。

    guacamole-common-js 用字符计数，故此处按 ``str`` 长度（码点）解析，
    返回 ``(opcode, args)`` 列表；空 opcode 表示隧道内部指令（ping/nop 等）。
    """
    results: list[tuple[str, list[str]]] = []
    i = 0
    n = len(frame)

    def read_element(start: int):
        dot = frame.find(".", start)
        if dot == -1:
            return None
        try:
            length = int(frame[start:dot])
        except ValueError:
            return None
        end = dot + 1 + length
        if end > n:
            return None
        return frame[dot + 1:end], end

    while i < n:
        head = read_element(i)
        if head is None:
            break
        opcode, i = head
        args: list[str] = []
        while i < n and frame[i] == ",":
            i += 1  # 跳过逗号
            elem = read_element(i)
            if elem is None:
                break
            value, i = elem
            args.append(value)
        if i < n and frame[i] == ";":
            i += 1  # 消费分号
        results.append((opcode, args))
    return results


def _build_connect_args(arg_names: list[str], *, target_host: str,
                        target_port: int, username: str, password: str,
                        width: int, height: int,
                        recording_path: str | None = None,
                        recording_name: str | None = None) -> list[str]:
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
    if recording_path:
        # guacd 原生图形会话录制：把整段 Guacamole 协议指令流（含 sync
        # 时间戳）写入 recording-path/recording-name，会话结束后封口，
        # 供 guacamole-common-js SessionRecording 回放。
        values.update({
            "recording-path": recording_path,
            "recording-name": recording_name or "recording",
            # 目录不存在时由 guacd 自建；已存在则无副作用
            "create-recording-path": "true",
            # 同时记录键盘事件，使回放含操作轨迹
            "recording-include-keys": "true",
        })
    return [values.get(name, "") for name in arg_names]


async def _handshake(guac: "GuacReader", writer: asyncio.StreamWriter,
                     websocket=None, *, target_host: str, target_port: int,
                     username: str, password: str, width: int,
                     height: int, recording_path: str | None = None,
                     recording_name: str | None = None) -> list[str]:
    """完成到 ready 为止的 guacd 握手，成功返回 guacd 声明的参数名列表。

    guacd 下行的 ready/error 等为普通 Guacamole 指令，按**字符计数**转发浏览器
    （``_wrap``）；websocket 为 None（如连通性探测）时跳过转发。
    """
    writer.write(encode("select", ["rdp"]))
    await writer.drain()

    opcode, arg_names_b = await guac.read_instruction()
    if opcode != "args":
        raise GuacError(f"guacd 异常响应：{opcode}")
    arg_names = [b.decode() for b in arg_names_b]
    logger.info("guacd 返回 %d 个连接参数", len(arg_names))

    ordered = _build_connect_args(
        arg_names, target_host=target_host, target_port=target_port,
        username=username, password=password, width=width, height=height,
        recording_path=recording_path, recording_name=recording_name)
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
        decoded = [a.decode("utf-8", "replace") for a in sub_args]
        if sub_opcode == "ready":
            if websocket is not None:
                await websocket.send_text(_wrap("ready", decoded))
            logger.info("guacd 已就绪：%s:%s 登录成功", target_host, target_port)
            return arg_names
        if sub_opcode == "error":
            if websocket is not None:
                await websocket.send_text(_wrap("error", decoded))
            reason = decoded[0] if decoded else "未知错误"
            code = 511
            if len(decoded) > 1 and decoded[1].isdigit():
                code = int(decoded[1])
            raise GuacError(f"目标 RDP 拒绝连接：{reason}", code)
        logger.info("ready 前指令：%s", sub_opcode)
        if websocket is not None:
            await websocket.send_text(_wrap(sub_opcode, decoded))


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
                 height: int = DEFAULT_HEIGHT,
                 recording_path: str | None = None,
                 recording_name: str | None = None) -> None:
    """建立到 guacd 的隧道并与给定 websocket 双向桥接，直到任一端关闭。

    recording_path/name 非空时开启 guacd 原生图形录制（写指令流文件）。
    """
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(guacd_host, guacd_port), timeout=5
    )
    logger.info("RDP 隧道开始：guacd=%s:%s -> 目标=%s:%s@%s",
                guacd_host, guacd_port, username, target_port, target_host)

    try:
        guac = GuacReader(reader)

        # 1) 首帧：空操作码 + 单参数 tunnel UUID（guacamole-common-js 据此 setUUID + OPEN）
        tunnel_uuid = str(uuid.uuid4())
        await websocket.send_text(_wrap("", [tunnel_uuid]))

        # 2) 浏览器 -> guacd 泵送（贯穿整个会话）：
        #    - 空 opcode（ping/nop 等内部指令）：原样回显给浏览器以重置 15s
        #      receiveTimeout 保活；
        #    - connect/select：浏览器重发，本桥已在握手阶段发给 guacd，忽略；
        #    - 其余真实 opcode（key/mouse/...）：按字节计数重新编码后转发 guacd。
        async def ws_to_guacd() -> None:
            async for message in websocket.iter_text():
                if not message:
                    continue
                for opcode, args in parse_client_tunnel(message):
                    if not opcode:
                        # 浏览器内部指令（ping/nop）：原样回显以保活
                        await websocket.send_text(_wrap(opcode, args))
                    elif opcode in ("connect", "select"):
                        continue  # 本桥已驱动握手，忽略浏览器重发的
                    else:
                        writer.write(encode(opcode, args))
                        await writer.drain()

        pump_task = asyncio.create_task(ws_to_guacd())

        # 3) 握手到 ready（此期间浏览器的 ping 由上面的泵送任务回显保活）
        try:
            await asyncio.wait_for(
                _handshake(guac, writer, websocket, target_host=target_host,
                           target_port=target_port, username=username,
                           password=password, width=width, height=height,
                           recording_path=recording_path,
                           recording_name=recording_name),
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
                await websocket.send_text(
                    _wrap(op, [a.decode("utf-8", "replace") for a in args]))

        forward_task = asyncio.create_task(guacd_to_ws())
        await asyncio.wait({pump_task, forward_task},
                           return_when=asyncio.FIRST_COMPLETED)
        for task in (pump_task, forward_task):
            task.cancel()
        await asyncio.gather(pump_task, forward_task, return_exceptions=True)
    except Exception as e:
        # 把错误以 error 指令形式透传给浏览器，避免静默断开
        try:
            if isinstance(e, GuacError):
                await websocket.send_text(encode_error(str(e), e.code))
            else:
                await websocket.send_text(encode_error(f"隧道异常：{e}", 511))
        except Exception:
            pass
        raise
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass
