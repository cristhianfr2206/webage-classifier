from celery import Celery
from celery.app.registry import TaskRegistry
from kombu import Exchange, Queue

from app.config import get_settings

settings = get_settings()
browser_celery_app = Celery(
    "webage-browser", broker=settings.browser_redis_url, set_as_current=False
)
browser_celery_app._tasks = TaskRegistry()
browser_celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_default_exchange="webage-browser",
    task_default_exchange_type="direct",
    task_default_queue="browser",
    task_queues=tuple(
        Queue(
            name,
            Exchange("webage-browser"),
            routing_key=name,
            queue_arguments={"x-max-priority": 10},
        )
        for name in ("browser_realtime", "browser", "maintenance")
    ),
    task_routes={
        "app.browser_tasks.inspect_browser": {"queue": "browser"},
        "app.browser_tasks.cleanup_browser_artifacts": {"queue": "maintenance"},
        "app.browser_tasks.recover_stale_browser_runs": {"queue": "maintenance"},
    },
    task_soft_time_limit=settings.browser_task_soft_time_limit_seconds,
    task_time_limit=settings.browser_task_hard_time_limit_seconds,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    worker_max_tasks_per_child=settings.browser_chromium_max_tasks,
    imports=("app.browser_tasks",),
    beat_schedule={
        "cleanup-expired-browser-artifacts": {
            "task": "app.browser_tasks.cleanup_browser_artifacts",
            "schedule": 900.0,
            "options": {"queue": "maintenance", "routing_key": "maintenance"},
        },
        "recover-stale-browser-runs": {
            "task": "app.browser_tasks.recover_stale_browser_runs",
            "schedule": 300.0,
            "options": {"queue": "maintenance", "routing_key": "maintenance"},
        },
    },
)
