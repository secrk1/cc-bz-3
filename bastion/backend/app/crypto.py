"""资产凭证对称加密（Fernet）。

密钥从 JWT_SECRET 派生（部署时请覆盖该环境变量），保证静态存储的
RDP 口令 / SSH 密钥不以明文落库。
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

_fernet = Fernet(base64.urlsafe_b64encode(
    hashlib.sha256(settings.jwt_secret.encode()).digest()
))


def encrypt_secret(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return None
