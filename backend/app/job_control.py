import secrets
from dataclasses import dataclass
from typing import Protocol

from app.models import RunStatus

RETRYABLE_ERROR_CODES = {
    "domain_locked",
    "fetch_failed",
    "response_timeout",
    "redirect_limit",
    "task_timeout",
    "dns_temporary_failure",
    "dns_timeout",
}
TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
ALLOWED_TRANSITIONS = {
    RunStatus.PENDING: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.RETRYING: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.RUNNING: {
        RunStatus.COMPLETED,
        RunStatus.RETRYING,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    },
    RunStatus.FAILED: {RunStatus.PENDING},
    RunStatus.CANCELLED: {RunStatus.PENDING},
    RunStatus.COMPLETED: set(),
}


def transition_allowed(current: RunStatus, target: RunStatus) -> bool:
    return current == target or target in ALLOWED_TRANSITIONS[current]


def retry_delay(retries: int, jitter: int) -> int:
    return int(min(60, (2 ** min(retries, 8)) * 2) + max(0, min(jitter, 3)))


def should_retry(code: str, attempts: int, max_attempts: int) -> bool:
    return code in RETRYABLE_ERROR_CODES and attempts < max_attempts


class LockRedis(Protocol):
    def set(self, name: str, value: str, *, nx: bool, ex: int) -> object: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object: ...


RELEASE_LOCK = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


@dataclass
class DomainLock:
    client: LockRedis
    domain: str
    ttl_seconds: int
    token: str = ""

    def acquire(self) -> bool:
        self.token = secrets.token_urlsafe(24)
        return bool(
            self.client.set(f"lock:domain:{self.domain}", self.token, nx=True, ex=self.ttl_seconds)
        )

    def release(self) -> None:
        if self.token:
            self.client.eval(RELEASE_LOCK, 1, f"lock:domain:{self.domain}", self.token)
