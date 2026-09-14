"""FastAPI 入口：REST + WebSocket 路由装配、健康检查。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import assets, auth, observe, sessions, sftp, transfers, ws
from app.config import settings
from app.redis_client import redis_client, redis_client_bytes
from app.services.recorder import ensure_recording_dir

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 共享卷首次由 root 创建时 guacd(uid 1000) 不可写，启动即放开为 1777
    ensure_recording_dir(settings.recording_dir)
    yield
    await redis_client.aclose()
    await redis_client_bytes.aclose()


app = FastAPI(title="轻量堡垒机", version="1.0.0", lifespan=lifespan)

# 同源部署（nginx 反代），这里放开 CORS 便于本地联调
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(sftp.router)
app.include_router(transfers.router)
app.include_router(transfers.audit_router)
app.include_router(sessions.router)
app.include_router(observe.router)
app.include_router(ws.router)


@app.get("/health")
async def health() -> dict[str, str]:
    try:
        await redis_client.ping()
        redis_ok = "up"
    except Exception:  # noqa: BLE001
        redis_ok = "down"
    return {"status": "ok", "redis": redis_ok}
