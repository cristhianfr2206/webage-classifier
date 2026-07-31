import asyncio
import logging
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from typing import NoReturn

from billiard.exceptions import TimeLimitExceeded, WorkerLostError  # type: ignore[import-untyped]
from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from celery.signals import task_failure
from redis import Redis
from sqlalchemy import select

from app.artifacts import ArtifactStore
from app.browser_celery_app import browser_celery_app
from app.browser_inspector import BrowserInspectionError
from app.browser_service import BrowserJobCancelled, execute_browser_classification
from app.config import get_settings
from app.database import SessionLocal, engine
from app.job_control import DomainLock, retry_delay
from app.models import (
    BrowserInspection,
    BrowserStatus,
    ClassificationRun,
    QueueName,
    RunStatus,
    Website,
)

logger = logging.getLogger(__name__)
settings = get_settings()
RETRYABLE = {"navigation_timeout", "browser_failed", "browser_unavailable", "domain_locked"}


def _run(coro: Awaitable[object]) -> object:
    async def runner() -> object:
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(runner())


async def _domain(inspection_id: uuid.UUID) -> str | None:
    async with SessionLocal() as db:
        inspection = await db.get(BrowserInspection, inspection_id)
        run = await db.get(ClassificationRun, inspection.run_id) if inspection else None
        website = await db.get(Website, run.website_id) if run else None
        return website.registrable_domain if website else None


async def _failure(inspection_id: uuid.UUID, code: str, retrying: bool) -> None:
    async with SessionLocal() as db:
        inspection = await db.scalar(
            select(BrowserInspection).where(BrowserInspection.id == inspection_id).with_for_update()
        )
        if inspection is None or inspection.status in {
            BrowserStatus.COMPLETED,
            BrowserStatus.CANCELLED,
            BrowserStatus.FAILED,
        }:
            return
        run = await db.get(ClassificationRun, inspection.run_id)
        inspection.failure_code = code
        if retrying and inspection.attempts < inspection.max_attempts:
            inspection.status = BrowserStatus.RETRYING
            if run is not None:
                run.status = RunStatus.RETRYING
        else:
            inspection.status = BrowserStatus.FAILED
            inspection.finished_at = datetime.now(UTC)
            if run is not None and run.status != RunStatus.COMPLETED:
                run.status = RunStatus.FAILED
                run.error_code = "browser_inspection_failed"
                run.completed_at = datetime.now(UTC)
        await db.commit()


def _retry(task: Task, inspection_id: uuid.UUID, code: str) -> NoReturn:
    countdown = retry_delay(task.request.retries, uuid.uuid4().int % 4)
    _run(_failure(inspection_id, code, True))
    raise task.retry(countdown=countdown, max_retries=settings.browser_max_retries)


@browser_celery_app.task(
    bind=True,
    name="app.browser_tasks.inspect_browser",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=settings.browser_task_soft_time_limit_seconds,
    time_limit=settings.browser_task_hard_time_limit_seconds,
)
def inspect_browser(self: Task, inspection_id_value: str) -> None:
    try:
        inspection_id = uuid.UUID(inspection_id_value)
    except ValueError:
        logger.error("invalid_browser_task_payload")
        return
    domain = _run(_domain(inspection_id))
    if not isinstance(domain, str):
        return
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    lock = DomainLock(client, domain, settings.domain_lock_ttl_seconds)
    if not lock.acquire():
        client.close()
        _retry(self, inspection_id, "domain_locked")
    try:
        _run(_execute(inspection_id))
        _run(_enqueue_ai(inspection_id))
    except BrowserJobCancelled:
        return
    except BrowserInspectionError as exc:
        code = str(exc)
        if code in RETRYABLE and self.request.retries < settings.browser_max_retries:
            _retry(self, inspection_id, code)
        _run(_failure(inspection_id, code, False))
    except SoftTimeLimitExceeded:
        if self.request.retries < settings.browser_max_retries:
            _retry(self, inspection_id, "task_timeout")
        _run(_failure(inspection_id, "task_timeout", False))
    except Exception:
        logger.exception("browser_task_failed", extra={"inspection_id": str(inspection_id)})
        if self.request.retries < settings.browser_max_retries:
            _retry(self, inspection_id, "browser_failed")
        _run(_failure(inspection_id, "browser_failed", False))
    finally:
        lock.release()
        client.close()


async def _execute(inspection_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        await execute_browser_classification(db, settings, inspection_id)


async def _enqueue_ai(inspection_id: uuid.UUID) -> None:
    from app.ai_queueing import dispatch_ai
    from app.ai_service import create_automatic_ai_fallback

    async with SessionLocal() as db:
        inspection = await db.get(BrowserInspection, inspection_id)
        if not inspection:
            return
        job = await create_automatic_ai_fallback(db, settings, inspection.run_id)
        if job:
            await dispatch_ai(job, QueueName.AI)


@browser_celery_app.task(name="app.browser_tasks.cleanup_browser_artifacts")
def cleanup_browser_artifacts() -> int:
    result = _run(_cleanup())
    return result if isinstance(result, int) else 0


async def _cleanup() -> int:
    now = datetime.now(UTC)
    removed = 0
    store = ArtifactStore(settings.browser_artifact_root, settings.browser_screenshot_max_bytes)
    async with SessionLocal() as db:
        expired = list(
            (
                await db.scalars(
                    select(BrowserInspection).where(
                        BrowserInspection.artifact_expires_at < now,
                        BrowserInspection.artifact_id.is_not(None),
                    )
                )
            ).all()
        )
        known = {item.artifact_id for item in expired if item.artifact_id}
        for item in expired:
            if item.artifact_id and store.delete(item.artifact_id):
                removed += 1
            item.artifact_id = None
            item.artifact_expires_at = None
        all_known = set(
            (
                await db.scalars(
                    select(BrowserInspection.artifact_id).where(
                        BrowserInspection.artifact_id.is_not(None)
                    )
                )
            ).all()
        )
        for orphan in store.ids() - all_known - known:
            if store.delete(orphan):
                removed += 1
        await db.commit()
    return removed


@browser_celery_app.task(name="app.browser_tasks.recover_stale_browser_runs")
def recover_stale_browser_runs() -> int:
    result = _run(_recover_stale())
    return result if isinstance(result, int) else 0


async def _recover_stale() -> int:
    cutoff = datetime.now(UTC) - timedelta(
        seconds=settings.browser_task_hard_time_limit_seconds + 10
    )
    async with SessionLocal() as db:
        stale = list(
            (
                await db.scalars(
                    select(BrowserInspection).where(
                        BrowserInspection.status == BrowserStatus.RUNNING,
                        BrowserInspection.started_at < cutoff,
                    )
                )
            ).all()
        )
        for inspection in stale:
            inspection.status = BrowserStatus.FAILED
            inspection.failure_code = "stale_browser_run"
            inspection.finished_at = datetime.now(UTC)
            run = await db.get(ClassificationRun, inspection.run_id)
            if run is not None and run.status != RunStatus.COMPLETED:
                run.status = RunStatus.FAILED
                run.error_code = "browser_worker_lost"
                run.completed_at = datetime.now(UTC)
        await db.commit()
        return len(stale)


@task_failure.connect
def record_browser_worker_failure(
    sender: object = None,
    exception: BaseException | None = None,
    args: tuple[object, ...] | None = None,
    **_: object,
) -> None:
    if getattr(sender, "name", None) != "app.browser_tasks.inspect_browser" or not args:
        return
    try:
        inspection_id = uuid.UUID(str(args[0]))
    except ValueError:
        return
    if isinstance(exception, TimeLimitExceeded | SoftTimeLimitExceeded):
        code = "task_timeout"
    elif isinstance(exception, WorkerLostError):
        code = "browser_worker_lost"
    else:
        return
    logger.error(
        "browser_worker_task_failure",
        extra={"inspection_id": str(inspection_id), "error_type": type(exception).__name__},
    )
    _run(_failure(inspection_id, code, False))


for registered_name in tuple(browser_celery_app.tasks):
    if (
        registered_name.startswith("app.ai_tasks.")
        or registered_name == "app.tasks.classify_website"
    ):
        browser_celery_app.tasks.unregister(registered_name)
