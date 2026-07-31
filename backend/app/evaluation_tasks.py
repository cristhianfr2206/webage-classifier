import asyncio
import uuid
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta

from celery import Task
from sqlalchemy import select

from app.database import SessionLocal, engine
from app.evaluation_celery_app import evaluation_celery_app
from app.evaluation_service import execute_evaluation
from app.models import (
    BrowserInspection,
    BrowserStatus,
    ClassificationRun,
    EvaluationRun,
    EvaluationStatus,
    PilotItem,
    PilotRun,
    PilotStatus,
    QueueName,
    RunStatus,
)
from app.queueing import dispatch_run


def _run(coro: Awaitable[object]) -> object:
    async def runner() -> object:
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(runner())


@evaluation_celery_app.task(bind=True, name="app.evaluation_tasks.run_evaluation", acks_late=True)
def run_evaluation(self: Task, run_id: str) -> None:
    async def execute() -> None:
        async with SessionLocal() as db:
            run = await db.get(EvaluationRun, uuid.UUID(run_id))
            if run is None or run.status in {
                EvaluationStatus.COMPLETED,
                EvaluationStatus.CANCELLED,
            }:
                return
            run.task_id = self.request.id
            await db.commit()
            await execute_evaluation(db, run.id)

    _run(execute())


@evaluation_celery_app.task(name="app.evaluation_tasks.recover_evaluations")
def recover_evaluations() -> None:
    async def execute() -> None:
        async with SessionLocal() as db:
            stale = list(
                (
                    await db.scalars(
                        select(EvaluationRun).where(
                            EvaluationRun.status == EvaluationStatus.RUNNING,
                            EvaluationRun.started_at < datetime.now(UTC) - timedelta(minutes=15),
                        )
                    )
                ).all()
            )
            for run in stale:
                run.status = EvaluationStatus.FAILED
            await db.commit()

    _run(execute())


@evaluation_celery_app.task(name="app.evaluation_tasks.advance_pilots")
def advance_pilots() -> int:
    # asyncio.run creates a fresh loop for every Celery invocation. _run
    # disposes asyncpg connections bound to the prior task's loop.
    result = _run(_advance_pilots())
    return result if isinstance(result, int) else 0


async def _advance_pilots() -> int:
    dispatched = 0
    async with SessionLocal() as db:
        pilots = list(
            (
                await db.scalars(
                    select(PilotRun).where(
                        PilotRun.status.in_((PilotStatus.RUNNING, PilotStatus.PAUSED))
                    )
                )
            ).all()
        )
        for pilot in pilots:
            items = list(
                (await db.scalars(select(PilotItem).where(PilotItem.pilot_id == pilot.id))).all()
            )
            active = 0
            for item in items:
                run = (
                    await db.get(ClassificationRun, item.classification_run_id)
                    if item.classification_run_id
                    else None
                )
                if run is None:
                    continue
                if run.status == RunStatus.COMPLETED:
                    item.status = "completed"
                elif run.status == RunStatus.FAILED:
                    recovery = await db.scalar(
                        select(BrowserInspection).where(BrowserInspection.source_run_id == run.id)
                    )
                    if recovery is None or recovery.status == BrowserStatus.FAILED:
                        item.status = "failed"
                    elif recovery.status == BrowserStatus.CANCELLED:
                        item.status = "cancelled"
                    elif recovery.status == BrowserStatus.COMPLETED:
                        item.status = "completed"
                    else:
                        item.status = (
                            "retrying" if recovery.status == BrowserStatus.RETRYING else "running"
                        )
                        active += 1
                elif run.status == RunStatus.CANCELLED:
                    item.status = "cancelled"
                else:
                    item.status = "retrying" if run.status == RunStatus.RETRYING else "running"
                    active += 1
            pilot.processed_count = sum(item.status == "completed" for item in items)
            pilot.failed_count = sum(item.status == "failed" for item in items)
            slots = 0
            if pilot.status == PilotStatus.RUNNING:
                slots = min(
                    pilot.capacity_limit - active,
                    sum(
                        item.classification_run_id is None and item.status == "pending"
                        for item in items
                    ),
                )
            new_runs: list[ClassificationRun] = []
            if slots > 0:
                pending = sorted(
                    (
                        item
                        for item in items
                        if item.classification_run_id is None and item.status == "pending"
                    ),
                    key=lambda item: item.selection_order,
                )[:slots]
                for item in pending:
                    run = ClassificationRun(
                        website_id=item.website_id,
                        requested_by_id=pilot.requested_by_id,
                        classifier_version_id=pilot.classifier_version_id,
                        queue_name=QueueName.STANDARD,
                        priority=3,
                        task_id=str(uuid.uuid4()),
                    )
                    db.add(run)
                    await db.flush()
                    item.classification_run_id = run.id
                    item.status = "queued"
                    new_runs.append(run)
                pilot.queued_count += len(new_runs)
            terminal = (
                pilot.processed_count
                + pilot.failed_count
                + sum(item.status == "cancelled" for item in items)
            )
            if (
                pilot.status == PilotStatus.RUNNING
                and not new_runs
                and active == 0
                and terminal == len(items)
            ):
                pilot.status = PilotStatus.COMPLETED
                pilot.completed_at = datetime.now(UTC)
            await db.commit()
            for run in new_runs:
                await dispatch_run(run)
                dispatched += 1
    return dispatched


# Celery copies finalized task definitions between app registries. Keep the
# dedicated evaluation tasks out of normal, browser, and AI worker registries.
from app.ai_celery_app import ai_celery_app  # noqa: E402
from app.browser_celery_app import browser_celery_app  # noqa: E402
from app.celery_app import celery_app  # noqa: E402

for other_app in (celery_app, browser_celery_app, ai_celery_app):
    for registered_name in tuple(other_app.tasks):
        if registered_name.startswith("app.evaluation_tasks."):
            other_app.tasks.unregister(registered_name)
