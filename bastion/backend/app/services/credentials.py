"""资产连接凭证：Redis 短期缓存 + Fernet 解密回源。"""
import json

from app.crypto import decrypt_secret
from app.models import Asset
from app.redis_client import credential_key, redis_client
from app.config import settings


async def get_asset_password(asset: Asset) -> str | None:
    """读取资产明文口令：命中 Redis 直接复用，否则解密并回写短 TTL 缓存。"""
    key = credential_key(asset.id)
    cached = await redis_client.get(key)
    if cached is not None:
        return json.loads(cached).get("password")
    password = decrypt_secret(asset.secret_encrypted)
    await redis_client.set(key, json.dumps({"password": password}),
                           ex=settings.credential_ttl)
    return password
