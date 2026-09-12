"""集中式配置：全部通过环境变量注入，由 docker-compose 提供默认值。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://bastion:bastion@db:5432/bastion"
    redis_url: str = "redis://redis:6379/0"

    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

    # 初始管理员（仅在库中无用户时创建）
    admin_user: str = "admin"
    admin_password: str = "admin123"

    # 凭证写入 Redis 后的 TTL（秒），用户申请临时授权时使用
    credential_ttl: int = 300

    # 录屏（asciicast v2）落盘目录
    recording_dir: str = "/data/recordings"

    # guacd RDP 网关守护进程
    guacd_host: str = "guacd"
    guacd_port: int = 4822


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
