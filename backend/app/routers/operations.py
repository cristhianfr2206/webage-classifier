import asyncio

from fastapi import APIRouter

from app.ai_celery_app import ai_celery_app
from app.browser_celery_app import browser_celery_app
from app.celery_app import celery_app
from app.config import get_settings
from app.dependencies import AdminUser, Db
from app.evaluation_celery_app import evaluation_celery_app
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
    normal, browser, ai, evaluation = await asyncio.gather(
        asyncio.to_thread(celery_app.control.inspect(timeout=1.0).ping),
        asyncio.to_thread(browser_celery_app.control.inspect(timeout=1.0).ping),
        asyncio.to_thread(ai_celery_app.control.inspect(timeout=1.0).ping),
        asyncio.to_thread(evaluation_celery_app.control.inspect(timeout=1.0).ping),
    )
    names = sorted(set(normal or {}) | set(browser or {}) | set(ai or {}) | set(evaluation or {}))
    required = (
        "realtime@",
        "standard@",
        "maintenance@",
        "browser_realtime@",
        "browser@",
        "ai_realtime@",
        "ai@",
        "evaluation@",
        "evaluation_maintenance@",
    )
    healthy = all(any(name.startswith(prefix) for name in names) for prefix in required)
    return WorkerHealthResponse(healthy=healthy, workers=names)
