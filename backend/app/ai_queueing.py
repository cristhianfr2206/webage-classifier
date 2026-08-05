import asyncio

from celery.result import AsyncResult

from app.ai_celery_app import ai_celery_app
from app.models import AIClassification, AIRecommendation, QueueName


async def dispatch_ai(job: AIClassification, queue: QueueName) -> None:
    if not job.task_id or queue not in {QueueName.AI, QueueName.AI_REALTIME}:
        raise RuntimeError("invalid AI dispatch")
    await asyncio.to_thread(
        ai_celery_app.send_task,
        "app.ai_tasks.classify_ai",
        args=[str(job.id)],
        task_id=job.task_id,
        queue=queue.value,
        routing_key=queue.value,
    )


async def revoke_ai(job: AIClassification) -> None:
    if job.task_id:
        await asyncio.to_thread(AsyncResult(job.task_id, app=ai_celery_app).revoke, terminate=False)


async def dispatch_recommendation(
    job: AIRecommendation,
    queue: QueueName = QueueName.AI,
    *,
    task_id: str,
    attempt_id: str,
) -> None:
    if not task_id or not attempt_id or queue not in {QueueName.AI, QueueName.AI_REALTIME}:
        raise RuntimeError("invalid recommendation queue")
    await asyncio.to_thread(
        ai_celery_app.send_task,
        "app.ai_tasks.execute_recommendation",
        args=[str(job.id), attempt_id],
        task_id=task_id,
        queue=queue.value,
        routing_key=queue.value,
    )
