import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.dependencies import current_user
from app.main import app
from app.models import (
    Base,
    Category,
    ClassificationRun,
    ClassifierVersion,
    PilotItem,
    PilotRun,
    PilotStatus,
    PolicyVersion,
    Role,
    RulesetVersion,
    RunStatus,
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
                        pilot_eligible=True,
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
    assert first.json()["estimate"]["expected_ai_calls"] == 0
    assert first.json()["membership_count"] == 100
    async with evaluation_db[0]() as db:
        memberships = list(
            (
                await db.scalars(
                    select(PilotItem)
                    .where(PilotItem.pilot_id == uuid.UUID(first.json()["id"]))
                    .order_by(PilotItem.selection_order)
                )
            ).all()
        )
    assert len(memberships) == 100
    assert [item.selection_order for item in memberships] == list(range(1, 101))
    assert [item.original_tranco_rank for item in memberships] == list(range(1, 101))


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


async def test_non_contiguous_duplicate_ranks_persist_stable_membership(
    evaluation_db: tuple[async_sessionmaker[AsyncSession], User, User],
) -> None:
    maker, admin, _ = evaluation_db
    async with maker() as db:
        for rank in (7, 12):
            website = await db.scalar(select(Website).where(Website.tranco_rank == rank))
            assert website is not None
            website.pilot_eligible = False
        db.add_all(
            [
                Website(
                    domain="replacement-101.example",
                    registrable_domain="replacement-101.example",
                    canonical_url="https://replacement-101.example",
                    tranco_rank=101,
                    pilot_eligible=True,
                ),
                Website(
                    domain="replacement-102.example",
                    registrable_domain="replacement-102.example",
                    canonical_url="https://replacement-102.example",
                    tranco_rank=102,
                    pilot_eligible=True,
                ),
                Website(
                    domain="stale-duplicate-rank.example",
                    registrable_domain="stale-duplicate-rank.example",
                    canonical_url="https://stale-duplicate-rank.example",
                    tranco_rank=2,
                    pilot_eligible=True,
                ),
            ]
        )
        await db.commit()

    payload = {"size": 100, "rank_start": 1, "dry_run": True, "capacity_limit": 10}
    first = await request("POST", "/api/pilots", admin, payload)
    second = await request("POST", "/api/pilots", admin, payload)
    assert first.status_code == second.status_code == 201

    async def membership(pilot_id: str) -> list[tuple[uuid.UUID, int, int]]:
        async with maker() as db:
            rows = (
                await db.execute(
                    select(
                        PilotItem.website_id,
                        PilotItem.selection_order,
                        PilotItem.original_tranco_rank,
                    )
                    .where(PilotItem.pilot_id == uuid.UUID(pilot_id))
                    .order_by(PilotItem.selection_order)
                )
            ).all()
            return [(row.website_id, row.selection_order, row.original_tranco_rank) for row in rows]

    first_members = await membership(first.json()["id"])
    second_members = await membership(second.json()["id"])
    assert first_members == second_members
    assert len(first_members) == 100
    assert len({item[0] for item in first_members}) == 100
    assert len({item[2] for item in first_members}) == 100
    assert [item[1] for item in first_members] == list(range(1, 101))
    assert first_members[-1][2] == 102


async def test_pilot_creation_rejects_insufficient_eligible_websites(
    evaluation_db: tuple[async_sessionmaker[AsyncSession], User, User],
) -> None:
    _, admin, _ = evaluation_db
    response = await request(
        "POST",
        "/api/pilots",
        admin,
        {"size": 100, "rank_start": 2, "dry_run": True, "capacity_limit": 10},
    )
    assert response.status_code == 422
    assert "Only 99 pilot-eligible websites" in response.json()["detail"]


async def test_pause_resume_reuses_persisted_membership(
    evaluation_db: tuple[async_sessionmaker[AsyncSession], User, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    maker, admin, _ = evaluation_db

    async def no_dispatch(_: object) -> None:
        return None

    monkeypatch.setattr("app.routers.evaluation.dispatch_run", no_dispatch)
    created = await request(
        "POST",
        "/api/pilots",
        admin,
        {"size": 100, "rank_start": 1, "dry_run": False, "capacity_limit": 10},
    )
    assert created.status_code == 201
    pilot_id = created.json()["id"]

    async def ids() -> list[uuid.UUID]:
        async with maker() as db:
            return list(
                (
                    await db.scalars(
                        select(PilotItem.website_id)
                        .where(PilotItem.pilot_id == uuid.UUID(pilot_id))
                        .order_by(PilotItem.selection_order)
                    )
                ).all()
            )

    original = await ids()
    assert (await request("POST", f"/api/pilots/{pilot_id}/start", admin)).status_code == 200
    assert (await request("POST", f"/api/pilots/{pilot_id}/pause", admin)).status_code == 200
    assert (await request("POST", f"/api/pilots/{pilot_id}/resume", admin)).status_code == 200
    assert await ids() == original
    async with maker() as db:
        pilot = await db.get(PilotRun, uuid.UUID(pilot_id))
        assert pilot is not None
        assert pilot.queued_count == 10


async def test_paused_pilot_reconciles_settled_work_without_dispatch(
    evaluation_db: tuple[async_sessionmaker[AsyncSession], User, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    maker, admin, _ = evaluation_db
    dispatched: list[object] = []

    async def capture(run: object) -> None:
        dispatched.append(run)

    monkeypatch.setattr("app.routers.evaluation.dispatch_run", capture)
    created = await request(
        "POST",
        "/api/pilots",
        admin,
        {"size": 100, "rank_start": 1, "dry_run": False, "capacity_limit": 10},
    )
    pilot_id = uuid.UUID(created.json()["id"])
    assert (await request("POST", f"/api/pilots/{pilot_id}/start", admin)).status_code == 200
    assert len(dispatched) == 10
    assert (await request("POST", f"/api/pilots/{pilot_id}/pause", admin)).status_code == 200

    async with maker() as db:
        items = list(
            (
                await db.scalars(
                    select(PilotItem)
                    .where(PilotItem.pilot_id == pilot_id)
                    .order_by(PilotItem.selection_order)
                )
            ).all()
        )
        original_membership = [item.website_id for item in items]
        for index, item in enumerate(items[:10]):
            run = await db.get(ClassificationRun, item.classification_run_id)
            assert run is not None
            if index < 6:
                run.status = RunStatus.COMPLETED
            elif index < 9:
                run.status = RunStatus.FAILED
            else:
                run.status = RunStatus.RUNNING
        await db.commit()

    monkeypatch.setattr("app.evaluation_tasks.SessionLocal", maker)

    async def forbidden(_: object) -> None:
        raise AssertionError("paused reconciliation dispatched work")

    monkeypatch.setattr("app.evaluation_tasks.dispatch_run", forbidden)
    from app.evaluation_tasks import _advance_pilots

    assert await _advance_pilots() == 0
    async with maker() as db:
        pilot = await db.get(PilotRun, pilot_id)
        assert pilot is not None
        assert pilot.status == PilotStatus.PAUSED
        assert pilot.processed_count == 6
        assert pilot.failed_count == 3
        items = list(
            (
                await db.scalars(
                    select(PilotItem)
                    .where(PilotItem.pilot_id == pilot_id)
                    .order_by(PilotItem.selection_order)
                )
            ).all()
        )
        assert [item.website_id for item in items] == original_membership
        assert sum(item.classification_run_id is None for item in items) == 90
