import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.classification_service import UT1_CATEGORY_ALIASES, execute_classification
from app.config import Settings
from app.extraction import ExtractedPage
from app.models import (
    AgePolicy,
    Base,
    Category,
    ClassificationRun,
    ClassificationSource,
    ManualReviewCase,
    QueueName,
    Role,
    User,
    Website,
    WebsiteClassification,
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


async def test_safe_infrastructure_exclusion_skips_http_and_has_no_age_policy(
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.classification_service")
    run_id = await seed(maker, "gtld-servers.net")

    async def unexpected(_: object, __: str) -> None:
        raise AssertionError("HTTP inspection must be skipped")

    monkeypatch.setattr("app.classification_service.WebsiteInspector.inspect", unexpected)
    async with maker() as db:
        results = await execute_classification(db, settings(), run_id)
        run = await db.get(ClassificationRun, run_id)
        classifications = list(
            (
                await db.scalars(
                    select(WebsiteClassification).where(WebsiteClassification.run_id == run_id)
                )
            ).all()
        )
    assert results == []
    assert classifications == []
    assert run is not None
    assert run.error_code == "non_consumer_infrastructure:dns_nameserver"
    assert run.status.value == "completed"
    record = next(
        item for item in caplog.records if item.message == "infrastructure_classification_excluded"
    )
    assert record.confidence_tier == "safe_exclude"
    assert record.evidence == "authoritative TLD nameserver hostname"


async def test_evidence_only_infrastructure_continues_http(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = await seed(maker, "cloudflare.com")
    called = False

    async def inspect(_: object, __: str) -> Any:
        nonlocal called
        called = True
        return await fake_inspection(__)

    monkeypatch.setattr("app.classification_service.WebsiteInspector.inspect", inspect)
    async with maker() as db:
        results = await execute_classification(db, settings(), run_id)
    assert called
    assert results[0].category_id is not None
    detector_evidence = next(
        item for item in results[0].evidence if item.get("source") == "infrastructure_detector"
    )
    assert detector_evidence["confidence_tier"] == "evidence_only"


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


@pytest.mark.parametrize("feed_category", ["social_networks", "audio-video"])
async def test_preliminary_ut1_categories_continue_http(
    maker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    feed_category: str,
) -> None:
    fixture = tmp_path / "ut1.csv"
    fixture.write_text(f"domain,category\nnetflix.com,{feed_category}\n", encoding="utf-8")
    run_id = await seed(maker, "netflix.com")
    called = False

    async def inspect(_: object, __: str) -> Any:
        nonlocal called
        called = True
        return await fake_inspection(__)

    monkeypatch.setattr("app.classification_service.WebsiteInspector.inspect", inspect)
    async with maker() as db:
        results = await execute_classification(db, settings(str(fixture)), run_id)
    assert called
    evidence = next(item for item in results[0].evidence if item.get("source") == "offline_ut1")
    assert evidence["category"] == feed_category
    assert evidence["status"] == "preliminary"


@pytest.mark.parametrize("feed_category", ["shopping", "games"])
async def test_unsupported_ut1_categories_do_not_create_final_category(
    maker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    feed_category: str,
) -> None:
    fixture = tmp_path / "ut1.csv"
    fixture.write_text(f"domain,category\nnetflix.com,{feed_category}\n", encoding="utf-8")
    run_id = await seed(maker, "netflix.com")
    called = False

    async def inspect(_: object, __: str) -> Any:
        nonlocal called
        called = True
        return await fake_inspection(__)

    monkeypatch.setattr("app.classification_service.WebsiteInspector.inspect", inspect)
    async with maker() as db:
        results = await execute_classification(db, settings(str(fixture)), run_id)
    assert called
    evidence = next(item for item in results[0].evidence if item.get("source") == "offline_ut1")
    assert evidence["status"] == "unsupported"
    assert evidence.get("mapped_category") is None


@pytest.mark.parametrize("feed_category", ["adult", "gambling"])
async def test_high_risk_ut1_categories_require_confirmation(
    maker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    feed_category: str,
) -> None:
    fixture = tmp_path / "ut1.csv"
    fixture.write_text(f"domain,category\nnetflix.com,{feed_category}\n", encoding="utf-8")
    run_id = await seed(maker, "netflix.com")
    called = False

    async def inspect(_: object, __: str) -> Any:
        nonlocal called
        called = True
        return await fake_inspection(__)

    monkeypatch.setattr("app.classification_service.WebsiteInspector.inspect", inspect)
    async with maker() as db:
        results = await execute_classification(db, settings(str(fixture)), run_id)
        review = await db.scalar(
            select(ManualReviewCase).where(ManualReviewCase.classification_run_id == run_id)
        )
    assert called
    evidence = next(item for item in results[0].evidence if item.get("source") == "offline_ut1")
    assert evidence["status"] == "high_risk"
    assert evidence["risk"] == "high"
    assert evidence["manual_review_required"] is True
    assert evidence.get("mapped_category") is None
    assert review is not None


async def test_unknown_and_unconfigured_fixture_continue_existing_http_behavior(
    maker: async_sessionmaker[AsyncSession], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "ut1.csv"
    fixture.write_text("domain,category\nnetflix.com,education\n", encoding="utf-8")
    run_id = await seed(maker, "adobe.com")
    called = False

    async def inspect(_: object, __: str) -> Any:
        nonlocal called
        called = True
        return await fake_inspection(__)

    monkeypatch.setattr("app.classification_service.WebsiteInspector.inspect", inspect)
    async with maker() as db:
        results = await execute_classification(db, settings(str(fixture)), run_id)
    assert called
    assert results[0].evidence[0]["rule"] == "course"


def test_exact_offline_evidence_is_the_browser_skip_signal() -> None:
    from app.tasks import _has_exact_offline_evidence

    assert _has_exact_offline_evidence(
        [
            SimpleNamespace(
                evidence=[{"source": "offline_ut1", "match": "exact", "category": "education"}]
            )
        ]
    )
    assert not _has_exact_offline_evidence(
        [
            SimpleNamespace(
                evidence=[
                    {"source": "offline_ut1", "match": "exact", "category": "social_networks"}
                ]
            )
        ]
    )
    assert not _has_exact_offline_evidence(
        [SimpleNamespace(evidence=[{"source": "offline_ut1", "match": "parent-domain"}])]
    )


def test_ut1_aliases_do_not_expand_application_taxonomy() -> None:
    assert UT1_CATEGORY_ALIASES == {
        "audio-video": "entertainment",
        "social_networks": "social",
    }
    assert not {"shopping", "games", "adult", "gambling"}.intersection(
        UT1_CATEGORY_ALIASES.values()
    )
