from celery import Celery
from celery.app.registry import TaskRegistry
from kombu import Exchange, Queue

from app.config import get_settings

settings = get_settings()
evaluation_celery_app = Celery("webage-evaluation", broker=settings.redis_url, set_as_current=False)
evaluation_celery_app._tasks = TaskRegistry()
evaluation_celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_backend=None,
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_exchange="webage-evaluation",
    task_default_exchange_type="direct",
    task_default_queue="evaluation",
    task_queues=tuple(
        Queue(name, Exchange("webage-evaluation"), routing_key=name)
        for name in ("evaluation", "evaluation_maintenance")
    ),
    task_routes={
        "app.evaluation_tasks.run_evaluation": {"queue": "evaluation"},
        "app.evaluation_tasks.recover_evaluations": {"queue": "evaluation_maintenance"},
        "app.evaluation_tasks.advance_pilots": {"queue": "evaluation_maintenance"},
    },
    imports=("app.evaluation_tasks",),
    worker_cancel_long_running_tasks_on_connection_loss=True,
    task_soft_time_limit=300,
    task_time_limit=330,
    beat_schedule={
        "recover-stale-evaluations": {
            "task": "app.evaluation_tasks.recover_evaluations",
            "schedule": 300.0,
            "options": {
                "queue": "evaluation_maintenance",
                "routing_key": "evaluation_maintenance",
            },
        },
        "advance-controlled-pilots": {
            "task": "app.evaluation_tasks.advance_pilots",
            "schedule": 10.0,
            "options": {
                "queue": "evaluation_maintenance",
                "routing_key": "evaluation_maintenance",
            },
        },
    },
)
