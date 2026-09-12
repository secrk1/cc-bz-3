"""Redis 客户端单例：会话索引与临时凭证缓存。"""
import redis.asyncio as aioredis

from app.config import settings

redis_client: aioredis.Redis = aioredis.from_url(
    settings.redis_url, decode_responses=True
)

# ---- key 约定 -----------------------------------------------------------
# sess:{id}        -> 会话元信息 JSON（TTL = 会话活跃期）
# cred:{asset_id}  -> 资产解密后的连接凭证 JSON（短 TTL，临时授权）
# audit:queue      -> 待落库审计事件的轻量队列（可选消费）


def session_key(session_id: str) -> str:
    return f"sess:{session_id}"


def credential_key(asset_id: int) -> str:
    return f"cred:{asset_id}"
