"""Redis 客户端单例：会话索引、凭证缓存、实时旁观广播/强踢控制。"""
import redis.asyncio as aioredis

from app.config import settings

# 文本客户端：会话索引 / 凭证 JSON
redis_client: aioredis.Redis = aioredis.from_url(
    settings.redis_url, decode_responses=True
)

# 二进制客户端：旁观直播帧（SSH/RDP 原始字节），不做 UTF-8 解码
redis_client_bytes: aioredis.Redis = aioredis.from_url(
    settings.redis_url, decode_responses=False
)

# ---- key 约定 -----------------------------------------------------------
# sess:{id}              -> 会话元信息 JSON（TTL = 会话活跃期）
# cred:{asset_id}        -> 资产解密后的连接凭证 JSON（短 TTL，临时授权）
# live:{id}              -> 旁观直播频道（Pub/Sub，二进制帧）
# livebuf:{id}           -> 旁观接入回看缓冲（list，带容量/TTL）
# sessctrl:{id}          -> 强踢/结束控制频道（Pub/Sub，文本命令）
#
# 直播帧封装（二进制 list 元素，便于批量 RPUSH / 订阅透传）：
#   b"o" + payload   下行帧（资产 -> 用户终端/桌面）
#   b"i" + payload   上行帧（用户键入/键鼠，仅审计性旁观时使用）


def session_key(session_id: str) -> str:
    return f"sess:{session_id}"


def credential_key(asset_id: int) -> str:
    return f"cred:{asset_id}"


def live_channel(session_id: str) -> str:
    return f"live:{session_id}"


def live_buffer_key(session_id: str) -> str:
    return f"livebuf:{session_id}"


def control_channel(session_id: str) -> str:
    return f"sessctrl:{session_id}"
