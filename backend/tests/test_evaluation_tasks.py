import asyncio
from types import SimpleNamespace

import pytest

from app import evaluation_tasks


def test_maintenance_task_disposes_async_resources_between_invocations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loops: list[asyncio.AbstractEventLoop] = []
    disposed: list[asyncio.AbstractEventLoop] = []

    async def advance() -> int:
        loops.append(asyncio.get_running_loop())
        return 0

    async def dispose() -> None:
        disposed.append(asyncio.get_running_loop())

    monkeypatch.setattr(evaluation_tasks, "_advance_pilots", advance)
    monkeypatch.setattr(evaluation_tasks, "engine", SimpleNamespace(dispose=dispose))
    assert evaluation_tasks.advance_pilots.run() == 0
    assert evaluation_tasks.advance_pilots.run() == 0
    assert loops[0] is not loops[1]
    assert disposed == loops


def test_maintenance_task_does_not_shutdown_a_default_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def advance() -> int:
        return 0

    async def dispose() -> None:
        return None

    async def forbidden_default_executor_shutdown(self: asyncio.AbstractEventLoop) -> None:
        raise AssertionError("evaluation maintenance must not start a default executor")

    monkeypatch.setattr(evaluation_tasks, "_advance_pilots", advance)
    monkeypatch.setattr(evaluation_tasks, "engine", SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(
        asyncio.BaseEventLoop, "shutdown_default_executor", forbidden_default_executor_shutdown
    )
    assert evaluation_tasks.advance_pilots.run() == 0
    assert evaluation_tasks.advance_pilots.run() == 0
