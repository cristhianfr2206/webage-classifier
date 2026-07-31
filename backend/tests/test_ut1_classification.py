import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.classification_service import execute_classification
from app.config import Settings
from app.extraction import ExtractedPage
from app.models import (
    AgePolicy,
    Base,
    Category,
    ClassificationRun,
    ClassificationSource,
    QueueName,
    Role,
    User,
    Website,
)


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield session_maker
    finally:
        await engine.dispose()


def settings(fixture_path: str = "") -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite://",
        secret_key="test-secret-key-that-is-at-least-thirty-two-bytes",  # noqa: S106
        initial_admin_email="admin@example.com",
        initial_admin_password="long-test-password",  # noqa: S106
        allowed_origins=["http://localhost"],
        allowed_hosts=["localhost"],
        ut1_fixture_path=fixture_path,
    )


async def seed(maker: async_sessionmaker[AsyncSession], domain: str) -> uuid.UUID:
    async with maker() as db:
        user = User(  # noqa: S106
            email=f"{uuid4()}@example.com",
            password_hash="hash",  # noqa: S106
            role=Role.ADMIN,  # noqa: S106
        )
        policy = AgePolicy(name=f"policy-{uuid4()}", minimum_age=8, maximum_age=17)
        category = Category(name="Education", slug="education")
        website = Website(
            domain=domain,
            registrable_domain="example.com",
            canonical_url=f"https://{domain}/",
        )
        db.add_all([user, policy, category, website])
        await db.flush()
        category.age_policy_id = policy.id
        run = ClassificationRun(
            website=website,
            requested_by_id=user.id,
            queue_name=QueueName.STANDARD,
            task_id=str(uuid4()),
        )
        db.add(run)
        await db.commit()
        return run.id


async def fake_inspection(_: str) -> Any:
    return type(
        "Inspection",
        (),
        {
            "page": ExtractedPage("Course", "", "learn course school"),
            "final_url": "https://example.com/",
        },
    )()


async def test_exact_match_skips_http_and_records_offline_evidence(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "ut1.csv"
    fixture.write_text("domain,category\nnetflix.com,education\n", encoding="utf-8")
    run_id = await seed(maker, "netflix.com")

    async def unexpected(_: object, __: object) -> None:
        raise AssertionError("HTTP inspection must be skipped")

    monkeypatch.setattr("app.classification_service.WebsiteInspector.inspect", unexpected)
    async with maker() as db:
        results = await execute_classification(db, settings(str(fixture)), run_id)
        assert results[0].source == ClassificationSource.RULES
        assert results[0].confidence == 95
        assert results[0].evidence[0]["source"] == "offline_ut1"
        assert results[0].evidence[0]["match"] == "exact"


async def test_parent_match_continues_http_and_records_lower_confidence_evidence(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "ut1.csv"
    fixture.write_text("domain,category\nnetflix.com,education\n", encoding="utf-8")
    run_id = await seed(maker, "www.netflix.com")
    called = False

    async def inspect(_: object, __: str) -> Any:
        nonlocal called
        called = True
        return await fake_inspection(__)

    monkeypatch.setattr("app.classification_service.WebsiteInspector.inspect", inspect)
    async with maker() as db:
        results = await execute_classification(db, settings(str(fixture)), run_id)
    assert called
    assert any(item.get("match") == "parent-domain" for item in results[0].evidence)


async def test_unknown_and_unconfigured_fixture_continue_existing_http_behavior(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = await seed(maker, "adobe.com")
    called = False

    async def inspect(_: object, __: str) -> Any:
        nonlocal called
        called = True
        return await fake_inspection(__)

    monkeypatch.setattr("app.classification_service.WebsiteInspector.inspect", inspect)
    async with maker() as db:
        results = await execute_classification(db, settings(), run_id)
    assert called
    assert results[0].evidence[0]["rule"] == "course"


def test_exact_offline_evidence_is_the_browser_skip_signal() -> None:
    from app.tasks import _has_exact_offline_evidence

    assert _has_exact_offline_evidence(
        [SimpleNamespace(evidence=[{"source": "offline_ut1", "match": "exact"}])]
    )
    assert not _has_exact_offline_evidence(
        [SimpleNamespace(evidence=[{"source": "offline_ut1", "match": "parent-domain"}])]
    )
