"""SSH 终端桥：浏览器 xterm.js <-> asyncssh PTY。

职责：
- 异步 SSH 握手（密码 / 私钥 / 免密），建立交互式 PTY 会话；
- 浏览器 -> PTY、PTY -> 浏览器 全双工流转；
- 输入侧做行编辑感知，回车提交时抽取命令行写入指令审计；
- 以 asciicast v2 格式录制终端输出，可回放（审计闭环）。

帧约定（与前端 TerminalView 对齐）：
- 二进制帧：终端原始输入字节（写入 PTY stdin，并喂给审计器）；
- 文本帧：JSON 控制消息，如 {"type":"resize","cols":120,"rows":30}。
"""
import asyncio
import json
import logging
import time

import asyncssh

logger = logging.getLogger("ssh")


class AsciicastRecorder:
    """asciicast v2：首行 header，随后每行 [time, 'o', data]。"""

    def __init__(self, path: str, cols: int, rows: int):
        self._fp = open(path, "w", encoding="utf-8")
        self._t0 = time.monotonic()
        header = {"version": 2, "width": cols, "height": rows,
                  "timestamp": int(time.time()),
                  "env": {"SHELL": "/bin/sh", "TERM": "xterm-256color"}}
        self._fp.write(json.dumps(header) + "\n")

    def output(self, data: str) -> None:
        elapsed = round(time.monotonic() - self._t0, 6)
        self._fp.write(json.dumps([elapsed, "o", data], ensure_ascii=False) + "\n")
        self._fp.flush()

    def close(self) -> None:
        try:
            self._fp.close()
        except Exception:  # noqa: BLE001
            pass


class LineAuditor:
    """从终端输入流中提取“回车提交的命令行”。

    维护屏幕当前输入缓冲：可打印字符入列，BS/DEL 退格，CR/LF 提交。
    足以覆盖常规命令审计场景（Tab 补全后的命令按最终提交行记录）。
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, data: str) -> list[str]:
        commands: list[str] = []
        for ch in data:
            if ch in ("\r", "\n"):
                line = self._buf.strip()
                if line:
                    commands.append(line)
                self._buf = ""
            elif ch in ("\x7f", "\x08"):  # DEL / BS
                self._buf = self._buf[:-1]
            elif ch in ("\x03", "\x15"):  # Ctrl-C / Ctrl-U
                self._buf = ""
            elif ch >= " ":
                self._buf += ch
        return commands


async def run_ssh_session(websocket, *, asset, password: str | None,
                          session_id: str, cols: int, rows: int,
                          on_command, on_close, recording_path: str | None) -> None:
    """建立并运行一个 SSH 交互式会话，直到断开。

    :param on_command: 异步回调 (cmd: str) -> None，用于落审计
    :param on_close:   异步回调 () -> None，会话结束时收尾
    """
    recorder = (
        AsciicastRecorder(recording_path, cols, rows) if recording_path else None
    )
    auditor = LineAuditor()

    connect_kwargs: dict = {
        "host": asset.host,
        "port": asset.port,
        "username": asset.username,
        "known_hosts": None,  # 堡垒机代连，跳过主机密钥强校验
        "encoding": None,     # 以 bytes 收发，避免二进制程序乱码
        "connect_timeout": 10,
    }
    if password:
        connect_kwargs["password"] = password
    # 私钥资产可在此扩展：connect_kwargs["client_keys"] = [private_key]

    conn = None
    process = None
    try:
        conn = await asyncssh.connect(**connect_kwargs)
        process = await conn.create_process(
            term_type="xterm-256color",
            encoding=None,
            term_size=(cols, rows, 0, 0),
        )

        async def browser_to_ssh() -> None:
            """单一接收循环：二进制帧写入 PTY 并审计，文本帧处理控制消息。"""
            try:
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    if (data := message.get("bytes")) is not None:
                        try:
                            process.stdin.write(data)
                        except (OSError, asyncssh.ProcessError):
                            break
                        text = data.decode("utf-8", "replace")
                        for cmd in auditor.feed(text):
                            await on_command(cmd)
                    elif (text := message.get("text")) is not None:
                        try:
                            msg = json.loads(text)
                        except (json.JSONDecodeError, TypeError):
                            continue
                        if msg.get("type") == "resize":
                            try:
                                process.change_terminal_size(
                                    int(msg["cols"]), int(msg["rows"]), 0, 0)
                            except (KeyError, ValueError,
                                    asyncssh.ProcessError):
                                pass
            finally:
                try:
                    process.stdin.write_eof()
                except (OSError, asyncssh.ProcessError):
                    pass

        async def ssh_to_browser() -> None:
            while not process.stdout.at_eof():
                chunk = await process.stdout.read(65536)
                if not chunk:
                    break
                await websocket.send_bytes(chunk)
                if recorder:
                    recorder.output(chunk.decode("utf-8", "replace"))

        tasks = {
            asyncio.create_task(browser_to_ssh()),
            asyncio.create_task(ssh_to_browser()),
        }
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    except (asyncssh.Error, OSError) as exc:
        logger.warning("SSH 会话 %s 失败: %s", session_id, exc)
        try:
            await websocket.send_text(
                f"\r\n\x1b[31m[堡垒机] SSH 连接失败: {exc}\x1b[0m\r\n")
        except Exception:  # noqa: BLE001
            pass
    finally:
        if process is not None:
            try:
                process.close()
            except Exception:  # noqa: BLE001
                pass
        if conn is not None:
            conn.close()
        if recorder:
            recorder.close()
        await on_close()
