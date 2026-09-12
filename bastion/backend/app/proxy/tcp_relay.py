"""原生 TCP 中继网关（RDP 也可走该通道，供本地客户端/扩展接入）。

浏览器内的 RDP 体验由 guacd 桥（guac.py）承载；本模块提供协议无关的
裸 TCP 双向转发，作为“RDP 网关中继”的底层实现：浏览器侧为二进制
WebSocket，服务端侧直连资产 host:port，两个方向全双工 copy。
"""
import asyncio
import logging

logger = logging.getLogger("tcp-relay")


async def bridge_binary(websocket, *, target_host: str, target_port: int) -> int:
    """桥接二进制 WebSocket 与目标 TCP 端口，返回转发字节总数。"""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(target_host, target_port), timeout=5
    )
    forwarded = 0

    async def ws_to_target() -> None:
        nonlocal forwarded
        try:
            async for message in websocket.iter_bytes():
                writer.write(message)
                forwarded += len(message)
                await writer.drain()
        finally:
            writer.close()

    async def target_to_ws() -> None:
        nonlocal forwarded
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                forwarded += len(chunk)
                await websocket.send_bytes(chunk)
        finally:
            writer.close()

    try:
        await asyncio.wait(
            {asyncio.create_task(ws_to_target()),
             asyncio.create_task(target_to_ws())},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, BrokenPipeError):
            pass
    return forwarded
