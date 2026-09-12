"""资产管理（仅管理员可写，普通用户可读）+ 连通性探测。"""
import asyncio
import json

import asyncssh
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret
from app.database import get_db
from app.models import Asset, User
from app.proxy import guac
from app.redis_client import credential_key, redis_client
from app.schemas import AssetIn, AssetOut
from app.security import get_current_user, require_admin

router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.get("", response_model=list[AssetOut])
async def list_assets(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Asset]:
    return list(await db.scalars(select(Asset).order_by(Asset.id)))


@router.post("", response_model=AssetOut, status_code=201)
async def create_asset(
    payload: AssetIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> Asset:
    if await db.scalar(select(Asset).where(Asset.name == payload.name)):
        raise HTTPException(status_code=409, detail="资产名称已存在")
    asset = Asset(
        name=payload.name, protocol=payload.protocol, host=payload.host,
        port=payload.port, username=payload.username,
        secret_encrypted=encrypt_secret(payload.secret),
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> None:
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    await db.delete(asset)
    await db.commit()


async def _get_password(asset: Asset) -> str | None:
    """读取凭证：优先 Redis 短期缓存，否则解密资产密文。"""
    key = credential_key(asset.id)
    cached = await redis_client.get(key)
    if cached is not None:
        return json.loads(cached).get("password")
    return decrypt_secret(asset.secret_encrypted)


@router.post("/{asset_id}/check")
async def check_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
) -> dict:
    """真实连通性探测：SSH 实际握手鉴权；RDP 经 guacd 走完整登录握手。"""
    asset = await db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")

    password = await _get_password(asset)
    try:
        if asset.protocol == "ssh":
            kwargs = dict(host=asset.host, port=asset.port,
                          username=asset.username, known_hosts=None,
                          connect_timeout=8)
            if password:
                kwargs["password"] = password
            conn = await asyncio.wait_for(asyncssh.connect(**kwargs), timeout=10)
            conn.close()
            return {"ok": True, "detail": "SSH 连接与认证成功"}

        await guac.probe_rdp(
            guacd_host=settings.guacd_host, guacd_port=settings.guacd_port,
            target_host=asset.host, target_port=asset.port,
            username=asset.username, password=password or "")
        return {"ok": True, "detail": "RDP 网关握手与登录成功"}
    except guac.GuacError as exc:
        return {"ok": False, "detail": str(exc)}
    except (asyncssh.Error, OSError, asyncio.TimeoutError) as exc:
        return {"ok": False, "detail": f"连接失败：{exc}"}
