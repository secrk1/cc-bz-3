"""每用户文件传输并发槽（Redis 计数）。

单用户同时进行的文件传输（上传 + 下载）最多 MAX_CONCURRENT 个。
计数键带 TTL：进程崩溃/客户端断连导致的槽位泄漏最多存活 SLOT_TTL 秒后自愈；
正常传输在每个分片/流式心跳时刷新 TTL。
"""
from app.redis_client import redis_client

MAX_CONCURRENT = 2
SLOT_TTL = 600  # 秒；崩溃泄漏的最长占用时间


def _key(user_id: int) -> str:
    return f"xfers:{user_id}"


async def acquire(user_id: int) -> bool:
    """占用一个槽位；已达上限返回 False（调用方返回 429）。"""
    key = _key(user_id)
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, SLOT_TTL)
    if count > MAX_CONCURRENT:
        await redis_client.decr(key)
        return False
    return True


async def release(user_id: int) -> None:
    """归还槽位（不低于 0）。"""
    key = _key(user_id)
    count = await redis_client.decr(key)
    if count <= 0:
        await redis_client.delete(key)


async def refresh(user_id: int) -> None:
    """长传输心跳：刷新 TTL，避免大文件传输中途槽位过期。"""
    await redis_client.expire(_key(user_id), SLOT_TTL)
