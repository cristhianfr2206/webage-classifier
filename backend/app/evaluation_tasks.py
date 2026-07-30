import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from celery import Task
from sqlalchemy import select

from app.database import SessionLocal
from app.evaluation_celery_app import evaluation_celery_app
from app.evaluation_service import execute_evaluation
from app.models import (
    ClassificationRun,
    EvaluationRun,
    EvaluationStatus,
    PilotItem,
    PilotRun,
    PilotStatus,
    QueueName,
    RunStatus,
    Website,
)
from app.queueing import dispatch_run


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

    asyncio.run(execute())


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

    asyncio.run(execute())


@evaluation_celery_app.task(name="app.evaluation_tasks.advance_pilots")
def advance_pilots() -> int:
    async def execute() -> int:
        dispatched = 0
        async with SessionLocal() as db:
            pilots = list(
                (
                    await db.scalars(select(PilotRun).where(PilotRun.status == PilotStatus.RUNNING))
                ).all()
            )
            for pilot in pilots:
                items = list(
                    (
                        await db.scalars(select(PilotItem).where(PilotItem.pilot_id == pilot.id))
                    ).all()
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
                        item.status = "failed"
                    elif run.status == RunStatus.CANCELLED:
                        item.status = "cancelled"
                    else:
                        active += 1
                pilot.processed_count = sum(item.status == "completed" for item in items)
                pilot.failed_count = sum(item.status == "failed" for item in items)
                slots = min(
                    pilot.capacity_limit - active,
                    pilot.size - len(items),
                )
                new_runs: list[ClassificationRun] = []
                if slots > 0:
                    existing_ids = select(PilotItem.website_id).where(
                        PilotItem.pilot_id == pilot.id
                    )
                    websites = list(
                        (
                            await db.scalars(
                                select(Website)
                                .where(
                                    Website.tranco_rank >= pilot.rank_start,
                                    Website.tranco_rank < pilot.rank_start + pilot.size,
                                    Website.id.not_in(existing_ids),
                                )
                                .order_by(Website.tranco_rank)
                                .limit(slots)
                            )
                        ).all()
                    )
                    for website in websites:
                        run = ClassificationRun(
                            website_id=website.id,
                            requested_by_id=pilot.requested_by_id,
                            classifier_version_id=pilot.classifier_version_id,
                            queue_name=QueueName.STANDARD,
                            priority=3,
                            task_id=str(uuid.uuid4()),
                        )
                        db.add(run)
                        await db.flush()
                        db.add(
                            PilotItem(
                                pilot_id=pilot.id,
                                website_id=website.id,
                                classification_run_id=run.id,
                                status="queued",
                            )
                        )
                        new_runs.append(run)
                    pilot.queued_count += len(new_runs)
                terminal = (
                    pilot.processed_count
                    + pilot.failed_count
                    + sum(item.status == "cancelled" for item in items)
                )
                if (
                    not new_runs
                    and active == 0
                    and (len(items) >= pilot.size or terminal == len(items))
                ):
                    pilot.status = PilotStatus.COMPLETED
                    pilot.completed_at = datetime.now(UTC)
                await db.commit()
                for run in new_runs:
                    await dispatch_run(run)
                    dispatched += 1
        return dispatched

    return asyncio.run(execute())
