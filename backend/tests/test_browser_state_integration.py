import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.browser_inspector import BrowserInspectionError, BrowserResult
from app.browser_service import (
    BrowserJobCancelled,
    create_automatic_browser_fallback,
    execute_browser_classification,
)
from app.browser_tasks import _failure, _recover_stale
from app.config import get_settings
from app.database import SessionLocal as PostgresSession
from app.database import engine as application_engine
from app.extraction import ExtractedPage
from app.models import (
    AgePolicy,
    Base,
    BrowserInspection,
    BrowserStatus,
    Category,
    ClassificationRun,
    ClassificationSource,
    QueueName,
    Role,
    RunStatus,
    User,
    Website,
    WebsiteClassification,
)


@pytest.fixture
async def session_maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield maker
    finally:
        await engine.dispose()


async def seed_browser_run(
    maker: async_sessionmaker[AsyncSession],
    *,
    status: BrowserStatus = BrowserStatus.PENDING,
    prior: bool = True,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with maker() as db:
        user = User(
            email=f"{uuid.uuid4()}@example.com",
            password_hash=uuid.uuid4().hex,
            role=Role.ADMIN,
        )
        policy = AgePolicy(
            name=f"policy-{uuid.uuid4()}",
            minimum_age=8,
            maximum_age=17,
            is_active=True,
        )
        db.add_all([user, policy])
        await db.flush()
        category = Category(
            name=f"Entertainment-{uuid.uuid4()}",
            slug="entertainment",
            age_policy_id=policy.id,
        )
        website = Website(
            domain=f"{uuid.uuid4()}.example.com",
            registrable_domain="example.com",
            canonical_url="https://example.com/",
        )
        db.add_all([category, website])
        await db.flush()
        if prior:
            prior_run = ClassificationRun(
                website_id=website.id,
                requested_by_id=user.id,
                status=RunStatus.COMPLETED,
                queue_name=QueueName.REALTIME,
                task_id=str(uuid.uuid4()),
            )
            db.add(prior_run)
            await db.flush()
            db.add(
                WebsiteClassification(
                    website_id=website.id,
                    run_id=prior_run.id,
                    category_id=category.id,
                    age_policy_id=policy.id,
                    source=ClassificationSource.RULES,
                    confidence=90,
                    evidence=[{"rule": "prior"}],
                    title="Prior valid classification",
                )
            )
        run = ClassificationRun(
            website_id=website.id,
            requested_by_id=user.id,
            status=RunStatus.PENDING,
            queue_name=QueueName.BROWSER,
            task_id=str(uuid.uuid4()),
        )
        db.add(run)
        await db.flush()
        inspection = BrowserInspection(
            run_id=run.id,
            status=status,
            trigger="test",
            task_id=run.task_id,
        )
        db.add(inspection)
        await db.commit()
        return inspection.id, website.id


def rendered_result(text: str = "video games gaming gameplay multiplayer console") -> BrowserResult:
    return BrowserResult(
        final_url="https://example.com/rendered",
        page=ExtractedPage(
            title="<script>inert()</script>",
            description="",
            visible_text=text,
        ),
        headings=("<img onerror=inert()>",),
        links=(),
        buttons=("<script>button()</script>",),
        request_count=3,
        transferred_bytes=512,
        blocked_requests=1,
        duration_ms=25,
        artifact_id=None,
        browser_version="test-browser",
        playwright_version="test-playwright",
    )


async def test_successful_browser_run_stores_rendered_evidence(
    session_maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_id, _ = await seed_browser_run(session_maker, prior=False)

    class Inspector:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def inspect(self, value: str) -> BrowserResult:
            del value
            return rendered_result()

    monkeypatch.setattr("app.browser_service.BrowserInspector", Inspector)
    async with session_maker() as db:
        created = await execute_browser_classification(db, get_settings(), inspection_id)
        assert created
        assert all(item.source == ClassificationSource.RENDERED for item in created)
        assert all(
            evidence["source"] == "rendered_html" for item in created for evidence in item.evidence
        )
        inspection = await db.get(BrowserInspection, inspection_id)
        assert inspection is not None
        assert inspection.status == BrowserStatus.COMPLETED
        assert inspection.rendered_title == "<script>inert()</script>"


@pytest.mark.parametrize(
    "failure",
    [
        BrowserInspectionError("navigation_timeout"),
        BrowserInspectionError("browser_failed"),
        RuntimeError("invalid_browser_output"),
    ],
)
async def test_failed_browser_output_never_replaces_prior_classification(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    inspection_id, website_id = await seed_browser_run(session_maker)

    class Inspector:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def inspect(self, value: str) -> BrowserResult:
            del value
            raise failure

    monkeypatch.setattr("app.browser_service.BrowserInspector", Inspector)
    async with session_maker() as db:
        with pytest.raises(type(failure)):
            await execute_browser_classification(db, get_settings(), inspection_id)
    async with session_maker() as db:
        classifications = list(
            (
                await db.scalars(
                    select(WebsiteClassification).where(
                        WebsiteClassification.website_id == website_id
                    )
                )
            ).all()
        )
        assert len(classifications) == 1
        assert classifications[0].title == "Prior valid classification"


async def test_cancelled_browser_run_preserves_prior_classification(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    inspection_id, website_id = await seed_browser_run(session_maker)
    async with session_maker() as db:
        inspection = await db.get(BrowserInspection, inspection_id)
        assert inspection is not None
        inspection.cancel_requested = True
        await db.commit()
        with pytest.raises(BrowserJobCancelled, match="cancelled"):
            await execute_browser_classification(db, get_settings(), inspection_id)
    async with session_maker() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WebsiteClassification)
                .where(WebsiteClassification.website_id == website_id)
            )
            == 1
        )


async def test_duplicate_automatic_fallback_creates_only_one_active_run(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    inspection_id, _ = await seed_browser_run(session_maker, prior=False)
    async with session_maker() as db:
        inspection = await db.get(BrowserInspection, inspection_id)
        assert inspection is not None
        source_run = await db.get(ClassificationRun, inspection.run_id)
        assert source_run is not None
        source_run.queue_name = QueueName.REALTIME
        source_run.status = RunStatus.COMPLETED
        category = await db.scalar(select(Category).where(Category.slug == "entertainment"))
        assert category is not None
        db.add(
            WebsiteClassification(
                website_id=source_run.website_id,
                run_id=source_run.id,
                category_id=category.id,
                age_policy_id=category.age_policy_id,
                source=ClassificationSource.RULES,
                confidence=10,
                evidence=[{"rule": "low"}],
                text_excerpt="short",
            )
        )
        await db.delete(inspection)
        await db.commit()
        first = await create_automatic_browser_fallback(db, get_settings(), source_run.id)
        second = await create_automatic_browser_fallback(db, get_settings(), source_run.id)
        assert first is not None
        assert second is None


async def test_concurrent_postgres_browser_runs_are_database_unique() -> None:
    if application_engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL-only partial unique-index integration test")
    async with PostgresSession() as db:
        user = User(
            email=f"duplicate-{uuid.uuid4()}@example.com",
            password_hash=uuid.uuid4().hex,
            role=Role.ADMIN,
        )
        website = Website(
            domain=f"{uuid.uuid4()}.example.com",
            registrable_domain="example.com",
            canonical_url="https://example.com/",
        )
        db.add_all([user, website])
        await db.commit()
        user_id, website_id = user.id, website.id

    barrier = asyncio.Barrier(2)

    async def create() -> bool:
        async with PostgresSession() as db:
            await barrier.wait()
            run = ClassificationRun(
                website_id=website_id,
                requested_by_id=user_id,
                queue_name=QueueName.BROWSER,
                task_id=str(uuid.uuid4()),
            )
            db.add(run)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return False
            return True

    try:
        assert sorted(await asyncio.gather(create(), create())) == [False, True]
        async with PostgresSession() as db:
            active_count = await db.scalar(
                select(func.count())
                .select_from(ClassificationRun)
                .where(
                    ClassificationRun.website_id == website_id,
                    ClassificationRun.status.in_(
                        (RunStatus.PENDING, RunStatus.RETRYING, RunStatus.RUNNING)
                    ),
                )
            )
            assert active_count == 1
    finally:
        async with PostgresSession() as db:
            await db.execute(delete(Website).where(Website.id == website_id))
            await db.execute(delete(User).where(User.id == user_id))
            await db.commit()


async def test_timeout_failure_transitions_are_retry_bounded(
    session_maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_id, _ = await seed_browser_run(session_maker, status=BrowserStatus.RUNNING)
    monkeypatch.setattr("app.browser_tasks.SessionLocal", session_maker)
    await _failure(inspection_id, "task_timeout", True)
    async with session_maker() as db:
        inspection = await db.get(BrowserInspection, inspection_id)
        assert inspection is not None
        assert inspection.status == BrowserStatus.RETRYING
        inspection.attempts = inspection.max_attempts
        await db.commit()
    await _failure(inspection_id, "task_timeout", True)
    async with session_maker() as db:
        inspection = await db.get(BrowserInspection, inspection_id)
        assert inspection is not None
        assert inspection.status == BrowserStatus.FAILED
        assert inspection.failure_code == "task_timeout"


async def test_stale_worker_loss_recovery_fails_run_without_promoting_output(
    session_maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection_id, website_id = await seed_browser_run(session_maker, status=BrowserStatus.RUNNING)
    async with session_maker() as db:
        inspection = await db.get(BrowserInspection, inspection_id)
        assert inspection is not None
        inspection.started_at = datetime.now(UTC) - timedelta(minutes=10)
        await db.commit()
    monkeypatch.setattr("app.browser_tasks.SessionLocal", session_maker)
    assert await _recover_stale() == 1
    async with session_maker() as db:
        inspection = await db.get(BrowserInspection, inspection_id)
        assert inspection is not None
        run = await db.get(ClassificationRun, inspection.run_id)
        assert run is not None
        assert inspection.status == BrowserStatus.FAILED
        assert inspection.failure_code == "stale_browser_run"
        assert run.error_code == "browser_worker_lost"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(WebsiteClassification)
                .where(WebsiteClassification.website_id == website_id)
            )
            == 1
        )
