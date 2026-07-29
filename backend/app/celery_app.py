from celery import Celery
from kombu import Exchange, Queue

from app.config import get_settings

settings = get_settings()

celery_app = Celery("webage", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_default_exchange="webage",
    task_default_exchange_type="direct",
    task_default_queue="standard",
    task_queues=(
        Queue(
            "realtime",
            Exchange("webage"),
            routing_key="realtime",
            queue_arguments={"x-max-priority": 10},
        ),
        Queue(
            "standard",
            Exchange("webage"),
            routing_key="standard",
            queue_arguments={"x-max-priority": 10},
        ),
        Queue(
            "maintenance",
            Exchange("webage"),
            routing_key="maintenance",
            queue_arguments={"x-max-priority": 10},
        ),
    ),
    task_routes={"app.tasks.classify_website": {"queue": "standard"}},
    task_soft_time_limit=settings.task_soft_time_limit_seconds,
    task_time_limit=settings.task_hard_time_limit_seconds,
    worker_cancel_long_running_tasks_on_connection_loss=True,
)

celery_app.autodiscover_tasks(["app"])
