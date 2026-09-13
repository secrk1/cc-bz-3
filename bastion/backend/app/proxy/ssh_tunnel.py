"""SSH 终端桥：浏览器 xterm.js <-> asyncssh PTY。

职责：
- 异步 SSH 握手（密码 / 私钥 / 免密），建立交互式 PTY 会话；
- 浏览器 -> PTY、PTY -> 浏览器 全双工流转；
- 输入侧做行编辑感知，回车提交时抽取命令行写入指令审计；
- 输出侧在命令响应静默后生成去 ANSI 的“响应摘要”回写审计；
- 双向 I/O 以 asciicast v2 事件喂给异步录像器（独立队列落盘，
  录制热路径只做内存入队，绝不阻塞转发）。

帧约定（与前端 TerminalView 对齐）：
- 二进制帧：终端原始输入字节（写入 PTY stdin，并喂给审计/录像器）；
- 文本帧：JSON 控制消息，如 {"type":"resize","cols":120,"rows":30}。
"""
import asyncio
import json
import logging
import re

import asyncssh

from app.services.recorder import AsyncCastRecorder

logger = logging.getLogger("ssh")

# 命令输出静默多久后认为“响应已结束”，据此截取摘要
RESPONSE_QUIET_SECONDS = 0.3
# 响应摘要最多保留的字符数
SUMMARY_MAX_CHARS = 200

# ANSI 转义序列：OSC / CSI / 字符集指定 / 两字符简单转义
_ANSI_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_CHARSET = re.compile(r"\x1b[()][0-9A-Za-z]")
_ANSI_SIMPLE = re.compile(r"\x1b[@-Z\\-_]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# 末尾新 shell 提示符行（如 user@host:~# 、root@x /root #）
_PROMPT_LINE = re.compile(r"^.*[@:].*[#$]\s*$")


def summarize_output(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    """把原始终端输出清洗为简短的响应摘要：去转义、去首尾提示符、截断。"""
    text = _ANSI_OSC.sub("", text)
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_CHARSET.sub("", text)
    text = _ANSI_SIMPLE.sub("", text)
    text = _CONTROL.sub("", text)
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]
    lines = [ln for ln in lines if ln]
    # 命令执行完后新提示符会落在捕获区间末尾，它不是命令响应，剔除
    while lines and _PROMPT_LINE.match(lines[-1]):
        lines.pop()
    summary = " | ".join(lines[:3])
    if len(summary) > limit:
        summary = summary[:limit] + "…"
    return summary


def strip_command_echo(summary: str, cmd: str | None) -> str:
    """若摘要首行以命令本身结尾（回显混入），去掉该首行。"""
    if not cmd or not summary:
        return summary
    head, sep, rest = summary.partition(" | ")
    if head.rstrip().endswith(cmd):
        return rest if sep else ""
    return summary


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
                          on_command, on_command_summary, on_close,
                          recorder: AsyncCastRecorder | None) -> None:
    """建立并运行一个 SSH 交互式会话，直到断开。

    :param on_command:         异步回调 (cmd: str) -> int，回车提交时落审计，返回审计行 ID
    :param on_command_summary: 异步回调 (log_id: int, summary: str) -> None，响应静默后回写摘要
    :param on_close:           异步回调 () -> None，会话结束时收尾（含刷盘）
    :param recorder:           已打开的异步 asciicast 录像器
    """
    auditor = LineAuditor()

    # ---- 命令响应摘要捕获状态 ----
    # 命令提交后置 capturing=True 并累积 PTY 输出；输出静默
    # RESPONSE_QUIET_SECONDS 后认为命令执行完毕（新 shell 提示符已出现），
    # 清洗累积输出并按该命令的审计行 ID 精确回写摘要。
    capturing = False
    pending: list[str] = []
    current_log_id: int | None = None
    current_cmd: str | None = None
    quiet_timer: asyncio.TimerHandle | None = None
    loop = asyncio.get_running_loop()

    def cancel_quiet_timer() -> None:
        nonlocal quiet_timer
        if quiet_timer is not None:
            quiet_timer.cancel()
            quiet_timer = None

    def flush_pending() -> None:
        """把已累积输出清洗成摘要并异步回写；无内容则仅复位状态。"""
        nonlocal capturing, quiet_timer, pending, current_log_id, current_cmd
        quiet_timer = None
        if not capturing:
            return
        capturing = False
        log_id, captured, cmd = current_log_id, pending, current_cmd
        current_log_id, current_cmd, pending = None, None, []
        if not captured or log_id is None:
            return
        summary = strip_command_echo(
            summarize_output("".join(captured)), cmd)
        if summary:
            asyncio.create_task(on_command_summary(log_id, summary))

    def begin_capture(log_id: int, cmd: str) -> None:
        """上一条命令定稿（若有），开始累积新命令的响应。"""
        nonlocal capturing, pending, current_log_id, current_cmd
        flush_pending()
        capturing = True
        pending = []
        current_log_id = log_id
        current_cmd = cmd

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
            """单一接收循环：二进制帧写入 PTY 并审计/录像，文本帧处理控制消息。"""
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
                        if recorder:
                            # 热路径：仅入队，不 await 磁盘
                            recorder.record_input(text)
                        for cmd in auditor.feed(text):
                            # 新命令提交意味着上一条命令的响应已结束（新提示符
                            # 已出现），立刻定稿其摘要，再开始捕获本条响应
                            log_id = await on_command(cmd)
                            begin_capture(log_id, cmd)
                    elif (text_msg := message.get("text")) is not None:
                        try:
                            msg = json.loads(text_msg)
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
                text = chunk.decode("utf-8", "replace")
                if recorder:
                    # 热路径：仅入队，worker 协程异步攒批写盘
                    recorder.record_output(text)
                if capturing:
                    pending.append(text)
                    cancel_quiet_timer()
                    quiet_timer = loop.call_later(
                        RESPONSE_QUIET_SECONDS, flush_pending)

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
        cancel_quiet_timer()
        if process is not None:
            try:
                process.close()
            except Exception:  # noqa: BLE001
                pass
        if conn is not None:
            conn.close()
        # 断连时最后一条命令的静默定时器可能尚未触发，同步定稿其响应摘要
        if capturing and pending and current_log_id is not None:
            summary = strip_command_echo(
                summarize_output("".join(pending)), current_cmd)
            if summary:
                try:
                    await on_command_summary(current_log_id, summary)
                except Exception:  # noqa: BLE001
                    logger.exception("回写命令响应摘要失败: %s", session_id)
        await on_close()
