"""Pydantic I/O 模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    is_admin: bool = False


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    is_admin: bool


class AssetIn(BaseModel):
    name: str
    protocol: str = Field(pattern="^(ssh|rdp)$")
    host: str
    port: int
    username: str
    # 明文密码/密钥由前端一次性提交，服务端加密入库；为空表示免密
    secret: str | None = None


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    protocol: str
    host: str
    port: int
    username: str


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    asset_id: int
    protocol: str
    status: str
    client_ip: str | None
    started_at: datetime
    ended_at: datetime | None


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: str
    event_type: str
    content: str | None
    created_at: datetime
