import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import cast

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from redis import Redis
from sqlalchemy import select

from app.ai_celery_app import ai_celery_app
from app.ai_provider import AIProviderError
from app.ai_service import AIJobError, execute_ai_classification
from app.config import get_settings
from app.database import SessionLocal, engine
from app.job_control import DomainLock
from app.models import (
    AIClassification,
    AIStatus,
    ClassificationRun,
    ManualReviewCase,
    ReviewStatus,
    RunStatus,
    Website,
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _run(coro: object) -> object:
    async def runner() -> object:
        try:
            return await coro  # type: ignore[misc]
        finally:
            await engine.dispose()

    return asyncio.run(runner())


async def _execute(ai_id: uuid.UUID) -> None:
    async with SessionLocal() as db:
        await execute_ai_classification(db, settings, ai_id)


async def _domain(ai_id: uuid.UUID) -> str | None:
    async with SessionLocal() as db:
        job = await db.get(AIClassification, ai_id)
        website = await db.get(Website, job.website_id) if job else None
        return website.registrable_domain if website else None


async def _fail(ai_id: uuid.UUID, code: str) -> None:
    async with SessionLocal() as db:
        job = await db.get(AIClassification, ai_id)
        if not job or job.status in {
            AIStatus.COMPLETED,
            AIStatus.CANCELLED,
            AIStatus.REVIEW_REQUIRED,
        }:
            return
        run = await db.get(ClassificationRun, job.run_id)
        job.status = AIStatus.FAILED
        job.failure_code = code
        job.failure_message = "AI classification did not complete"
        job.request_finished_at = datetime.now(UTC)
        job.manual_review_required = True
        if run:
            run.status = RunStatus.FAILED
            run.error_code = "ai_classification_failed"
            run.completed_at = datetime.now(UTC)
            existing_review = await db.scalar(
                select(ManualReviewCase).where(ManualReviewCase.classification_run_id == run.id)
            )
            if existing_review is None:
                db.add(
                    ManualReviewCase(
                        website_id=job.website_id,
                        classification_run_id=run.id,
                        status=ReviewStatus.PENDING,
                        reason="AI classification failed and requires human review",
                    )
                )
        await db.commit()


@ai_celery_app.task(
    bind=True,
    name="app.ai_tasks.classify_ai",
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=settings.ai_timeout_seconds + 5,
    time_limit=settings.ai_timeout_seconds + 10,
)
def classify_ai(self: Task, ai_id_value: str) -> None:
    try:
        ai_id = uuid.UUID(ai_id_value)
    except ValueError:
        logger.error("invalid_ai_task_payload")
        return
    domain = _run(_domain(ai_id))
    if not isinstance(domain, str):
        return
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    lock = DomainLock(client, f"ai:{domain}", settings.domain_lock_ttl_seconds)
    circuit_key = f"ai:circuit:{settings.ai_provider}"
    try:
        circuit_value = cast(str | bytes | int | None, client.get(circuit_key))
        if int(circuit_value or 0) >= 5:
            _run(_fail(ai_id, "provider_circuit_open"))
            client.close()
            return
    except Exception:
        _run(_fail(ai_id, "provider_circuit_unavailable"))
        client.close()
        return
    if not lock.acquire():
        _run(_fail(ai_id, "domain_locked"))
        client.close()
        return
    try:
        _run(_execute(ai_id))
        client.delete(circuit_key)
    except AIProviderError as exc:
        failures = client.incr(circuit_key)
        if failures == 1:
            client.expire(circuit_key, 60)
        if exc.retryable and self.request.retries < settings.ai_max_retries:
            raise self.retry(
                countdown=min(60, 2**self.request.retries + uuid.uuid4().int % 4),
                max_retries=settings.ai_max_retries,
            ) from exc
        _run(_fail(ai_id, str(exc)))
    except AIJobError as exc:
        _run(_fail(ai_id, str(exc)))
    except SoftTimeLimitExceeded:
        _run(_fail(ai_id, "task_timeout"))
    except Exception:
        failures = client.incr(circuit_key)
        if failures == 1:
            client.expire(circuit_key, 60)
        logger.exception("ai_task_failed", extra={"ai_id": str(ai_id)})
        _run(_fail(ai_id, "provider_failure"))
    finally:
        lock.release()
        client.close()


@ai_celery_app.task(name="app.ai_tasks.recover_stale_ai")
def recover_stale_ai() -> int:
    async def recover() -> int:
        cutoff = datetime.now(UTC) - timedelta(seconds=settings.ai_timeout_seconds + 30)
        async with SessionLocal() as db:
            jobs = list(
                (
                    await db.scalars(
                        select(AIClassification).where(
                            AIClassification.status == AIStatus.RUNNING,
                            AIClassification.request_started_at < cutoff,
                        )
                    )
                ).all()
            )
            for job in jobs:
                await _fail(job.id, "worker_lost")
            return len(jobs)

    result = _run(recover())
    return result if isinstance(result, int) else 0


for registered_name in tuple(ai_celery_app.tasks):
    if (
        registered_name.startswith("app.browser_tasks.")
        or registered_name == "app.tasks.classify_website"
    ):
        ai_celery_app.tasks.unregister(registered_name)
