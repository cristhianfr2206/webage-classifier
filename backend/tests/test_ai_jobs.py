from app.ai_celery_app import ai_celery_app


def test_ai_queues_and_routes_are_isolated() -> None:
    queues = {queue.name for queue in ai_celery_app.conf.task_queues}
    assert queues == {"ai_realtime", "ai", "ai_maintenance"}
    assert ai_celery_app.conf.accept_content == ["json"]
    assert ai_celery_app.conf.task_serializer == "json"
    assert ai_celery_app.conf.task_acks_late is True
    assert ai_celery_app.conf.task_reject_on_worker_lost is True
    assert ai_celery_app.conf.task_routes["app.ai_tasks.classify_ai"] == {"queue": "ai"}
    assert ai_celery_app.conf.task_routes["app.ai_tasks.recover_stale_ai"] == {
        "queue": "ai_maintenance"
    }
    assert ai_celery_app.conf.task_routes["app.ai_tasks.execute_recommendation"] == {"queue": "ai"}
    assert ai_celery_app.conf.task_routes["app.ai_tasks.reconcile_stale_recommendations"] == {
        "queue": "ai_maintenance"
    }


def test_ai_worker_registry_contains_only_ai_tasks() -> None:
    from app import ai_tasks

    del ai_tasks
    names = {name for name in ai_celery_app.tasks if name.startswith("app.")}
    assert names == {
        "app.ai_tasks.classify_ai",
        "app.ai_tasks.execute_recommendation",
        "app.ai_tasks.reconcile_stale_recommendations",
        "app.ai_tasks.recover_stale_ai",
    }
