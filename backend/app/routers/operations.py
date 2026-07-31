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
    settings = get_settings()
    ai_probe = (
        asyncio.to_thread(ai_celery_app.control.inspect(timeout=1.0).ping)
        if settings.ai_enabled
        else asyncio.sleep(0, result={})
    )
    normal, browser, ai, evaluation = await asyncio.gather(
        asyncio.to_thread(celery_app.control.inspect(timeout=1.0).ping),
        asyncio.to_thread(browser_celery_app.control.inspect(timeout=1.0).ping),
        ai_probe,
        asyncio.to_thread(evaluation_celery_app.control.inspect(timeout=1.0).ping),
    )
    names = sorted(set(normal or {}) | set(browser or {}) | set(ai or {}) | set(evaluation or {}))
    required = [
        "realtime@",
        "standard@",
        "maintenance@",
        "browser_realtime@",
        "browser@",
        "browser_maintenance@",
        "ai_realtime@",
        "ai@",
        "ai_maintenance@",
        "evaluation@",
        "evaluation_maintenance@",
    ]
    ai_prefixes = ["ai_realtime@", "ai@", "ai_maintenance@"]
    if not settings.ai_enabled:
        required = [prefix for prefix in required if prefix not in ai_prefixes]
    missing = [prefix for prefix in required if not any(name.startswith(prefix) for name in names)]
    optional_disabled = ai_prefixes if not settings.ai_enabled else []
    idle = [name for name in names if any(name.startswith(prefix) for prefix in optional_disabled)]
    return WorkerHealthResponse(
        healthy=not missing,
        workers=names,
        required_missing=missing,
        optional_disabled=optional_disabled,
        healthy_idle=idle,
    )
