"""ORM 模型：用户 / 资产 / 会话 / 审计日志。"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                server_default=func.now())


class Asset(Base):
    """被纳管的远程主机。credential_* 对称加密后入库。"""
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    protocol: Mapped[str] = mapped_column(String(8), default="ssh")  # ssh / rdp
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(128))
    # Fernet 令牌（不含 b'...' 包裹），为 None 表示免密（如内置测试靶机）
    secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                server_default=func.now())

    sessions: Mapped[list["SessionRecord"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan")


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    protocol: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active/closed
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recording_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                      nullable=True)

    asset: Mapped[Asset] = relationship(back_populates="sessions")
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="session", cascade="all, delete-orphan")


class AuditLog(Base):
    """指令级审计：SSH 侧记录回车提交的命令行；RDP 侧记录连接事件。"""
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))  # command / login / close
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 server_default=func.now())

    session: Mapped[SessionRecord] = relationship(back_populates="audit_logs")
