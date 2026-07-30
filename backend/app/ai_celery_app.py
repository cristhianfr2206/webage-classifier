from celery import Celery
from celery.app.registry import TaskRegistry
from kombu import Exchange, Queue

from app.config import get_settings

settings = get_settings()
ai_celery_app = Celery("webage-ai", broker=settings.ai_redis_url, set_as_current=False)
ai_celery_app._tasks = TaskRegistry()
ai_celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_exchange="webage-ai",
    task_default_exchange_type="direct",
    task_default_queue="ai",
    task_queues=tuple(
        Queue(name, Exchange("webage-ai"), routing_key=name)
        for name in ("ai_realtime", "ai", "ai_maintenance")
    ),
    task_routes={
        "app.ai_tasks.classify_ai": {"queue": "ai"},
        "app.ai_tasks.recover_stale_ai": {"queue": "ai_maintenance"},
    },
    imports=("app.ai_tasks",),
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_soft_time_limit=settings.ai_timeout_seconds + 5,
    task_time_limit=settings.ai_timeout_seconds + 10,
    beat_schedule={
        "recover-stale-ai-runs": {
            "task": "app.ai_tasks.recover_stale_ai",
            "schedule": 300.0,
            "options": {"queue": "ai_maintenance", "routing_key": "ai_maintenance"},
        }
    },
)
