from app.celery_app import celery_app
from app.job_control import DomainLock, retry_delay, should_retry, transition_allowed
from app.models import RunStatus
from app.queueing import capacity_remaining


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, name: str, value: str, *, nx: bool, ex: int) -> object:
        del ex
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object:
        del script, numkeys
        key, token = keys_and_args
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


def test_domain_lock_prevents_duplicates_and_only_owner_releases() -> None:
    redis = FakeRedis()
    first = DomainLock(redis, "example.com", 90)
    second = DomainLock(redis, "example.com", 90)
    assert first.acquire()
    assert not second.acquire()
    second.release()
    assert not DomainLock(redis, "example.com", 90).acquire()
    first.release()
    assert DomainLock(redis, "example.com", 90).acquire()


def test_retry_policy_is_bounded_and_error_specific() -> None:
    assert retry_delay(0, 0) == 2
    assert retry_delay(20, 99) == 63
    assert should_retry("fetch_failed", 1, 4)
    assert not should_retry("unsafe_destination", 1, 4)
    assert not should_retry("fetch_failed", 4, 4)


def test_job_state_transitions_protect_completed_results() -> None:
    assert transition_allowed(RunStatus.PENDING, RunStatus.RUNNING)
    assert transition_allowed(RunStatus.RUNNING, RunStatus.RETRYING)
    assert transition_allowed(RunStatus.RUNNING, RunStatus.COMPLETED)
    assert transition_allowed(RunStatus.FAILED, RunStatus.PENDING)
    assert not transition_allowed(RunStatus.COMPLETED, RunStatus.FAILED)
    assert not transition_allowed(RunStatus.CANCELLED, RunStatus.COMPLETED)


def test_celery_worker_safety_and_task_limits_are_configured() -> None:
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.worker_cancel_long_running_tasks_on_connection_loss is True
    assert celery_app.conf.task_soft_time_limit == 45
    assert celery_app.conf.task_time_limit == 60


def test_all_milestone_three_queues_are_priority_enabled() -> None:
    queues = {queue.name: queue for queue in celery_app.conf.task_queues}
    assert set(queues) == {"realtime", "standard", "maintenance"}
    for name, queue in queues.items():
        assert queue.routing_key == name
        assert queue.queue_arguments == {"x-max-priority": 10}


def test_bulk_capacity_is_bounded_by_remaining_global_capacity() -> None:
    assert capacity_remaining(9_998, 10_000) == 2
    assert capacity_remaining(10_000, 10_000) == 0
    assert capacity_remaining(10_001, 10_000) == 0
