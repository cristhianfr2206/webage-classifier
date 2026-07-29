import asyncio
import logging
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import task_failure
from redis import Redis
from sqlalchemy import select

from app.celery_app import celery_app
from app.classification_service import CancelledJob, execute_classification
from app.config import get_settings
from app.database import SessionLocal, engine
from app.inspector import InspectionError
from app.job_control import DomainLock, retry_delay, should_retry
from app.models import ClassificationRun, RunStatus, Website

logger = logging.getLogger(__name__)
settings = get_settings()


async def _domain_for_run(run_id: uuid.UUID) -> str | None:
    async with SessionLocal() as db:
        run = await db.get(ClassificationRun, run_id)
        website = await db.get(Website, run.website_id) if run else None
        return website.registrable_domain if website else None


async def _execute(run_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        await execute_classification(db, settings, run_id)


async def _record_failure(
    run_id: uuid.UUID, code: str, *, retrying: bool, countdown: int = 0
) -> None:
    async with SessionLocal() as db:
        run = await db.scalar(
            select(ClassificationRun).where(ClassificationRun.id == run_id).with_for_update()
        )
        if run is None or run.status in {RunStatus.COMPLETED, RunStatus.CANCELLED}:
            return
        run.error_code = code
        run.heartbeat_at = datetime.now(UTC)
        if retrying and run.attempts < run.max_attempts and not run.cancel_requested:
            run.status = RunStatus.RETRYING
            run.next_retry_at = datetime.now(UTC) + timedelta(seconds=countdown)
        else:
            run.status = RunStatus.FAILED
            run.completed_at = datetime.now(UTC)
            run.next_retry_at = None
        await db.commit()


def _run(coro: Awaitable[object]) -> object:
    async def runner() -> object:
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(runner())


def _retry(task: Task, run_id: uuid.UUID, code: str) -> NoReturn:
    exponent = min(task.request.retries, settings.task_max_retries)
    countdown = retry_delay(exponent, uuid.uuid4().int % 4)
    _run(_record_failure(run_id, code, retrying=True, countdown=countdown))
    raise task.retry(countdown=countdown, max_retries=settings.task_max_retries)


@celery_app.task(
    bind=True,
    name="app.tasks.classify_website",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=settings.task_soft_time_limit_seconds,
    time_limit=settings.task_hard_time_limit_seconds,
)
def classify_website(self: Task, run_id_value: str) -> None:
    try:
        run_id = uuid.UUID(run_id_value)
    except ValueError:
        logger.error("invalid_task_payload")
        return
    domain = _run(_domain_for_run(run_id))
    if not isinstance(domain, str):
        return
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    lock = DomainLock(client, domain, settings.domain_lock_ttl_seconds)
    if not lock.acquire():
        client.close()
        _retry(self, run_id, "domain_locked")
    try:
        _run(_execute(run_id))
    except CancelledJob:
        return
    except InspectionError as exc:
        code = str(exc)
        if should_retry(code, self.request.retries, settings.task_max_retries):
            _retry(self, run_id, code)
        _run(_record_failure(run_id, "inspection_failed", retrying=False))
    except SoftTimeLimitExceeded:
        if self.request.retries < settings.task_max_retries:
            _retry(self, run_id, "task_timeout")
        _run(_record_failure(run_id, "task_timeout", retrying=False))
    except Exception:
        logger.exception("classification_task_failed", extra={"run_id": str(run_id)})
        if self.request.retries < settings.task_max_retries:
            _retry(self, run_id, "worker_error")
        _run(_record_failure(run_id, "worker_error", retrying=False))
    finally:
        lock.release()
        client.close()


@task_failure.connect
def record_worker_failure(
    sender: object = None,
    task_id: str | None = None,
    exception: BaseException | None = None,
    args: tuple[object, ...] | None = None,
    **_: object,
) -> None:
    if getattr(sender, "name", None) != "app.tasks.classify_website" or not args:
        return
    try:
        run_id = uuid.UUID(str(args[0]))
    except ValueError:
        return
    logger.error(
        "worker_task_failure",
        extra={"run_id": str(run_id), "task_id": task_id, "error_type": type(exception).__name__},
    )
    _run(_record_failure(run_id, "worker_lost", retrying=False))
