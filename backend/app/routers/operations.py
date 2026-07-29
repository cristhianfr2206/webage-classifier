import asyncio

from fastapi import APIRouter

from app.browser_celery_app import browser_celery_app
from app.celery_app import celery_app
from app.config import get_settings
from app.dependencies import AdminUser, Db
from app.queueing import queue_snapshot
from app.schemas import QueueStatusResponse, WorkerHealthResponse

router = APIRouter(prefix="/api/operations", tags=["operations"])


@router.get("/queues", response_model=QueueStatusResponse)
async def queues(_: AdminUser, db: Db) -> QueueStatusResponse:
    snapshot = await queue_snapshot(db, get_settings())
    return QueueStatusResponse(
        redis_ok=snapshot.redis_ok,
        active_jobs=snapshot.active_jobs,
        queues=snapshot.queues,
    )


@router.get("/workers", response_model=WorkerHealthResponse)
async def workers(_: AdminUser) -> WorkerHealthResponse:
    normal, browser = await asyncio.gather(
        asyncio.to_thread(celery_app.control.inspect(timeout=1.0).ping),
        asyncio.to_thread(browser_celery_app.control.inspect(timeout=1.0).ping),
    )
    names = sorted(set(normal or {}) | set(browser or {}))
    required = ("realtime@", "standard@", "maintenance@", "browser_realtime@", "browser@")
    healthy = all(any(name.startswith(prefix) for name in names) for prefix in required)
    return WorkerHealthResponse(healthy=healthy, workers=names)
