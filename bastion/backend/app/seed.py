"""首次启动种子数据：管理员账号 + 内置测试靶机资产。"""
import asyncio

from sqlalchemy import select

from app.crypto import encrypt_secret
from app.database import SessionLocal
from app.models import Asset, User
from app.security import hash_password
from app.config import settings


async def seed() -> None:
    async with SessionLocal() as db:
        existing = await db.scalar(select(User).limit(1))
        if existing is None:
            db.add(User(username=settings.admin_user,
                        password_hash=hash_password(settings.admin_password),
                        is_admin=True))
            await db.commit()
            print(f"[seed] created admin user '{settings.admin_user}'")

        if await db.scalar(select(Asset).where(Asset.name == "内置SSH靶机")) is None:
            # linuxserver/openssh-server: 通过 PASSWORD_ACCESS 开启密码登录
            db.add(Asset(name="内置SSH靶机", protocol="ssh",
                         host="ssh-target", port=2222, username="testuser",
                         secret_encrypted=encrypt_secret("testpass")))
        if await db.scalar(select(Asset).where(Asset.name == "内置RDP靶机")) is None:
            # linuxserver/rdesktop 默认账号 abc/abc
            db.add(Asset(name="内置RDP靶机", protocol="rdp",
                         host="rdp-target", port=3389, username="abc",
                         secret_encrypted=encrypt_secret("abc")))
        await db.commit()
        print("[seed] ensured built-in target assets")


if __name__ == "__main__":
    asyncio.run(seed())
