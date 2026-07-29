from app.browser_celery_app import browser_celery_app
from app.browser_service import should_use_browser
from app.config import get_settings


def test_browser_fallback_is_http_first_and_threshold_bounded() -> None:
    settings = get_settings()
    assert should_use_browser("", 90, settings)
    assert should_use_browser("enough rendered-looking content " * 20, 20, settings)
    assert should_use_browser("enough content " * 30, 90, settings, javascript_likely=True)
    assert not should_use_browser("enough static content " * 30, 90, settings)


def test_browser_queues_are_separate_priority_queues() -> None:
    queues = {queue.name: queue for queue in browser_celery_app.conf.task_queues}
    assert set(queues) == {"browser_realtime", "browser", "maintenance"}
    assert "realtime" not in queues
    assert "standard" not in queues
    for name, queue in queues.items():
        assert queue.routing_key == name
        assert queue.queue_arguments == {"x-max-priority": 10}


def test_browser_worker_delivery_and_limits_are_safe() -> None:
    settings = get_settings()
    assert browser_celery_app.conf.accept_content == ["json"]
    assert browser_celery_app.conf.task_serializer == "json"
    assert browser_celery_app.conf.task_acks_late is True
    assert browser_celery_app.conf.task_reject_on_worker_lost is True
    assert browser_celery_app.conf.worker_prefetch_multiplier == 1
    assert (
        browser_celery_app.conf.task_soft_time_limit
        == settings.browser_task_soft_time_limit_seconds
    )
    assert browser_celery_app.conf.task_time_limit == settings.browser_task_hard_time_limit_seconds
    assert browser_celery_app.conf.worker_max_tasks_per_child == settings.browser_chromium_max_tasks


def test_normal_and_browser_task_registries_are_isolated() -> None:
    from app import browser_tasks

    del browser_tasks
    browser_tasks = {name for name in browser_celery_app.tasks if name.startswith("app.")}
    assert browser_tasks == {
        "app.browser_tasks.cleanup_browser_artifacts",
        "app.browser_tasks.inspect_browser",
        "app.browser_tasks.recover_stale_browser_runs",
    }
    assert all(not name.startswith("app.tasks.") for name in browser_tasks)


def test_each_browser_task_routes_only_to_its_dedicated_queue() -> None:
    routes = browser_celery_app.conf.task_routes
    assert routes["app.browser_tasks.inspect_browser"] == {"queue": "browser"}
    assert routes["app.browser_tasks.cleanup_browser_artifacts"] == {"queue": "maintenance"}
    assert routes["app.browser_tasks.recover_stale_browser_runs"] == {"queue": "maintenance"}
