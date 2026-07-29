import asyncio
import uuid
from dataclasses import dataclass
from typing import cast

from celery.result import AsyncResult
from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.celery_app import celery_app
from app.config import Settings
from app.models import ClassificationRun, QueueName, RunStatus

TASK_NAME = "app.tasks.classify_website"
ACTIVE_STATUSES = (RunStatus.PENDING, RunStatus.RETRYING, RunStatus.RUNNING)


@dataclass(frozen=True)
class QueueSnapshot:
    redis_ok: bool
    active_jobs: int
    queues: dict[str, int]


def capacity_remaining(active_jobs: int, maximum_jobs: int) -> int:
    return max(0, maximum_jobs - active_jobs)


def redis_client(settings: Settings) -> Redis:
    return cast(Redis, Redis.from_url(settings.redis_url, decode_responses=True))


async def enforce_enqueue_rate(settings: Settings, user_id: uuid.UUID) -> None:
    client = redis_client(settings)
    key = f"rate:enqueue:{user_id}"
    try:
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, settings.enqueue_rate_window_seconds)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Queue service unavailable"
        ) from exc
    finally:
        await client.aclose()
    if count > settings.enqueue_rate_limit:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Enqueue rate limit exceeded")


async def enforce_capacity(db: AsyncSession, settings: Settings) -> None:
    await db.execute(select(func.pg_advisory_xact_lock(923_003)))
    active = await db.scalar(
        select(func.count())
        .select_from(ClassificationRun)
        .where(ClassificationRun.status.in_(ACTIVE_STATUSES))
    )
    if (active or 0) >= settings.max_active_jobs:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Classification queue is full")


async def remaining_capacity(db: AsyncSession, settings: Settings) -> int:
    await db.execute(select(func.pg_advisory_xact_lock(923_003)))
    active = await db.scalar(
        select(func.count())
        .select_from(ClassificationRun)
        .where(ClassificationRun.status.in_(ACTIVE_STATUSES))
    )
    return capacity_remaining(int(active or 0), settings.max_active_jobs)


async def dispatch_run(run: ClassificationRun) -> None:
    if not run.task_id:
        raise RuntimeError("run task id must be assigned before dispatch")
    await asyncio.to_thread(
        celery_app.send_task,
        TASK_NAME,
        args=[str(run.id)],
        task_id=run.task_id,
        queue=run.queue_name.value,
        routing_key=run.queue_name.value,
        priority=run.priority,
    )


async def revoke_run(run: ClassificationRun) -> None:
    if run.task_id:
        await asyncio.to_thread(AsyncResult(run.task_id, app=celery_app).revoke, terminate=False)


async def queue_snapshot(db: AsyncSession, settings: Settings) -> QueueSnapshot:
    active = await db.scalar(
        select(func.count())
        .select_from(ClassificationRun)
        .where(ClassificationRun.status.in_(ACTIVE_STATUSES))
    )
    client = redis_client(settings)
    try:
        await client.ping()
        queue_sizes = {}
        for queue in QueueName:
            size = 0
            async for key in client.scan_iter(match=f"{queue.value}*"):
                if await client.type(key) == "list":
                    value = await client.execute_command(  # type: ignore[no-untyped-call]
                        "LLEN", key
                    )
                    size += int(value)
            queue_sizes[queue.value] = size
        redis_ok = True
    except Exception:
        queue_sizes = {queue.value: -1 for queue in QueueName}
        redis_ok = False
    finally:
        await client.aclose()
    return QueueSnapshot(redis_ok, int(active or 0), queue_sizes)
