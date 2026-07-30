import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.dependencies import current_user
from app.main import app
from app.models import (
    Base,
    Category,
    ClassifierVersion,
    PolicyVersion,
    Role,
    RulesetVersion,
    User,
    Website,
)


@pytest.fixture
async def evaluation_db() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], User, User]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        admin = User(
            email=f"{uuid.uuid4()}@example.com",
            password_hash=uuid.uuid4().hex,
            role=Role.ADMIN,
        )
        viewer = User(
            email=f"{uuid.uuid4()}@example.com",
            password_hash=uuid.uuid4().hex,
            role=Role.VIEWER,
        )
        rules = RulesetVersion(version="test-rules", weights={}, thresholds={}, checksum="a" * 64)
        policy = PolicyVersion(version="test-policy", snapshot={}, checksum="b" * 64)
        db.add_all([admin, viewer, rules, policy])
        await db.flush()
        db.add_all(
            [
                Category(name="Education", slug="education"),
                ClassifierVersion(
                    version="test",
                    ruleset_version_id=rules.id,
                    policy_version_id=policy.id,
                    is_active=True,
                ),
                *[
                    Website(
                        domain=f"fixture-{rank}.example",
                        registrable_domain=f"fixture-{rank}.example",
                        canonical_url=f"https://fixture-{rank}.example",
                        tranco_rank=rank,
                    )
                    for rank in range(1, 101)
                ],
            ]
        )
        await db.commit()

    async def database() -> AsyncIterator[AsyncSession]:
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = database
    yield maker, admin, viewer
    app.dependency_overrides.clear()
    await engine.dispose()


async def request(
    method: str, path: str, user: User | None, body: dict[str, object] | None = None
) -> httpx.Response:
    if user is None:
        app.dependency_overrides.pop(current_user, None)
    else:
        app.dependency_overrides[current_user] = lambda: user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost"
    ) as client:
        return await client.request(
            method,
            path,
            json=body,
            headers={"X-CSRF-Token": "token"},
            cookies={"csrf_token": "token"},
        )


async def test_published_dataset_lineage_and_access_control(
    evaluation_db: tuple[async_sessionmaker[AsyncSession], User, User],
) -> None:
    _, admin, viewer = evaluation_db
    body: dict[str, object] = {
        "name": "fixture",
        "version": "1.0",
        "source_format": "csv",
        "content": (
            "domain,primary_category,expected_age,expected_rating,expected_blocked\n"
            "learn.example,education,8,children,false\n"
        ),
        "change_notes": "Initial reviewed labels",
        "publish": True,
    }
    assert (await request("POST", "/api/evaluation/datasets/import", None, body)).status_code == 401
    assert (
        await request("POST", "/api/evaluation/datasets/import", viewer, body)
    ).status_code == 403
    response = await request("POST", "/api/evaluation/datasets/import", admin, body)
    assert response.status_code == 201
    first = response.json()
    assert first["published"] is True
    body["version"] = "1.1"
    body["prior_version_id"] = first["id"]
    body["content"] = str(body["content"]).replace(",8,", ",9,")
    successor = await request("POST", "/api/evaluation/datasets/import", admin, body)
    assert successor.status_code == 201
    assert successor.json()["prior_version_id"] == first["id"]
    assert successor.json()["checksum"] != first["checksum"]


async def test_dry_run_dispatches_no_jobs_and_is_reproducible(
    evaluation_db: tuple[async_sessionmaker[AsyncSession], User, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, admin, _ = evaluation_db
    monkeypatch.setattr(
        "app.routers.evaluation.evaluation_celery_app.send_task",
        lambda *args, **kwargs: SimpleNamespace(id="unexpected"),
    )
    payload = {
        "size": 100,
        "rank_start": 1,
        "dry_run": True,
        "capacity_limit": 10,
    }
    first = await request("POST", "/api/pilots", admin, payload)
    second = await request("POST", "/api/pilots", admin, payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["jobs_dispatched"] == 0
    assert first.json()["estimate_hash"] == second.json()["estimate_hash"]
    assert first.json()["estimate"]["pending_domains"] == 100


async def test_full_million_pilot_is_rejected(
    evaluation_db: tuple[async_sessionmaker[AsyncSession], User, User],
) -> None:
    _, admin, _ = evaluation_db
    response = await request(
        "POST",
        "/api/pilots",
        admin,
        {"size": 1_000_000, "dry_run": True, "capacity_limit": 100},
    )
    assert response.status_code == 422
