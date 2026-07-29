import asyncio

from celery.result import AsyncResult

from app.browser_celery_app import browser_celery_app
from app.models import BrowserInspection, QueueName

BROWSER_TASK_NAME = "app.browser_tasks.inspect_browser"


async def dispatch_browser(inspection: BrowserInspection, queue: QueueName) -> None:
    if not inspection.task_id or queue not in {QueueName.BROWSER, QueueName.BROWSER_REALTIME}:
        raise RuntimeError("invalid browser dispatch")
    await asyncio.to_thread(
        browser_celery_app.send_task,
        BROWSER_TASK_NAME,
        args=[str(inspection.id)],
        task_id=inspection.task_id,
        queue=queue.value,
        routing_key=queue.value,
        priority=9 if queue == QueueName.BROWSER_REALTIME else 5,
    )


async def revoke_browser(inspection: BrowserInspection) -> None:
    if inspection.task_id:
        await asyncio.to_thread(
            AsyncResult(inspection.task_id, app=browser_celery_app).revoke,
            terminate=False,
        )
